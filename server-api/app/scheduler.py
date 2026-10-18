"""定时任务调度：抓取 cron 与热度重算 cron（P1-21 起相互独立）。

用标准库 asyncio 实现，避免引入 APScheduler 依赖：
- 启动后按各自周期执行，支持手动触发抓取（POST /api/news/refresh）
- 单次执行异常不影响后续调度
- **多实例互斥**：PostgreSQL 下用会话级 advisory lock 保证同一时刻只有一个
  实例真正执行，避免多 worker/多实例重复抓取与并发写（M6）。

P1-21：热度重算从抓取任务中**拆出**为独立 cron。此前后果是——
`FEED_CRON_ENABLED=false` 时热度永不衰减（hot / for-you 排序冻结）；
重算节奏被抓取周期绑定；`run_ingest` 抛异常时重算被连带跳过。
两条 cron 各持一把 advisory lock（键不同），互不阻塞：
抓取锁不该挡住热度任务，反之亦然。
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from .config import (
    FEED_CRON_ENABLED,
    FEED_CRON_INTERVAL_MINUTES,
    HOT_CRON_ENABLED,
    HOT_CRON_INTERVAL_MINUTES,
    HOT_STALE_MINUTES,
)
from .database import SchedulerSessionLocal, dispose
from .feeds.health import prune as prune_health_events
from .feeds.ingest import FetchReport, prune_old_news, run_ingest
from .hit_logs import prune_hit_logs
from .ranking import recompute_hot_scores
from .search_stats import prune_search_logs

logger = logging.getLogger("toutiao.scheduler")

_task: asyncio.Task | None = None
_hot_task: asyncio.Task | None = None

# advisory lock 键（"tout" / "hot"），仅 PG 生效。**两把锁键不同**：
# 热度任务不该因为另一实例正在抓取而整轮跳过。
_ADVISORY_LOCK_KEY = 0x746F7574
_HOT_ADVISORY_LOCK_KEY = 0x686F74

# 热度任务状态（进程内，供 /api/health 暴露；重启即清零）
_hot_status: dict = {"last_at": None, "last_count": 0, "last_error": None}


def hot_status() -> dict:
    """热度重算状态快照（只读副本）。"""
    return dict(_hot_status)


def _try_lock(db, key: int) -> bool:
    """尝试获取指定键的锁；非 PostgreSQL 方言视为始终成功。"""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    try:
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
    except Exception as e:
        logger.warning("advisory lock 获取异常，按可执行处理：%s", e)
        return True


def _release_lock(db, key: int) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    try:
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    except Exception as e:
        logger.warning("advisory lock 释放异常: %s", e)


async def _job(channel: str | None = None, keyword: str | None = None) -> None:
    """单次抓取任务（在线程池中跑同步的 DB/网络操作）。"""
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, lambda: _run_once(channel, keyword))
        if report.fetched or report.inserted or report.updated:
            logger.info(
                "定时抓取完成：抓取 %d / 新增 %d / 更新 %d",
                report.fetched,
                report.inserted,
                report.updated,
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # 任务级兜底：不让一次失败终止整个调度循环
        logger.exception("定时抓取异常（将在下个周期重试）: %s", e)


def _run_once(channel: str | None = None, keyword: str | None = None) -> FetchReport:
    """获取互斥锁后执行一次抓取；未获得锁则跳过本轮。"""
    with SchedulerSessionLocal() as db:
        if not _try_lock(db, _ADVISORY_LOCK_KEY):
            logger.info("未获得抓取锁（其他实例正在执行），跳过本轮")
            return FetchReport()
        try:
            report = run_ingest(db, channel=channel, keyword=keyword)
            # 清理：过旧资讯与过期搜索日志。
            # `prune_old_news` 此前定义了却从未被调用，清理实际从未执行（表会无限增长）。
            try:
                pruned = prune_old_news(db)
                if pruned:
                    logger.info("清理过旧资讯：%d 条", pruned)
                logs = prune_search_logs(db)
                if logs:
                    logger.info("清理过期搜索日志：%d 条", logs)
                # P1-19：健康历史按时间清理（不设保底条数——"最近 N 天"
                # 就是它的全部价值，过期即无意义）
                events = prune_health_events(db)
                if events:
                    logger.info("清理过期源健康事件：%d 条", events)
                # C7：命中日志留存（snippet 是用户原文，到点即删）
                hits = prune_hit_logs(db)
                if hits["sensitive"] or hits["nickname"]:
                    logger.info(
                        "清理过期命中日志：敏感词 %d 条 / 昵称 %d 条",
                        hits["sensitive"],
                        hits["nickname"],
                    )
            except Exception as e:
                logger.warning("清理失败（不影响抓取结果）: %s", e)
                db.rollback()
            return report
        finally:
            _release_lock(db, _ADVISORY_LOCK_KEY)


def _run_hot_once() -> int:
    """增量重算热度分（只刷过期行）；未获得锁则跳过本轮。"""
    with SchedulerSessionLocal() as db:
        if not _try_lock(db, _HOT_ADVISORY_LOCK_KEY):
            logger.info("未获得热度锁（其他实例正在执行），跳过本轮")
            return 0
        try:
            return recompute_hot_scores(db, only_stale=True, stale_minutes=HOT_STALE_MINUTES)
        finally:
            _release_lock(db, _HOT_ADVISORY_LOCK_KEY)


async def _hot_job() -> None:
    """单次热度重算（在线程池中跑同步 DB 操作），并刷新进程内状态。"""
    loop = asyncio.get_running_loop()
    try:
        n = await loop.run_in_executor(None, _run_hot_once)
        _hot_status["last_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _hot_status["last_count"] = n
        _hot_status["last_error"] = None
        if n:
            logger.info("热度分重算：%d 条", n)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # 热度是派生数据：一次失败只记录状态，绝不终止调度循环
        _hot_status["last_error"] = str(e)
        logger.warning("热度分重算失败（将在下个周期重试）: %s", e)


async def _loop(interval_minutes: int) -> None:
    # 启动即执行一次，随后按周期执行（避免首轮等待一整个周期）
    await _job()
    while True:
        await asyncio.sleep(interval_minutes * 60)
        await _job()


async def _hot_loop(interval_minutes: int) -> None:
    # 启动即执行一次：存量行 hot_score_at 为 NULL，首轮补锚点，之后走增量
    await _hot_job()
    while True:
        await asyncio.sleep(interval_minutes * 60)
        await _hot_job()


async def start_scheduler() -> None:
    """启动后台调度任务（幂等）。抓取与热度两条 cron 相互独立。"""
    global _task, _hot_task
    if FEED_CRON_ENABLED and _task is None:
        _task = asyncio.create_task(_loop(FEED_CRON_INTERVAL_MINUTES), name="feed-cron")
        logger.info("定时抓取已启动，周期 %d 分钟", FEED_CRON_INTERVAL_MINUTES)
    if HOT_CRON_ENABLED and _hot_task is None:
        _hot_task = asyncio.create_task(_hot_loop(HOT_CRON_INTERVAL_MINUTES), name="hot-cron")
        logger.info(
            "热度重算已启动，周期 %d 分钟（staleness 窗口 %d 分钟）",
            HOT_CRON_INTERVAL_MINUTES,
            HOT_STALE_MINUTES,
        )


async def stop_scheduler() -> None:
    """停止调度并释放连接。"""
    global _task, _hot_task
    for name, task in (("feed-cron", _task), ("hot-cron", _hot_task)):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("%s 已停止", name)
    _task = None
    _hot_task = None
    dispose()
