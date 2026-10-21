"""管理端 · 资讯审核与下架（P1-20）：软下架 + 理由必填 + 全程审计；公开查询实时过滤。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import base64
import binascii
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from ...database import get_db
from ...feeds.sources import CHANNEL_KEYS
from ...models import News, User, now_str
from ...response import ok
from ...schemas import NewsHideIn, NewsTopIn
from ..news import _escape_like
from . import require_admin
from ._shared import record_audit, AUDIT_NEWS_HIDE, AUDIT_NEWS_RESTORE, AUDIT_NEWS_UPDATE, _PUB

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

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
):
    """管理端资讯列表（P1-20）：筛选 + 搜索 + 游标分页。

    - `status`：`all`（默认，**含已下架**——运营要能核验自己的下架操作）/
      `visible` / `hidden`；
    - `keyword`：标题/摘要 ILIKE（转义规则与公开搜索同一实现）；
    - 排序 `published_at DESC, id DESC`：管理端要的是时间线，不搞置顶优先；
    - `total` 始终返回：管理端低频，COUNT 的代价可接受，界面要显示"共 N 条"。
    """
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
    news_id: str, db: Session = Depends(get_db)
):
    """管理端单条资讯（含下架状态）。

    40404 与公开端的 40400 刻意分开：管理端查的是"库里有没有这条"，
    不存在就是不存在；公开端才需要把"被下了"伪装成"不存在"。
    """
    item = db.get(News, news_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": 40404, "message": "资讯不存在"})
    return ok(_news_out(item))


@router.post("/news/{news_id}/hide", response_model=None)
def hide_news(
    news_id: str,
    body: NewsHideIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """下架资讯（软下架，**幂等**）。

    理由必填在 schema 层把关（空/纯空白 → 422）——「留痕是准入条件」。
    已下架时**不覆盖原理由、不重复记审计**（与 update_source「无变化不记」
    同思路），直接返回当前状态：重复下架要么是界面 Bug，要么是网络重试，
    都不该再造一条痕迹、抹掉第一次下架的原始理由。
    """
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
    news_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """恢复已下架资讯（**幂等**）。

    恢复即重新出现在全部公开查询中——过滤条件是实时的，无需等下一轮抓取；
    聚合按 id 去重，恢复也不会造成重复入库。已可见时直接返回、不记审计。
    """
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
    user: User = Depends(require_admin),
):
    """局部更新资讯（当前仅置顶开关）。字段级 diff 审计，对齐 `update_source`。"""
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


