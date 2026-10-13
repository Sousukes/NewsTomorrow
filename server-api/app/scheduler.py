"""定时抓取调度。

用标准库 asyncio 实现，避免引入 APScheduler 依赖：
- 启动后按 FEED_CRON_INTERVAL_MINUTES 周期执行
- 支持手动触发（POST /api/news/refresh）
- 单次执行异常不影响后续调度
- **多实例互斥**：PostgreSQL 下用会话级 advisory lock 保证同一时刻只有一个
  实例真正执行抓取，避免多 worker/多实例重复抓取与并发写（M6）。
"""
import asyncio
import logging

from sqlalchemy import text

from .config import FEED_CRON_ENABLED, FEED_CRON_INTERVAL_MINUTES
from .database import SessionLocal, dispose
from .feeds.ingest import FetchReport, prune_old_news, run_ingest
from .ranking import recompute_hot_scores
from .search_stats import prune_search_logs

logger = logging.getLogger("toutiao.scheduler")

_task: asyncio.Task | None = None

# advisory lock 键（"tout"），仅 PG 生效
_ADVISORY_LOCK_KEY = 0x746F7574


def _try_lock(db) -> bool:
    """尝试获取抓取锁；非 PostgreSQL 方言视为始终成功。"""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    try:
        return bool(
            db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}).scalar()
        )
    except Exception as e:
        logger.warning("advisory lock 获取异常，按可执行处理：%s", e)
        return True


def _release_lock(db) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    try:
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
    except Exception as e:
        logger.warning("advisory lock 释放异常：%s", e)


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
    with SessionLocal() as db:
        if not _try_lock(db):
            logger.info("未获得抓取锁（其他实例正在执行），跳过本轮")
            return FetchReport()
        try:
            report = run_ingest(db, channel=channel, keyword=keyword)
            # 抓取后顺带重算热度：新内容需有初始分值，存量内容的时间衰减也要刷新
            try:
                n = recompute_hot_scores(db)
                if n:
                    logger.info("热度分重算：%d 条", n)
            except Exception as e:
                logger.warning("热度分重算失败（不影响抓取结果）: %s", e)
                db.rollback()
            # 清理：过旧资讯与过期搜索日志。
            # `prune_old_news` 此前定义了却从未被调用，清理实际从未执行（表会无限增长）。
            try:
                pruned = prune_old_news(db)
                if pruned:
                    logger.info("清理过旧资讯：%d 条", pruned)
                logs = prune_search_logs(db)
                if logs:
                    logger.info("清理过期搜索日志：%d 条", logs)
            except Exception as e:
                logger.warning("清理失败（不影响抓取结果）: %s", e)
                db.rollback()
            return report
        finally:
            _release_lock(db)


async def _loop(interval_minutes: int) -> None:
    # 启动即执行一次，随后按周期执行（避免首轮等待一整个周期）
    await _job()
    while True:
        await asyncio.sleep(interval_minutes * 60)
        await _job()


async def start_scheduler() -> None:
    """启动后台调度任务（幂等）。"""
    global _task
    if not FEED_CRON_ENABLED or _task is not None:
        return
    _task = asyncio.create_task(_loop(FEED_CRON_INTERVAL_MINUTES), name="feed-cron")
    logger.info("定时抓取已启动，周期 %d 分钟", FEED_CRON_INTERVAL_MINUTES)


async def stop_scheduler() -> None:
    """停止调度并释放连接。"""
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
        logger.info("定时抓取已停止")
    dispose()
