"""管理端路由：RSS 源配置（可运行时增删启停）+ 管理员白名单（P1-14）+ 资讯审核（P1-20）
+ 评论治理（P1-22）+ 敏感词库与命中日志（P1-23）。

鉴权与 `POST /api/news/refresh` 一致：需登录且具备管理能力——
`ADMIN_PHONES` 环境变量 **∪** `admin_users` 表白名单（P1-14 起）；
两边都为空表示该能力不可用（403）——避免生产环境因配置疏漏而暴露管理接口。

设计要点：
- **读取即播种**：列表/抓取前确保内置源已入库，新环境无需手工初始化；
- **停用而非删除**为默认建议，但删除能力保留（运营确实加错了源时需要）；
- **URL 协议白名单**：仅允许 http/https，防止注入 `file://` 等被抓取器请求；
- 健康字段由抓取流程回写，本模块只读不写；
- **末位管理员保护**：移除后若任何来源都不再有管理员，拒绝（40904）——
  否则生产环境可能把自己锁在门外，只能改环境变量重启来恢复；
- **资讯软下架**（P1-20）：理由必填 + 全程审计留痕；公开查询实时过滤
  （恢复无需等下一轮抓取），聚合按 id 去重也不会重复入库；
- **评论软隐藏**（P1-22）：与软下架同一处方——理由必填 + 审计留痕 +
  公开四路实时过滤 + 幂等；计数/热度联动让「显示 N 条却看到 N-1 条」
  的旧 bug（P1-12）不以另一种形式回来；
- **敏感词库**（P1-23）：运营自助维护（block 拦截 / mask 脱敏待复核 /
  allow 白名单豁免），改动**立即失效本进程缓存**（保存即生效，无需重启），
  并提供 `reload` 供集群下手动同步；命中日志是误杀复盘与申诉的唯一证据，
  故只读不可改。
"""
import base64
import binascii
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from ..config import (
    ADMIN_PHONES,
    FEED_HEALTH_KEEP_DAYS,
    FEED_HEALTH_LIST_DAYS,
    MODERATION_MIN_WORD_LEN,
)
from ..database import get_db
from ..deps import get_current_user
from ..feeds.health import daily_points, recent_failures, summarize, summarize_many, window_days
from ..feeds.sources import BUILTIN_SOURCES, CHANNEL_KEYS, ensure_seeded
from ..moderation import engine, normalize
from ..models import (
    AdminAuditLog,
    AdminUser,
    Comment,
    News,
    RssSource,
    SensitiveHit,
    SensitiveWord,
    User,
    now_str,
)
from ..ranking import bump_hot_score
from ..response import ok
from ..schemas import (
    AdminIn,
    CommentHideIn,
    NewsHideIn,
    NewsTopIn,
    SourceIn,
    SourceUpdateIn,
    WordIn,
    WordUpdateIn,
)
# ILIKE 转义只保留一份实现：规则虽短，但漏转义 `%` 等于把通配符交给用户输入
# （既是错误结果，也是潜在的信息收集面），不允许两边各写一遍后悄悄漂移。
from .news import _escape_like

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("toutiao.admin")

ALLOWED_SCHEMES = ("http", "https")

# 审计动作：`对象.动作` 命名，前端据此映射中文标签（无需再维护一份枚举表）
AUDIT_SOURCE_CREATE = "source.create"
AUDIT_SOURCE_UPDATE = "source.update"
AUDIT_SOURCE_DELETE = "source.delete"
AUDIT_SOURCE_RESET = "source.reset"
AUDIT_ADMIN_ADD = "admin.add"
AUDIT_ADMIN_REMOVE = "admin.remove"
# P1-20：资讯审核与下架
AUDIT_NEWS_HIDE = "news.hide"
AUDIT_NEWS_RESTORE = "news.restore"
AUDIT_NEWS_UPDATE = "news.update"
# P1-22：评论治理（软隐藏/恢复）
AUDIT_COMMENT_HIDE = "comment.hide"
AUDIT_COMMENT_RESTORE = "comment.restore"
# P1-23：敏感词库与复核
AUDIT_WORD_CREATE = "word.create"
AUDIT_WORD_UPDATE = "word.update"
AUDIT_WORD_DELETE = "word.delete"
AUDIT_WORD_RELOAD = "word.reload"
AUDIT_COMMENT_REVIEW = "comment.review"

# 管理端资讯排序键：published_at 为空按空串处理（倒序时排最后），与公开列表同口径
_PUB = func.coalesce(News.published_at, "")


def is_admin(phone: str | None, db: Session | None = None) -> bool:
    """是否具备管理能力。

    **双源并集**（P1-14）：
    - `ADMIN_PHONES` 环境变量保留为 break-glass——改配置重启即生效，不依赖库；
    - `admin_users` 表供日常运营——应用内增删，无需重启服务；
    - 两边都为空 = 整体关闭：配置疏漏时不应暴露管理接口（生产安全）。

    抽成独立函数而非在调用处各写一遍：登录响应也要复用同一判定
    （前端据此决定是否展示管理入口），两处实现一旦漂移，就会出现
    "入口看得见、一点就 403"或反之的怪象。
    """
    if not phone:
        return False
    if ADMIN_PHONES and phone in ADMIN_PHONES:
        return True
    return db is not None and db.get(AdminUser, phone) is not None


