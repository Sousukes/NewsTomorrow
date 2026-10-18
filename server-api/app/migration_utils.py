"""迁移辅助：把某一列换成新类型，**不靠 CAST**。

为什么需要它（P1-53 的翻车）：
    Alembic 常见的 `batch_alter_table(...).alter_column(...)` 在 SQLite 上用
    `CAST(旧列 AS 新类型)` 拷贝，而 SQLite 把**以数字开头的字符串当数字**：

        SELECT CAST('2026-10-18T06:00:00+00:00' AS DATETIME)  -->  2026

    整列时间被截成年份，**且不报错、行数不变、排序看着还正常**。
    本模块改为：反射 → 建新表 → **Python 逐行转换** → 换名。

为什么不能只写一次迁移就完事：
    重建类迁移的坑不在算法，而在**"你记得搬哪些"**。P1-53 连踩三次：
    索引丢了（自造了一个名字相近但语义不同的）、外键丢了、
    `ON DELETE CASCADE` 退化成 `NO ACTION`。所以搬的东西**一律反射拿**，
    不靠人记——尤其索引，直接用 `get_indexes()` 读出真名与真列。

跨方言：
    - PG 用原生 `ALTER TABLE ... TYPE ... USING col::type`（能正确解析 ISO
      并保留偏移，不走本模块）；
    - SQLite 走本模块的重建路径。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import sqlalchemy as sa

TMP_SUFFIX = "__conv_tmp"


def parse_utc(value: Any) -> datetime | None:
    """ISO 串 → aware datetime（UTC）。

    - 空值（`''` / `None`）→ `None`，**不编造**；
    - naive 一律**按 UTC 解释**——与 `models.parse_dt`、迁移 0027 回填同一口径；
    - 不可解析 → `None`，调用方负责计数上报。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def convert_column_type(
    bind,
    table_name: str,
    column_name: str,
    new_type,
    *,
    parse: Callable[[Any], datetime | None] = parse_utc,
    tmp_name: str | None = None,
) -> dict:
    """在 SQLite 上把 `table_name.column_name` 换成 `new_type`（逐行转换）。

    PG 请用原生 ALTER（见模块文档）；本函数在 PG 上也能跑，但会退化成
    「建新表 + 搬数据」，大表上不划算，且会短暂占用更多空间。

    返回 `{"rows": 总行数, "converted": 转换成功数, "skipped": 不可解析数}`，
    迁移脚本负责打印——**"清了多少、丢了多少"必须在部署日志里可见**。
    """
    tmp = tmp_name or f"{table_name}{TMP_SUFFIX}"

    # 反射：旧表的列、外键、索引全都要搬
    old = sa.Table(table_name, sa.MetaData(), autoload_with=bind)
    inspector = sa.inspect(bind)
    old_indexes = inspector.get_indexes(table_name) or []

    columns = []
    for col in old.columns:
        args = []
        if len(col.foreign_keys) == 1:
            # 位置参数传 ForeignKey：写成 `foreign_keys=` 会被 SQLAlchemy
            # 当成方言专用参数（被拆成方言名 `foreign`）。ondelete 也要搬，
            # 否则 CASCADE 退化成 NO ACTION，源删后子行不清理且毫无症状。
            fk = next(iter(col.foreign_keys))
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
        columns.append(
            sa.Column(
                col.name,
                new_type if col.name == column_name else col.type,
                *args,
                nullable=col.nullable,
                primary_key=col.primary_key,
            )
        )

    # 挂在**反射表的同一个 MetaData** 上：外键目标必须在同一 MetaData 才能解析
    new = sa.Table(tmp, old.metadata, *columns)
    # **先扫描、后动手**：NOT NULL 列上一旦有不可解析的值，写 NULL 会在插入
    # 阶段抛 IntegrityError——错误信息里只有"NOT NULL 约束"，看不出根因是
    # "值不是时间"，而且已经建了临时表。早一个版本边插边判，脏数据撞约束的
    # 报错先于我们自己的提示出现。现在改成：脏行先收集完，确认能过再开始建表。
    rows = bind.execute(sa.select(old)).fetchall()
    column_nullable = next((c.nullable for c in old.columns if c.name == column_name), True)
    parsed_rows = []
    dirty = []
    for row in rows:
        data = {name: getattr(row, name) for name in old.columns.keys()}
        if column_name in data:
            original = data[column_name]
            parsed = parse(original)
            # 注意先取 `original` 再覆盖：早一个版本先覆盖再判断
            # `data[column_name] is not None`，于是"不可解析"的计数恒为 0——
            # 脏数据被静默写成 NULL，而日志里说"没有脏数据"。
            data[column_name] = parsed
            if parsed is None and original is not None:
                dirty.append({name: getattr(row, name) for name in old.columns.keys()})
        parsed_rows.append(data)

    # NOT NULL 列上有脏值：**明确失败**并列出样本。按"不编造"的立场，宁可停下来
    # 让人先处理脏数据，也不要迁移出一张被静默改写过的表。
    if dirty and not column_nullable:
        raise RuntimeError(
            f"{table_name}.{column_name} 是 NOT NULL 列，但有 {len(dirty)} 行的值"
            f"无法解析为时间（样例：{dirty[:3]}）。迁移已中止，数据库未做任何改动。\n"
            f"请先清理这些脏数据（把不可解析的值改成合法时间，或删除这些行），"
            f"再重新运行迁移——不要在这里自动编造时间。"
        )

    new.create(bind)

    for data in parsed_rows:
        bind.execute(new.insert().values(**data))

    bind.execute(sa.text(f"DROP TABLE {table_name}"))  # noqa: S608
    bind.execute(sa.text(f"ALTER TABLE {tmp} RENAME TO {table_name}"))  # noqa: S608

    # 索引按**反射到的真名与真列**重建——不靠人记，也不自造名字
    for idx in old_indexes:
        cols: Sequence[str] = idx.get("column_names") or []
        if not cols:
            continue  # 表达式索引，SQLite 下重建不了，跳过（记录在日志里）
        unique = "UNIQUE " if idx.get("unique") else ""
        bind.execute(
            sa.text(  # noqa: S608
                f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS "
                f"{idx['name']} ON {table_name} ({', '.join(cols)})"
            )
        )

    converted = sum(1 for d in parsed_rows if d.get(column_name) is not None)
    return {"rows": len(rows), "converted": converted, "skipped": len(dirty)}