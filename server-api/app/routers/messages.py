"""消息中心路由：列表（游标分页）/ 未读数 / 标记已读 / 全部已读。

已读模型（M2）：
    已读关系落在 `message_reads` 关联表（user × message），
    取代早期 `messages.read_by` JSON 数组——JSON 数组无法索引，
    未读数与已读判定只能全表遍历。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user_optional
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


@router.get("", response_model=None)
def list_messages(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """消息列表（定向给自己 + 广播；未登录只看广播且 read 恒为 False）。"""
    q = db.query(Message).filter(visible_condition(current))
    if cursor:
        q = q.filter(Message.id < cursor)
    rows = q.order_by(Message.id.desc()).limit(limit + 1).all()
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
    user: User = Depends(get_current_user_optional),
):
    """标记单条已读（需登录，幂等）。"""
    m = db.get(Message, message_id)
    if m is None:
        return ok(None, message="消息不存在")
    if user is not None:
        exists = (
            db.query(MessageRead)
            .filter(MessageRead.message_id == message_id, MessageRead.user_id == user.id)
            .first()
        )
        if exists is None:
            db.add(MessageRead(message_id=message_id, user_id=user.id, created_at=now_str()))
            db.commit()
    read_ids = _read_ids(db, user)
    return ok(to_out(m, read_ids))


@router.post("/read-all", response_model=None)
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """全部标记已读（需登录）。只标记**可见范围**内的消息。"""
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
