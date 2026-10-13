"""P1-15：运营审计日志（admin_audit_logs）

Revision ID: 0016_admin_audit_logs
Revises: 0015_admin_whitelist
Create Date: 2026-10-15

背景：
    权限体系（P1-14）回答了「谁能进后台」，但没回答「谁干了什么」——
    源被停用 / 换地址 / 删除、管理员被加入或移除，此前**全部无痕迹**，
    事后排查只能靠猜（尤其是多个管理员共用后台时）。

设计：
    - `id` 自增主键同时充当**游标**（单调，避免秒级时间戳同值导致的排序抖动）；
    - `action` 用 `对象.动作` 命名，前端据此映射中文标签，无需再维护一份枚举表；
    - `detail` 用 JSON 字符串存变更明细：更新记 `changes`（旧→新），增删记 `snapshot`；
    - 索引覆盖两种查询：全局时间线（`created_at`）、单对象历史（`target, id`）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_admin_audit_logs"
down_revision: Union[str, None] = "0015_admin_whitelist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_phone", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("target", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_audit_logs_actor_phone"), "admin_audit_logs", ["actor_phone"])
    op.create_index(op.f("ix_admin_audit_logs_action"), "admin_audit_logs", ["action"])
    op.create_index(op.f("ix_admin_audit_logs_created_at"), "admin_audit_logs", ["created_at"])
    op.create_index("ix_admin_audit_target_id", "admin_audit_logs", ["target", "id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_target_id", table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_created_at"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_action"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_actor_phone"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
