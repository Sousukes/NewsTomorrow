"""管理端路由：RSS 源配置（可运行时增删启停）+ 管理员白名单（P1-14）+ 资讯审核（P1-20）。

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
  （恢复无需等下一轮抓取），聚合按 id 去重也不会重复入库。
"""
import base64
import binascii
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from ..config import ADMIN_PHONES, FEED_HEALTH_KEEP_DAYS, FEED_HEALTH_LIST_DAYS
from ..database import get_db
from ..deps import get_current_user
from ..feeds.health import daily_points, recent_failures, summarize, summarize_many, window_days
from ..feeds.sources import BUILTIN_SOURCES, CHANNEL_KEYS, ensure_seeded
from ..models import AdminAuditLog, AdminUser, News, RssSource, User, now_str
from ..response import ok
from ..schemas import AdminIn, NewsHideIn, NewsTopIn, SourceIn, SourceUpdateIn
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
