"""Alembic 环境：复用 app.config 的连接配置与 Base.metadata。

单一配置源：迁移与运行时使用同一个 DATABASE_URL。
"""
import logging
import os
from logging.config import fileConfig
from urllib.parse import urlparse

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import DATABASE_URL
from app.database import Base

# 导入模型以注册到 metadata（新增模型务必在此导入）
import app.models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _masked_target() -> str:
    """打印迁移目标（隐藏口令），便于确认「到底作用在哪一个库上」。

    来源优先级：环境变量 DATABASE_URL（测试用例用它指向临时库）。
    """
    try:
        p = urlparse(DATABASE_URL)
        if not p.scheme:
            return DATABASE_URL
        host = f"{p.hostname}:{p.port}" if p.port else (p.hostname or "")
        db = (p.path or "").lstrip("/") or "(memory)"
        return f"{p.scheme}://{host}/{db}"
    except Exception:
        return "(无法解析)"


logging.getLogger("alembic.env").info(
    "迁移目标: %s（来源: %s）",
    _masked_target(),
    "环境变量 DATABASE_URL" if os.environ.get("DATABASE_URL") else "默认配置/ .env",
)


def run_migrations_offline() -> None:
    """生成 SQL 脚本而不连库（alembic upgrade head --sql）。"""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # 参与类型变更检测
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()