"""P1：评论归属（comments.user_id）

Revision ID: 0007_comment_user_id
Revises: 0006_user_token_version
Create Date: 2026-10-11

背景：
    评论此前只有 `user_name`（字符串），无法可靠判定归属——重名即可能越权。
    这卡住了「删除自己的评论」「我的评论」「评论举报/治理」等能力。

改动：
    1. `comments.user_id` 可空外键 → `users.id`，ON DELETE SET NULL，并建索引；
    2. 回填：按 `users.name = comments.user_name` 匹配补 user_id。
       无法匹配的历史行保持 NULL（语义：作者未知，任何人不得以其身份操作）。

注意：种子评论的 user_name（观察者小张/沿海居民/理性看世界）不对应任何真实
用户，因此回填后仍为 NULL，这是**预期行为**——宁可留空也不要错误归属。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_comment_user_id"
down_revision: Union[str, None] = "0006_user_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_index(op.f("ix_comments_user_id"), "comments", ["user_id"], unique=False)
    # 仅在 PostgreSQL 上加外键：SQLite 不支持为已存在表直接 ADD CONSTRAINT，
    # 且 SQLite 默认不启用外键约束；引用完整性由应用层保证（见 0003 的同款处理）。
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_comments_user_id_users",
            "comments",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 回填：仅当用户名能唯一匹配到用户时才写入，避免把重名评论错误归属
    op.execute(
        """
        UPDATE comments
        SET user_id = (
            SELECT u.id FROM users u WHERE u.name = comments.user_name
            LIMIT 1
        )
        WHERE user_id IS NULL
          AND EXISTS (
            SELECT 1 FROM users u2 WHERE u2.name = comments.user_name
          )
          AND (
            SELECT COUNT(*) FROM users u3 WHERE u3.name = comments.user_name
          ) = 1
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_comments_user_id_users", "comments", type_="foreignkey")
    op.drop_index(op.f("ix_comments_user_id"), table_name="comments")
    op.drop_column("comments", "user_id")
