"""E6b：用户关注来源（user_follows 表）

Revision ID: 0032_user_follows
Revises: 0031_user_reports
Create Date: 2026-10-18

设计见 `docs/后端/E6b关注作者设计.md`。要点：
- 关注对象是 `rss_sources.key`（稳定标识），**不是** `name`——名称可被运营改名；
- 复合主键 `(user_id, source_key)` 天然幂等，重复关注由主键冲突兜住；
- **不建外键到 `rss_sources`**：源可删除/重建，关注历史不该连带丢；
- `ix_user_follows_source` 支撑「某源被多少人关注」（第一版不用，但建起来便宜，
  不建的话将来加榜单要重建表）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_user_follows"
down_revision: Union[str, None] = "0031_user_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_follows",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "source_key"),
    )
    op.create_index("ix_user_follows_source", "user_follows", ["source_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_follows_source", table_name="user_follows")
    op.drop_table("user_follows")
