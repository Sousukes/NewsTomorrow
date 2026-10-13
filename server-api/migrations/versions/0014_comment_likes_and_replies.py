"""P1-12：二级评论（parent_id/root_id）+ 点赞关系表化（comment_likes）

Revision ID: 0014_comment_likes_and_replies
Revises: 0013_rss_sources
Create Date: 2026-10-14

背景：
    1. 评论此前只有一级——「回复他人的评论」无处安放，互动讨论深度受限。
    2. `comments.liked_by` 是 JSON 数组：无法建索引，点赞判定要整行读出解析，
       并发点赞还会写丢失（读-改-写整个数组）。这与 `messages.read_by` 当年
       的问题完全同构（见 0004），按同一处方处理：搬进关联表。

设计：
    1. `comment_likes`（comment × user）复合主键替代 `liked_by`：
       - 复合主键天然保证「同一用户对同一评论只点一次」（幂等，INSERT 冲突即重复点赞）；
       - 判定当前用户是否点赞过 = 一次 O(log n) 主键查询，无需读出整行 JSON。
    2. 两级评论结构（自引用）：
       - `parent_id`：被回复的评论（展示 @对象 用）；
       - `root_id`：所属主评论。**回复的回复拍平到同一 root**——列表页只展示
         主评论 + 回复数，点开某条主评论才加载其下全部回复；
       - 主评论 `root_id IS NULL`，回复 `root_id = 所属主评论 id`。
       - 自引用外键 **仅 PostgreSQL 创建**（与 0003/0007/0010 同一策略：
         SQLite 不支持 ALTER ADD CONSTRAINT 且默认不强制外键）。
       - ON DELETE CASCADE：评论若被物理删除，其回复/点赞随之清理，
         不会留下 root_id 悬空导致回复「升级」成主评论。
    3. 回填（Python 端做，零 SQL 方言差异，与 0004 同款）：
       - `liked_by` 逐条解析 → `comment_likes` 行；**去重**、**跳过非整数**、
         **跳过悬空 user_id**（PG 外键强制生效，悬空 id 会让整条迁移失败）；
       - 回填后 `likes = COUNT(comment_likes)` 全量刷新——冗余计数与事实对齐。
    4. `liked_by` 回填完成后删除（SQLite 走 batch 重建表，与 0004 同款）。
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_comment_likes_and_replies"
down_revision: Union[str, None] = "0013_rss_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 历史点赞无法还原真实时间，统一回填为基准值（与 0004 read_by 回填同一策略）
_LIKE_EPOCH = "1970-01-01T00:00:00+00:00"

# 自引用外键：SQLite 不创建（见文件头说明），仅 PG 补建
_PG_SELF_FKS = (
    ("fk_comments_parent_id_comments", "parent_id"),
    ("fk_comments_root_id_comments", "root_id"),
)


def _parse_liked_by(raw) -> list:
    """防御性解析 liked_by：脏数据（非 JSON 文本 / 非数组）一律按空处理。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            user_ids = json.loads(raw) if raw.strip() else []
        except (ValueError, TypeError):
            return []
    elif isinstance(raw, (list, tuple)):
        user_ids = list(raw)
    else:
        return []
    return user_ids if isinstance(user_ids, list) else []


def upgrade() -> None:
    # ---- 1. 两级评论结构 ----
    op.add_column("comments", sa.Column("parent_id", sa.String(length=64), nullable=True))
    op.add_column("comments", sa.Column("root_id", sa.String(length=64), nullable=True))
    # 回复列表按 root_id 查询；索引名与模型 index=True 的自动命名对齐
    op.create_index(op.f("ix_comments_root_id"), "comments", ["root_id"], unique=False)

    # ---- 2. 点赞关系表（外键内联声明，两种方言均可；与 0004 同款） ----
    op.create_table(
        "comment_likes",
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comment_id", "user_id"),
    )

    # ---- 3. 回填 liked_by → comment_likes ----
    bind = op.get_bind()
    # PG 外键强制生效：悬空 user_id 会让 INSERT 失败、整条迁移回滚，先取合法集合
    valid_users = {row[0] for row in bind.execute(sa.text("SELECT id FROM users"))}
    rows = bind.execute(sa.text("SELECT id, liked_by FROM comments")).fetchall()
    for comment_id, raw in rows:
        seen: set = set()
        for uid in _parse_liked_by(raw):
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue  # 非整数 id：脏数据，跳过
            if uid in seen or uid not in valid_users:
                continue  # 数组内重复 / 悬空 user_id
            seen.add(uid)
            bind.execute(
                sa.text(
                    "INSERT INTO comment_likes (comment_id, user_id, created_at) "
                    "VALUES (:c, :u, :t)"
                ),
                {"c": comment_id, "u": uid, "t": _LIKE_EPOCH},
            )

    # ---- 4. likes 冗余计数与关系表对齐（历史数据可能已漂移） ----
    op.execute(
        "UPDATE comments SET likes = "
        "(SELECT COUNT(*) FROM comment_likes WHERE comment_likes.comment_id = comments.id)"
    )

    # ---- 5. 删除 liked_by（SQLite 走 batch 重建表，与 0004 同款） ----
    with op.batch_alter_table("comments") as batch:
        batch.drop_column("liked_by")

    # ---- 6. 补建自引用外键（仅 PG） ----
    if bind.dialect.name == "postgresql":
        for name, column in _PG_SELF_FKS:
            op.create_foreign_key(
                name, "comments", "comments", [column], ["id"], ondelete="CASCADE"
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. 还原 liked_by 列，并从 comment_likes 反向回填（保住点赞数据，可逆迁移）
    with op.batch_alter_table("comments") as batch:
        batch.add_column(sa.Column("liked_by", sa.JSON(), nullable=True))

    by_comment: dict[str, list] = {}
    for comment_id, user_id in bind.execute(
        sa.text("SELECT comment_id, user_id FROM comment_likes")
    ).fetchall():
        by_comment.setdefault(comment_id, []).append(user_id)
    for comment_id, user_ids in by_comment.items():
        payload = json.dumps(user_ids)
        if is_pg:
            # liked_by 在 PG 是 JSONB：文本参数必须显式 CAST，否则类型不匹配
            bind.execute(
                sa.text("UPDATE comments SET liked_by = CAST(:p AS JSONB) WHERE id = :c"),
                {"p": payload, "c": comment_id},
            )
        else:
            bind.execute(
                sa.text("UPDATE comments SET liked_by = :p WHERE id = :c"),
                {"p": payload, "c": comment_id},
            )

    # 2. 拆除两级评论结构与关系表
    if is_pg:
        for name, _column in reversed(_PG_SELF_FKS):
            op.drop_constraint(name, "comments", type_="foreignkey")
    op.drop_index(op.f("ix_comments_root_id"), table_name="comments")
    op.drop_table("comment_likes")
    with op.batch_alter_table("comments") as batch:
        batch.drop_column("root_id")
        batch.drop_column("parent_id")
