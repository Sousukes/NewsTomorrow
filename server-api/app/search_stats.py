"""搜索词统计：记录、热词聚合、日志清理。

设计要点：
    1. **记原始日志**（`search_logs`）而非聚合计数，热词才能表达"近期热度"；
       代价是需要定期清理（日志会持续增长）。
    2. **同一用户短时间内同词去重**：否则反复搜同一个词就能把它刷上热榜。
       未登录无 `user_id`，去重窗口无法施加，故此类记录权重天然较低（每次都记，
       但单人重复搜索的绝对量远小于真实群体行为）。
    3. **时间可注入**：热词窗口与清理阈值都接受 `now`，测试不依赖真实时钟。
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .cache import build_cache
from .config import HOT_TERMS_TTL_SECONDS, REDIS_URL
from .models import SearchLog, now_str

logger = logging.getLogger("toutiao.search_stats")

# 热搜词缓存（D6）：进程内 / Redis 由 `cache.build_cache` 按 REDIS_URL 选。
# 键包含 `days` 与 `limit`——两者的任意组合都是一份不同的榜，共用一个键会串数据。
_hot_cache = build_cache(HOT_TERMS_TTL_SECONDS, REDIS_URL)

# 同一用户同一词的去重窗口（秒）：窗口内重复搜索不重复计数
DEDUP_WINDOW_SECONDS = 60
# 热词默认统计窗口（天）
HOT_WINDOW_DAYS = 7
# 日志保留天数：超出即清理，避免表无限增长
LOG_KEEP_DAYS = 30
# 词长上限（与模型 String(50) 对齐，防御性截断）
TERM_MAX = 50


def _iso(dt: datetime) -> str:
    """ISO 秒精度串。A3 后 cutoff 必须与 `created_at` 同为 UTC aware 格式——
    这里的比较全是**字符串**比较（SQL 侧 `created_at >= cutoff`），
    两种口径混用会整段错位（字典序只在同格式下才是时间序）。
    """
    return dt.isoformat(timespec="seconds")


def record_search(db: Session, term: str, user_id: int | None = None, now=None) -> bool:
    """记录一次搜索。返回是否真的写入（去重命中时为 False）。

    旁路调用：失败由调用方决定是否忽略（搜索结果不应因统计失败而不可用）。
    """
    clean = (term or "").strip()[:TERM_MAX]
    if not clean:
        return False
    now = now or datetime.now(timezone.utc)

    if user_id is not None:
        cutoff = _iso(now - timedelta(seconds=DEDUP_WINDOW_SECONDS))
        dup = (
            db.query(SearchLog.id)
            .filter(
                SearchLog.user_id == user_id,
                SearchLog.term == clean,
                SearchLog.created_at >= cutoff,
            )
            .first()
        )
        if dup is not None:
            return False

    db.add(
        SearchLog(
            # 必须随机：同一秒内同一词（如未登录连续搜索）若用确定性 ID 会撞主键
            id=f"s_{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            term=clean,
            created_at=_iso(now),
        )
    )
    db.commit()
    return True


def recent_terms(
    db: Session, days: int = HOT_WINDOW_DAYS, now=None, limit: int = 100
) -> list[tuple[str, int]]:
    """窗口内搜索次数 Top-N 的词（次数倒序，同次数按词升序——顺序稳定）。

    **热搜榜与搜索联想共用这一份查询**：两处口径必须一致，否则会出现
    "榜上是 A、联想里却没有 A"这种既难复现也难解释的漂移。联想需要的候选
    比榜上的多，故 `limit` 由调用方给，榜给展示条数、联想给候选池大小。
    """
    now = now or datetime.now(timezone.utc)
    cutoff = _iso(now - timedelta(days=days))
    rows = (
        db.query(SearchLog.term, func.count(SearchLog.id).label("c"))
        .filter(SearchLog.created_at >= cutoff)
        .group_by(SearchLog.term)
        .order_by(func.count(SearchLog.id).desc(), SearchLog.term.asc())
        .limit(limit)
        .all()
    )
    return [(t, int(c)) for t, c in rows]


def hot_terms(db: Session, limit: int = 10, days: int = HOT_WINDOW_DAYS, now=None) -> list[dict]:
    """近 `days` 天热词，按搜索次数倒序；同次数按词升序（保证顺序稳定）。

    为什么要缓存（D6）：这是一条 `GROUP BY term + ORDER BY count` 的聚合，
    每次打开搜索页都打一次，而**窗口内的结果对所有用户都相同**——典型的
    "同样的昂贵计算被重复做了 N 次"。

    两条刻意设计：
    1. **显式传 `now` 时绕过缓存**。`now` 是测试与回放的注入点，缓存会让
       "换个时间点重算"直接拿到上一份结果——测试之间会互相污染，
       而且这种失败看起来像"聚合算错了"，极难定位。
    2. **不做主动失效**（只靠 TTL，默认 60s）。为它加失效意味着每次
       `record_search` 都要删一次全站共享的键——高频写路径上多一次往返，
       换来的只是"刚搜的词立刻上榜"，不值。
    """
    use_cache = now is None
    key = f"hot_terms:{days}:{limit}"
    if use_cache:
        cached = _hot_cache.get(key)
        if cached is not None:
            return cached

    out = [
        {"term": t, "count": c}
        for t, c in recent_terms(db, days=days, now=now, limit=limit)
    ]
    if use_cache:
        # 空榜也缓存：无数据时每次都跑聚合同样是浪费（而且空榜更常见于新站）
        _hot_cache.set(key, out, HOT_TERMS_TTL_SECONDS)
    return out


def prune_search_logs(db: Session, keep_days: int = LOG_KEEP_DAYS, now=None) -> int:
    """清理超过保留期的搜索日志，返回删除条数。"""
    now = now or datetime.now(timezone.utc)
    cutoff = _iso(now - timedelta(days=keep_days))
    deleted = (
        db.query(SearchLog)
        .filter(SearchLog.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)
