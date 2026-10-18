"""D1-0：时间列的"空串哨兵"归一为 NULL（**只清洗，不改类型**）

Revision ID: 0029_time_empty_to_null
Revises: 0028_user_must_change_password
Create Date: 2026-10-18

背景：
    时间列里混着两种"没有时间"的表达：`NULL`（正确）与 `''`（空串哨兵）。
    `''` 出现在三个地方，性质完全不同：

    - **历史哨兵**：早期代码用 `''` 表示"没有"，读侧靠 `!= ""` 排除；
    - **模型默认值**：`News.time` 等展示列 `default=""`（**那些不是时间列**，
      本迁移不碰——见下方白名单说明）；
    - **误入的展示文案**：理论上可能（「刚刚」），实测写入路径从不产生
      （`published_at` 只写 `None` 或 `.isoformat()`）。

    为什么现在要清：`''` 与 `NULL` 语义重复，且**空串会让后续的列类型迁移
    直接失败**——`batch_alter_table` 整表重建时按新类型解析每行，遇到 `''`
    抛错（已由 `tests/test_time_migration_spike.py` 实测确认）。
    也就是说：**不先清洗，D1-1 根本跑不起来**。

行为等价性（已核对所有读点，**本迁移不改任何查询代码**）：
    - 抓取保留期清理：`or_(published_at.is_(None), published_at == "")` 排除两者；
    - 排序：`coalesce(published_at, '')` 遇 NULL 仍是 `''`，沉底行为不变；
    - 热度衰减：`ranking._parse_dt(None)` 返回 None，走"未知"分支。
    读侧早已同时兼容两种表达，因此清洗前后**对外行为完全一致**。
    代码里的 `== ""` 条件**故意保留**：它是防御性检查（外部导入的数据可能
    再带空串），删掉会让未清洗的库把 `''` 当成"有发布时间"而排到前面。

不可解析的脏值（如「刚刚」）：
    **不清洗、不编造**，只计数并打印。与 0027 同一立场：留一条可查的脏数据，
    比静默改成 NULL 更能说明问题。

不可逆说明：
    `''` → NULL 在语义上是**无损**的（两者都表示"没有时间"），但技术上无法
    区分某行原先是 `''` 还是 NULL。downgrade 为**空操作**：强行回退会把
    真正的 NULL 变成 `''`，那是把"正确"改成"错误"。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0029_time_empty_to_null"
down_revision: Union[str, None] = "0028_user_must_change_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0027 同一份白名单：只含**绝对时间戳**列。
# 刻意排除 `news.time` / `comments.time` / `messages.time` —— 它们存的是
# 「1小时前」这类相对展示文案，`''` 在那里是**合法的默认值**，
# 把它们归一成 NULL 会让展示层多出一堆"空时间"。
_TABLES: dict[str, list[str]] = {
    "users": ["created_at", "name_reviewed_at"],
    "news": ["published_at", "hidden_at", "hot_score_at"],
    "comments": ["created_at", "deleted_at", "hidden_at", "reviewed_at"],
    "comment_likes": ["created_at"],
    "messages": ["created_at"],
    "message_reads": ["created_at"],
    "user_favorites": ["created_at"],
    "user_history": ["viewed_at"],
    "search_logs": ["created_at"],
    "rss_sources": [
        "created_at",
        "updated_at",
        "last_fetched_at",
        "last_alerted_at",
        "next_retry_at",
    ],
    "rss_source_health_events": ["at"],
    "admin_audit_logs": ["created_at"],
    "admin_users": ["created_at"],
    "sensitive_words": ["created_at", "updated_at"],
    "sensitive_hits": ["created_at"],
    "nickname_words": ["created_at", "updated_at"],
    "nickname_hits": ["created_at"],
}


def upgrade() -> None:
    conn = op.get_bind()
    cleaned = 0
    dirty = 0
    for table, cols in _TABLES.items():
        for col in cols:
            res = conn.execute(
                text(f"UPDATE {table} SET {col} = NULL WHERE {col} = :empty"),  # noqa: S608
                {"empty": ""},
            )
            cleaned += res.rowcount or 0
    # 不可解析的非空脏值：只报告、不改。留着比静默清掉更诚实——
    # 它说明有写入路径绕过了统一口径，需要人去查。
    for table, cols in _TABLES.items():
        for col in cols:
            dirty += conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} "  # noqa: S608
                    f"WHERE {col} IS NOT NULL AND {col} <> '' "
                    f"AND {col} NOT LIKE '____-__-__T__:__:__%'"
                )
            ).scalar() or 0
    print(f"[0029] 时间列空串归一为 NULL：{cleaned} 行")
    if dirty:
        print(
            f"[0029] 警告：另有 {dirty} 行的值不像 ISO 时间（保留原样，未编造）。"
            "请人工检查写入路径。"
        )


def downgrade() -> None:
    """空操作（见文件头「不可逆说明」）。"""
    pass
