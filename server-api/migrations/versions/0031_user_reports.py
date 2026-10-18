"""E5：用户举报（reports 表）

Revision ID: 0031_user_reports
Revises: 0030_health_event_at_datetime
Create Date: 2026-10-18

设计见 `docs/后端/E5用户举报设计.md`。要点：
- `target_id` 是异构主键（评论/资讯两类 id），**不建外键**；
- `UNIQUE(reporter_id, target_type, target_id)` 幂等去重——自动处置防刷的地基。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_user_reports"
down_revision: Union[str, None] = "0030_health_event_at_datetime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=10), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("resolution", sa.String(length=12), nullable=True),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.Column("resolved_at", sa.String(length=30), nullable=True),
        sa.Column("resolved_by", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reporter_id", "target_type", "target_id", name="uq_report_once"
        ),
    )
    op.create_index(
        "ix_reports_status_created", "reports", ["status", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_reports_status_created", table_name="reports")
    op.drop_table("reports")
