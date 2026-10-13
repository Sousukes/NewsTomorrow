"""新增 published_at 字段（供增量/时效清理）

Revision ID: 0002_news_published_at
Revises: 0001_init
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_news_published_at"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news", sa.Column("published_at", sa.String(length=30), nullable=True))
    op.create_index(op.f("ix_news_published_at"), "news", ["published_at"], unique=False)
    # 用种子数据的时间字段回填一部分（相对时间无法精确还原，留空由后续抓取补齐）
    op.execute("UPDATE news SET published_at = NULL WHERE published_at IS NULL")


def downgrade() -> None:
    op.drop_index(op.f("ix_news_published_at"), table_name="news")
    op.drop_column("news", "published_at")