"""P1-16：抓取礼仪与健康告警字段（rss_sources）

Revision ID: 0017_fetch_politeness
Revises: 0016_admin_audit_logs
Create Date: 2026-10-15

背景：
    抓取链路此前只有"统一 UA + 条件请求"两件礼貌措施，且多源**串行**抓取：
    源变多后一轮耗时线性增长；若直接改成并发，又会把压力转嫁给对方站点。
    同时源挂掉或长期没抓成功，只能靠人去翻列表——健康字段落了库却没人被通知。

设计：
    - `respect_robots`：逐源开关。默认遵守 robots.txt；个别源的 robots 写得过严
      （整站 Disallow）却实际提供公开 RSS，一刀切会让这些源失去作用，故留开关。
    - `last_alerted_at`：告警节流的时间锚点。**落库而非进程内变量**——
      多实例部署时各实例共享同一节流窗口（定时抓取有 advisory lock，但手动
      触发不走锁），进程重启也不会重新刷屏。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_fetch_politeness"
down_revision: Union[str, None] = "0016_admin_audit_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 与模型 default=True 对齐：存量行也要是「遵守」而非 NULL，
    # 否则 `respect_robots IS NULL` 的旧行在判定里会掉进"不遵守"分支。
    op.add_column(
        "rss_sources",
        sa.Column(
            "respect_robots", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column("rss_sources", sa.Column("last_alerted_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("rss_sources", "last_alerted_at")
    op.drop_column("rss_sources", "respect_robots")
