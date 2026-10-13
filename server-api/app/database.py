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
    """关闭全部连接（进程退出前调用）。"""
    engine.dispose()
    logger.info("连接池已释放")
