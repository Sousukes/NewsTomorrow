"""P1-6：收藏 / 浏览历史云端化（user_favorites / user_history）

Revision ID: 0010_user_marks
Revises: 0009_message_recipient
Create Date: 2026-10-11

背景：
    收藏与浏览历史此前**只存在前端 localStorage**——换设备、清缓存即丢失，
    也无法跨端一致。落到服务端后，登录即可同步（并集合并，不丢本地数据）。

设计：
    - 复合主键 (user_id, news_id) 天然保证「同一用户同一资讯只有一条」（幂等）。
    - 收藏按 `created_at`、历史按 `viewed_at` 倒序，各建 (user_id, 时间列) 索引。
    - 外键 ON DELETE CASCADE：资讯/用户被删除时标记随之清理。
      **仅 PostgreSQL 建外键**——SQLite 默认不强制外键且不支持 ALTER 约束；
      SQLite 下由 `prune_old_news` 显式清理（与 0003/0007 同一策略）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_user_marks"
down_revision: Union[str, None] = "0009_message_recipient"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PG_FKS = (
    ("fk_user_favorites_user_id_users", "user_favorites", "users", "user_id"),
    ("fk_user_favorites_news_id_news", "user_favorites", "news", "news_id"),
    ("fk_user_history_user_id_users", "user_history", "users", "user_id"),
    ("fk_user_history_news_id_news", "user_history", "news", "news_id"),
)


def upgrade() -> None:
    op.create_table(
        "user_favorites",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "news_id"),
    )
    op.create_index(
        "ix_user_favorites_user_created", "user_favorites", ["user_id", "created_at"]
    )

    op.create_table(
        "user_history",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_id", sa.String(length=32), nullable=False),
        sa.Column("viewed_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "news_id"),
    )
    op.create_index("ix_user_history_user_viewed", "user_history", ["user_id", "viewed_at"])

    if op.get_bind().dialect.name == "postgresql":
        for name, table, ref_table, column in _PG_FKS:
            op.create_foreign_key(
                name, table, ref_table, [column], ["id"], ondelete="CASCADE"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name, table, _ref_table, _column in reversed(_PG_FKS):
            op.drop_constraint(name, table, type_="foreignkey")
    op.drop_index("ix_user_history_user_viewed", table_name="user_history")
    op.drop_table("user_history")
    op.drop_index("ix_user_favorites_user_created", table_name="user_favorites")
    op.drop_table("user_favorites")
