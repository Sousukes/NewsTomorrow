"""互动事件 → 消息中心通知。

设计要点：
1. **定向**：`Message.user_id` 指定收件人；NULL 表示广播（系统通知）。
2. **不打扰自己**：触发者本人永远不收到自己行为产生的消息。
3. **去重**：相同 `(收件人, 类型, 资讯, 摘要)` 的消息不重复插入。
   点赞可以被反复取消/重加，不去重会刷屏。
4. **失败不影响主流程**：通知是旁路逻辑，抛错不应让评论/点赞失败，
   由调用方决定是否吞掉异常。
"""
import logging
import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Comment, Message, now_str

logger = logging.getLogger("toutiao.notify")


def _dedupe_exists(
    db: Session,
    *,
    user_id: int,
    type_: str,
    news_id: str | None,
    summary: str,
) -> bool:
    """是否已存在内容相同的未重复消息（避免点赞反复切换导致刷屏）。"""
    q = select(Message.id).where(
        Message.user_id == user_id,
        Message.type == type_,
        Message.summary == summary,
    )
    if news_id:
        q = q.where(Message.news_id == news_id)
    return db.execute(q.limit(1)).first() is not None


def _create(
    db: Session,
    *,
    user_id: int,
    type_: str,
    title: str,
    summary: str,
    news_id: str | None = None,
) -> Message | None:
    if _dedupe_exists(db, user_id=user_id, type_=type_, news_id=news_id, summary=summary):
        return None
    m = Message(
        id=f"msg_{uuid.uuid4().hex[:16]}",
        type=type_,
        title=title,
        summary=summary,
        news_id=news_id,
        user_id=user_id,
        time="刚刚",
    )
    db.add(m)
    return m


def notify_comment_on_article(
    db: Session,
    *,
    news_id: str,
    actor_id: int,
    actor_name: str,
    content: str,
) -> int:
    """有人在资讯下发表评论 → 通知**同样评论过该资讯**的其他用户。

    不通知：触发者本人、匿名/无归属的历史评论（user_id 为 NULL）。
    @returns 产生的消息条数
    """
    snippet = content[:40] + ("…" if len(content) > 40 else "")
    summary = f"{actor_name} 评论：{snippet}"

    # 该资讯下所有评论的作者（去重），排除触发者与无归属评论
    rows = db.execute(
        select(Comment.user_id)
        .where(
            Comment.news_id == news_id,
            Comment.is_deleted.is_(False),
            Comment.user_id.isnot(None),
            Comment.user_id != actor_id,
        )
        .distinct()
    ).scalars()

    recipients = [uid for uid in rows if uid is not None]
    created = 0
    for uid in recipients:
        if _create(
            db,
            user_id=uid,
            type_="comment",
            title="你评论过的资讯有新动态",
            summary=summary,
            news_id=news_id,
        ):
            created += 1
    if created:
        logger.info("评论通知：资讯 %s → %d 位用户", news_id, created)
    return created


def notify_comment_liked(
    db: Session,
    *,
    comment: Comment,
    actor_id: int,
    actor_name: str,
) -> bool:
    """有人点赞评论 → 通知评论作者。

    仅在**新增点赞**时调用（取消点赞不通知）。作者为自己或无归属时不通知。
    @returns 是否产生了消息
    """
    author_id: int | None = comment.user_id
    if author_id is None or author_id == actor_id:
        return False

    snippet = (comment.content or "")[:30]
    summary = f"{actor_name} 赞了你的评论：{snippet}…"
    created = _create(
        db,
        user_id=author_id,
        type_="comment",
        title="你的评论收到新点赞",
        summary=summary,
        news_id=comment.news_id,
    )
    if created:
        logger.info("点赞通知：评论 %s → 用户 %d", comment.id, author_id)
    return created is not None


def notify_comment_replied(
    db: Session,
    *,
    parent: Comment,
    actor_id: int,
    actor_name: str,
) -> bool:
    """有人回复了评论 → 通知**被回复者**（一级通知，比「资讯有新评论」更贴身）。

    不通知：被回复者是自己、无归属的历史评论（user_id 为 NULL）。
    @returns 是否产生了消息
    """
    author_id: int | None = parent.user_id
    if author_id is None or author_id == actor_id:
        return False

    snippet = (parent.content or "")[:30]
    summary = f"{actor_name} 回复了你的评论：{snippet}…"
    created = _create(
        db,
        user_id=author_id,
        type_="comment",
        title="你的评论收到新回复",
        summary=summary,
        news_id=parent.news_id,
    )
    if created:
        logger.info("回复通知：评论 %s → 用户 %d", parent.id, author_id)
    return created is not None


def _notify_many(db: Session, recipients: Iterable[int], **kwargs) -> int:
    """批量通知（预留：后续关注/回复等事件复用）。"""
    created = 0
    for uid in recipients:
        if _create(db, user_id=uid, **kwargs):
            created += 1
    return created
