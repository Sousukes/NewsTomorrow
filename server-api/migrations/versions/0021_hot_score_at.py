"""P1-21：热度重算独立调度——news.hot_score_at 锚点列

Revision ID: 0021_hot_score_at
Revises: 0020_news_soft_hide
Create Date: 2026-10-15

背景：
    热度分（`news.hot_score`）的时间衰减此前靠抓取 cron 顺带重算——
    `FEED_CRON_ENABLED=false` 时衰减永不推进，hot / for-you 排序冻结；
    且每轮**全表重写**（无变动也写），是纯写入放大。

设计：
    - **独立 cron**：热度重算拆为独立任务（见 `app/scheduler.py`），
      独立开关 / 周期 / 第二把 advisory lock，抓取停不停衰减都按时推进。
    - **增量重算**：本列是锚点。只有 `hot_score_at` 为 NULL（存量行首轮补锚点）
      或早于 staleness 窗口的行才参与本轮；互动 bump 与全量重算都会刷新它。
      窗口（默认 60 分钟）大于周期（默认 15 分钟）时，每行每窗口最多重写一次。
    - **不建索引**：表有 prune 上限（保留天数 + 最小条数），每轮扫全表找过期行
      足够便宜；索引只会拖慢每轮写入。
    - 可空、无 server_default：NULL 即「从未算过」，天然覆盖迁移前存量行。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_hot_score_at"
down_revision: Union[str, None] = "0020_news_soft_hide"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news", sa.Column("hot_score_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("news", "hot_score_at")
