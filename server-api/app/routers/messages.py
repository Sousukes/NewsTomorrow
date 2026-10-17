"""消息中心路由：列表（游标分页）/ 未读数 / 标记已读 / 全部已读。

已读模型（M2）：
    已读关系落在 `message_reads` 关联表（user × message），
    取代早期 `messages.read_by` JSON 数组——JSON 数组无法索引，
    未读数与已读判定只能全表遍历。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_current_user_optional
from ..models import Message, MessageRead, User, now_str
from ..response import ok

router = APIRouter(prefix="/api/messages", tags=["messages"])

DEFAULT_LIMIT = 30


def visible_condition(current: Optional[User]):
    """可见范围：**定向给自己的** 或 **广播（user_id IS NULL）**。

    未登录时只看广播消息，且 read 恒为 False。
    """
    broadcast = Message.user_id.is_(None)
    if current is None:
        return broadcast
    return or_(Message.user_id == current.id, broadcast)


def visible_ids_stmt(current: Optional[User]):
    return select(Message.id).where(visible_condition(current))


def to_out(m: Message, read_ids: set[str]) -> dict:
    return {
        "id": m.id,
        "type": m.type,
        "title": m.title,
        "summary": m.summary or "",
        "newsId": m.news_id,
        "time": m.time,
        "read": m.id in read_ids,
    }


def _read_ids(db: Session, current: Optional[User]) -> set[str]:
    """当前用户已读的消息 id 集合（仅取 id，避免全表加载）。"""
    if current is None:
        return set()
    rows = (
        db.query(MessageRead.message_id).filter(MessageRead.user_id == current.id).all()
    )
    return {r[0] for r in rows}


# ---- A2 排序键 ----
# 消息 id 是随机串（`msg_{uuid4hex}`），按 id 排序与真实时间无关。
# 改为按绝对创建时间倒序；存量行没有 created_at（迁移 0026 前的数据），
# coalesce 到空串兜底——空串小于任何 ISO 时间串，DESC 下自然排最后，
# 且 PG 与 SQLite 行为一致（不依赖 NULLS LAST，两者语法支持度不同）。
# 与 `news.published_at` 的 `_PUB` 同一款处方。
_SORT_TS = func.coalesce(Message.created_at, "")


@router.get("", response_model=None)
def list_messages(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """消息列表（定向给自己 + 广播；未登录只看广播且 read 恒为 False）。

    排序与分页（A2）：`(created_at DESC, id DESC)`，同秒用 id 决胜保证稳定。
    游标**格式不变**——仍是上一页末条的 id；服务端按该 id 回查排序键，
    取「严格更早」的一页。这样做而不是把时间戳编进游标：
    - 老格式游标（纯 id）继续可用，前端零改动；
    - 键值以库中权威行为准，客户端改游标也翻不出可见范围
      （锚点查询同样带 visible_condition）。
    锚点行不存在（被删/不可见）时忽略游标返回第一页——消息没有清理任务，
    这只在手工删数据后出现，此时「从头开始」比报错更不影响使用。
    """
    q = db.query(Message).filter(visible_condition(current))
    if cursor:
        anchor = (
            db.query(Message)
            .filter(Message.id == cursor, visible_condition(current))
            .first()
        )
        if anchor is not None:
            anchor_key = anchor.created_at or ""
            q = q.filter(
                or_(
                    _SORT_TS < anchor_key,
                    (_SORT_TS == anchor_key) & (Message.id < cursor),
                )
            )
    rows = q.order_by(_SORT_TS.desc(), Message.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    read_ids = _read_ids(db, current)
    return ok(
        {
            "list": [to_out(m, read_ids) for m in items],
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.get("/unread-count", response_model=None)
def unread_count(
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """未读数：给 TabBar 徽标用。

    只统计**可见范围内**的消息；已读数也要限定在可见集合内，
    否则用户读过又被移除可见性的消息会让未读数偏小甚至为负。
    """
    if current is None:
        return ok(0)
    visible = visible_ids_stmt(current)
    total = db.query(func.count(Message.id)).filter(visible_condition(current)).scalar() or 0
    read = (
        db.query(func.count(MessageRead.message_id))
        .filter(MessageRead.user_id == current.id, MessageRead.message_id.in_(visible))
        .scalar()
        or 0
    )
    return ok(max(total - read, 0))


@router.patch("/{message_id}", response_model=None)
def mark_read(
    message_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """标记单条已读（需登录，幂等）。

    越权防护（H11）：**先按可见范围过滤，再操作**。
    此前 `db.get(Message, message_id)` 直接取出任意一行并返回其
    `title` / `summary` / `newsId`——任何登录用户（依赖甚至是 optional，
    未登录也能打）只要拿到或猜到消息 id，就能读到他人定向消息的内容，
    而种子 id 形如 `m1..m4` 可直接枚举。

    「不存在」与「不可见」返回**同一个 40400**：对外不暴露「它存在但你看不到」，
    与资讯软下架（`hidden` 与「不存在」同码）同一条口径。
    """
    m = (
        db.query(Message)
        .filter(Message.id == message_id, visible_condition(user))
        .first()
    )
    if m is None:
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "消息不存在"})

    exists = (
        db.query(MessageRead)
        .filter(MessageRead.message_id == message_id, MessageRead.user_id == user.id)
        .first()
    )
    if exists is None:
        db.add(MessageRead(message_id=message_id, user_id=user.id, created_at=now_str()))
        db.commit()
    return ok(to_out(m, _read_ids(db, user)))


@router.post("/read-all", response_model=None)
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """全部标记已读。只标记**可见范围**内的消息。

    与 `mark_read` 不同，这里**不强制登录**：未登录时没有可标记的对象，
    返回 0 即可（只回计数、不回任何消息内容，无泄露面），没必要抛 401
    去触发前端的全局未授权跳转。
    """
    if user is None:
        return ok(0, message="未登录")
    already = _read_ids(db, user)
    all_ids = [r[0] for r in db.query(Message.id).filter(visible_condition(user)).all()]
    missing = [mid for mid in all_ids if mid not in already]
    for mid in missing:
        db.add(MessageRead(message_id=mid, user_id=user.id, created_at=now_str()))
    if missing:
        db.commit()
    return ok(len(missing), message="已全部标记为已读")
