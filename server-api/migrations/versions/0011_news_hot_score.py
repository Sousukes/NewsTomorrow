"""P1-7：热度分落库（news.hot_score）

Revision ID: 0011_news_hot_score
Revises: 0010_user_marks
Create Date: 2026-10-13

背景：
    列表排序此前只有「置顶 → 发布时间倒序」，`recommend`（推荐）流因此只是
    「最新的全部内容」，缺乏热度与个性化语义。要支持热榜与「为你推荐」，
    需要一个**可排序、可索引**的热度维度。

设计：
    - 热度分**落库**而非查询时计算：查询时算要 JOIN 聚合收藏数，既慢又无法建索引，
      且游标分页要求排序键是稳定标量。落库后可建 `(channel, is_top, hot_score, id)` 索引。
    - `nullable=False` + `server_default="0"`：存量行直接填 0，无需回填；
      首次重算（定时任务或启动触发）即得到真实分值。
    - 分值会随时间衰减，故由 `ranking.recompute_hot_scores()` 周期重算；
      评论/收藏等互动发生时增量 bump，保证「刚发生的事立刻可见」。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_news_hot_score"
down_revision: Union[str, None] = "0010_user_marks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news",
        sa.Column("hot_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_news_hot", "news", ["channel", "is_top", "hot_score", "id"])


def downgrade() -> None:
    op.drop_index("ix_news_hot", table_name="news")
    op.drop_column("news", "hot_score")
