"""P1-22：评论软隐藏（comments.hidden / hidden_at / hidden_reason）

Revision ID: 0022_comment_soft_hide
Revises: 0021_hot_score_at
Create Date: 2026-10-15

背景：
    评论治理此前只有作者自主软删除（is_deleted）一个手段——遇到垃圾广告、
    引战、侵权内容时运营**没有任何**处置能力：不能删（不是自己的评论），
    也不能改（用户下次编辑又写回）。P1-20 给资讯开了软下架的口子，评论是
    同一种需求，抄同一张处方。

设计（对齐 0020 资讯软下架）：
    - **软隐藏三列**：`hidden` 非空默认 false（存量行必须「可见」而非 NULL，
      否则过滤条件会把老评论整体漏掉）；`hidden_at` 记时间；
      `hidden_reason` 必填（trim 后 ≤300，「留痕是准入条件」）。
    - **不建索引**：隐藏集预期很小，与 is_deleted 同理——加了只会拖慢
      每轮评论写入。
    - 恢复（restore）清空 reason 与 at：理由只对隐藏状态有意义。
    - 与 is_deleted 的分工：is_deleted 是**作者**行为（删自己的，不可逆）；
      hidden 是**运营**行为（治理别人的，可恢复、有留痕）。两者都让评论从
      公开查询消失，但 hidden 不抹除互动记录（点赞/回复结构保留）。
    - 计数联动：隐藏/恢复同步维护 news.comment_count 与热度分——公开界面
      看到的评论数必须与真实可见条数一致（P1-12 修过的旧 bug 不能以另
      一种形式回来）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_comment_soft_hide"
down_revision: Union[str, None] = "0021_hot_score_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default=sa.false() 与模型 default=False 对齐：存量行也必须是「可见」，
    # NULL 会在公开查询的过滤条件里掉出结果集——「老评论突然消失」且极难定位。
    op.add_column(
        "comments",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("comments", sa.Column("hidden_at", sa.String(length=30), nullable=True))
    op.add_column("comments", sa.Column("hidden_reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "hidden_reason")
    op.drop_column("comments", "hidden_at")
    op.drop_column("comments", "hidden")