def _require_admin(user: User, db: Session) -> None:
    if not is_admin(user.phone, db):
        raise HTTPException(status_code=403, detail={"code": 40301, "message": "无权限执行该操作"})


def record_audit(db: Session, actor: str, action: str, target: str = "", detail=None) -> None:
    """写一条审计（**不提交**，由调用方与业务变更一起 commit）。

    刻意不做 try/except 静默：「改成功了但没留痕」比「改失败」更糟——
    前者会让事后排查误以为没人动过。同事务还能保证两者要么都成、要么都不成。
    """
    db.add(
        AdminAuditLog(
            actor_phone=actor,
            action=action,
            target=target or "",
            detail=json.dumps(detail or {}, ensure_ascii=False, default=str),
            created_at=now_str(),
        )
    )


def _validate_url(url: str) -> None:
    """仅允许 http/https：抓取器会真的去请求这个地址，必须挡住 file:// 等协议。"""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise HTTPException(
            status_code=400, detail={"code": 40004, "message": "源地址必须为 http/https URL"}
        )


def _out(row: RssSource, health: dict | None = None) -> dict:
    return {
        "key": row.key,
        "name": row.name,
        "channel": row.channel,
        "url": row.url,
        "weight": row.weight,
        "enabled": bool(row.enabled),
        "lastStatus": row.last_status,
        "lastError": row.last_error,
        "lastItemCount": row.last_item_count,
        "lastFetchedAt": row.last_fetched_at,
        # P1-16：抓取礼仪（是否遵守对方 robots.txt），逐源可关
        "respectRobots": bool(row.respect_robots),
        # P1-18：失败分级与熔断。只报"异常"不够——"连挂 7 次"和"偶发抖一下"
        # 对运营是完全不同的两件事，前者要立刻处理，后者等系统自己重试即可。
        "consecutiveFailures": row.consecutive_failures or 0,
        "failureKind": row.failure_kind,
        "nextRetryAt": row.next_retry_at,
        # P1-19：近期成功率。`rate` 为 None 表示"窗口内没抓过"，
        # 与"抓了但全失败"是两回事——前端据此才能区分「近期无数据」与「近期全挂」。
        # 单源趋势页用的也是同一份数据，这里只放摘要：列表页的核心问题是
        # "该点进哪个源看细节"，而不是复刻趋势图。
        "health": health,
    }


