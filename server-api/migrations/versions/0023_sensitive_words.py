"""P1-23：评论敏感词审核（sensitive_words / sensitive_hits + comments 待复核列）

Revision ID: 0023_sensitive_words
Revises: 0022_comment_soft_hide
Create Date: 2026-10-16

背景：
    评论治理此前只有「事后处置」——P1-22 给了运营软隐藏的能力，但违规内容
    **必须先被人看见**才会被处置：垃圾广告发出去那一刻就在讨论区挂着，等运营
    巡到可能已经是几小时后。评论是 UGC 里风险最高的一类：可匿名、可秒发、
    可被截图传播。需要一道**事前**闸门。

设计：
    - **词库表 `sensitive_words`**：落地在库里而非配置文件——运营要能在后台增删
      （配置文件意味着每次改词都要发版）。三种动作：
        * `block`：直接拒绝提交（高危，如引流联系方式、违禁品）；
        * `mask`：脱敏后照常发布并置 `pending_review`，交人工复核（默认，见下）；
        * `allow`：**白名单词**——命中它的区间不再按敏感处理，专治误杀
          （如单字「射」会命中「射击」）。
      `enabled` 允许停用而非删除：删错了词要能一秒回滚。
      `hit_count` 累计命中次数：高频命中的词要么是热词、要么是误杀大户，
      两者都需要运营看见。
    - **命中日志 `sensitive_hits`**：每一次命中一行（含**原文片段**）。
      没有它就无法回答「这个月拦了多少条」「哪个词在误杀」——脱敏后的正文
      是不留原文的，误杀要靠日志才能复盘与申诉。
      记**词本身**而非 word_id：词被删后日志仍要读得懂。
    - **`comments.pending_review`**：脱敏评论不是"通过了审核"，而是"待人工看一眼"。
      与 `hidden` 正交：hidden 是「已处置、不可见」，pending 是「未处置、可见」。
      **不建索引**（与 hidden / is_deleted 同处方）：待复核集预期很小，
      索引只会拖慢每次评论写入。
    - `reviewed_at` 记复核时间：复核动作同样要留痕（审计 `comment.review`）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_sensitive_words"
down_revision: Union[str, None] = "0022_comment_soft_hide"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "sensitive_words",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        # block = 直接拦截；mask = 脱敏 + 待复核；allow = 白名单（豁免，专治误杀）
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=True),
        # 热更新指纹：`MAX(updated_at)` + COUNT(*)。词库变动靠它感知，
        # 比整表拉取便宜（一次聚合 vs 全表传输 + 重建自动机）。
        sa.Column("updated_at", sa.String(length=30), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # 唯一**索引**而非唯一约束：模型侧 `Column(unique=True, index=True)` 生成的
    # 正是唯一索引（SQLAlchemy 不会再造一个独立 UniqueConstraint）。
    # 写成 UniqueConstraint + 普通索引会让 `alembic check` 永远报漂移。
    op.create_index("ix_sensitive_words_word", "sensitive_words", ["word"], unique=True)
    op.create_index("ix_sensitive_words_updated_at", "sensitive_words", ["updated_at"])

    op.create_table(
        "sensitive_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        # 检测字段：content（正文）/ name（昵称）
        sa.Column("field", sa.String(length=10), nullable=False),
        sa.Column("comment_id", sa.String(length=64), nullable=True),
        sa.Column("news_id", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(length=50), nullable=False),
        # **原文**片段：脱敏后正文已不可复原，误杀复盘/申诉全靠它
        sa.Column("snippet", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensitive_hits_word_id", "sensitive_hits", ["word", "id"])
    op.create_index("ix_sensitive_hits_created_at", "sensitive_hits", ["created_at"])

    # server_default=sa.false() 与模型对齐：存量评论必须是「无需复核」，
    # NULL 会让 `pending_review IS NOT TRUE` 之类的过滤把老评论整体筛掉。
    op.add_column(
        "comments",
        sa.Column("pending_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("comments", sa.Column("reviewed_at", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "reviewed_at")
    op.drop_column("comments", "pending_review")
    op.drop_index("ix_sensitive_hits_created_at", table_name="sensitive_hits")
    op.drop_index("ix_sensitive_hits_word_id", table_name="sensitive_hits")
    op.drop_table("sensitive_hits")
    op.drop_index("ix_sensitive_words_updated_at", table_name="sensitive_words")
    op.drop_index("ix_sensitive_words_word", table_name="sensitive_words")
    op.drop_table("sensitive_words")
