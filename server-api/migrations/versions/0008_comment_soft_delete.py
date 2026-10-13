"""P1：评论软删除（comments.is_deleted / deleted_at）

Revision ID: 0008_comment_soft_delete
Revises: 0007_comment_user_id
Create Date: 2026-10-11

背景：
    作者可以删除自己的评论。若物理删除，该评论上的点赞记录会一并丢失，
    后续审核/申诉也无原文可追溯。故采用软删除：
    - `is_deleted` 标记（NOT NULL，默认 false）
    - `deleted_at` 删除时间
    列表接口过滤已删除项；点赞接口拒绝已删除评论。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_comment_soft_delete"
down_revision: Union[str, None] = "0007_comment_user_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("comments", sa.Column("deleted_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "deleted_at")
    op.drop_column("comments", "is_deleted")
