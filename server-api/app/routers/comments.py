"""评论路由：主评论列表（游标分页）/ 回复列表 / 发表评论（含回复）/ 点赞。

结构契约（P1-12，两级评论）：
    主评论 `root_id IS NULL`；回复 `root_id = 所属主评论 id`（回复的回复
    拍平到同一 root，`parent_id` 仅用于展示「回复 @谁」）。
    - 主列表只返回主评论，附 `replyCount` 与最早 2 条回复预览；
    - 完整回复走 `GET /{news_id}/comments/{comment_id}/replies`，
      按时间**正序**（讨论先后）游标分页；
    - 主评论被软删除后其回复不可见（回复接口返回空列表）——主评论消失后，
      失去语境的回复不应继续裸露在讨论区。

敏感词审核（P1-23）：
    提交时先在**服务端**做一次敏感词检测（前端校验只能防误触，防不了直接
    调 API）。命中 `block` 词直接拒绝提交（提示不回显命中了哪个词——回显
    等于给绕过者一份对照表）；命中 `mask` 词则脱敏后照常发布并置
    `pending_review`，交人工复核——机器判断是概率性的，"先兜住再让人看"
    比"直接吞掉用户的话"代价小。命中一律留日志（含原文片段）：脱敏不可逆，
    误杀复盘与申诉全靠它。检测只在**写路径**发生，读路径零开销。

隐藏过滤（P1-22）：
    运营软隐藏与软删除对公开侧完全同构：`hidden` 的评论同样从全部公开
    查询消失（主列表/回复/预览/计数），主评论被隐藏后其回复也不再可见；
    区别只在治理语义——删除不可逆（作者自主），隐藏可恢复（运营处置，
    理由留痕）。过滤条件实时生效，恢复无需等任何定时任务。

排序契约（H2）：
    主评论按绝对时间 `created_at` 倒序（新的在前），`id` 兜底保证稳定。
    历史实现按展示文案 `time`（"1小时前" / ISO 混排）排序，字典序与真实
    时间不一致，会得到错误顺序。回复按时间正序 + `id` 兜底。

分页契约（M2）：
    支持 `cursor` + `limit`（keyset），缺省返回前 N 条；同时返回 `hasMore`。
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from ..config import MODERATION_ENABLED
from ..database import get_db
from ..deps import get_current_user, get_current_user_optional, require_password_current
from ..moderation import engine, snippet_of
from ..models import (
    Comment,
    CommentLike,
    News,
    SensitiveHit,
    SensitiveWord,
    User,
    display_time,
    now_str,
)
from ..notify import (
    notify_comment_liked,
    notify_comment_on_article,
    notify_comment_replied,
)
from ..ranking import bump_hot_score
from ..response import ok
from ..schemas import CommentCreateIn, LikeIn

router = APIRouter(prefix="/api/news", tags=["comments"])
logger = logging.getLogger("toutiao.comments")

DEFAULT_LIMIT = 20
# 主评论在列表里附带的回复预览条数（完整列表走 replies 接口）
REPLY_PREVIEW_COUNT = 2


def _liked_ids(db: Session, comment_ids: list[str], current: Optional[User]) -> set[str]:
    """批量判定当前用户点赞过哪些评论（一次 IN 查询，避免 N+1）。"""
    if current is None or not comment_ids:
        return set()
    return set(
        db.execute(
            select(CommentLike.comment_id).where(
                CommentLike.comment_id.in_(comment_ids),
                CommentLike.user_id == current.id,
            )
        ).scalars()
    )


def _parent_map(db: Session, comments: list[Comment]) -> dict[str, Comment]:
    """批量取回复的父评论行（展示「回复 @谁」）。"""
    parent_ids = {c.parent_id for c in comments if c.parent_id}
    if not parent_ids:
        return {}
    return {p.id: p for p in db.query(Comment).filter(Comment.id.in_(parent_ids)).all()}


def to_out(
    c: Comment,
    current: Optional[User],
    *,
    liked: bool = False,
    reply_count: int = 0,
    replies: Optional[list] = None,
    reply_to: Optional[Comment] = None,
) -> dict:
    return {
        "id": c.id,
        "name": c.user_name,
        "seed": c.seed,
        "content": c.content,
        # 展示文案由绝对时间推导（comments.time 存的是 ISO 串，原样透传会让用户
        # 新发的评论显示 2026-10-18T14:42:17+00:00）；排序/过滤只用 createdAt
        "time": display_time(c.time, c.created_at),
        "createdAt": c.created_at,
        "likes": c.likes or 0,
        "liked": liked,
        # 作者归属：历史/种子评论为 None（作者未知，不可被任何用户以作者身份操作）
        "userId": c.user_id,
        "mine": bool(current and c.user_id is not None and c.user_id == current.id),
        "isDeleted": bool(c.is_deleted),
        # 待人工复核（P1-23）：命中敏感词被脱敏后发布的评论。
        # 作者据此知道"我的话被处理过"（而不是莫名少了几個字符），前端只对
        # `mine` 的评论展示提示；他人看到的是已脱敏内容，无额外信息。
        "pendingReview": bool(c.pending_review),
        # 两级评论（P1-12）：parentId/rootId 定位结构，replyCount/replies 供
        # 主评论展示「N 条回复」与预览，replyTo* 供回复展示「回复 @谁」。
        "parentId": c.parent_id,
        "rootId": c.root_id,
        "replyCount": reply_count,
        "replies": replies or [],
        "replyToName": reply_to.user_name if reply_to is not None else None,
        "replyToUserId": reply_to.user_id if reply_to is not None else None,
    }


def _decorate_roots(db: Session, roots: list[Comment], current: Optional[User]) -> list[dict]:
    """主评论 → 输出 dict：批量附带回复数、最早 N 条回复预览与点赞判定。"""
    if not roots:
        return []
    root_ids = [c.id for c in roots]

    # 回复数（按 root 聚合；已删除、已隐藏的回复不计入）
    counts = dict(
        db.execute(
            select(Comment.root_id, func.count())
            .where(
                Comment.root_id.in_(root_ids),
                Comment.is_deleted.is_(False),
                Comment.hidden.is_(False),
            )
            .group_by(Comment.root_id)
        ).all()
    )

    # 最早 REPLY_PREVIEW_COUNT 条回复：**窗口函数 + 子查询**把「每组取前 N」下推到 SQL。
    #
    # 不能直接在同一个 SELECT 里 `WHERE rn <= N`——SQL 不允许 WHERE 引用窗口列；
    # 也不能像早期实现那样先捞全部回复再在 Python 侧截断：爆款评论有几百上千条
    # 回复时，一页 20 条主评论就是 20 份全量回复进内存，这是评论页最陡的性能悬崖。
    # 子查询内算 rn、外层过滤，DB 只返回每组前 N 行（组内顺序再在 Python 侧兜底排一次）。
    rn = (
        func.row_number()
        .over(partition_by=Comment.root_id, order_by=(Comment.created_at.asc(), Comment.id.asc()))
        .label("rn")
    )
    preview_subq = select(Comment, rn).where(
        Comment.root_id.in_(root_ids),
        Comment.is_deleted.is_(False),
        Comment.hidden.is_(False),
    ).subquery()
    # aliased(Comment, subq)：子查询列与 Comment 一一对应，可映射回 ORM 实体
    preview_entity = aliased(Comment, preview_subq)
    preview_rows = db.query(preview_entity).filter(preview_subq.c.rn <= REPLY_PREVIEW_COUNT).all()
    previews: dict[str, list[Comment]] = {}
    for entity in preview_rows:
        previews.setdefault(entity.root_id, []).append(entity)

    # 点赞判定一次覆盖主评论 + 预览回复；「回复 @谁」按需批量取父行
    preview_entities = [e for lst in previews.values() for e in lst]
    liked_ids = _liked_ids(db, root_ids + [e.id for e in preview_entities], current)
    parents = _parent_map(db, preview_entities)

    def _reply_out(r: Comment) -> dict:
        return to_out(
            r,
            current,
            liked=r.id in liked_ids,
            reply_to=parents.get(r.parent_id) if r.parent_id else None,
        )

    out = []
    for c in roots:
        plist = sorted(previews.get(c.id, []), key=lambda r: (r.created_at, r.id))
        out.append(
            to_out(
                c,
                current,
                liked=c.id in liked_ids,
                reply_count=counts.get(c.id, 0),
                replies=[_reply_out(r) for r in plist],
            )
        )
    return out


def _get_news_or_404(db: Session, news_id: str) -> News:
    # P1-20：已下架资讯的讨论区一并不可见（列表/回复/发表评论都走这里）。
    # 与「不存在」同码——对外不暴露"它存在但被下了"。
    item = db.get(News, news_id)
    if item is None or item.hidden:
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "内容不存在或已下架"})
    return item


def _order_desc(q):
    """最新在前：created_at 倒序，id 倒序兜底。"""
    return q.order_by(Comment.created_at.desc(), Comment.id.desc())


def _record_hits(
    db: Session,
    hits: list,
    *,
    originals: dict,
    news_id: str,
    user: User,
    comment_id: str | None,
) -> None:
    """写命中日志 + 累加词的命中次数（**不提交**，由调用方与业务一起 commit）。

    同一 `(字段, 词)` 在一次提交里只记一行：同一段话三次命中同一个词，
    对运营是「这条评论命中了 X」一件事，不是三条独立事件——重复行只会
    把命中列表淹掉，让"哪个词在误杀"看不出来。
    """
    ts = now_str()
    seen: set[tuple[str, str]] = set()
    for h in hits:
        key = (h.field, h.word)
        if key in seen:
            continue
        seen.add(key)
        db.add(
            SensitiveHit(
                word=h.word,
                action=h.action,
                field=h.field,
                comment_id=comment_id,
                news_id=news_id,
                user_id=user.id,
                # 记账号真实昵称：运营要按人定位，脱敏后的展示名没有这条信息
                user_name=user.name,
                snippet=snippet_of(originals.get(h.field, ""), h.start, h.end),
                created_at=ts,
            )
        )
        # 自增走 SQL 表达式而不是 Python 侧的 `+=`：并发提交下先读后写会丢更新
        db.execute(
            update(SensitiveWord)
            .where(SensitiveWord.word == h.word)
            .values(hit_count=SensitiveWord.hit_count + 1)
        )


@router.get("/{news_id}/comments", response_model=None)
def list_comments(
    news_id: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """主评论列表：按时间倒序（新的在前），游标为上一页末条 id。

    只返回**主评论**（root_id IS NULL）；每条附回复数与最早 2 条回复预览，
    完整回复走 replies 接口。已删除（is_deleted）、已隐藏（hidden，P1-22）
    的评论不返回——两者对公开侧同构：实时过滤，无需等任何定时任务。
    """
    _get_news_or_404(db, news_id)
    q = db.query(Comment).filter(
        Comment.news_id == news_id,
        Comment.is_deleted.is_(False),
        Comment.hidden.is_(False),
        Comment.root_id.is_(None),
    )

    if cursor:
        anchor = db.query(Comment).filter(Comment.id == cursor).first()
        if anchor is not None:
            # (created_at, id) < (anchor.created_at, anchor.id)
            q = q.filter(
                or_(
                    Comment.created_at < anchor.created_at,
                    and_(Comment.created_at == anchor.created_at, Comment.id < anchor.id),
                )
            )

    # 多取 1 条用于判断是否还有下一页
    rows = _order_desc(q).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": _decorate_roots(db, items, current),
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.get("/{news_id}/comments/{comment_id}/replies", response_model=None)
def list_replies(
    news_id: str,
    comment_id: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    current: Optional[User] = Depends(get_current_user_optional),
):
    """某条主评论下的回复列表：按时间**正序**（讨论先后），游标分页。

    root 已软删除或已隐藏（P1-22）→ 回复不可见（返回空列表）：主评论从
    公开视图消失后，失去语境的回复不应继续裸露在讨论区。
    """
    _get_news_or_404(db, news_id)
    root = db.get(Comment, comment_id)
    if root is None or root.news_id != news_id or root.is_deleted or root.hidden:
        return ok({"list": [], "hasMore": False, "nextCursor": None})

    q = db.query(Comment).filter(
        Comment.news_id == news_id,
        Comment.root_id == comment_id,
        Comment.is_deleted.is_(False),
        Comment.hidden.is_(False),
    )
    if cursor:
        anchor = db.query(Comment).filter(Comment.id == cursor).first()
        if anchor is not None:
            # (created_at, id) > (anchor.created_at, anchor.id)
            q = q.filter(
                or_(
                    Comment.created_at > anchor.created_at,
                    and_(Comment.created_at == anchor.created_at, Comment.id > anchor.id),
                )
            )

    rows = q.order_by(Comment.created_at.asc(), Comment.id.asc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]

    liked_ids = _liked_ids(db, [c.id for c in items], current)
    parents = _parent_map(db, items)
    return ok(
        {
            "list": [
                to_out(
                    c,
                    current,
                    liked=c.id in liked_ids,
                    reply_to=parents.get(c.parent_id) if c.parent_id else None,
                )
                for c in items
            ],
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.post("/{news_id}/comments", response_model=None)
def create_comment(
    news_id: str,
    body: CommentCreateIn,
    db: Session = Depends(get_db),
    # C4：受限账号（刚被代重置密码）不得发言——否则临时密码可长期使用
    user: User = Depends(require_password_current),
):
    """发表评论；带 `parentId` 时为回复（二级评论）。

    回复约束：被回复评论必须属于同一篇文章、未删除且**未隐藏**（P1-22）；
    回复的回复拍平到同一 root。回复只通知被回复者（一级通知），不再走
    「资讯有新评论」广播——避免同一个人收到两条语义重复的消息。

    敏感词审核（P1-23）：落库**之前**检测正文与昵称。命中 block → 40005
    （提示不回显命中词）；命中 mask → 脱敏后照常发布 + 置待复核。
    """
    _get_news_or_404(db, news_id)
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail={"code": 40002, "message": "评论内容不能为空"})

    parent: Optional[Comment] = None
    if body.parentId:
        parent = db.get(Comment, body.parentId)
        # 隐藏与被删除对回复者同构：都该像"不存在"一样拒绝
        # （对一条运营已处置的内容继续盖楼没有意义）
        if parent is None or parent.news_id != news_id or parent.is_deleted or parent.hidden:
            raise HTTPException(
                status_code=400, detail={"code": 40003, "message": "回复的评论不存在"}
            )

    # id 先生成：命中日志要能关联到评论（block 时没有评论，故仍可为空）
    cid = f"c_{uuid.uuid4().hex[:12]}"
    display_name = user.name
    pending_review = False
    if MODERATION_ENABLED:
        # 引擎在进程内缓存词库：TTL 内不查库，检测本身只是几十微秒的内存扫描
        inspection = engine.inspect(db, content=content, name=user.name)
        if inspection.hits:
            _record_hits(
                db,
                inspection.hits,
                originals={"content": content, "name": user.name},
                news_id=news_id,
                user=user,
                comment_id=None if inspection.blocked else cid,
            )
        if inspection.blocked:
            # 拦截必须留痕：否则"拦了多少人"是黑箱，也认不出反复试探的行为。
            # 先提交日志再抛错——日志与"评论没写成"是两件事，都要为真。
            db.commit()
            raise HTTPException(
                status_code=400,
                detail={"code": 40005, "message": "评论包含不当内容，请修改后重试"},
            )
        content = inspection.content
        display_name = inspection.name
        pending_review = inspection.masked

    ts = now_str()
    c = Comment(
        id=cid,
        news_id=news_id,
        # 归属：发布者用户 id（供"删除自己的评论"/"我的评论"使用）
        user_id=user.id,
        # 昵称可能被脱敏（P1-23）：只改这条评论的展示名快照，不动账号昵称
        user_name=display_name,
        seed="me",
        content=content,
        time=ts,
        created_at=ts,
        likes=0,
        # 待人工复核（P1-23）：被脱敏的评论不是"通过了审核"，机器只是先兜住
        pending_review=pending_review,
        # 两级评论：回复挂到父评论所属的 root 下（父是主评论时 root 即父本身）
        parent_id=parent.id if parent is not None else None,
        root_id=(parent.root_id or parent.id) if parent is not None else None,
    )
    db.add(c)
    # 维护冗余计数：`comment_count` 此前只在种子/入库时赋值，评论增删从未更新，
    # 导致详情页显示的评论数与真实条数不符，热度分也失真。
    # 回复同样计数——「评论数」含回复，与讨论区实际展示的互动量一致。
    news = db.query(News).filter(News.id == news_id).first()
    if news is not None:
        news.comment_count = (news.comment_count or 0) + 1
    db.commit()
    db.refresh(c)

    # 热度分随互动即时生效（派生数据，失败不影响评论本身）
    bump_hot_score(db, news_id)

    # 旁路通知：主评论广播给同样评论过该资讯的其他用户；回复只通知被回复者
    # （失败不影响评论本身）
    try:
        if parent is not None:
            notify_comment_replied(db, parent=parent, actor_id=user.id, actor_name=user.name)
        else:
            notify_comment_on_article(
                db,
                news_id=news_id,
                actor_id=user.id,
                actor_name=user.name,
                content=content,
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("评论通知失败（不影响评论）: %s", e)

    # 返回体带 parentId/rootId 与「回复 @谁」，前端可直接渲染
    return ok(to_out(c, user, liked=False, reply_to=parent), message="评论成功")


@router.patch("/{news_id}/comments/{comment_id}", response_model=None)
def set_like(
    news_id: str,
    comment_id: str,
    body: LikeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_password_current),
):
    """点赞/取消点赞（幂等）。

    点赞走 comment_likes 关系表（0014）：复合主键保证同一用户对同一评论
    只点一次；`likes` 冗余计数随行的插入/删除同步维护。
    """
    c = db.query(Comment).filter(Comment.id == comment_id, Comment.news_id == news_id).first()
    # 已删除、已隐藏（P1-22）的评论不可再点赞（避免为一条已不可见的内容
    # 累加计数，也避免点赞列表里出现"幽灵"条目）
    if c is None or c.is_deleted or c.hidden:
        raise HTTPException(status_code=404, detail={"code": 40401, "message": "评论不存在"})

    existing = db.get(CommentLike, {"comment_id": c.id, "user_id": user.id})
    just_liked = False
    if body.liked and existing is None:
        db.add(CommentLike(comment_id=c.id, user_id=user.id, created_at=now_str()))
        c.likes = (c.likes or 0) + 1
        just_liked = True
    elif not body.liked and existing is not None:
        db.delete(existing)
        c.likes = max((c.likes or 0) - 1, 0)
    db.commit()
    db.refresh(c)

    # 仅在**新增点赞**时通知作者（取消赞不打扰；失败不影响点赞结果）
    if just_liked:
        try:
            notify_comment_liked(db, comment=c, actor_id=user.id, actor_name=user.name)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("点赞通知失败（不影响点赞）: %s", e)

    # liked 取操作后的真实状态（无变化时保持原值）
    liked = db.get(CommentLike, {"comment_id": c.id, "user_id": user.id}) is not None
    return ok(to_out(c, user, liked=liked))


@router.delete("/{news_id}/comments/{comment_id}", response_model=None)
def delete_comment(
    news_id: str,
    comment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_password_current),
):
    """删除自己的评论（软删除，幂等）。

    - **仅作者可删**：依赖 `comments.user_id`（0007 建立）；
      历史无归属评论（user_id 为 NULL）任何人都删不掉。
    - 软删除：保留原文与点赞记录，列表不再返回。
    - 主评论被删后其回复不可见（replies 接口对已删除 root 返回空）——
      回复行本身保留，不随主评论一并软删除，避免误伤且保留可追溯性。
    - 幂等：重复删除同一条返回成功。
    - 已**被运营隐藏**的评论（P1-22）仍可被作者删除：隐藏不剥夺作者对
      自己内容的所有权。但隐藏时已扣过计数/热度，删除不再重复扣——
      否则「隐藏一次 + 删除一次」会把计数扣穿下限。
    """
    c = db.query(Comment).filter(Comment.id == comment_id, Comment.news_id == news_id).first()
    if c is None or c.is_deleted:
        raise HTTPException(status_code=404, detail={"code": 40401, "message": "评论不存在"})

    if c.user_id is None or c.user_id != user.id:
        raise HTTPException(
            status_code=403, detail={"code": 40302, "message": "只能删除自己发表的评论"}
        )

    if not c.is_deleted:
        c.is_deleted = True
        c.deleted_at = now_str()
        # 隐藏时已扣过计数/热度的评论，删除时不再重复扣（下限 0，防历史脏数据）
        was_visible = not c.hidden
        if was_visible:
            news = db.query(News).filter(News.id == news_id).first()
            if news is not None:
                news.comment_count = max((news.comment_count or 0) - 1, 0)
        db.commit()
        db.refresh(c)
        if was_visible:
            bump_hot_score(db, news_id)
    liked = db.get(CommentLike, {"comment_id": c.id, "user_id": user.id}) is not None
    return ok(to_out(c, user, liked=liked), message="评论已删除")
