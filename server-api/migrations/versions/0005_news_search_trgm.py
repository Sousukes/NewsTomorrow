"""M1：资讯搜索的 pg_trgm GIN 索引（PostgreSQL）

Revision ID: 0005_news_search_trgm
Revises: 0004_feed_order_reads
Create Date: 2026-10-08

背景：搜索使用 `title ILIKE '%kw%'` / `summary ILIKE '%kw%'`，前缀通配符
使 B-tree 索引失效，退化为全表扫描。`pg_trgm` 的 GIN 索引可加速 LIKE/ILIKE
的模糊匹配。

说明：
- 仅对 PostgreSQL 生效；SQLite 等其它方言直接跳过（保持迁移链可执行）。
- `CREATE EXTENSION` 需要相应权限；若当前角色无权创建，则跳过建索引并告警，
  不阻断迁移（搜索仍可用，只是未加速）。
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_news_search_trgm"
down_revision: Union[str, None] = "0004_feed_order_reads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.news_search_trgm")

INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_news_title_trgm ON news USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_news_summary_trgm ON news USING gin (summary gin_trgm_ops)",
)


def _ensure_trgm(bind) -> bool:
    if bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar():
        return True
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        return True
    except Exception as e:  # 权限不足等
        logger.warning("无法创建 pg_trgm 扩展，跳过搜索索引：%s", e)
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _ensure_trgm(bind):
        return
    for stmt in INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_news_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_news_summary_trgm")
