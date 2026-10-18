"""管理端 · 评论敏感词库与命中日志（P1-23）：词库维护、缓存失效、命中日志（只读）。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ...database import get_db
from ...config import MODERATION_MIN_WORD_LEN
from ...moderation import engine, normalize
from ...models import SensitiveHit, SensitiveWord, User, now_str
from ...response import ok
from ...schemas import WordIn, WordUpdateIn
from ..news import _escape_like
from . import require_admin
from ._shared import (
    record_audit,
    AUDIT_WORD_CREATE,
    AUDIT_WORD_DELETE,
    AUDIT_WORD_RELOAD,
    AUDIT_WORD_UPDATE,
)

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

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
):
    """敏感词库列表。

    用 `limit + offset` 而非游标：运营要的是「一遍扫完 + 能跳页核对」，
    词库量级（百~千）也不支持 keyset 的那点性能收益。
    """
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
    user: User = Depends(require_admin),
):
    """新增敏感词。

    - 归一化后不足 `MODERATION_MIN_WORD_LEN` 个有效字符 → **42202**：1 字词
      在正常表达里遍地都是，挡在入库这一步比事后救火便宜。
      （刻意不复用 42201——它已承载「频道不存在 / 排序方式不支持」两种语义，
      同一个码挂三种场景会让前端文案错配；）
    - 重复词 → 40905（唯一约束在库里，但提前判一次才能给出可解释的错误）。
    """
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
    user: User = Depends(require_admin),
):
    """局部更新敏感词（**不允许改词本身**：改词等于删一条加一条）。"""
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
    word_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """删除敏感词（审计留快照：删错了要能知道原来是什么）。

    停用（`enabled=false`）通常比删除更合适——删除后命中日志里那个词会失去
    对应配置，复盘时读不出它当年是 block 还是 mask。
    """
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
def reload_words(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """强制重建本进程的词库索引。

    改词已经会自动失效本进程缓存，这个端点是给**其他实例**用的：
    集群下 B 实例要等 TTL 才感知到 A 实例的改动，需要即时生效时点一下它。
    """
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
):
    """敏感词命中日志（只读）。

    它的价值不在"看出事了"，而在**回答误杀**：哪个人、哪句话、命中哪个词、
    上下文是什么。脱敏后正文不可复原，这是唯一的证据来源——故只读、不清理。
    """
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


