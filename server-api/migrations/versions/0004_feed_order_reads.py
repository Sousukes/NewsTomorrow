"""批次 H/M：feed 排序索引 + 评论绝对时间 + 消息已读关联表

Revision ID: 0004_feed_order_reads
Revises: 0003_msg_fk_set_null
Create Date: 2026-10-08

包含三项结构性变更：
1. `comments.created_at`：新增绝对时间列并建索引（排序唯一依据），
   历史行回填为固定基准值，使 `id DESC` 兜底顺序稳定。
2. `news` 复合索引 `ix_news_feed`(channel, is_top, published_at, id)：
   支撑 `ORDER BY is_top DESC, published_at DESC, id DESC` 与频道过滤。
3. `message_reads` 关联表（message × user）取代 `messages.read_by` JSON 数组：
   先建表 → 从旧 JSON 回填 → 再删除旧列（SQLite 走 batch 模式）。
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_feed_order_reads"
down_revision: Union[str, None] = "0003_msg_fk_set_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 历史评论无法还原真实时间，统一回填为基准值（配合 id DESC 兜底保证稳定顺序）
_COMMENT_EPOCH = "1970-01-01T00:00:00+00:00"


def upgrade() -> None:
    # ---- 1. comments.created_at ----
    op.add_column("comments", sa.Column("created_at", sa.String(length=30), nullable=True))
    op.create_index(op.f("ix_comments_created_at"), "comments", ["created_at"], unique=False)
    op.execute(f"UPDATE comments SET created_at = '{_COMMENT_EPOCH}' WHERE created_at IS NULL")

    # ---- 2. news 复合索引 ----
    op.create_index(
        "ix_news_feed",
        "news",
        ["channel", "is_top", "published_at", "id"],
        unique=False,
    )

    # ---- 3. message_reads ----
    op.create_table(
        "message_reads",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", "user_id"),
    )

    # 回填：把 messages.read_by（JSON 数组）逐行搬到 message_reads
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, read_by FROM messages")).fetchall()
    for msg_id, raw in rows:
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                user_ids = json.loads(raw) if raw.strip() else []
            except (ValueError, TypeError):
                user_ids = []
        elif isinstance(raw, (list, tuple)):
            user_ids = list(raw)
        else:
            user_ids = []
        for uid in user_ids:
            bind.execute(
                sa.text(
                    "INSERT INTO message_reads (message_id, user_id, created_at) "
                    "VALUES (:m, :u, :t)"
                ),
                {"m": msg_id, "u": int(uid), "t": _COMMENT_EPOCH},
            )

    # 删除旧列（SQLite 需 batch 重建表）
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("read_by")


def downgrade() -> None:
    # 还原 read_by 列（JSON 文本，内容不做反向迁移）
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("read_by", sa.JSON(), nullable=True))

    op.drop_table("message_reads")
    op.drop_index("ix_news_feed", table_name="news")
    op.drop_index(op.f("ix_comments_created_at"), table_name="comments")
    op.drop_column("comments", "created_at")
