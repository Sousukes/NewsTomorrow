"""P1-24：用户昵称敏感词校验（nickname_words / nickname_hits + users 待复核列）

Revision ID: 0024_nickname_moderation
Revises: 0023_sensitive_words
Create Date: 2026-10-17

背景：
    昵称是评论之外第二个用户可控的展示面。P1-23 给评论加了事前闸门，但昵称
    **此前的唯一写入点是注册时的一句话**（`auth.py`，`body.name or 默认名`），
    没有任何修改入口、也没有任何校验——一个脏昵称会跟着用户的每一条评论、
    每一个通知展示出去，等于让绕过者换个地方贴。

    与 P1-23 的关键差异（为什么另起一套而不复用 sensitive_words）：
    - **动作集不同**：评论有 mask（脱敏后照常发布），昵称没有这个语义——
      昵称是账号属性，"脱敏"意味着改掉用户自己起的名字，而他此刻改不了。
      昵称的动作是 block（拒绝设置）与 review（放行 + 待人工复核）。
    - **别名只对昵称有意义**：拼音全拼/首字母/谐音（"shabi"、"sb"）是昵称
      绕过的主力，评论正文走自然语言不需要它。
    所以新表 + 新引擎（复用 moderation.AhoCorasick 自动机与指纹缓存模式）。

设计：
    - **词库表 `nickname_words`**：与 sensitive_words 同构（enabled 停用而非删除、
      hit_count 累计、updated_at 参与热更新指纹），多一列 `aliases`（JSON 数组）
      存拼音/谐音/变形，与主词走同一条归一化管道。
    - **命中日志 `nickname_hits`**：一次命中一行，`matched` 记实际命中的模式
      （主词或别名）、`source` 区分注册/改名。误杀复盘与申诉靠它。
    - **`users.name_pending_review`**：命中 review 词时放行但置位，进人工复核
      队列。**不建索引**（与 comments.pending_review / hidden 同处方：待复核集
      预期很小，索引只会拖慢每次昵称写入）。复核通过只清标志、不改名字。
    - `name_reviewed_at` 记复核时间：复核动作同样要留痕（审计 `nickname.review`）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024_nickname_moderation"
down_revision: Union[str, None] = "0023_sensitive_words"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

# SQLite 用 JSON，PostgreSQL 用 JSONB（与 app.models.JSONType 保持一致）
JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "nickname_words",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        # block = 拒绝设置；review = 放行 + 待人工复核（默认）；allow = 白名单豁免
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        # 别名（拼音全拼/首字母/谐音/变形），与主词走同一条归一化管道
        sa.Column("aliases", JSONType, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=True),
        # 热更新指纹：MAX(updated_at) + COUNT(*)
        sa.Column("updated_at", sa.String(length=30), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # 唯一**索引**而非唯一约束：与迁移 0023 / 0015 同一套判定（模型侧
    # `Column(unique=True, index=True)` 生成的正是唯一索引，写成
    # UniqueConstraint + 普通索引会让 `alembic check` 永远报漂移）。
    op.create_index("ix_nickname_words_word", "nickname_words", ["word"], unique=True)
    op.create_index("ix_nickname_words_updated_at", "nickname_words", ["updated_at"])

    op.create_table(
        "nickname_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        # 实际命中的模式（主词本身或某条别名）：回答"是哪条别名拦住的"
        sa.Column("matched", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        # 触发场景：register（注册）/ update（改昵称）
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(length=50), nullable=False),
        # 提交的昵称原文（≤24 字符，整体即上下文）
        sa.Column("snippet", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nickname_hits_word_id", "nickname_hits", ["word", "id"])
    op.create_index("ix_nickname_hits_created_at", "nickname_hits", ["created_at"])

    # server_default=sa.false() 与模型对齐：存量用户必须是"无需复核"，
    # NULL 会让 `name_pending_review IS NOT TRUE` 之类的过滤把老用户整体筛掉。
    # **不建索引**：待复核集预期很小，加了只会拖慢每次昵称写入。
    op.add_column(
        "users",
        sa.Column("name_pending_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("name_reviewed_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "name_reviewed_at")
    op.drop_column("users", "name_pending_review")
    op.drop_index("ix_nickname_hits_created_at", table_name="nickname_hits")
    op.drop_index("ix_nickname_hits_word_id", table_name="nickname_hits")
    op.drop_table("nickname_hits")
    op.drop_index("ix_nickname_words_updated_at", table_name="nickname_words")
    op.drop_index("ix_nickname_words_word", table_name="nickname_words")
    op.drop_table("nickname_words")
