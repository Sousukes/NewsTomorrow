"""P1-9：RSS 源可配置化（rss_sources）

Revision ID: 0013_rss_sources
Revises: 0012_search_logs
Create Date: 2026-10-13

背景：
    RSS 源**硬编码**在 `app/feeds/sources.py` 的 `RSS_SOURCES` 列表里——
    新增/停用/调整权重都要改代码并重新部署，运营无法介入。

设计：
    - 内置源保留在代码中作为**播种来源**与无库环境的回退，
      实际抓取读库；表为空时自动播种，新环境开箱可用。
    - `key` 作主键，同时是内置源与库记录对齐的依据（改内置源不会重复插入）。
    - 健康字段（状态/错误/条数/时间）由抓取流程回写：
      源变多后，"哪个源挂了"是排障首要问题，靠翻日志很低效。
    - `ix_rss_sources_channel_enabled` 支撑按频道取启用源。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_rss_sources"
down_revision: Union[str, None] = "0012_search_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rss_sources",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_status", sa.String(length=10), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("last_item_count", sa.Integer(), nullable=True),
        sa.Column("last_fetched_at", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.Column("updated_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_rss_sources_channel_enabled", "rss_sources", ["channel", "enabled"])
    op.create_index(op.f("ix_rss_sources_channel"), "rss_sources", ["channel"])


def downgrade() -> None:
    op.drop_index(op.f("ix_rss_sources_channel"), table_name="rss_sources")
    op.drop_index("ix_rss_sources_channel_enabled", table_name="rss_sources")
    op.drop_table("rss_sources")
