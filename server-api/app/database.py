"""数据库引擎、会话与连接池。

按方言定制策略：
- PostgreSQL：QueuePool + pre_ping + recycle + application_name（生产主力路径）
- SQLite 文件库：QueuePool（多线程需 check_same_thread=False）
- SQLite 内存库：StaticPool（保持单连接，否则每次访问都是新库）
"""
import logging
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import (
    DATABASE_URL,
    DB_APP_NAME,
    DB_CONNECT_TIMEOUT,
    DB_MAX_OVERFLOW,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
    FEED_DB_APP_NAME,
    FEED_DB_MAX_OVERFLOW,
    FEED_DB_POOL_SIZE,
)

logger = logging.getLogger("toutiao.db")


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> Dict[str, Any]:
    if url.startswith("sqlite"):
        kwargs: Dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or "mode=memory" in url:
            # 内存库必须复用同一连接，否则数据不共享
            kwargs["poolclass"] = StaticPool
        return kwargs

    # PostgreSQL（及其他 SQLAlchemy 支持的服务端库）
    return {
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_timeout": DB_POOL_TIMEOUT,
        # 回收长连接：避免服务端/防火墙静默断开后拿到死连接
        "pool_recycle": DB_POOL_RECYCLE,
        # 取连接前探活，避免把死连接交给业务
        "pool_pre_ping": DB_POOL_PRE_PING,
        # 后进先出：让闲置连接尽早回收，减少服务端 idle 连接数
        "pool_use_lifo": True,
        "connect_args": {
            "application_name": DB_APP_NAME,
            "connect_timeout": DB_CONNECT_TIMEOUT,
        },
    }


engine: Engine = create_engine(DATABASE_URL, future=True, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _scheduler_engine_kwargs(url: str) -> Dict[str, Any]:
    """后台任务（抓取 / 热度重算）的池参数（B5）。

    与 API 池的差别只有两点：**容量小**、**应用名不同**。

    为什么必须分开：抓取周期**整段持有**一条连接——advisory lock 绑在会话上，
    中途不能关（关了锁就没了）；而一个周期里要发 N 个 HTTP 请求、各带超时，
    这条连接会被占几分钟。与 API 共用 5+10 的池时，抓取 + 热度重算能长期
    吃掉 2 条，请求峰值就在 `pool_timeout` 上排队。

    共享连接池不是"省资源"，而是**把两种完全不同的占用时长混在一起**：
    请求是毫秒级占用，抓取是分钟级占用。混池的后果是后者决定前者的尾延迟。
    """
    kwargs = _engine_kwargs(url)
    kwargs["pool_size"] = FEED_DB_POOL_SIZE
    kwargs["max_overflow"] = FEED_DB_MAX_OVERFLOW
    kwargs["connect_args"] = {
        **kwargs.get("connect_args", {}),
        "application_name": FEED_DB_APP_NAME,
    }
    return kwargs


# SQLite（含内存库）**必须复用同一个 engine**：内存库靠 `StaticPool` 共享同一条
# 连接才有数据可言，另建 engine 会得到一个空库——症状是"表不存在"，
# 而根因是池隔离。所以池隔离只对服务端库生效。
scheduler_engine: Engine = (
    engine
    if DATABASE_URL.startswith("sqlite")
    else create_engine(DATABASE_URL, future=True, **_scheduler_engine_kwargs(DATABASE_URL))
)
SchedulerSessionLocal = sessionmaker(
    bind=scheduler_engine, autoflush=False, autocommit=False, future=True
)


def get_db():
    """FastAPI 依赖：请求级会话，结束归还连接。

    出异常时先 rollback，避免把未提交事务带进连接池复用。
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def pool_status() -> dict:
    """连接池观测指标（供 /api/health 使用）。"""
    pool = engine.pool
    status = {
        "dialect": engine.dialect.name,
        "size": getattr(pool, "size", lambda: None)(),
        "checked_in": getattr(pool, "checkedin", lambda: None)(),
        "checked_out": getattr(pool, "checkedout", lambda: None)(),
        "overflow": getattr(pool, "overflow", lambda: None)(),
    }
    return {k: v for k, v in status.items() if v is not None}


def dispose() -> None:
    """关闭全部连接（进程退出前调用）。

    两个池都要释放：只 dispose `engine` 会留下后台任务的连接，
    进程退出时服务端仍看到它们（PG 端表现为 idle 连接慢慢堆积）。
    """
    engine.dispose()
    if scheduler_engine is not engine:
        scheduler_engine.dispose()
    logger.info("连接池已释放（API + 后台任务）")
