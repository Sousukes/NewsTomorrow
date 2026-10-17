"""A2：消息列表按真实时间排序（messages.created_at）

Revision ID: 0026_message_created_at
Revises: 0025_news_content
Create Date: 2026-10-18

背景：
    消息列表此前按 `Message.id DESC` 排序、游标用 `Message.id < cursor`，
    而互动消息的 id 是 `msg_{uuid4hex}`——**随机串**。后果：
    - 排序与真实时间无关，keyset 游标语义失效（"上一页末条之后"
      翻出来的可能比它更早或更晚）；
    - 最旧的消息可能排在最前，用户看到的消息顺序与 `time` 文案脱节。

设计：
    - **`created_at` 可空、不设 server_default**：存量行没有可信时间源——
      `messages.time` 存的是「1小时前」这类相对文案，不是时间戳，
      不能拿它反推绝对时间。强行回填等于编造数据。
    - **排序用 `coalesce(created_at, '')`**：与 `news.published_at` 的
      `_PUB` 同款处方——空串在 DESC 下排最后，存量行自然沉底，
      且 PG 与 SQLite 行为一致（不需要 NULLS LAST，两者语法支持度不同）。
    - **建索引 `ix_messages_created_at`**：列表的 ORDER BY 键。
      可见范围是 `user_id = X OR user_id IS NULL`（OR 无法走复合索引），
      所以该索引服务的是「排序」而非「过滤」——避免每页全表排序。
    - **模型侧 `default=now_str`**：新消息（`notify.py` / `alert.py` /
      种子）无需逐个改动即自动写入时间戳，单一来源。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_message_created_at"
down_revision: Union[str, None] = "0025_news_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("created_at", sa.String(length=30), nullable=True))
    op.create_index("ix_messages_created_at", "messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_column("messages", "created_at")
