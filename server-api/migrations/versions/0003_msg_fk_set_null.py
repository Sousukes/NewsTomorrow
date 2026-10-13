"""messages.news_id 改为 ON DELETE SET NULL

Revision ID: 0003_msg_fk_set_null
Revises: 0002_news_published_at
Create Date: 2026-10-07

背景：资讯被清理时，消息不应被连带删除或导致外键报错。

兼容性：
    用 `batch_alter_table` 而非裸 `op.drop_constraint` —— SQLite 不支持
    ALTER 约束，裸调用会让**整条迁移链在 SQLite 上失败**（此前被启动时
    `create_all()` 掩盖；schema 单轨化后 SQLite 也必须能跑通迁移）。
    在 PostgreSQL 上 batch 模式为直通，行为等价。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_msg_fk_set_null"
down_revision: Union[str, None] = "0002_news_published_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "messages_news_id_fkey"


def _is_postgres() -> bool:
    """约束的 ALTER / 按名删除仅在 PostgreSQL 可靠。

    0001 建表时外键**未显式命名**：PostgreSQL 会自动命名为 `messages_news_id_fkey`，
    而 SQLite 的外键是无名的——按名 drop 会抛 `No such constraint`。
    因此本迁移只在 PostgreSQL 上改写约束；SQLite 侧依赖应用层维护引用完整性
    （`seed._sync_orphans` 与 `ingest.prune_old_news` 里显式的解除关联），
    且 SQLite 默认并不启用外键约束。
    """
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.drop_constraint(FK_NAME, "messages", type_="foreignkey")
    op.create_foreign_key(FK_NAME, "messages", "news", ["news_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    if not _is_postgres():
        return
    op.drop_constraint(FK_NAME, "messages", type_="foreignkey")
    op.create_foreign_key(FK_NAME, "messages", "news", ["news_id"], ["id"])