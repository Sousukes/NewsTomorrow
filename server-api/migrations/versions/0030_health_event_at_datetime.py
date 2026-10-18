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
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.migration_utils import convert_column_type

revision: str = "0030_health_event_at_datetime"
down_revision: Union[str, None] = "0029_time_empty_to_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "rss_source_health_events"
TMP = "rss_source_health_events_d1tmp"


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

    # SQLite：走公共 helper（反射 → 建新表 → Python 逐行转换 → 换名 → 按**反射到的
    # 真名**重建索引）。helper 在 `app/migration_utils.py`，带 12 项通用测试——
    # 不在这里重写一遍，是因为重建类迁移的坑不在算法而在"你记得搬哪些"，
    # 复制粘贴的版本下一个迁移就会忘了搬外键。
    res = convert_column_type(bind, TABLE, "at", sa.DateTime(timezone=True))
    print(
        f"[0030] at 列已转 DateTime：转换 {res['converted']} 行"
        + (f"，{res['skipped']} 行不可解析（写 NULL）" if res["skipped"] else "")
    )


def downgrade() -> None:
    """空操作（见文件头「不可逆说明」）。"""
    pass