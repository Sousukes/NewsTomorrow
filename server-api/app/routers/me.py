"""个人标记路由：收藏 / 浏览历史（云端同步）。

设计要点：
- 全部需登录；数据按 `user_id` 严格隔离。
- 写入接口统一返回**完整的最新集合** `{ ids, list }`——服务端为唯一事实源，
  客户端无需自行推导增量，同步逻辑因此可以做到"调用即可，结果即真相"。
- 合并（PUT）语义为**并集**：只增不删。避免客户端本地为空时误删云端数据；
  删除一律走显式 DELETE，意图明确、可审计。
- 返回 `list` 为完整资讯对象，使**新设备**无需额外请求即可渲染收藏/历史列表。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import News, User, UserFavorite, UserHistory
from ..ranking import bump_hot_score, recompute_hot_scores
from ..response import ok
from ..schemas import MarkBulkIn, MarkIn
from .news import to_out as news_to_out

router = APIRouter(prefix="/api/me", tags=["me"])

# 历史保留上限（与前端 `.slice(0, 50)` 对齐）
HISTORY_MAX = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_base(existing_max: str | None, count: int) -> datetime:
    """并入条目的时间基准。

    保证并入项**严格新于**云端已有记录（`count` 秒的余量），从而合并后的顺序完全确定：
    先按传入顺序排列并入项，再排云端原有项。
    语义假设：把"本地并入的条目"视为最近发生——客户端并未携带真实时间戳，
    这是可解释且可预测的近似。
    """
    now = _now()
    mx = _parse_iso(existing_max)
    start = now if mx is None or mx < now else mx
    return start + timedelta(seconds=max(count, 1))


def _news_map(db: Session, ids: list[str]) -> dict[str, News]:
    """批量取资讯，避免逐条查询（N+1）。"""
    if not ids:
        return {}
    rows = db.execute(select(News).where(News.id.in_(ids))).scalars().all()
    return {n.id: n for n in rows}


def _payload(db: Session, user: User, model, time_col) -> dict:
    """统一的 `{ ids, list }` 载荷：按时间倒序，仅返回仍存在的资讯。

    标记可能因资讯被时效清理而滞后（SQLite 无外键级联），故此处做一次过滤。
    """
    ids = (
        db.execute(
            select(model.news_id)
            .where(model.user_id == user.id)
            .order_by(time_col.desc(), model.news_id.desc())
        )
        .scalars()
        .all()
    )
    mapping = _news_map(db, ids)
    alive = [i for i in ids if i in mapping]
    return {"ids": alive, "list": [news_to_out(mapping[i]) for i in alive]}


def _favorites_payload(db: Session, user: User) -> dict:
    return _payload(db, user, UserFavorite, UserFavorite.created_at)


def _history_payload(db: Session, user: User) -> dict:
    return _payload(db, user, UserHistory, UserHistory.viewed_at)


def _trim_history(db: Session, user: User) -> int:
    """把历史裁剪到 HISTORY_MAX，删除最旧的溢出记录。返回删除条数。"""
    stale = (
        db.execute(
            select(UserHistory.news_id)
            .where(UserHistory.user_id == user.id)
            .order_by(UserHistory.viewed_at.desc(), UserHistory.news_id.desc())
            .offset(HISTORY_MAX)
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0
    db.execute(
        UserHistory.__table__.delete().where(
            UserHistory.user_id == user.id, UserHistory.news_id.in_(stale)
        )
    )
    db.commit()
    return len(stale)


def _existing_ids(db: Session, model, user: User) -> set[str]:
    return set(
        db.execute(select(model.news_id).where(model.user_id == user.id)).scalars().all()
    )


def _valid_news_ids(db: Session, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return set(db.execute(select(News.id).where(News.id.in_(ids))).scalars().all())


def _merge(db: Session, user: User, model, time_col, ids: list[str]) -> int:
    """并集合并的公共实现：只增不删，非法 id 静默跳过。返回新增条数。"""
    existing = _existing_ids(db, model, user)
    valid = _valid_news_ids(db, ids)
    candidates = [nid for nid in ids if nid not in existing and nid in valid]
    if not candidates:
        return 0

    existing_max = db.execute(
        select(func.max(time_col)).where(model.user_id == user.id)
    ).scalar()
    base = _merge_base(existing_max, len(candidates))
    for k, nid in enumerate(candidates):
        ts = (base - timedelta(seconds=k)).isoformat(timespec="seconds")
        db.add(model(user_id=user.id, news_id=nid, **{time_col.key: ts}))
    db.commit()
    return len(candidates)


# ------------------------------ 收藏 ------------------------------


@router.get("/favorites", response_model=None)
def list_favorites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """我的收藏（按收藏时间倒序）。"""
    return ok(_favorites_payload(db, user))


@router.post("/favorites", response_model=None)
def add_favorite(
    body: MarkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """收藏一条资讯（幂等）。资讯不存在返回 404。"""
    if db.get(News, body.newsId) is None:
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "内容不存在或已下架"})
    if db.get(UserFavorite, {"user_id": user.id, "news_id": body.newsId}) is None:
        db.add(UserFavorite(user_id=user.id, news_id=body.newsId, created_at=_now_iso()))
        db.commit()
        # 收藏数是热度分的组成项，即时重算使"刚被收藏"立刻反映到热榜
        bump_hot_score(db, body.newsId)
    return ok(_favorites_payload(db, user), message="已收藏")


@router.delete("/favorites/{news_id}", response_model=None)
def remove_favorite(
    news_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """取消收藏（幂等；未收藏也返回成功）。"""
    row = db.get(UserFavorite, {"user_id": user.id, "news_id": news_id})
    if row is not None:
        db.delete(row)
        db.commit()
        bump_hot_score(db, news_id)
    return ok(_favorites_payload(db, user), message="已取消收藏")


@router.put("/favorites", response_model=None)
def merge_favorites(
    body: MarkBulkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """并集合并（登录时把本地收藏并入云端）。

    只增不删；不存在的资讯 id 静默跳过（客户端可能持有已下架/已清理的 id）。
    """
    added = _merge(db, user, UserFavorite, UserFavorite.created_at, body.ids)
    if added:
        # 批量并入会一次改变多条的热度，交给全量重算更划算（增量逐条 bump 会 N 次 UPDATE）
        recompute_hot_scores(db)
    return ok(_favorites_payload(db, user), message=f"已合并 {added} 条收藏")


# ------------------------------ 浏览历史 ------------------------------


@router.get("/history", response_model=None)
def list_history(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """我的浏览历史（按浏览时间倒序，最多 HISTORY_MAX 条）。"""
    return ok(_history_payload(db, user))


@router.post("/history", response_model=None)
def record_history(
    body: MarkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """记录一次浏览（同一资讯只刷新时间，幂等）。

    这是详情页的**被动副作用**：资讯已不存在时静默忽略而非报错，
    避免与时效清理产生竞态时向用户抛出无意义错误。
    """
    if db.get(News, body.newsId) is None:
        return ok(_history_payload(db, user))
    row = db.get(UserHistory, {"user_id": user.id, "news_id": body.newsId})
    ts = _now_iso()
    if row is None:
        db.add(UserHistory(user_id=user.id, news_id=body.newsId, viewed_at=ts))
    else:
        row.viewed_at = ts
    db.commit()
    _trim_history(db, user)
    return ok(_history_payload(db, user))


@router.delete("/history", response_model=None)
def clear_history(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """清空浏览历史。"""
    db.execute(UserHistory.__table__.delete().where(UserHistory.user_id == user.id))
    db.commit()
    return ok({"ids": [], "list": []}, message="已清空浏览历史")


@router.put("/history", response_model=None)
def merge_history(
    body: MarkBulkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """并集合并（登录时把本地历史并入云端），随后按上限裁剪。"""
    added = _merge(db, user, UserHistory, UserHistory.viewed_at, body.ids)
    _trim_history(db, user)
    return ok(_history_payload(db, user), message=f"已合并 {added} 条历史")
