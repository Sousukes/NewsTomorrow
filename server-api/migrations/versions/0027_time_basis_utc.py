"""A3：时间基准统一——存量时间列回填为 UTC aware

Revision ID: 0027_time_basis_utc
Revises: 0026_message_created_at
Create Date: 2026-10-18

背景：
    统一前全库混着**三种**时间口径：
    - `now_str()` 写本地 naive（"2026-10-18T14:30:00"）——绝大多数列；
    - `ranking._iso_seconds` 写 naive-UTC（"2026-10-18T06:30:00"）——`news.hot_score_at`；
    - 抓取/种子写 aware-UTC（"2026-10-18T06:30:00+00:00"）——`news.published_at` 等。

    「同格式 ISO 的字典序即时间序」这条契约因此不成立：字符串排序与真实
    时间序脱节，读侧只能靠 `_to_naive_utc` 打补丁，而补丁把 naive 值
    （实为本地时间）按 UTC 解释，衰减计算有整小时 skew。
    代码侧已统一为 aware UTC（`now_str()` / `parse_dt()`），本迁移负责存量。

回填口径（**只补偏移、不改数字**）：
    - naive → 原样补 `+00:00`（"14:30:00" → "14:30:00+00:00"）；
    - aware → 归一到 UTC（当前只有 `+00:00`，转换是恒等）；
    - 不可解析的脏值 → 保持原样并计入日志（不编造）。

    为什么不按「本地时间 +8h」移位：存量 naive 值的来源不可恢复——
    `hot_score_at` 的存量本就是 naive-UTC 数字（移位会**错**），
    其余列是写入机器的本地时间（该机器时区无法从数据反推）。
    补偏移保住了唯一能验证的性质：**相对顺序精确保留**（这正是
    「字符串排序即时间序」契约需要的），而绝对值最大偏差一个有界值，
    对 48h 半衰期的衰减影响 ≤ 偏移/半衰期，随时间自愈。

不可逆说明：
    downgrade 是**空操作**。回填只改格式，无法区分某行原先是 naive 还是
    本就 aware——强行回退会把真 UTC 值改成假 naive。数据类迁移的
    可逆性在这里不存在，与其造一个假的，不如不造。
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0027_time_basis_utc"
down_revision: Union[str, None] = "0026_message_created_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

# 白名单而非「所有 String 列」：`news.time` / `messages.time` / `comments.time`
# 存的是「1小时前」这类相对展示文案，不是时间戳——误转会破坏展示。
# 键 = 表名，值 = (主键列, 时间列)。主键用于逐行 UPDATE（复合主键一并列出）。
_TABLES: dict[str, tuple[list[str], list[str]]] = {
    "users": (["id"], ["created_at", "name_reviewed_at"]),
    "news": (["id"], ["published_at", "hidden_at", "hot_score_at"]),
    "comments": (["id"], ["created_at", "deleted_at", "hidden_at", "reviewed_at"]),
    "comment_likes": (["comment_id", "user_id"], ["created_at"]),
    "messages": (["id"], ["created_at"]),
    "message_reads": (["message_id", "user_id"], ["created_at"]),
    "user_favorites": (["user_id", "news_id"], ["created_at"]),
    "user_history": (["user_id", "news_id"], ["viewed_at"]),
    "search_logs": (["id"], ["created_at"]),
    "rss_sources": (
        ["key"],
        ["created_at", "updated_at", "last_fetched_at", "last_alerted_at", "next_retry_at"],
    ),
    "rss_source_health_events": (["id"], ["at"]),
    "admin_audit_logs": (["id"], ["created_at"]),
    "admin_users": (["phone"], ["created_at"]),
    "sensitive_words": (["id"], ["created_at", "updated_at"]),
    "sensitive_hits": (["id"], ["created_at"]),
    "nickname_words": (["id"], ["created_at", "updated_at"]),
    "nickname_hits": (["id"], ["created_at"]),
}


def _normalize(value: str) -> str | None:
    """naive 补偏移 / aware 归一到 UTC；不可解析返回 None（调用方保持原值）。"""
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # 只补偏移、不改数字——顺序精确保留（见文件头「回填口径」）
        return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def upgrade() -> None:
    conn = op.get_bind()
    total = 0
    skipped = 0
    for table, (pks, cols) in _TABLES.items():
        where = " AND ".join(f"{pk} = :{pk}" for pk in pks)
        select_sql = text(f"SELECT {', '.join(pks + cols)} FROM {table}")
        for col in cols:
            update_sql = text(f"UPDATE {table} SET {col} = :value WHERE {where}")
            rows = conn.execute(select_sql).fetchall()
            for row in rows:
                # 行内取值：pks 在前、cols 依序在后
                old = row[len(pks) + cols.index(col)]
                if not old:
                    continue
                new = _normalize(old)
                if new is None:
                    skipped += 1
                    continue
                if new == old:
                    continue
                params = {"value": new}
                params.update({pk: row[i] for i, pk in enumerate(pks)})
                conn.execute(update_sql, params)
                total += 1
    if total or skipped:
        print(f"[0027] 时间列回填：{total} 行归一为 UTC aware，{skipped} 行脏值跳过")


def downgrade() -> None:
    """空操作：回填不可逆（无法区分原值是 naive 还是本就 aware），见文件头。"""
