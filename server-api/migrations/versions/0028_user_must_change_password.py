"""C4：管理员代重置密码 + 强制改密（users.must_change_password）

Revision ID: 0028_user_must_change_password
Revises: 0027_time_basis_utc
Create Date: 2026-10-18

背景：
    此前**没有任何改密通道**——用户忘记密码只能无解，管理员也只能新建账号
    （旧号的数据与身份就此悬空）。C4 的最小可用解法是
    「管理员代重置 + 强制改密」。

设计：
    - `must_change_password` **NOT NULL + server_default false**：存量用户一律
      false（他们没用临时密码，不需要被拦），新列不得引入 NULL 三态——
      布尔列出现 NULL 时，「是否受限」这条判断就会在部分行上失效。
    - **不建索引**：该列只参与「按 user_id 取到用户后读一个布尔」的判断，
      没有按它过滤的查询；索引只会拖慢每次写入。
    - 与 `token_version` 配合：重置密码时一并自增版本号 → 该用户**所有已签发
      令牌立即失效**（密码泄露场景下这是关键一步，否则旧的 access 还能用 30 分钟）。
    - downgrade 直接删列：改密标志是运行时状态，回滚后已改过密码的用户
      不会被重新要求改密（这不是安全问题，只是提示消失）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_user_must_change_password"
down_revision: Union[str, None] = "0027_time_basis_utc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")