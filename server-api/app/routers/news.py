"""资讯路由：列表（游标分页）/ 搜索 / 详情。

排序契约（H1 / P1-7 扩展）：
    置顶优先 → 排序键倒序 → id 倒序（保证稳定，避免翻页重复/漏项）。
    历史实现按字符串主键 `id` 排序，顺序既非时间也非热度且 `is_top` 失效，
    这里统一收敛到 `_order_by()`。

    `sort` 三模式（P1-7）：
    - `latest`（默认）：排序键为 `published_at`，与 H1 完全一致；
    - `hot`：排序键为落库的 `hot_score`（互动加权 × 时间衰减）；
    - `for-you`：排序键为 `hot_score + 频道偏好加分`，偏好来自用户收藏/浏览历史。

分页契约（H5 / P1-7 扩展）：
    - 推荐 `cursor`（游标/keyset 分页），深分页 O(1)，返回 `nextCursor` + `hasMore`；
    - 兼容旧 `page` 参数（offset 分页），便于灰度；
    - 游标为 base64(JSON)，对客户端不透明。**v2 起携带 sort 与排序键**：
      `[2, sort, is_top, key, id]`；v1 `[is_top, published_at, id]` 仍按 latest 兼容解析。
      若不携带 sort，`hot` 游标会被当成 `published_at` 解析而错位。
"""
import base64
import binascii
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user_optional
from ..models import News, User, UserFollow
from ..ranking import (
    SORT_FOR_YOU,
    SORT_HOT,
    SORT_LATEST,
    SORT_MODES,
    channel_preferences,
    rank_key,
)
from ..search_stats import HOT_WINDOW_DAYS, hot_terms, record_search
from ..response import ok
from ..schemas import NewsOut

router = APIRouter(prefix="/api/news", tags=["news"])
logger = logging.getLogger("toutiao.news")

# 排序用的"发布时间"表达式：published_at 为空时按空串处理（倒序时排最后）
_PUB = func.coalesce(News.published_at, "")

# pg_trgm 可用性缓存：扩展是否存在不会在运行时变化，没必要每次请求都查
_TRGM_CHECKED = False
_TRGM_AVAILABLE = False

# 相似度下限：低于此值说明标题几乎无共同三元组，不宜作为"相关"呈现
_SIMILARITY_THRESHOLD = 0.05

# 游标版本：v2 起携带 sort 与排序键（见模块 docstring）
_CURSOR_V2 = 2

# 关注流标识（E6b）。**不做成 channel**：`CHANNELS` 同时被后端用来校验运营填的
# 频道（`feeds/sources.py::CHANNEL_KEYS`），混进去会让管理端多出一个非法频道选项。
FEED_FOLLOWING = "following"


def _trgm_available(db: Session) -> bool:
    """`pg_trgm` 扩展是否可用（决定能否用 similarity() 做相关度召回）。

    迁移 0005 会在 PostgreSQL 上创建该扩展，但**权限不足时会跳过**；
    SQLite 等方言则根本没有。因此必须运行时探测，不可无条件使用
    `func.similarity()`——否则在 SQLite 上会直接报"函数不存在"。
    """
    global _TRGM_CHECKED, _TRGM_AVAILABLE
    if _TRGM_CHECKED:
        return _TRGM_AVAILABLE
    if db.bind is None or db.bind.dialect.name != "postgresql":
        _TRGM_AVAILABLE = False
    else:
        try:
            _TRGM_AVAILABLE = bool(
                db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar()
            )
        except Exception as e:
            logger.warning("pg_trgm 探测失败，相关推荐退回同频道兜底：%s", e)
            _TRGM_AVAILABLE = False
    _TRGM_CHECKED = True
    return _TRGM_AVAILABLE


# ---- 搜索相关度（P1-11）----
# 分层信号强度：标题整词命中是最强相关信号（搜索词出现在标题里，基本就是用户要找的
# 那篇）；仅摘要命中弱一层；pg_trgm 相似度只在层内做细粒度排序（< 加分的最小间隔，
# 因此层级严格成立）。权重经本地 PG 真实中文查询验证（记录于《优化落地记录》P1-11）。
_SEARCH_TITLE_BONUS = 100
_SEARCH_SUMMARY_BONUS = 10
_SEARCH_TITLE_WEIGHT = 2

# ILIKE 模式串里的特殊字符：不转义时用户输入 `%` 会被当成通配符，
# 搜一个字就"匹配全部"——既是错误结果，也是潜在的信息收集面。
_LIKE_SPECIAL = ("\\", "%", "_")


