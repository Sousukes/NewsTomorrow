"""管理端 · 昵称词库与复核队列（P1-24）：与评论词库同构但资源独立。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from ...database import get_db
from ...config import NICKNAME_MIN_WORD_LEN
from ...models import NicknameHit, NicknameWord, User, now_str
from ...nickname_moderation import ACTION_REVIEW, alias_list, normalize_forms
from ...nickname_moderation import engine as nickname_engine
from ...response import ok
from ...schemas import NicknameWordIn, NicknameWordUpdateIn
from ..news import _escape_like
from . import require_admin
from ._shared import (
    record_audit,
    AUDIT_NICKNAME_REVIEW,
    AUDIT_NICKNAME_WORD_CREATE,
    AUDIT_NICKNAME_WORD_DELETE,
    AUDIT_NICKNAME_WORD_RELOAD,
    AUDIT_NICKNAME_WORD_UPDATE,
)

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

def _nickname_word_out(w: NicknameWord) -> dict:
    return {
        "id": w.id,
        "word": w.word,
        "action": w.action,
        "category": w.category,
        # 脏数据（非数组/非字符串项）按空处理：前端永远拿到数组，不做二次防御
        "aliases": alias_list(w.aliases),
        "enabled": bool(w.enabled),
        "hitCount": w.hit_count or 0,
        "createdBy": w.created_by,
        "createdAt": w.created_at,
        "updatedAt": w.updated_at,
    }


def _nickname_hit_out(h: NicknameHit) -> dict:
    return {
        "id": h.id,
        "word": h.word,
        # 实际命中的模式（主词或某条别名）：回答"是哪条别名拦住的"，
        # 也是判断"该补哪个别名"的依据
        "matched": h.matched,
        "action": h.action,
        "source": h.source,
        "userId": h.user_id,
        "userName": h.user_name,
        # 提交的昵称原文：昵称整体就是上下文，判断误杀不需要更多信息
        "snippet": h.snippet,
        "createdAt": h.created_at,
    }


def _nickname_word_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": 40407, "message": "昵称敏感词不存在"})


def _normalized_len(text: str) -> int:
    """归一化后的有效字符数（`normalize_forms` 的第一个形态恒为 base）。"""
    return len(normalize_forms(text)[0].text)


@router.get("/nickname-words", response_model=None)
def list_nickname_words(
    keyword: str = Query("", max_length=50),
    action: str = Query("", pattern="^(|block|review|allow)$"),
    enabled: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """昵称敏感词库列表（P1-24）。

    与 P1-23 `list_words` 同构：`limit + offset` 而非游标——运营要的是
    「一遍扫完 + 能跳页核对」，词库量级（百~千）也不支持 keyset 的那点收益。
    `keyword` 只匹配主词不搜别名：别名在每行的 `aliases` 字段里可见，
    为搜别名把 JSON 列拖进 ILIKE 不值得（两边方言的 JSON→text 行为还不一样）。
    """
    q = db.query(NicknameWord)
    kw = keyword.strip()
    if kw:
        like = f"%{_escape_like(kw)}%"
        q = q.filter(NicknameWord.word.ilike(like, escape="\\"))
    if action:
        q = q.filter(NicknameWord.action == action)
    if enabled is not None:
        q = q.filter(NicknameWord.enabled.is_(enabled))
    total = q.count()
    rows = (
        q.order_by(NicknameWord.hit_count.desc(), NicknameWord.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return ok({"list": [_nickname_word_out(w) for w in rows], "total": total})


@router.post("/nickname-words", response_model=None)
def create_nickname_word(
    body: NicknameWordIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """新增昵称敏感词（P1-24）。

    - 主词或任一别名归一化后不足 `NICKNAME_MIN_WORD_LEN` 个有效字符 →
      **42204**（新码：42202 已承载评论词库的「太短」语义，复用会让前端
      文案错配）。1 字词在正常昵称里遍地都是（「射」会命中「射手」），
      挡在入库这一步比事后救火便宜；
    - **别名逐个查**而不是只查主词：引擎加载时会静默跳过过短的形态，
      「配了却没生效」比报错难发现得多；
    - 重复词 → 40906（唯一约束在库里，但提前判一次才能给出可解释的错误）。
    """
    word = body.word  # schema 已 strip
    if _normalized_len(word) < NICKNAME_MIN_WORD_LEN:
        raise HTTPException(
            status_code=422,
            detail={
                "code": 42204,
                "message": f"昵称敏感词至少需 {NICKNAME_MIN_WORD_LEN} 个有效字符（空格与标点不计）",
            },
        )
    for a in body.aliases:
        if _normalized_len(a) < NICKNAME_MIN_WORD_LEN:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": 42204,
                    "message": f"别名「{a}」至少需 {NICKNAME_MIN_WORD_LEN} 个有效字符（空格与标点不计）",
                },
            )
    if db.query(NicknameWord).filter(NicknameWord.word == word).first() is not None:
        raise HTTPException(status_code=409, detail={"code": 40906, "message": "该昵称敏感词已存在"})
    ts = now_str()
    row = NicknameWord(
        word=word,
        action=body.action,
        category=body.category,
        aliases=body.aliases,
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
        AUDIT_NICKNAME_WORD_CREATE,
        word,
        {
            "word": word,
            "action": body.action,
            "category": body.category,
            "aliases": body.aliases,
            "enabled": body.enabled,
        },
    )
    db.commit()
    db.refresh(row)
    # 保存即生效：让本进程的昵称词库缓存立刻失效（集群其他实例靠 TTL 兜底）
    nickname_engine.invalidate()
    logger.info("管理员 %s 新增昵称敏感词 %s（%s）", user.phone, word, body.action)
    return ok(_nickname_word_out(row), message="已添加")


@router.patch("/nickname-words/{word_id}", response_model=None)
def update_nickname_word(
    word_id: int,
    body: NicknameWordUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """局部更新昵称敏感词（**不允许改词本身**：改词等于删一条加一条）。

    `aliases` 传空数组 = 清空全部别名；不传 = 不动（schema 用 `None` 区分）。
    改别名同样走 invalidate——别名是模式串的一部分，改了不重载等于没改。
    """
    row = db.get(NicknameWord, word_id)
    if row is None:
        raise _nickname_word_not_found()
    changes: dict[str, list] = {}
    if body.action is not None and body.action != row.action:
        changes["action"] = [row.action, body.action]
        row.action = body.action
    if body.category is not None and body.category != row.category:
        changes["category"] = [row.category, body.category]
        row.category = body.category
    if body.aliases is not None:
        before = alias_list(row.aliases)
        if body.aliases != before:
            changes["aliases"] = [before, body.aliases]
            row.aliases = body.aliases
    if body.enabled is not None and body.enabled != bool(row.enabled):
        changes["enabled"] = [bool(row.enabled), body.enabled]
        row.enabled = body.enabled
    if not changes:
        return ok(_nickname_word_out(row))
    row.updated_at = now_str()
    record_audit(db, user.phone, AUDIT_NICKNAME_WORD_UPDATE, row.word, {"changes": changes})
    db.commit()
    db.refresh(row)
    nickname_engine.invalidate()
    return ok(_nickname_word_out(row), message="已更新")


@router.delete("/nickname-words/{word_id}", response_model=None)
def delete_nickname_word(
    word_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """删除昵称敏感词（审计留快照：删错了要能知道原来是什么）。

    停用（`enabled=false`）通常比删除更合适——删除后命中日志里那个词会失去
    对应配置，复盘时读不出它当年是 block 还是 review。
    """
    row = db.get(NicknameWord, word_id)
    if row is None:
        raise _nickname_word_not_found()
    snapshot = _nickname_word_out(row)
    record_audit(db, user.phone, AUDIT_NICKNAME_WORD_DELETE, row.word, {"snapshot": snapshot})
    db.delete(row)
    db.commit()
    nickname_engine.invalidate()
    return ok(snapshot, message="已删除")


@router.post("/nickname-words/reload", response_model=None)
def reload_nickname_words(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """强制重建本进程的昵称词库索引（与 P1-23 `reload` 同定位）。

    改词已经会自动失效本进程缓存，这个端点是给**其他实例**用的：
    集群下 B 实例要等 TTL 才感知到 A 实例的改动，需要即时生效时点一下它。
    """
    nickname_engine.invalidate()
    nickname_engine.ensure(db)
    count = db.query(NicknameWord).filter(NicknameWord.enabled.is_(True)).count()
    record_audit(db, user.phone, AUDIT_NICKNAME_WORD_RELOAD, "", {"count": count})
    db.commit()
    return ok({"count": count}, message="昵称词库已重载")


@router.get("/nickname-hits", response_model=None)
def list_nickname_hits(
    word: str = Query("", max_length=100),
    action: str = Query("", pattern="^(|block|review)$"),
    source: str = Query("", pattern="^(|register|update)$"),
    userId: int | None = Query(None, description="只看某个用户的命中"),
    limit: int = Query(20, ge=1, le=100),
    cursor: int | None = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
):
    """昵称敏感词命中日志（只读，P1-24）。

    价值不在「看出事了」，而在**回答误杀与受理申诉**：哪个人、哪个昵称、
    命中哪个词（含哪条别名）、从注册还是改名入口来的。故只读、不清理。

    `userId` 筛选让复核队列与命中日志互相印证：队列里看到某个名字吃不准，
    按人一筛就能看到它历次被拦/待复核的完整记录。
    """
    q = db.query(NicknameHit)
    if word.strip():
        q = q.filter(NicknameHit.word == word.strip())
    if action:
        q = q.filter(NicknameHit.action == action)
    if source:
        q = q.filter(NicknameHit.source == source)
    if userId is not None:
        q = q.filter(NicknameHit.user_id == userId)
    total = q.count()
    if cursor is not None:
        q = q.filter(NicknameHit.id < cursor)
    rows = q.order_by(NicknameHit.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": [_nickname_hit_out(h) for h in items],
            "total": total,
            "hasMore": has_more,
            "nextCursor": items[-1].id if has_more and items else None,
        }
    )


@router.get("/nickname-reviews", response_model=None)
def list_nickname_reviews(
    keyword: str = Query("", max_length=50),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """昵称待人工复核队列（P1-24）。

    与评论复核（P1-23 `pending_review`）同构，但对象是**账号属性**：
    昵称从设置那一刻起就公开展示，复核只回答「这个名字要不要管」——
    通过则清标志；认为仍不合适时走线下处理（本站不提供改他人昵称的能力，
    那是比隐藏评论重得多的动作，不该由一个按钮完成）。

    队列回答的是「**哪个提交**需要人看」，不是「他现在叫什么」：用户完全可能
    已经把名字改掉了（`name` 是当前值），于是三个标量子查询都取该用户最近一条
    **review 级**命中的 `created_at` / `snippet`（当初提交的昵称）/ `matched`
    （哪条别名拦的）。按最近被标记排序，新提交的坏名字浮在上面。
    """
    latest = (
        select(func.max(NicknameHit.created_at))
        .where(NicknameHit.user_id == User.id, NicknameHit.action == ACTION_REVIEW)
        .scalar_subquery()
    )
    flagged_name = (
        select(NicknameHit.snippet)
        .where(NicknameHit.user_id == User.id, NicknameHit.action == ACTION_REVIEW)
        .order_by(NicknameHit.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    flagged_matched = (
        select(NicknameHit.matched)
        .where(NicknameHit.user_id == User.id, NicknameHit.action == ACTION_REVIEW)
        .order_by(NicknameHit.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    q = db.query(
        User,
        latest.label("flagged_at"),
        flagged_name.label("flagged_name"),
        flagged_matched.label("flagged_matched"),
    ).filter(User.name_pending_review.is_(True))
    kw = keyword.strip()
    if kw:
        like = f"%{_escape_like(kw)}%"
        q = q.filter(
            or_(User.name.ilike(like, escape="\\"), User.phone.ilike(like, escape="\\"))
        )
    total = q.count()
    rows = (
        q.order_by(func.coalesce(latest, "").desc(), User.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return ok(
        {
            "list": [
                {
                    "userId": u.id,
                    "name": u.name,
                    "phone": u.phone,
                    "flaggedAt": flagged_at,
                    "flaggedName": flagged_name,
                    "flaggedMatched": flagged_matched,
                }
                for u, flagged_at, flagged_name, flagged_matched in rows
            ],
            "total": total,
        }
    )


@router.post("/nickname-reviews/{user_id}", response_model=None)
def review_nickname(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """通过昵称人工复核（**幂等**，P1-24）。

    只清 `name_pending_review` 并写 `name_reviewed_at`，**不改昵称本身**——
    名字是用户自己起的，复核是「人看过了」这个事实，不是运营替他改名。
    重复调用不重复审计（与 P1-23 评论复核同处方：没有新事实就不留新痕）。
    """
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail={"code": 40408, "message": "用户不存在"})
    if u.name_pending_review:
        u.name_pending_review = False
        u.name_reviewed_at = now_str()
        record_audit(
            db,
            user.phone,
            AUDIT_NICKNAME_REVIEW,
            str(u.id),
            {"name": u.name, "phone": u.phone},
        )
        db.commit()
        db.refresh(u)
        logger.info("管理员 %s 复核通过昵称（用户 %s：%s）", user.phone, u.id, u.name)
    return ok(
        {
            "userId": u.id,
            "name": u.name,
            "phone": u.phone,
            "pendingReview": False,
            "reviewedAt": u.name_reviewed_at,
        },
        message="已通过复核",
    )