@router.get("/sources", response_model=None)
def list_sources(
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """源列表（含抓取健康状态 + **近期成功率**）。

    成功率取自 P1-19 的健康历史（一次 GROUP BY 查全部源，避免 N+1）：
    「现在挂没挂」是 P1-16 就有的信息，列表上真正缺的是
    "**哪个源在悄悄退化**"——一个源可以一直绿着，但条数在持续下滑。
    """
    _require_admin(user, db)
    ensure_seeded(db)
    rows = db.query(RssSource).order_by(RssSource.weight.desc(), RssSource.key).all()
    health = summarize_many(db, [r.key for r in rows], FEED_HEALTH_LIST_DAYS)
    return ok(
        {
            "list": [_out(r, health.get(r.key)) for r in rows],
            "total": len(rows),
            # 前端要标出"近 N 天"，否则 96% 这个数字没有时间范围就是误导
            "healthDays": FEED_HEALTH_LIST_DAYS,
        }
    )


@router.get("/sources/{key}/health", response_model=None)
def source_health(
    key: str,
    days: int | None = Query(None, ge=1, le=365, description="回溯天数，默认取保留期"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单源健康趋势（P1-19）：汇总 + 按天序列 + 最近失败明细。

    与列表页的区别：列表回答"哪个源值得点进来"，这里回答"**它到底怎么了**"。
    三段数据各有分工，缺一段就答不全：

    - `summary`——窗口总览（成功率 / 成功轮均条数），判断"是抖动还是持续恶化"；
    - `points`——按天序列，判断"是不是每天凌晨固定挂"（对方维护窗口，别乱改配置）；
    - `failures`——失败原文，判断"要不要去改 URL / 换源"。

    `days` 会被收敛到保留期：请求 90 天而只留 30 天，只会得到 60 个空格子。
    响应里回传实际生效的 `days`，前端才能正确标注时间范围。
    """
    _require_admin(user, db)
    ensure_seeded(db)
    if db.get(RssSource, key) is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    win = window_days(days)
    return ok(
        {
            "key": key,
            "days": win,
            # 保留期一并发给前端：窗口被收敛时，界面要能解释"为什么最多只能看 30 天"
            "keepDays": FEED_HEALTH_KEEP_DAYS,
            "summary": summarize(db, key, win),
            "points": daily_points(db, key, win),
            "failures": recent_failures(db, key, win),
        }
    )


@router.post("/sources", response_model=None)
def create_source(
    body: SourceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新增源。key 冲突返回 409。"""
    _require_admin(user, db)
    _validate_url(body.url)
    ensure_seeded(db)
    if db.get(RssSource, body.key) is not None:
        # 40902 而非 40901：40901 已表示「手机号已注册」，同一码两义会让前端文案错配
        raise HTTPException(
            status_code=409, detail={"code": 40902, "message": "该源标识已存在"}
        )
    now = now_str()
    row = RssSource(
        key=body.key,
        name=body.name,
        channel=body.channel,
        url=body.url,
        weight=body.weight,
        enabled=body.enabled,
        respect_robots=body.respectRobots,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    record_audit(
        db,
        user.phone,
        AUDIT_SOURCE_CREATE,
        body.key,
        {"snapshot": _out(row)},
    )
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已新增源")


@router.patch("/sources/{key}", response_model=None)
def update_source(
    key: str,
    body: SourceUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """局部更新源（改权重 / 启停 / 换地址）。"""
    _require_admin(user, db)
    # 先确保内置源已入库：否则在尚未触发过抓取的新环境里，改一个"明明存在"的内置源会 404
    ensure_seeded(db)
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    if body.url is not None:
        _validate_url(body.url)
    before = _out(row)
    # 入参字段名 → 模型列名。多词入参走驼峰（`respectRobots`），模型侧是蛇形——两边
    # 必须显式对齐，否则 pydantic 会静默丢掉前端传来的字段（详见 schemas.SourceIn）。
    COLUMN_OF = {"respectRobots": "respect_robots"}
    for field in ("name", "channel", "url", "weight", "enabled", "respectRobots"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, COLUMN_OF.get(field, field), value)
    row.updated_at = now_str()
    after = _out(row)
    # 只记真正变化的字段：无变化的 PATCH（前端常因表单整体提交而触发）不该灌进时间线
    changes = {
        k: [before[k], after[k]]
        for k in ("name", "channel", "url", "weight", "enabled", "respectRobots")
        if before[k] != after[k]
    }
    if changes:
        record_audit(db, user.phone, AUDIT_SOURCE_UPDATE, key, {"changes": changes})
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已更新源")


@router.delete("/sources/{key}", response_model=None)
def delete_source(
    key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除源。删除后若表空了，下次读取会重新播种内置源。"""
    _require_admin(user, db)
    ensure_seeded(db)  # 同上：避免删除内置源时因尚未播种而 404
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    # 删除后行就没了，快照必须在 delete 之前取
    record_audit(db, user.phone, AUDIT_SOURCE_DELETE, key, {"snapshot": _out(row)})
    db.delete(row)
    db.commit()
    return ok({"key": key}, message="已删除源")


@router.post("/sources/reset", response_model=None)
def reset_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """重置为内置源：删除库中全部源并重新播种。

    用于把运营改坏的配置恢复出厂。会丢弃自定义源，故单独一个端点而非复用删除。
    """
    _require_admin(user, db)
    db.query(RssSource).delete()
    db.commit()
    seeded = ensure_seeded(db)
    record_audit(db, user.phone, AUDIT_SOURCE_RESET, "", {"seeded": seeded})
    db.commit()
    logger.info("管理员 %s 重置 RSS 源为内置 %d 个", user.phone, seeded)
    return ok({"seeded": seeded}, message=f"已重置为 {seeded} 个内置源")


# ---------------------------------------------------------------- 资讯审核与下架（P1-20）


def _news_out(n: News) -> dict:
    """管理端资讯行 → 输出：公开 `news.to_out` 的超集，附下架三件套与排查字段。

    `publishedAt` / `hotScore` 管理端专供：置顶决策看热度，时间线定位看发布时间。
    `hiddenReason` / `hiddenAt` 仅在已下架时有值（恢复即清空）。
    """
    return {
        "id": n.id,
        "title": n.title,
        "summary": n.summary or None,
        "source": n.source,
        "commentCount": n.comment_count or 0,
        "time": n.time,
        "images": n.images or None,
        "cover": n.cover or None,
        "videoUrl": n.video_url or None,
        "playCount": n.play_count,
        "isTop": bool(n.is_top),
        "channel": n.channel,
        "variant": n.variant,
        "publishedAt": n.published_at,
        # round 掉浮点噪声（0.30000000000000004 在界面上就是纯噪音）
        "hotScore": round(float(n.hot_score or 0.0), 4),
        "hidden": bool(n.hidden),
        "hiddenAt": n.hidden_at,
        "hiddenReason": n.hidden_reason,
    }


def _encode_news_cursor(n: News) -> str:
    """游标 = base64(JSON) `[published_at, id]`，与排序键逐一对应。"""
    raw = json.dumps(
        [n.published_at or "", n.id], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_news_cursor(cursor: str) -> tuple[str, str] | None:
    """解码游标 → `(published_at, id)`；非法返回 None（调用方 400）。"""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        pub, nid = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(pub, str) or not isinstance(nid, str):
        return None
    return pub, nid


@router.get("/news", response_model=None)
def list_news_admin(
    status: str = Query("all", pattern="^(all|visible|hidden)$"),
    keyword: str = Query("", max_length=50),
    channel: str | None = Query(None, max_length=30),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="上一页末条的游标"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """管理端资讯列表（P1-20）：筛选 + 搜索 + 游标分页。

    - `status`：`all`（默认，**含已下架**——运营要能核验自己的下架操作）/
      `visible` / `hidden`；
    - `keyword`：标题/摘要 ILIKE（转义规则与公开搜索同一实现）；
    - 排序 `published_at DESC, id DESC`：管理端要的是时间线，不搞置顶优先；
    - `total` 始终返回：管理端低频，COUNT 的代价可接受，界面要显示"共 N 条"。
    """
    _require_admin(user, db)
    if channel is not None and channel not in CHANNEL_KEYS:
        raise HTTPException(
            status_code=422, detail={"code": 42201, "message": f"频道不存在：{channel}"}
        )
    q = db.query(News)
    if status == "visible":
        q = q.filter(News.hidden.is_(False))
    elif status == "hidden":
        q = q.filter(News.hidden.is_(True))
    if channel:
        q = q.filter(News.channel == channel)
    kw = keyword.strip()
    if kw:
        like = f"%{_escape_like(kw)}%"
        q = q.filter(
            or_(News.title.ilike(like, escape="\\"), News.summary.ilike(like, escape="\\"))
        )
    total = q.count()

    if cursor:
        decoded = _decode_news_cursor(cursor)
        if decoded is None:
            raise HTTPException(status_code=400, detail={"code": 40003, "message": "分页游标无效"})
        pub, nid = decoded
        q = q.filter(or_(_PUB < pub, and_(_PUB == pub, News.id < nid)))

    rows = q.order_by(_PUB.desc(), News.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    last = items[-1] if items else None
    return ok(
        {
            "list": [_news_out(n) for n in items],
            "total": total,
            "hasMore": has_more,
            "nextCursor": _encode_news_cursor(last) if has_more and last is not None else None,
        }
    )


@router.get("/news/{news_id}", response_model=None)
def get_news_admin(
    news_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """管理端单条资讯（含下架状态）。

    40404 与公开端的 40400 刻意分开：管理端查的是"库里有没有这条"，
    不存在就是不存在；公开端才需要把"被下了"伪装成"不存在"。
    """
    _require_admin(user, db)
    item = db.get(News, news_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": 40404, "message": "资讯不存在"})
    return ok(_news_out(item))


@router.post("/news/{news_id}/hide", response_model=None)
def hide_news(
    news_id: str,
    body: NewsHideIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """下架资讯（软下架，**幂等**）。

    理由必填在 schema 层把关（空/纯空白 → 422）——「留痕是准入条件」。
    已下架时**不覆盖原理由、不重复记审计**（与 update_source「无变化不记」
    同思路），直接返回当前状态：重复下架要么是界面 Bug，要么是网络重试，
    都不该再造一条痕迹、抹掉第一次下架的原始理由。
    """
    _require_admin(user, db)
    item = db.get(News, news_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": 40404, "message": "资讯不存在"})
    if not item.hidden:
        item.hidden = True
        item.hidden_at = now_str()
        item.hidden_reason = body.reason  # schema 已 strip
        # detail 带标题：审计页的 target 只有 id，运营认标题不认 id
        record_audit(
            db,
            user.phone,
            AUDIT_NEWS_HIDE,
            news_id,
            {"reason": item.hidden_reason, "title": item.title},
        )
        db.commit()
        db.refresh(item)
        logger.info("管理员 %s 下架资讯 %s（理由：%s）", user.phone, news_id, item.hidden_reason)
    return ok(_news_out(item), message="已下架")


@router.post("/news/{news_id}/restore", response_model=None)
def restore_news(
    news_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """恢复已下架资讯（**幂等**）。

    恢复即重新出现在全部公开查询中——过滤条件是实时的，无需等下一轮抓取；
    聚合按 id 去重，恢复也不会造成重复入库。已可见时直接返回、不记审计。
    """
    _require_admin(user, db)
    item = db.get(News, news_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": 40404, "message": "资讯不存在"})
    if item.hidden:
        item.hidden = False
        # 理由/时间一并清空：理由只对下架状态有意义，留着会让下一次下架的
        # 审计记录读起来张冠李戴
        item.hidden_at = None
        item.hidden_reason = None
        record_audit(db, user.phone, AUDIT_NEWS_RESTORE, news_id, {"title": item.title})
        db.commit()
        db.refresh(item)
        logger.info("管理员 %s 恢复资讯 %s", user.phone, news_id)
    return ok(_news_out(item), message="已恢复")


@router.patch("/news/{news_id}", response_model=None)
def update_news_admin(
    news_id: str,
    body: NewsTopIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """局部更新资讯（当前仅置顶开关）。字段级 diff 审计，对齐 `update_source`。"""
    _require_admin(user, db)
    item = db.get(News, news_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": 40404, "message": "资讯不存在"})
    before = _news_out(item)
    if body.isTop is not None:
        item.is_top = body.isTop
    after = _news_out(item)
    # 只记真正变化的字段：无变化的 PATCH（前端常因表单整体提交而触发）不该灌进时间线
    changes = {k: [before[k], after[k]] for k in ("isTop",) if before[k] != after[k]}
    if changes:
        record_audit(
            db,
            user.phone,
            AUDIT_NEWS_UPDATE,
            news_id,
            {"changes": changes, "title": item.title},
        )
        db.commit()
        db.refresh(item)
    return ok(_news_out(item), message="已更新")


# ---------------------------------------------------------------- 评论治理（P1-22）


def _comment_out(c: Comment, *, news_title: str = "", reply_count: int = 0) -> dict:
    """管理端评论行 → 输出。含隐藏三件套与所属资讯标题（治理认内容不认 id）。

    与公开侧 `to_out` 刻意分开：管理端要看隐藏状态/理由/时间；公开侧这些
    字段一概不暴露——隐藏的评论不该被看到，更不该被读到"它被隐藏了"。
    """
    return {
        "id": c.id,
        "newsId": c.news_id,
        "newsTitle": news_title,
        "userId": c.user_id,
        "userName": c.user_name,
        "content": c.content,
        "createdAt": c.created_at,
        "likes": c.likes or 0,
        "parentId": c.parent_id,
        "rootId": c.root_id,
        "replyCount": reply_count,
        "isDeleted": bool(c.is_deleted),
        "hidden": bool(c.hidden),
        "hiddenAt": c.hidden_at,
        "hiddenReason": c.hidden_reason,
        # P1-23：待复核 = 命中敏感词被脱敏后发布，还没被人工看过。
        # 与 hidden 正交（hidden 是已处置且不可见，pending 是未处置且可见），
        # 运营在列表里要能同时看见两种标记，否则会重复处置或漏处置。
        "pendingReview": bool(c.pending_review),
        "reviewedAt": c.reviewed_at,
    }


def _comment_not_found() -> HTTPException:
    """40405：管理端视角的「评论不存在」。

    与公开端的 40401 刻意分开——管理端查的是「库里有没有这条」。
    已软删除的评论对治理视图同样不存在：删除是作者的不可逆行为，
    没有可恢复的状态，混进列表只会干扰运营。
    """
    return HTTPException(
        status_code=404, detail={"code": 40405, "message": "评论不存在或已删除"}
    )


def _reply_counts(db: Session, items: list[Comment]) -> dict[str, int]:
    """批量统计主评论的**当前可见**回复数（已删除/已隐藏的不计）。

    只对主评论（root_id 为 NULL）统计；界面据此提示「隐藏主评论将同时
    隐藏其 N 条回复」。一次 GROUP BY，避免逐条 N+1。
    """
    root_ids = [c.id for c in items if c.root_id is None]
    if not root_ids:
        return {}
    return dict(
        db.query(Comment.root_id, func.count())
        .filter(
            Comment.root_id.in_(root_ids),
            Comment.is_deleted.is_(False),
            Comment.hidden.is_(False),
        )
        .group_by(Comment.root_id)
        .all()
    )


@router.get("/comments", response_model=None)
def list_comments_admin(
    # pending（P1-23）：命中敏感词被脱敏、等待人工复核的评论
    status: str = Query("all", pattern="^(all|visible|hidden|pending)$"),
    keyword: str = Query("", max_length=50),
    newsId: str | None = Query(None, max_length=32),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """管理端评论列表（P1-22）：筛选 + 搜索 + 游标分页。

    - `status`：`all`（默认，**含已隐藏**——运营要能核验自己的处置）/
      `visible` / `hidden` / `pending`（P1-23 脱敏待复核）；已软删除的一概
      不返回（作者侧不可逆行为）；
    - `keyword`：评论内容 ILIKE（转义规则与公开搜索同一实现）；
    - `newsId`：按资讯过滤（「这篇文章下有哪些评论被处置过」）；
    - 排序 `created_at DESC, id DESC`：与公开主列表同口径的时间线，
      游标为上一页末条 id（keyset，与公开评论分页同一套路）。
    """
    _require_admin(user, db)
    q = db.query(Comment).filter(Comment.is_deleted.is_(False))
    if status == "visible":
        q = q.filter(Comment.hidden.is_(False))
    elif status == "hidden":
        q = q.filter(Comment.hidden.is_(True))
    elif status == "pending":
        q = q.filter(Comment.pending_review.is_(True))
    if newsId:
        q = q.filter(Comment.news_id == newsId)
    kw = keyword.strip()
    if kw:
        like = f"%{_escape_like(kw)}%"
        q = q.filter(Comment.content.ilike(like, escape="\\"))
    total = q.count()

    if cursor:
        anchor = db.get(Comment, cursor)
        if anchor is not None:
            # (created_at, id) < (anchor.created_at, anchor.id)
            q = q.filter(
                or_(
                    Comment.created_at < anchor.created_at,
                    and_(Comment.created_at == anchor.created_at, Comment.id < anchor.id),
                )
            )

    rows = q.order_by(Comment.created_at.desc(), Comment.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    # 批量补两份上下文：所属资讯标题 + 主评论的可见回复数（各一次查询）
    news_ids = {c.news_id for c in items}
    titles = (
        {n.id: n.title for n in db.query(News).filter(News.id.in_(news_ids)).all()}
        if news_ids
        else {}
    )
    counts = _reply_counts(db, items)
    return ok(
        {
            "list": [
                _comment_out(
                    c, news_title=titles.get(c.news_id, ""), reply_count=counts.get(c.id, 0)
                )
                for c in items
            ],
            "total": total,
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.get("/comments/{comment_id}", response_model=None)
def get_comment_admin(
    comment_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """管理端单条评论（含隐藏三件套与所属资讯标题）。"""
    _require_admin(user, db)
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise _comment_not_found()
    title = c.news.title if c.news is not None else ""
    counts = _reply_counts(db, [c])
    return ok(_comment_out(c, news_title=title, reply_count=counts.get(c.id, 0)))


@router.post("/comments/{comment_id}/hide", response_model=None)
def hide_comment(
    comment_id: str,
    body: CommentHideIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """隐藏评论（软隐藏，**幂等**）——对齐 hide_news。

    理由必填在 schema 层把关（空/纯空白 → 42200）——「留痕是准入条件」。
    已隐藏时不覆盖原理由、不重复记审计。隐藏后公开四路（主列表/回复/
    预览/计数）实时不可见，恢复即时回来——过滤条件是实时的，无需等
    任何定时任务。计数与热度分同步回退（恢复时加回）。
    """
    _require_admin(user, db)
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise _comment_not_found()
    news = db.query(News).filter(News.id == c.news_id).first()
    title = news.title if news is not None else ""
    if not c.hidden:
        c.hidden = True
        c.hidden_at = now_str()
        c.hidden_reason = body.reason  # schema 已 strip
        if news is not None:
            # 隐藏即从公开视图消失：计数回退（下限 0，防历史脏数据算成负数），
            # 恢复时加回——「显示 N 条却只看到 N-1 条」是 P1-12 修过的旧 bug
            news.comment_count = max((news.comment_count or 0) - 1, 0)
        record_audit(
            db,
            user.phone,
            AUDIT_COMMENT_HIDE,
            comment_id,
            {
                "reason": c.hidden_reason,
                # 评论没有标题，审计时间线认内容片段（运营认内容不认 id）
                "content": c.content[:50],
                "newsTitle": title,
                "userName": c.user_name,
            },
        )
        db.commit()
        db.refresh(c)
        bump_hot_score(db, c.news_id)
        logger.info("管理员 %s 隐藏评论 %s（理由：%s）", user.phone, comment_id, c.hidden_reason)
    counts = _reply_counts(db, [c])
    return ok(
        _comment_out(c, news_title=title, reply_count=counts.get(c.id, 0)), message="已隐藏"
    )


@router.post("/comments/{comment_id}/restore", response_model=None)
def restore_comment(
    comment_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """恢复已隐藏评论（**幂等**）——对齐 restore_news。

    恢复清空理由/时间（理由只对隐藏状态有意义），计数与热度分同步加回。
    已可见时直接返回、不记审计。
    """
    _require_admin(user, db)
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise _comment_not_found()
    news = db.query(News).filter(News.id == c.news_id).first()
    title = news.title if news is not None else ""
    if c.hidden:
        c.hidden = False
        # 理由/时间一并清空：留着会让下一次隐藏的审计记录读起来张冠李戴
        c.hidden_at = None
        c.hidden_reason = None
        if news is not None:
            news.comment_count = (news.comment_count or 0) + 1
        record_audit(
            db,
            user.phone,
            AUDIT_COMMENT_RESTORE,
            comment_id,
            {
                "content": c.content[:50],
                "newsTitle": title,
                "userName": c.user_name,
            },
        )
        db.commit()
        db.refresh(c)
        bump_hot_score(db, c.news_id)
        logger.info("管理员 %s 恢复评论 %s", user.phone, comment_id)
    counts = _reply_counts(db, [c])
    return ok(
        _comment_out(c, news_title=title, reply_count=counts.get(c.id, 0)), message="已恢复"
    )


# ---------------------------------------------------------------- 敏感词库与命中日志（P1-23）


def _word_out(w: SensitiveWord) -> dict:
    return {
        "id": w.id,
        "word": w.word,
        "action": w.action,
        "category": w.category,
        "enabled": bool(w.enabled),
        # 命中次数：高频词要么是热点、要么是误杀大户——两种情况运营都必须
        # 看见，否则词库会悄悄腐化（一堆没人管的词每天在拦正常发言）
        "hitCount": w.hit_count or 0,
        "createdBy": w.created_by,
        "createdAt": w.created_at,
        "updatedAt": w.updated_at,
    }


def _hit_out(h: SensitiveHit) -> dict:
    return {
        "id": h.id,
        "word": h.word,
        "action": h.action,
        "field": h.field,
        "commentId": h.comment_id,
        "newsId": h.news_id,
        "userId": h.user_id,
        "userName": h.user_name,
        # **原文**片段：脱敏不可逆，判断误杀与受理申诉全靠它
        "snippet": h.snippet,
        "createdAt": h.created_at,
    }


def _word_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": 40406, "message": "敏感词不存在"})


@router.get("/words", response_model=None)
def list_words(
    keyword: str = Query("", max_length=50),
    action: str = Query("", pattern="^(|block|mask|allow)$"),
    enabled: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """敏感词库列表。

    用 `limit + offset` 而非游标：运营要的是「一遍扫完 + 能跳页核对」，
    词库量级（百~千）也不支持 keyset 的那点性能收益。
    """
    _require_admin(user, db)
    q = db.query(SensitiveWord)
    kw = keyword.strip()
    if kw:
        like = f"%{_escape_like(kw)}%"
        q = q.filter(SensitiveWord.word.ilike(like, escape="\\"))
    if action:
        q = q.filter(SensitiveWord.action == action)
    if enabled is not None:
        q = q.filter(SensitiveWord.enabled.is_(enabled))
    total = q.count()
    rows = q.order_by(SensitiveWord.hit_count.desc(), SensitiveWord.id.desc()).limit(limit).offset(offset).all()
    return ok({"list": [_word_out(w) for w in rows], "total": total})


@router.post("/words", response_model=None)
def create_word(
    body: WordIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新增敏感词。

    - 归一化后不足 `MODERATION_MIN_WORD_LEN` 个有效字符 → **42202**：1 字词
      在正常表达里遍地都是，挡在入库这一步比事后救火便宜。
      （刻意不复用 42201——它已承载「频道不存在 / 排序方式不支持」两种语义，
      同一个码挂三种场景会让前端文案错配；）
    - 重复词 → 40905（唯一约束在库里，但提前判一次才能给出可解释的错误）。
    """
    _require_admin(user, db)
    word = body.word  # schema 已 strip
    norm, _ = normalize(word)
    if len(norm) < MODERATION_MIN_WORD_LEN:
        raise HTTPException(
            status_code=422,
            detail={
                "code": 42202,
                "message": f"敏感词至少需 {MODERATION_MIN_WORD_LEN} 个有效字符（空格与标点不计）",
            },
        )
    if db.query(SensitiveWord).filter(SensitiveWord.word == word).first() is not None:
        raise HTTPException(status_code=409, detail={"code": 40905, "message": "该敏感词已存在"})
    ts = now_str()
    row = SensitiveWord(
        word=word,
        action=body.action,
        category=body.category,
        enabled=body.enabled,
        hit_count=0,
        created_by=user.phone,
        created_at=ts,
        updated_at=ts,
    )
    db.add(row)
    record_audit(
        db,
        user.phone,
        AUDIT_WORD_CREATE,
        word,
        {"word": word, "action": body.action, "category": body.category, "enabled": body.enabled},
    )
    db.commit()
    db.refresh(row)
    # 保存即生效：让本进程的词库缓存立刻失效（集群其他实例靠 TTL 兜底）
    engine.invalidate()
    logger.info("管理员 %s 新增敏感词 %s（%s）", user.phone, word, body.action)
    return ok(_word_out(row), message="已添加")


@router.patch("/words/{word_id}", response_model=None)
def update_word(
    word_id: int,
    body: WordUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """局部更新敏感词（**不允许改词本身**：改词等于删一条加一条）。"""
    _require_admin(user, db)
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise _word_not_found()
    changes: dict[str, list] = {}
    if body.action is not None and body.action != row.action:
        changes["action"] = [row.action, body.action]
        row.action = body.action
    if body.category is not None and body.category != row.category:
        changes["category"] = [row.category, body.category]
        row.category = body.category
    if body.enabled is not None and body.enabled != bool(row.enabled):
        changes["enabled"] = [bool(row.enabled), body.enabled]
        row.enabled = body.enabled
    if not changes:
        return ok(_word_out(row))
    row.updated_at = now_str()
    record_audit(db, user.phone, AUDIT_WORD_UPDATE, row.word, {"changes": changes})
    db.commit()
    db.refresh(row)
    engine.invalidate()
    return ok(_word_out(row), message="已更新")


@router.delete("/words/{word_id}", response_model=None)
def delete_word(
    word_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除敏感词（审计留快照：删错了要能知道原来是什么）。

    停用（`enabled=false`）通常比删除更合适——删除后命中日志里那个词会失去
    对应配置，复盘时读不出它当年是 block 还是 mask。
    """
    _require_admin(user, db)
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise _word_not_found()
    snapshot = _word_out(row)
    record_audit(db, user.phone, AUDIT_WORD_DELETE, row.word, {"snapshot": snapshot})
    db.delete(row)
    db.commit()
    engine.invalidate()
    return ok(snapshot, message="已删除")


@router.post("/words/reload", response_model=None)
def reload_words(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """强制重建本进程的词库索引。

    改词已经会自动失效本进程缓存，这个端点是给**其他实例**用的：
    集群下 B 实例要等 TTL 才感知到 A 实例的改动，需要即时生效时点一下它。
    """
    _require_admin(user, db)
    engine.invalidate()
    engine.ensure(db)
    count = (
        db.query(SensitiveWord).filter(SensitiveWord.enabled.is_(True)).count()
    )
    record_audit(db, user.phone, AUDIT_WORD_RELOAD, "", {"count": count})
    db.commit()
    return ok({"count": count}, message="词库已重载")


@router.get("/moderation/hits", response_model=None)
def list_hits(
    word: str = Query("", max_length=100),
    action: str = Query("", pattern="^(|block|mask)$"),
    field: str = Query("", pattern="^(|content|name)$"),
    limit: int = Query(20, ge=1, le=100),
    cursor: int | None = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """敏感词命中日志（只读）。

    它的价值不在"看出事了"，而在**回答误杀**：哪个人、哪句话、命中哪个词、
    上下文是什么。脱敏后正文不可复原，这是唯一的证据来源——故只读、不清理。
    """
    _require_admin(user, db)
    q = db.query(SensitiveHit)
    if word.strip():
        q = q.filter(SensitiveHit.word == word.strip())
    if action:
        q = q.filter(SensitiveHit.action == action)
    if field:
        q = q.filter(SensitiveHit.field == field)
    total = q.count()
    if cursor is not None:
        q = q.filter(SensitiveHit.id < cursor)
    rows = q.order_by(SensitiveHit.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": [_hit_out(h) for h in items],
            "total": total,
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.post("/comments/{comment_id}/review", response_model=None)
def review_comment(
    comment_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """通过人工复核（**幂等**）：认定脱敏后的评论可以照常展示。

    复核只清 `pending_review`，不改可见性、不动计数——评论从一开始就可见
    （只是被脱敏），复核是"人看过了"这个事实，不是"重新放行"。
    认为仍不合适时应当走**隐藏**（`POST .../hide`），而不是留着待复核标记。
    """
    _require_admin(user, db)
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise _comment_not_found()
    news = db.query(News).filter(News.id == c.news_id).first()
    title = news.title if news is not None else ""
    if c.pending_review:
        c.pending_review = False
        c.reviewed_at = now_str()
        record_audit(
            db,
            user.phone,
            AUDIT_COMMENT_REVIEW,
            comment_id,
            {"content": c.content[:50], "newsTitle": title, "userName": c.user_name},
        )
        db.commit()
        db.refresh(c)
        logger.info("管理员 %s 复核通过评论 %s", user.phone, comment_id)
    counts = _reply_counts(db, [c])
    return ok(
        _comment_out(c, news_title=title, reply_count=counts.get(c.id, 0)), message="已通过复核"
    )


# ---------------------------------------------------------------- 管理员白名单（P1-14）


def _admin_out(row: AdminUser, db: Session) -> dict:
    """白名单行 → 输出。`registered` 区分「已注册账号 / 预授权」供界面展示。"""
    # 按 phone 查而非 db.get(User, phone)：User 主键是自增 id，
    # 拿手机号当主键查会永远查不到（registered 恒为 False）。
    account = db.query(User).filter(User.phone == row.phone).first()
    return {
        "phone": row.phone,
        "note": row.note or "",
        "addedBy": row.added_by or "",
        "createdAt": row.created_at,
        "registered": account is not None,
        "name": account.name if account else "",
    }


@router.get("/users", response_model=None)
def list_admin_users(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """管理员白名单列表（DB 源）。

    附 `envAdminCount`（环境变量来源的管理员**数量**，不下发号码）：
    界面据此解释「为什么移除最后一位库管理员仍被允许」——env 是 break-glass。
    """
    _require_admin(user, db)
    rows = db.query(AdminUser).order_by(AdminUser.created_at, AdminUser.phone).all()
    return ok(
        {
            "list": [_admin_out(r, db) for r in rows],
            "total": len(rows),
            "envAdminCount": len(ADMIN_PHONES),
        }
    )


@router.post("/users", response_model=None)
def add_admin_user(
    body: AdminIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """添加管理员。

    已在**任一来源**（env 或库）中的手机号返回 40903——重复入库只会让
    界面出现一行"管不了其实也无需管"的冗余记录。
    """
    _require_admin(user, db)
    if db.get(AdminUser, body.phone) is not None or (
        ADMIN_PHONES and body.phone in ADMIN_PHONES
    ):
        raise HTTPException(status_code=409, detail={"code": 40903, "message": "该手机号已是管理员"})
    row = AdminUser(
        phone=body.phone,
        note=body.note.strip(),
        added_by=user.phone,
        created_at=now_str(),
    )
    db.add(row)
    record_audit(db, user.phone, AUDIT_ADMIN_ADD, body.phone, {"note": row.note or ""})
    db.commit()
    db.refresh(row)
    logger.info("管理员 %s 添加管理员 %s（备注：%s）", user.phone, body.phone, row.note or "无")
    return ok(_admin_out(row, db), message="已添加管理员")


@router.delete("/users/{phone}", response_model=None)
def remove_admin_user(
    phone: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """移除管理员。

    **末位保护**：移除后若任何来源都不再有任何管理员，拒绝（40904）。
    注意 env 管理员不受库操作影响——移除一位"同时也在 env 里"的管理员时，
    env 仍在授权，故只有 env 为空且这是库中最后一条时才拦截。
    """
    _require_admin(user, db)
    row = db.get(AdminUser, phone)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40403, "message": "管理员不存在"})
    if db.query(AdminUser).count() - 1 <= 0 and not ADMIN_PHONES:
        raise HTTPException(
            status_code=409, detail={"code": 40904, "message": "已是最后一位管理员，不能移除"}
        )
    record_audit(db, user.phone, AUDIT_ADMIN_REMOVE, phone, {"note": row.note or ""})
    db.delete(row)
    db.commit()
    logger.info("管理员 %s 移除管理员 %s", user.phone, phone)
    return ok({"phone": phone}, message="已移除管理员")


# ---------------------------------------------------------------- 运营审计（P1-15）


def _audit_out(row: AdminAuditLog) -> dict:
    """detail 存的是 JSON 串；解析失败降级为空对象（宁可少显示，也不要整页报错）。"""
    try:
        detail = json.loads(row.detail) if row.detail else {}
        if not isinstance(detail, dict):
            detail = {}
    except (ValueError, TypeError):
        detail = {}
    return {
        "id": row.id,
        "actorPhone": row.actor_phone,
        "action": row.action,
        "target": row.target or "",
        "detail": detail,
        "createdAt": row.created_at,
    }


@router.get("/audit", response_model=None)
def list_audit(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="上一页末条的 id"),
    target: str | None = Query(None, description="按对象筛选（源 key / 手机号）"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """审计时间线（新的在前，游标分页）。

    游标用**自增 id** 而非时间戳：created_at 是秒级精度，同秒多条会让 keyset 比较
    出现重复/遗漏（评论分页踩过同一个坑）。
    """
    _require_admin(user, db)
    q = db.query(AdminAuditLog)
    if target:
        q = q.filter(AdminAuditLog.target == target)
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=400, detail={"code": 40003, "message": "分页游标无效"})
        q = q.filter(AdminAuditLog.id < int(cursor))
    rows = q.order_by(AdminAuditLog.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": [_audit_out(r) for r in items],
            "hasMore": has_more,
            "nextCursor": str(items[-1].id) if has_more and items else None,
        }
    )
