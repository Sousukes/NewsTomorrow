"""M5：用户 token 版本号（支持服务端吊销）

Revision ID: 0006_user_token_version
Revises: 0005_news_search_trgm
Create Date: 2026-10-08

背景：无状态 JWT 此前无法吊销（`/logout` 服务端为空操作）。
新增 `users.token_version`：JWT 载荷携带 `ver`，校验时与库中版本比对；
`/logout` 自增版本即使用户所有已签发 token 立即失效。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_token_version"
down_revision: Union[str, None] = "0005_news_search_trgm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # 历史行统一为 0（server_default 已覆盖，这里显式回填以防方言差异）
    op.execute("UPDATE users SET token_version = 0 WHERE token_version IS NULL")


def downgrade() -> None:
    op.drop_column("users", "token_version")
