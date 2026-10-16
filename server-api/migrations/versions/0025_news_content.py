"""E1：资讯正文落地（news.content）

Revision ID: 0025_news_content
Revises: 0024_nickname_moderation
Create Date: 2026-10-18

背景：
    详情页此前展示的是前端写死的占位文案（`NewsDetail.vue` 里的 body 常量），
    而 RSS 的 `content:encoded` 早在 fetcher 里就被读取——只用来提图，正文被丢弃。
    用户点进任何一条资讯，看到的都是同一段「据相关方面介绍……」，
    这是产品可信度问题：界面在假装有内容。

设计：
    - **content 用 Text 且可空**：正文长度不可控（长评/专栏可达数万字符），
      String(n) 会撞长度上限；可空是因为存量行与无正文源本来就没有正文，
      用空串反而让「抓过但没抓到」与「没抓过」无法区分。
    - **不加索引**：正文只按 id 点查询详情，没有按正文检索的需求
      （搜索走标题/摘要，见 P1-11 的 pg_trgm 方案）。
    - **列表不带正文**：`to_out` 刻意不输出 content——一条正文几 KB，
      列表一页 20 条就是几十 KB 无效带宽；只有详情接口单独返回。
    - 存量数据不回填：没有原始 XML 可回溯，回填只会造出假正文。
      下一次抓取周期（`upsert_news` 增量刷新）自然会补上。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_news_content"
down_revision: Union[str, None] = "0024_nickname_moderation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("news", sa.Column("content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("news", "content")
