"""管理端 · 评论治理（P1-22）与内容审核（P1-23 的评论侧）：软隐藏 / 恢复 / 人工复核。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from ...database import get_db
from ...models import Comment, News, User, now_str
from ...ranking import bump_hot_score
from ...response import ok
from ...schemas import CommentHideIn
from ..news import _escape_like
from . import require_admin
from ._shared import (
    record_audit,
    AUDIT_COMMENT_HIDE,
    AUDIT_COMMENT_RESTORE,
    AUDIT_COMMENT_REVIEW,
)

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

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
    comment_id: str, db: Session = Depends(get_db)
):
    """管理端单条评论（含隐藏三件套与所属资讯标题）。"""
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
    user: User = Depends(require_admin),
):
    """隐藏评论（软隐藏，**幂等**）——对齐 hide_news。

    理由必填在 schema 层把关（空/纯空白 → 42200）——「留痕是准入条件」。
    已隐藏时不覆盖原理由、不重复记审计。隐藏后公开四路（主列表/回复/
    预览/计数）实时不可见，恢复即时回来——过滤条件是实时的，无需等
    任何定时任务。计数与热度分同步回退（恢复时加回）。
    """
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
    comment_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """恢复已隐藏评论（**幂等**）——对齐 restore_news。

    恢复清空理由/时间（理由只对隐藏状态有意义），计数与热度分同步加回。
    已可见时直接返回、不记审计。
    """
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


@router.post("/comments/{comment_id}/review", response_model=None)
def review_comment(
    comment_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """通过人工复核（**幂等**）：认定脱敏后的评论可以照常展示。

    复核只清 `pending_review`，不改可见性、不动计数——评论从一开始就可见
    （只是被脱敏），复核是"人看过了"这个事实，不是"重新放行"。
    认为仍不合适时应当走**隐藏**（`POST .../hide`），而不是留着待复核标记。
    """
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


