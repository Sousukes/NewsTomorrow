"""P1-18：抓取失败分级与熔断字段（rss_sources）

Revision ID: 0018_fetch_failure_grading
Revises: 0017_fetch_politeness
Create Date: 2026-10-15

背景：
    此前所有失败一视同仁：无论 404 还是连接超时，都按 `RETRY_TIMES=2` 退避重试两遍。
    - 对 404 / robots 拒绝这类**永久性**失败，重试多少次结果都一样，
      只是白白多打 N 个请求（对对方站点也是负担）；
    - 健康状态里也只有"最近一次失败"的快照，看不出"连挂了多久"，
      无法判断该继续等还是该换源。

设计：
    - `consecutive_failures`：连续失败次数，成功即归零。
      **落库而非进程内计数**——多实例部署时各实例各算各的，重启也会丢失。
    - `failure_kind`：最近一次失败的**性质**（transient / permanent），成功时置空。
      区分二者是 P1-18 的核心：前者"等一等就好"，后者"必须改配置或换源"，
      对策完全不同，混在一起就只能一刀切地重试。
    - `next_retry_at`：熔断到期时间。连续失败达阈值后设置，冷却期内整轮跳过该源。
      存量行三列均为"干净"初值（0 / NULL / NULL），语义即"没有正在发生的故障"，
      不会因为迁移而被误判成已经挂了很久。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_fetch_failure_grading"
down_revision: Union[str, None] = "0017_fetch_politeness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 与模型 default=0 对齐：存量行必须是 0 而非 NULL，
    # 否则 `consecutive_failures IS NULL` 会让熔断阈值比较静默失效。
    op.add_column(
        "rss_sources",
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("rss_sources", sa.Column("failure_kind", sa.String(length=20), nullable=True))
    op.add_column("rss_sources", sa.Column("next_retry_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("rss_sources", "next_retry_at")
    op.drop_column("rss_sources", "failure_kind")
    op.drop_column("rss_sources", "consecutive_failures")
