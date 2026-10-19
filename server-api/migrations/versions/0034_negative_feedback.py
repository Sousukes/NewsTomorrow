"""E3：负反馈与曝光去重（user_exposures / user_dislikes）

Revision ID: 0034_negative_feedback
Revises: 0033_news_source_key
Create Date: 2026-10-19

设计见 `docs/后端/E3负反馈与曝光去重设计.md`。要点：
- **两张表一起建**：它们是同一个功能的两个面（去重依赖曝光、负反馈决定去向），
  拆成两个迁移只会让"回滚一半功能"变成可能状态——而那个状态没有意义。
- `user_exposures` 复合主键 `(user_id, news_id)` 幂等：重复曝光只更新时间。
- **均不建外键到 `news`**：资讯被时效清理后记录仍应留存（与 `reports`
  /`user_follows` 同一处方），去重时 LEFT JOIN 自然过滤。
- `ix_user_exposures_user_at` 支撑保留期过滤（`user_id + exposed_at >= cutoff`），
  这是本表唯一的查询形态；**不给 news_id 建索引**——没有按单条反查的路径。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_negative_feedback"
down_revision: Union[str, None] = "0033_news_source_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_exposures",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_id", sa.String(length=32), nullable=False),
        sa.Column("exposed_at", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "news_id"),
    )
    op.create_index(
        "ix_user_exposures_user_at", "user_exposures", ["user_id", "exposed_at"], unique=False
    )

    op.create_table(
        "user_dislikes",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=10), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "target_type", "target_id"),
    )


def downgrade() -> None:
    op.drop_table("user_dislikes")
    op.drop_index("ix_user_exposures_user_at", table_name="user_exposures")
    op.drop_table("user_exposures")
