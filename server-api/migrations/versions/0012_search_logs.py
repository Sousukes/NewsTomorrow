"""P1-8：搜索词日志（search_logs）

Revision ID: 0012_search_logs
Revises: 0011_news_hot_score
Create Date: 2026-10-13

背景：
    搜索页的「热门搜索」此前是**前端写死的 6 个词**，与真实搜索行为无关。
    要给出真实热词，必须有服务端统计来源。

设计：
    - 记**原始日志**而非聚合计数（`term` + `count`）。聚合表只能算"历史累计最热"，
      旧词会永久占榜；日志表配合时间窗口才是真正的"近期热词"。
    - `user_id` 可空（未登录也能搜索），用于同一用户短时间内的重复词去重，
      避免反复搜同一词把该词刷上热榜。
    - 外键 ON DELETE SET NULL：用户注销后日志保留（统计总量不该因个别人消失而回落）。
      **仅 PostgreSQL 建外键**——SQLite 不支持 ALTER 加约束，与 0003/0007 同一策略。
    - `ix_search_logs_term_created` 支撑"按词 + 时间窗口"的聚合查询。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_search_logs"
down_revision: Union[str, None] = "0011_news_hot_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_logs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("term", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_logs_term_created", "search_logs", ["term", "created_at"])
    op.create_index(op.f("ix_search_logs_user_id"), "search_logs", ["user_id"])
    op.create_index(op.f("ix_search_logs_term"), "search_logs", ["term"])
    op.create_index(op.f("ix_search_logs_created_at"), "search_logs", ["created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_search_logs_user_id_users", "search_logs", "users", ["user_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_search_logs_user_id_users", "search_logs", type_="foreignkey")
    op.drop_index(op.f("ix_search_logs_created_at"), table_name="search_logs")
    op.drop_index(op.f("ix_search_logs_term"), table_name="search_logs")
    op.drop_index(op.f("ix_search_logs_user_id"), table_name="search_logs")
    op.drop_index("ix_search_logs_term_created", table_name="search_logs")
    op.drop_table("search_logs")
