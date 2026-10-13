"""P1：消息定向（messages.user_id）

Revision ID: 0009_message_recipient
Revises: 0008_comment_soft_delete
Create Date: 2026-10-11

背景：
    消息中心此前只有 4 条写死的系统通知，且是**全局广播**——所有用户看到同一份。
    真实互动事件（有人评论了我评论过的资讯、有人赞了我的评论）必须**定向**给
    具体用户，否则会出现"每个人都收到同一条别人的动态"。

改动：
    `messages.user_id` 可空外键 → `users.id`，ON DELETE CASCADE，并建索引。
    语义：**NULL = 广播**（全员可见）；非 NULL = 定向给该用户。
    历史行保持 NULL，即沿用既有的广播语义，无需回填。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_message_recipient"
down_revision: Union[str, None] = "0008_comment_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_messages_user_id_users",
            "messages",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_messages_user_id_users", "messages", type_="foreignkey")
    op.drop_index(op.f("ix_messages_user_id"), table_name="messages")
    op.drop_column("messages", "user_id")
