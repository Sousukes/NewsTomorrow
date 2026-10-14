"""P1-20：资讯软下架（news.hidden / hidden_at / hidden_reason）

Revision ID: 0020_news_soft_hide
Revises: 0019_source_health_events
Create Date: 2026-10-16

背景：
    内容全部来自 RSS 自动聚合——发现违规/侵权/错误资讯时，此前**没有任何**
    处置手段：不能删（没有记录），也不能改（聚合下一轮又会写回）。运营只能
    去停掉整个源，代价是同源的正常内容一起消失。

设计：
    - **软下架三列**（对齐评论的 is_deleted 处方）：`hidden` 非空默认 false
      （存量行必须是「可见」而非 NULL，否则过滤条件会把老资讯整体漏掉）；
      `hidden_at` 记录时间；`hidden_reason` 存**必填**理由（trim 后 ≤300）。
      留痕是准入条件——与 P1-18 自动禁用必须能解释同理。
    - **不建索引**：下架集预期很小，hidden 索引只会让热 feed 的索引膨胀、
      拖慢每轮写入；WHERE hidden = false 对全表扫描也足够便宜。
    - 恢复（restore）清空 reason 与 at：理由只对下架状态有意义，
      恢复后再留着会让下一次下架的审计读起来张冠李戴。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_news_soft_hide"
down_revision: Union[str, None] = "0019_source_health_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default=sa.false() 与模型 default=False 对齐：存量行也必须是「可见」，
    # NULL 会在公开查询的过滤条件里掉出结果集——「老资讯突然搜不到」且极难定位。
    op.add_column(
        "news",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("news", sa.Column("hidden_at", sa.String(length=30), nullable=True))
    op.add_column("news", sa.Column("hidden_reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("news", "hidden_reason")
    op.drop_column("news", "hidden_at")
    op.drop_column("news", "hidden")
