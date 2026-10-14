"""P1-19：源健康历史（rss_source_health_events）

Revision ID: 0019_source_health_events
Revises: 0018_fetch_failure_grading
Create Date: 2026-10-15

背景：
    P1-16~P1-18 把「当前状态」做到了位（robots / 告警 / 失败分级 / 熔断），
    但健康字段只有"最近一次"这一个点，**没有时间维度**。于是这几个问题都答不了：

    - "这个源是今天才开始挂的，还是已经烂了两周？"——决定要不要现在处理；
    - "是不是每天凌晨固定抖？"——规律性多半是对方维护窗口，不是我们的锅；
    - "上次改了地址，有好转吗？"——改动效果无从验证；
    - **"源一直返回成功，但条数从 30 条掉到 3 条"**——它永远是绿的，
      其实已经废了。这是现有健康字段**结构性看不见**的一类故障。

设计：
    - **每轮抓取一行**（原始事件），而非每日聚合表：聚合表只能表达"历史累计"，
      事后想换个窗口（7 天 / 30 天）重看就没数据了。代价是需要按时间清理。
    - `ok` + `item_count` 分开记：失败轮次天然 0 条，若只看平均条数会被失败率
      掩盖；"成功轮次的平均条数"才是看退化的指标。
    - `kind` 沿用 P1-18 的失败性质，回答"它当年是为什么挂的"。
    - 复合索引 `(source_key, at)`：主查询是「某源最近 N 天」的范围扫描。
    - 外键 `ON DELETE CASCADE`：源被删了，它的历史也失去可解释的对象，留着只会堆积。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_source_health_events"
down_revision: Union[str, None] = "0018_fetch_failure_grading"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rss_source_health_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=True),
        sa.Column("at", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(["source_key"], ["rss_sources.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 主查询是「某源最近 N 天」：复合索引让过滤与范围扫描都落在索引上。
    # 刻意**不建 `at` 单列索引**：清理查询（`at < cutoff`）在两万行量级上全表扫描
    # 只要几毫秒，而多一个索引要拖慢每轮十几次写入——不划算。
    op.create_index("ix_rss_health_key_at", "rss_source_health_events", ["source_key", "at"])


def downgrade() -> None:
    op.drop_index("ix_rss_health_key_at", table_name="rss_source_health_events")
    op.drop_table("rss_source_health_events")
