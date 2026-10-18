"""E6b：`news.source_key`（来源稳定标识）

Revision ID: 0032 之后 → 0033
Revises: 0032_user_follows
Create Date: 2026-10-18

设计见 `docs/后端/E6b关注作者设计.md`。

为什么需要这一列：
    `news.source` 是**名称快照**（来自 `rss_sources.name`），而运营可以在管理端改名。
    改名后同一来源会在 `news` 里出现两个名字，按名称关注会「只关注一半」；
    关注关系必须挂在稳定的 `rss_sources.key` 上。

本迁移做三件事：
1. 加列 `source_key`（**可空**：回填对不上的历史行留 NULL，展示不受影响）；
2. 建索引（关注流按 `source_key IN (...)` 过滤）；
3. 按名称回填：`news.source = rss_sources.name` → `key`。

回填用相关子查询（PG / SQLite 同一句）：
    UPDATE news SET source_key = (SELECT key FROM rss_sources WHERE name = news.source LIMIT 1)
`LIMIT 1` 不是装饰：运营若配了两个同名源，子查询会返回多行，PG 直接报
`more than one row returned by a subquery` 并中断整条迁移。

⚠️ 回填之后必须让**抓取侧也写入 source_key**（`feeds/fetcher.py` 的 RawItem +
`feeds/ingest.py` 的入库），否则新抓的数据会重新变成 NULL——迁移只补历史。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_news_source_key"
down_revision: Union[str, None] = "0032_user_follows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "news"
COLUMN = "source_key"
INDEX = "ix_news_source_key"

BACKFILL = (
    f"UPDATE {TABLE} SET {COLUMN} = "  # noqa: S608 —— 表名/列名均为本文件常量
    f"(SELECT key FROM rss_sources WHERE rss_sources.name = {TABLE}.source LIMIT 1)"
)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=64), nullable=True))
    op.create_index(INDEX, TABLE, [COLUMN], unique=False)

    res = bind.execute(sa.text(BACKFILL))
    print(f"[0033] news.source_key 回填：{res.rowcount if res.rowcount is not None else 0} 行")


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_column(TABLE, COLUMN)
        return
    # SQLite 不支持裸 drop_column：走 batch 重建（反射旧表 → 建新表 → 换名）
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column(COLUMN)
