"""迁移演练：带存量数据跑一遍并断言值没被截断（CI 的 PG 与本地 SQLite 都用它）。

为什么要有这个脚本：
    `alembic upgrade head` 在**空库**上跑，只能验证迁移脚本的**语法**，
    验证不了**存量转换行为**。而 P1-53 在 SQLite 上真正把一整列时间截成了
    年份（`CAST` 把 ISO 串当数字），它的可怕之处正是"空库演练看不出来、
    排序看着还正常"。

    所以 CI 现在多一步：造几条**真实格式**的存量行 → 再迁一次 → 断言值完整。

用法（CI 里的两步）:
    python scripts/_migration_drill.py --insert    # 造存量数据（0029 之后）
    python scripts/_migration_drill.py --verify    # 迁到 head 之后断言

本地也能跑（SQLite）：
    DATABASE_URL="sqlite:///../.tmp/drill.db" python -m alembic upgrade 0029_time_empty_to_null
    DATABASE_URL="sqlite:///../.tmp/drill.db" python scripts/_migration_drill.py --insert
    DATABASE_URL="sqlite:///../.tmp/drill.db" python -m alembic upgrade head
    DATABASE_URL="sqlite:///../.tmp/drill.db" python scripts/_migration_drill.py --verify
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 存量形态：A3/0027 之后写入的 aware ISO（`+00:00`）。
# 这里**不放 NULL 行**——`rss_source_health_events.at` 是 NOT NULL 列，
# 造一条 NULL 只会让演练脚本自己报约束错误（写出来才发现，那正是本脚本要避免的事）。
# "脏值混入 NOT NULL 列"的场景由 helper 单测覆盖：helper 会**明确失败并列出
# 样本**，而不是静默写 NULL 或编造时间。
SEED = [
    ("drill-a", 3, "2026-10-18T06:00:00+00:00"),
    ("drill-b", 5, "2026-10-17T06:00:00+00:00"),
    ("drill-c", 1, "2026-10-16T06:00:00+00:00"),
]


def _conn():
    import sqlalchemy as sa

    return sa.create_engine(os.environ["DATABASE_URL"]).connect()


def insert() -> None:
    import sqlalchemy as sa

    with _conn() as conn:
        for i, (key, items, at) in enumerate(SEED, start=1):
            conn.execute(
                sa.text(
                    "INSERT INTO rss_source_health_events "
                    "(id, source_key, ok, item_count, at) "
                    "VALUES (:i, :k, :ok, :n, :at)"
                ),
                {"i": i, "k": key, "ok": True, "n": items, "at": at},
            )
        conn.commit()
    print(f"[drill] 已插入 {len(SEED)} 条存量行")


def verify() -> None:
    """断言**瞬时正确**，而不是"字符串带 +00:00"。

    两端的存储形态天生不同：
    - PG 的 `timestamptz` 保留瞬时，读回是 aware datetime；
    - SQLite 没有原生时间类型，SQLAlchemy 存的是 `2026-10-18 06:00:00.000000`
      （**不带偏移**，读回时由 `models.iso_utc()` 补上）。

    所以正确的不变量是「解析出来的瞬时等于种子里的那个瞬时」。早一个版本直接
    要求文本含 `+00:00`，在 SQLite 上必然误报——那不是迁移出错，是断言没区分
    **存储形态**与**对外格式**（后者由 `iso_utc` 负责，已有单测覆盖）。
    """
    import sqlalchemy as sa

    from app.migration_utils import parse_utc

    failures = []
    expected = {key: parse_utc(at) for key, _items, at in SEED}
    with _conn() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT source_key, item_count, at FROM rss_source_health_events ORDER BY id"
            )
        ).fetchall()

    if len(rows) != len(SEED):
        failures.append(f"行数不对：期望 {len(SEED)}，实际 {len(rows)}")

    for key, items, at in rows:
        text = str(at)
        # ① 值没被截断（P1-53 的 CAST 毁数据就是这里现形）
        if len(text) < 19:
            failures.append(f"{key}: 时间被截断 → {text!r}")
            continue
        # ② 能解析成时间
        got = parse_utc(text)
        if got is None:
            failures.append(f"{key}: 无法解析为时间 → {text!r}")
            continue
        # ③ **瞬时**与种子一致（两端通吃的唯一正确不变量）
        if got != expected.get(key):
            failures.append(f"{key}: 瞬时不对，期望 {expected.get(key)} 实际 {got}")
        # ④ 带偏移的话必须是 +00:00（PG 侧不能被改写成别的时区）
        if ("+" in text or text.endswith("Z")) and not text.endswith(("+00:00", "Z")):
            failures.append(f"{key}: 偏移被改写 → {text!r}")
        # ⑤ 其它列必须原样
        want_items = next(n for k, n, _a in SEED if k == key)
        if items != want_items:
            failures.append(f"{key}: item_count 被改动 → {items}")

    ordered = [r[0] for r in sorted(rows, key=lambda r: str(r[2]), reverse=True)]
    if ordered != ["drill-a", "drill-b", "drill-c"]:
        failures.append(f"排序不对：{ordered}")

    if failures:
        print("[drill] ❌ 演练失败：")
        for f in failures:
            print("   -", f)
        raise SystemExit(1)
    print(f"[drill] ✅ {len(rows)} 行值完整、瞬时正确、偏移未被改写、排序正确")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--insert":
        insert()
    elif mode == "--verify":
        verify()
    else:
        print(__doc__)
        raise SystemExit(2)