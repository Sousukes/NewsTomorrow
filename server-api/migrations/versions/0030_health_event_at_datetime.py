"""D1-1（第一步）：`rss_source_health_events.at` 改成 `DateTime`（迁移 0030）

Revision ID: 0030_health_event_at_datetime
Revises: 0029_time_empty_to_null
Create Date: 2026-10-18

为什么先迁这一列：
    它是**唯一**参与 SQL 侧范围过滤的时间列（窗口查询 `at >= cutoff`）。
    一张表跑通就能把 D1-1 的全部经验试完（方言差异、输出边界、参数绑定），
    再决定其余列怎么推。

前置条件（P1-51 / P1-52 已完成）：
    - 0029 已把 `''` 归一为 NULL —— 否则整表重建会直接失败（已实测）；
    - `tests/test_time_contract.py` 钉住写入口径与"字典序 == 时间序"。

## SQLite 路径为什么不能直接用 batch_alter_table（踩过的坑）

第一版用了 Alembic 常见的 `batch_alter_table(...).alter_column(...)`，在 SQLite
上它会把新列用 `CAST(旧列 AS 新类型)` 拷贝，于是：

    SELECT CAST('2026-10-18T06:00:00+00:00' AS DATETIME)  -->  2026

SQLite 把**以数字开头的字符串当数字**处理，于是整列的时间被截成年份。
**这会静默毁数据**：迁移不报错、行数不变、排序看起来还正常（两行都变成
2026 之后相对次序恰好没变）。

所以 SQLite 路径改为**显式重建 + 逐行 Python 转换**：
反射旧表结构 → 建新表（`at` 换成 `DateTime`）→ Python 读旧值、`parse_dt`
解析后写入 → 删旧表改名。宁可慢，也不能让数据库猜时间怎么转。

## PG 路径

`ALTER TABLE ... TYPE timestamptz USING at::timestamptz`：PG 能正确解析 ISO
串且保留 `+00:00` 偏移，不会发生上面那种截断。

不可逆说明：
    downgrade 为**空操作**。转成 timestamptz 后无法区分某行原先是 naive 还是
    aware（0027 已统一），强行回退等于把真 UTC 值改写成假 naive。
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_health_event_at_datetime"
down_revision: Union[str, None] = "0029_time_empty_to_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "rss_source_health_events"
TMP = "rss_source_health_events_d1tmp"


def _parse(value):
    """ISO → aware datetime；不可解析或为空返回 None（不编造）。

    naive 一律**按 UTC 解释**——与 0027 回填、`models.parse_dt` 同一口径。
    """
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                f"ALTER TABLE {TABLE} ALTER COLUMN at TYPE timestamptz "  # noqa: S608
                f"USING at::timestamptz"
            )
        )
        return

    # ---- SQLite：显式重建（**不能用 CAST**，见文件头）----
    old = sa.Table(TABLE, sa.MetaData(), autoload_with=bind)
    columns = [c.name for c in old.columns]

    new_columns = []
    for col in old.columns:
        # **外键必须一起搬**：只搬列定义的话，重建出来的表会悄悄丢掉
        # `ON DELETE CASCADE`——源被删时健康历史不会跟着清理，而这种缺失
        # 没有任何症状（数据只会越积越多）。复合外键不搬（当前表没有）。
        # 用**位置参数**传 ForeignKey：写成 `foreign_keys=` 会被 SQLAlchemy
        # 当成方言专用参数（'foreign_keys' 被拆成方言名 'foreign'）。
        args = []
        if len(col.foreign_keys) == 1:
            fk = next(iter(col.foreign_keys))
            # `ondelete` 也要搬：不搬的话重建出来的外键变成 NO ACTION，
            # 源被删时健康历史**不会跟着清理**，而这种缺失没有任何症状
            #（数据只会越积越多，直到有人翻代码才发现）。
            args = [
                sa.ForeignKey(
                    fk.target_fullname,
                    ondelete=fk.ondelete,
                    onupdate=fk.onupdate,
                    initially=fk.initially,
                    deferrable=fk.deferrable,
                    use_alter=fk.use_alter,
                    name=fk.name,
                )
            ]
        new_columns.append(
            sa.Column(
                col.name,
                # 只换 `at` 的类型，其余原样搬
                sa.DateTime(timezone=True) if col.name == "at" else col.type,
                *args,
                nullable=col.nullable,
                primary_key=col.primary_key,
            )
        )
    # 挂在**反射表的同一个 MetaData** 上：外键目标 `rss_sources` 必须在同一个
    # MetaData 里才能解析得到（否则 NoReferencedTableError）。
    # 用 `create()` 而不是 `metadata.create_all()`——后者会连带重建旧表。
    new = sa.Table(TMP, old.metadata, *new_columns)
    new.create(bind)

    converted = 0
    skipped = 0
    # **不要**用 `with bind.begin()`：alembic 在迁移上下文里已经开启了事务，
    # 再 begin 一次会报 `This connection has already initialized a SQLAlchemy
    # Transaction()` —— 迁移直接中止，数据没转换却"看起来跑过了"。
    rows = bind.execute(sa.select(old)).fetchall()
    for row in rows:
        data = {name: getattr(row, name) for name in columns}
        data["at"] = _parse(data.get("at"))
        if data["at"] is None:
            skipped += 1
        else:
            converted += 1
        bind.execute(new.insert().values(**data))

    bind.execute(sa.text(f"DROP TABLE {TABLE}"))  # noqa: S608
    bind.execute(sa.text(f"ALTER TABLE {TMP} RENAME TO {TABLE}"))  # noqa: S608
    # 索引随旧表一并消失，必须按**模型里的原定义**补回——
    # 自造一个名字相近的索引会让 `test_migrations` 断言"索引名与模型一致"时
    # 报错，而真正的问题是"索引没了"被掩盖成"名字不对"。
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_rss_health_key_at ON {TABLE} (source_key, at)"  # noqa: S608
        )
    )

    print(
        f"[0030] at 列已转 DateTime：转换 {converted} 行"
        + (f"，{skipped} 行不可解析（写 NULL）" if skipped else "")
    )


def downgrade() -> None:
    """空操作（见文件头「不可逆说明」）。"""
    pass