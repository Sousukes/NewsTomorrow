"""负反馈与曝光去重的数据访问（E3）。

把这两块的读写从路由里抽出来，理由与 `search_stats.py` 相同：**统计口径只有一份**。
去重（列表要排除什么）与清理（定时任务要删什么）必须基于同一个"曝光在保留期内"
的定义，否则会出现"清理删了、列表还在排"或"列表不排了、行却越堆越多"——
而这两种漂移都不会报错，只会让用户觉得"我明明看过怎么又推"。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import EXPOSURE_RETENTION_DAYS
from .models import News, UserDislike, UserExposure, now_str

logger = logging.getLogger("toutiao.feedback")

# 负反馈目标类型（与 UserDislike.target_type 对齐）
DISLIKE_NEWS = "news"
DISLIKE_SOURCE = "source"
DISLIKE_TYPES = (DISLIKE_NEWS, DISLIKE_SOURCE)

# 来源级负反馈在 for-you 里的衰减系数（E3 设计 D1）：
# 0.3 意味着该来源的排序分被打到三成——"明显少了"但**没有归零**。
# 硬归零会让用户感知不到被屏蔽了多少，误操作也不可恢复。
SOURCE_DISLIKE_FACTOR = 0.3


def _cutoff(days: int, now: datetime) -> str:
    return (now - timedelta(days=max(0, days))).isoformat(timespec="seconds")


def record_exposures(db: Session, user_id: int, ids: list[str], now=None) -> int:
    """批量记录曝光（幂等 upsert）。返回本次写入/刷新的条数。

    用「先查后写」而不是 `ON CONFLICT DO UPDATE`：SQLite 的 upsert 语法
    （`INSERT ... ON CONFLICT`）与 PG 的参数占位符不同，走 SQLAlchemy 的
    dialect-specific insert 会牵扯两套方言分支；而批量上限只有 50 条，
    一次 `IN` 查询的成本远低于维护那套分支。
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    clean = [str(i)[:32] for i in ids if i]
    if not clean:
        return 0

    existing = dict(
        db.query(UserExposure.news_id, UserExposure.exposed_at)
        .filter(UserExposure.user_id == user_id, UserExposure.news_id.in_(clean))
        .all()
    )
    written = 0
    for nid in clean:
        row = existing.get(nid)
        if row is None:
            db.add(UserExposure(user_id=user_id, news_id=nid, exposed_at=stamp))
            written += 1
        elif row < stamp:
            existing[nid] = stamp
            db.query(UserExposure).filter(
                UserExposure.user_id == user_id, UserExposure.news_id == nid
            ).update({"exposed_at": stamp}, synchronize_session=False)
            written += 1
    db.commit()
    return written


def exposed_news_ids(db: Session, user_id: int, now=None) -> set[str]:
    """保留期内已曝光的资讯 id。"""
    now = now or datetime.now(timezone.utc)
    cutoff = _cutoff(EXPOSURE_RETENTION_DAYS, now)
    rows = (
        db.query(UserExposure.news_id)
        .filter(UserExposure.user_id == user_id, UserExposure.exposed_at >= cutoff)
        .all()
    )
    return {r[0] for r in rows}


def disliked_news_ids(db: Session, user_id: int) -> set[str]:
    rows = (
        db.query(UserDislike.target_id)
        .filter(UserDislike.user_id == user_id, UserDislike.target_type == DISLIKE_NEWS)
        .all()
    )
    return {r[0] for r in rows}


def disliked_source_keys(db: Session, user_id: int) -> set[str]:
    rows = (
        db.query(UserDislike.target_id)
        .filter(UserDislike.user_id == user_id, UserDislike.target_type == DISLIKE_SOURCE)
        .all()
    )
    return {r[0] for r in rows}


def source_dislike_expr(keys: set[str]):
    """来源级负反馈 → SQL CASE 表达式（命中则为 SOURCE_DISLIKE_FACTOR）。

    空集合返回字面量 1.0（"不衰减"），避免生成空 CASE。
    数值字面量内联：命中集合来自服务端查询结果，非用户输入。
    """
    from sqlalchemy import case, literal

    if not keys:
        return literal(1.0)
    # source_key 可空（历史数据回填不上）→ 未命中的（含 NULL）不衰减
    whens = [(News.source_key == k, SOURCE_DISLIKE_FACTOR) for k in keys]
    return case(*whens, else_=1.0)


def visible_news_count(db: Session) -> int:
    """可推荐内容总量（未下架）。探底保护的分母。"""
    return db.query(func.count(News.id)).filter(News.hidden.is_(False)).scalar() or 0


def prune_exposures(db: Session, keep_days: int = EXPOSURE_RETENTION_DAYS, now=None) -> int:
    """清理超过保留期的曝光记录，返回删除条数。

    只清理曝光，**不动负反馈**：负反馈是用户的明确表达，没有"过期"一说
    （取消只能由用户主动做）。
    """
    now = now or datetime.now(timezone.utc)
    cutoff = _cutoff(keep_days, now)
    stale = db.query(UserExposure.news_id).filter(UserExposure.exposed_at < cutoff).all()
    if not stale:
        return 0
    ids = [r[0] for r in stale]
    deleted = (
        db.query(UserExposure)
        .filter(UserExposure.news_id.in_(ids), UserExposure.exposed_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def set_dislike(db: Session, user_id: int, target_type: str, target_id: str) -> bool:
    """写入一条负反馈（幂等）。返回是否新增（已存在则只刷新时间）。"""
    row = db.get(
        UserDislike, {"user_id": user_id, "target_type": target_type, "target_id": target_id}
    )
    if row is not None:
        if row.created_at < now_str():
            row.created_at = now_str()
            db.commit()
        return False
    db.add(
        UserDislike(user_id=user_id, target_type=target_type, target_id=target_id, created_at=now_str())
    )
    db.commit()
    return True


def clear_dislike(db: Session, user_id: int, target_type: str, target_id: str) -> bool:
    """取消一条负反馈（幂等）。返回是否真的删掉了。"""
    row = db.get(
        UserDislike, {"user_id": user_id, "target_type": target_type, "target_id": target_id}
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