def _escape_like(s: str) -> str:
    """转义 ILIKE 模式串里的 `\\` `%` `_`；调用方须同时传 `escape="\\"`。"""
    for ch in _LIKE_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    return s


def _search_ranked(db: Session, keyword: str, page: int, pageSize: int):
    """PG + pg_trgm：ILIKE 召回 + 分层加权相似度排序。

    召回用 ILIKE 而非 `%` 运算符：`%` 的默认阈值 0.3 对中文短词几乎召不回
    （两字词与长标题的共同三元组占比极低），调低阈值又会受大小写与长词稀释
    影响造成召回缺口；而 **ILIKE 可直接吃 pg_trgm 的 GIN 索引**
    （EXPLAIN 实测 `Bitmap Index Scan on ix_news_title_trgm`），
    大小写不敏感、无阈值、与旧行为召回口径一致，短词才退化为全表扫描（毫秒级）。
    """
    like = f"%{_escape_like(keyword)}%"
    # 相似度大小写敏感，两侧同时 lower 以与 ILIKE 的语义对齐
    kw = keyword.lower()
    sim_title = func.similarity(func.lower(News.title), kw)
    sim_summary = func.similarity(func.lower(func.coalesce(News.summary, "")), kw)
    title_hit = case((News.title.ilike(like, escape="\\"), _SEARCH_TITLE_BONUS), else_=0)
    summary_hit = case((News.summary.ilike(like, escape="\\"), _SEARCH_SUMMARY_BONUS), else_=0)
    score = title_hit + summary_hit + sim_title * _SEARCH_TITLE_WEIGHT + sim_summary

    query = db.query(News).filter(
        or_(News.title.ilike(like, escape="\\"), News.summary.ilike(like, escape="\\")),
        # P1-20：已下架内容不参与搜索召回
        News.hidden.is_(False),
    )
    total = query.count()
    items = (
        query.order_by(score.desc(), _PUB.desc(), News.id.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    return items, total


def _search_fallback(db: Session, keyword: str, page: int, pageSize: int):
    """无 pg_trgm（SQLite / 扩展缺失）：ILIKE 召回 + 标题命中优先。

    排序规则与 PG 路径同构：标题命中（0）先于仅摘要命中（1），同级按发布时间倒序、
    id 倒序。确定性可比；层内细粒度相似度排序是 PG 专属能力，此处不模仿。
    """
    like = f"%{_escape_like(keyword)}%"
    title_hit = case((News.title.ilike(like, escape="\\"), 0), else_=1)
    query = db.query(News).filter(
        or_(News.title.ilike(like, escape="\\"), News.summary.ilike(like, escape="\\")),
        # P1-20：已下架内容不参与搜索召回
        News.hidden.is_(False),
    )
    total = query.count()
    items = (
        query.order_by(title_hit, _PUB.desc(), News.id.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    return items, total


def to_out(n: News) -> dict:
    """ORM → 前端 NewsItem（camelCase，与前端 api/news.ts 归一化一致）。"""
    return {
        "id": n.id,
        "title": n.title,
        "summary": n.summary or None,
        "source": n.source,
        # E6b：来源稳定标识（可空）。前端据此判断"是否已关注"，
        # 缺省时前端按"不可关注"处理（旧后端 / mock 未同步时不会报错）
        "sourceKey": n.source_key,
        "commentCount": n.comment_count or 0,
        "time": n.time,
        "images": n.images or None,
        "cover": n.cover or None,
        "videoUrl": n.video_url or None,
        "playCount": n.play_count,
        "isTop": bool(n.is_top),
        "channel": n.channel,
        "variant": n.variant,
    }


def _order_by(q, sort: str = SORT_LATEST, weights: Optional[dict] = None):
    """统一排序：置顶 → 排序键倒序 → id 倒序（稳定）。

    `latest` 用 `published_at`，其余模式用 `ranking.rank_key()` 得到的分值表达式。
    """
    if sort == SORT_LATEST:
        return q.order_by(News.is_top.desc(), _PUB.desc(), News.id.desc())
    return q.order_by(News.is_top.desc(), rank_key(sort, weights).desc(), News.id.desc())


def _cursor_key(n: News, sort: str, weights: Optional[dict]) -> str | float:
    """游标携带的排序键值：与 `_order_by` 的排序键必须逐一对应。"""
    if sort == SORT_LATEST:
        return n.published_at or ""
    # 分值模式：取整到 6 位，避免 JSON 序列化浮点噪声导致游标比对失败
    return round(float(n.hot_score or 0.0) + _boost_of(n, sort, weights), 6)


def _boost_of(n: News, sort: str, weights: Optional[dict]) -> float:
    """频道偏好加分（仅 for-you 生效；与 rank_key 的表达式保持一致）。"""
    if sort != SORT_FOR_YOU or not weights:
        return 0.0
    from ..ranking import CHANNEL_BOOST_MAX

    return float(weights.get(n.channel, 0.0)) * CHANNEL_BOOST_MAX


def _encode_cursor(n: News, sort: str = SORT_LATEST, weights: Optional[dict] = None) -> str:
    payload = [_CURSOR_V2, sort, 1 if n.is_top else 0, _cursor_key(n, sort, weights), n.id]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> Optional[tuple[str, int, object, str]]:
    """解码游标 → `(sort, is_top, key, id)`；非法返回 None（调用方决定忽略或报错）。

    兼容 v1（3 元素 `[is_top, published_at, id]`）：按 `latest` 解析，
    使升级前已下发到客户端的游标仍然可用。
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, list):
        return None
    if len(payload) == 3:
        top, pub, nid = payload
        return SORT_LATEST, int(top), str(pub), str(nid)
    if len(payload) == 5 and payload[0] == _CURSOR_V2:
        _, sort, top, key, nid = payload
        if sort not in SORT_MODES:
            return None
        return str(sort), int(top), key, str(nid)
    return None


def _keyset_filter(sort: str, top: int, key, nid: str, weights: Optional[dict] = None):
    """游标之后（严格更小）的记录。

    排序为 `is_top DESC, <排序键> DESC, id DESC`：
    - 游标落在置顶区（top=1）：剩余置顶项需 (键,id) 更小，非置顶项全部在后 → 全收；
    - 游标落在非置顶区（top=0）：置顶项已全部返回 → 仅取 (键,id) 更小的非置顶项。
    （SQLAlchemy 不允许布尔列与 Python bool 字面量做 `<`，故用 is_() 展开。）
    """
    if sort == SORT_LATEST:
        col, k = _PUB, str(key)
    else:
        col, k = rank_key(sort, weights), float(key)
    after = or_(col < k, and_(col == k, News.id < nid))
    if top:
        return or_(and_(News.is_top.is_(True), after), News.is_top.is_(False))
    return and_(News.is_top.is_(False), after)


def _page_payload(
    items: list[News],
    total: int,
    page: int,
    page_size: int,
    sort: str = SORT_LATEST,
    weights: Optional[dict] = None,
) -> dict:
    last = items[-1] if items else None
    return {
        "list": [to_out(n) for n in items],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "sort": sort,
        # 取满一页才可能有下一页（游标续接用）
        "nextCursor": (
            _encode_cursor(last, sort, weights) if last and len(items) >= page_size else None
        ),
        "hasMore": len(items) >= page_size,
    }


@router.get("", response_model=None)
def list_news(
    channel: str = Query("recommend"),
    sort: str = Query(SORT_LATEST),
    page: Optional[int] = Query(
        None, ge=1, description="offset 分页页码；**不传即走 keyset 游标分页**（推荐）"
    ),
    pageSize: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="游标分页；优先于 page"),
    feed: Optional[str] = Query(
        None, description="`following` = 关注流（需登录，忽略 channel）；缺省为普通信息流"
    ),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """资讯列表。

    - `recommend`：聚合所有频道的默认信息流（设计语义），按统一排序返回；
    - 其他 channel：精确过滤；
    - `sort`：`latest`（默认，时间）/ `hot`（热度）/ `for-you`（热度 + 频道偏好）；
      `for-you` 在未登录或无行为数据时退化为 `hot`（偏好权重为空，加分恒为 0）；
    - **默认走 keyset 分页**（B2）：不传 `page` 即按游标语义取一页，
      首页也不再全表 `count()`；显式传 `page` 才走旧的 offset 分页
      （深分页 O(offset)，仅留给需要「第 N 页」语义的调用方）。

下架过滤（P1-20）：
    软下架的资讯（`hidden`）对所有公开查询不可见：列表 / 详情 / 搜索 /
    相关推荐四路统一加 `News.hidden.is_(False)`；详情与相关推荐对已下架目标
    直接 404（与「不存在」同码——对外不该暴露"它存在但被下了"）。
    热度重算仍计算已下架行：恢复的那一刻就该拿到新鲜分数。
"""
    if sort not in SORT_MODES:
        raise HTTPException(
            status_code=422, detail={"code": 42201, "message": f"排序方式不支持：{sort}"}
        )
    if feed not in (None, "", FEED_FOLLOWING):
        raise HTTPException(
            status_code=422, detail={"code": 42200, "message": f"不支持的 feed：{feed}"}
        )

    # 频道偏好只服务 for-you；未登录时为空 → 自然退化为纯热度排序
    weights = channel_preferences(db, current.id) if (current and sort == SORT_FOR_YOU) else {}

    q = db.query(News)
    if feed == FEED_FOLLOWING:
        # 关注流（E6b）：必须登录——未登录没有关注关系，静默返回空列表会让客户端
        # 误以为"关注了但没内容"，不如直接 401 让前端引导登录
        if current is None:
            raise HTTPException(
                status_code=401, detail={"code": 40100, "message": "未登录或登录已过期"}
            )
        followed = (
            db.execute(select(UserFollow.source_key).where(UserFollow.user_id == current.id))
            .scalars()
            .all()
        )
        # **忽略 channel**：前端从别的 tab 切过来时会带着上一次的频道，
        # 报错只会制造无意义的失败；关注流本身就是跨频道的
        if not followed:
            return ok(
                {
                    "list": [],
                    "total": None,
                    "page": None,
                    "pageSize": pageSize,
                    "sort": sort,
                    "nextCursor": None,
                    "hasMore": False,
                }
            )
        q = q.filter(News.source_key.in_(followed))
    elif channel and channel != "recommend":
        q = q.filter(News.channel == channel)
    # P1-20：已下架内容不进公开信息流（游标分页同样生效——过滤加在 keyset 之前）
    q = q.filter(News.hidden.is_(False))

    # B2：**默认**走 keyset。只有显式传了 `page` 才回退 offset 分页。
    # 此前首页（还没 cursor 时）落在 offset 分支里，于是每次打开页面都做一次
    # 全表 `count()`——那条路径上的用户占比是 100%。
    if cursor or page is None:
        c_sort = sort
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                raise HTTPException(
                    status_code=400, detail={"code": 40003, "message": "分页游标无效"}
                )
            c_sort, top, key, nid = decoded
            # 游标自带 sort：续接必须沿用首请求时的排序键，否则 keyset 比较无意义
            q = q.filter(_keyset_filter(c_sort, top, key, nid, weights))
        items = _order_by(q, c_sort, weights).limit(pageSize).all()
        return ok(
            {
                "list": [to_out(n) for n in items],
                # keyset 模式不做全表 count（正是性能收益来源）；total 交给客户端累计
                "total": None,
                "page": None,
                "pageSize": pageSize,
                "sort": c_sort,
                "nextCursor": (
                    _encode_cursor(items[-1], c_sort, weights) if len(items) >= pageSize else None
                ),
                "hasMore": len(items) >= pageSize,
            }
        )

    # 兼容路径：显式 page 才走 offset（此时 total 有意义）
    total = q.count()
    items = _order_by(q, sort, weights).offset((page - 1) * pageSize).limit(pageSize).all()
    return ok(_page_payload(items, total, page, pageSize, sort, weights))


# 注意：静态路径 /search 必须声明在 /{news_id} 之前，否则会被当成 news_id 匹配
@router.get("/search", response_model=None)
def search_news(
    q: str = Query("", max_length=50),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """服务端搜索：标题/摘要匹配，**按相关度排序**（P1-11）。

    - **PG + pg_trgm**：ILIKE 召回（GIN 索引可加速，大小写不敏感），按
      `100×标题命中 + 10×摘要命中 + 2×similarity(标题) + similarity(摘要)`
      分层加权排序——标题整词命中压过摘要命中，层内按相似度细排；
    - **SQLite / 扩展缺失**：ILIKE 召回，标题命中优先，其余按时间倒序。

    为何不用 `tsvector` + `ts_rank`：PostgreSQL 内置分词器**不做中文分词**，
    连续汉字整体算一个 token，`to_tsquery('simple', '暴雨')` 匹配不到
    「暴雨蓝色预警」，相关度排名会退化成乱序；引入 zhparser 需编译扩展，
    超出当前部署能力。pg_trgm 按**字符三元组**计算相似度，对中文天然有效，
    索引在迁移 0005 就已建好——本批次**零迁移**。

    响应契约不变：`{ list, total, page, pageSize, keyword, hasMore }`。
    """
    keyword = q.strip()
    if not keyword:
        return ok({"list": [], "total": 0, "page": page, "keyword": "", "hasMore": False})

    # 旁路统计：记录搜索词供热搜榜使用。失败不影响搜索结果本身。
    try:
        record_search(db, keyword, user_id=current.id if current else None)
    except Exception as e:
        logger.warning("搜索词记录失败（不影响搜索结果）: %s", e)
        db.rollback()

    search = _search_ranked if _trgm_available(db) else _search_fallback
    items, total = search(db, keyword, page, pageSize)
    return ok(
        {
            "list": [to_out(n) for n in items],
            "total": total,
            "page": page,
            "pageSize": pageSize,
            "keyword": keyword,
            "hasMore": (page - 1) * pageSize + len(items) < total,
        }
    )


@router.get("/search/hot", response_model=None)
def hot_search_terms(
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(HOT_WINDOW_DAYS, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """热搜词：近 `days` 天被搜索最多的词（真实统计，非写死）。

    无数据时返回空数组——前端应回退到不展示该区块，而不是展示误导性的假热词。
    """
    return ok({"list": hot_terms(db, limit=limit, days=days), "days": days})


@router.get("/{news_id}/related", response_model=None)
def related_news(
    news_id: str,
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """相关推荐：详情页"看了又看"。

    召回策略（逐级兜底，保证任何环境都有内容，不会开天窗）：
    1. **标题相似度**：PostgreSQL + pg_trgm 时用 `similarity(title, 当前标题)` 召回，
       并按相似度阈值过滤，避免把毫不相干的内容当成"相关"；
    2. **同频道最新**：相似度结果不足时补齐（也是 SQLite / 无扩展时的主策略）；
    3. **全站最新**：仍不足时兜底。

    始终排除当前资讯自身，且三级结果按 id 去重。
    """
    target = db.get(News, news_id)
    # 已下架的资讯与「不存在」同码返回：对外不暴露"它存在但被下了"。
    # 正常路径也到不了这里——详情页先 404，相关推荐没有入口。
    if target is None or target.hidden:
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "内容不存在或已下架"})

    picked: list[News] = []
    seen: set[str] = {news_id}

    # ---- 1) 标题相似度召回（仅 pg_trgm 可用时）----
    if _trgm_available(db):
        sim = func.similarity(News.title, target.title)
        rows = (
            db.query(News)
            .filter(News.id != news_id, sim > _SIMILARITY_THRESHOLD, News.hidden.is_(False))
            .order_by(sim.desc(), _PUB.desc(), News.id.desc())
            .limit(limit)
            .all()
        )
        for n in rows:
            if n.id not in seen:
                picked.append(n)
                seen.add(n.id)

    # ---- 2) 同频道最新补齐 ----
    if len(picked) < limit:
        rows = (
            _order_by(
                db.query(News).filter(
                    News.id != news_id,
                    News.channel == target.channel,
                    News.hidden.is_(False),
                    ~News.id.in_(seen) if seen else True,
                )
            )
            .limit(limit - len(picked))
            .all()
        )
        for n in rows:
            if n.id not in seen:
                picked.append(n)
                seen.add(n.id)

    # ---- 3) 全站最新兜底 ----
    if len(picked) < limit:
        rows = (
            _order_by(
                db.query(News).filter(News.id != news_id, News.hidden.is_(False), ~News.id.in_(seen))
            )
            .limit(limit - len(picked))
            .all()
        )
        for n in rows:
            if n.id not in seen:
                picked.append(n)
                seen.add(n.id)

    return ok({"list": [to_out(n) for n in picked], "total": len(picked)})


@router.get("/{news_id}", response_model=None)
def news_detail(news_id: str, db: Session = Depends(get_db)):
    # 已下架与「不存在」同码：对外不暴露"它存在但被下了"（P1-20）
    item = db.get(News, news_id)
    if item is None or item.hidden:
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "内容不存在或已下架"})
    # E1：正文只在详情返回（列表 to_out 刻意不带——一页 20 条几十 KB 无效带宽）。
    # 无正文（老数据/源没给）返回 null，前端显示「暂无正文」而非占位文案。
    out = to_out(item)
    out["content"] = item.content or None
    return ok(out)
