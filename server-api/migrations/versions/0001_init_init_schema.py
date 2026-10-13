"""基线迁移：完整初始表结构

Revision ID: 0001_init
Revises:
Create Date: 2026-10-07

说明：
    此前表结构由应用启动时的 create_all() 建立（无迁移记录）。
    本迁移把现有结构固化为基线，使全新环境 `alembic upgrade head` 能正确建表。
    包含 users / news / comments / messages 四张表。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLite 用 JSON，PostgreSQL 用 JSONB（与 app.models.JSONType 保持一致）
# 注意：with_variant 返回类型实例，直接作为列类型传入（不要再加括号调用）
JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)

    op.create_table(
        "news",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("time", sa.String(length=30), nullable=True),
        sa.Column("images", JSONType, nullable=True),
        sa.Column("cover", sa.String(length=500), nullable=True),
        sa.Column("video_url", sa.String(length=500), nullable=True),
        sa.Column("play_count", sa.Integer(), nullable=True),
        sa.Column("is_top", sa.Boolean(), nullable=True),
        sa.Column("channel", sa.String(length=30), nullable=True),
        sa.Column("variant", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_news_channel"), "news", ["channel"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("news_id", sa.String(length=32), nullable=False),
        sa.Column("user_name", sa.String(length=50), nullable=True),
        sa.Column("seed", sa.String(length=50), nullable=True),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("time", sa.String(length=30), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("liked_by", JSONType, nullable=True),
        sa.ForeignKeyConstraint(["news_id"], ["news.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_comments_news_id"), "comments", ["news_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=True),
        sa.Column("news_id", sa.String(length=32), nullable=True),
        sa.Column("time", sa.String(length=30), nullable=True),
        sa.Column("read_by", JSONType, nullable=True),
        sa.ForeignKeyConstraint(["news_id"], ["news.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_news_id"), "messages", ["news_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_news_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_comments_news_id"), table_name="comments")
    op.drop_table("comments")
    op.drop_index(op.f("ix_news_channel"), table_name="news")
    op.drop_table("news")
    op.drop_index(op.f("ix_users_phone"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")