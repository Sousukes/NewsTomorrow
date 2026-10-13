"""P1-14：管理员白名单入库（admin_users）

Revision ID: 0015_admin_whitelist
Revises: 0014_comment_likes_and_replies
Create Date: 2026-10-15

背景：
    管理后台的「指定账号」此前只由 `ADMIN_PHONES` 环境变量承载——
    加/撤一名管理员要改部署配置并重启服务，运营无法自助。

设计：
    - 白名单落入 `admin_users` 表，应用内可增删，改人无需重启；
    - 与环境变量是**并集**：env 保留为 break-glass（重启即生效、不依赖库），
      两边都为空 = 管理能力整体关闭（生产安全语义不变）；
    - 手机号即主键、不设 users 外键：支持**预授权**（未注册账号先加入名单，
      其注册/登录时 isAdmin 即为 true）；
    - `added_by` 记录操作者，满足「谁能授权」的审计追问。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_admin_whitelist"
down_revision: Union[str, None] = "0014_comment_likes_and_replies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=100), nullable=False),
        sa.Column("added_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("phone"),
    )
    op.create_index(op.f("ix_admin_users_created_at"), "admin_users", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_users_created_at"), table_name="admin_users")
    op.drop_table("admin_users")
