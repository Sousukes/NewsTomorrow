"""SQLite → PostgreSQL 全量数据迁移（幂等，可重复执行）。

用法：
    # 1) 仅迁移数据（表结构由应用 startup 自动建）
    python -m scripts.migrate_sqlite_to_pg

    # 2) 指定源/目标
    python -m scripts.migrate_sqlite_to_pg --source ./toutiao.db \
        --target postgresql+psycopg://toutiao:***@127.0.0.1:5432/toutiao

行为：
    - 读取 SQLite 全部表数据，按主键 upsert 到 PG（重复执行不会产生重复行）
    - JSON 字段（images）自动适配 PG 的 JSONB
    - 源库若是 0014 以前的旧 schema（comments 还带 liked_by）：该列在模型侧
      已不存在，拷贝时自动过滤；其点赞数据会从 liked_by 回填进 comment_likes，
      **建议**也可先在源库执行 `alembic upgrade head`（SQLite 支持）让 0014
      自行回填，两种路径效果一致
    - 源库缺少某张表时跳过该表（旧库没有 comment_likes 属正常）
    - 迁移前打印源表行数，迁移后逐表校验行数一致
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# 允许以 `python scripts/migrate_sqlite_to_pg.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import DATABASE_URL  # noqa: E402
from app.database import Base, engine as default_engine  # noqa: E402
from app.models import Comment, CommentLike, News, User  # noqa: E402

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "toutiao.db"

# SQLite 表 → ORM 模型（comment_likes 必须在 comments 之后：外键引用）
TABLE_MODELS = {
    "users": User,
    "news": News,
    "comments": Comment,
    "comment_likes": CommentLike,
}

# JSON 列（PG 侧为 JSONB，需显式转换）。comments.liked_by 已随 0014 删除，
# 旧源库的该列由回填逻辑单独处理，不在此列。
JSON_COLUMNS = {"news": ["images"]}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    p.add_argument("--source", default=str(DEFAULT_SOURCE), help="源 SQLite 文件路径")
    p.add_argument("--target", default=DATABASE_URL, help="目标 PG 连接串（默认取 DATABASE_URL）")
    return p.parse_args()


def row_count_sqlite(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def table_exists(src: sqlite3.Connection, table: str) -> bool:
    return (
        src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def copy_table(
    src: sqlite3.Connection,
    dst: Session,
    table: str,
    model,
    json_cols: list[str],
) -> int:
    """把一张表的数据 upsert 到目标库，返回处理行数。"""
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    col_names = [d[0] for d in src.execute(f"SELECT * FROM {table}").description]
    model_cols = set(model.__table__.columns.keys())

    processed = 0
    for raw in rows:
        data = dict(zip(col_names, raw))
        # JSON 列：SQLite 存的是文本，需解析后再写入 JSONB
        for col in json_cols:
            val = data.get(col)
            if isinstance(val, str):
                try:
                    data[col] = json.loads(val)
                except json.JSONDecodeError:
                    data[col] = None
        # 过滤源库有、模型已没有的列（如旧 schema 的 comments.liked_by）——
        # 不过滤会以 TypeError 炸掉整个迁移
        data = {k: v for k, v in data.items() if k in model_cols}
        # None 化的空值处理：避免 NOT NULL 冲突（JSON 列即便为 NULL 也保留键）
        data = {k: v for k, v in data.items() if v is not None or k in json_cols}

        # 复合主键（如 comment_likes）不能走 Session.get 的单值形式，
        # 改用等值查询定位已有行
        pk_cols = list(model.__table__.primary_key.columns)
        pk = pk_cols[0].name
        if len(pk_cols) == 1:
            existing = dst.get(model, data.get(pk))
        else:
            existing = (
                dst.query(model)
                .filter_by(**{c.name: data.get(c.name) for c in pk_cols})
                .first()
            )
        if existing is None:
            dst.add(model(**data))
        else:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        processed += 1

    dst.commit()
    return processed


def backfill_comment_likes(src: sqlite3.Connection, dst: Session) -> int:
    """旧源库（comments.liked_by 仍在）→ 回填 comment_likes。

    去重、跳过非整数与悬空 user_id（PG 外键强制生效，悬空 id 会炸迁移）。
    源库已升级到 0014+（无 liked_by 列）时返回 0。
    """
    if "liked_by" not in {
        r[1] for r in src.execute("PRAGMA table_info(comments)").fetchall()
    }:
        return 0
    valid_users = {r[0] for r in src.execute("SELECT id FROM users").fetchall()}
    moved = 0
    for comment_id, raw in src.execute("SELECT id, liked_by FROM comments").fetchall():
        if not raw:
            continue
        try:
            user_ids = json.loads(raw) if isinstance(raw, str) else list(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(user_ids, list):
            continue
        seen: set = set()
        for uid in user_ids:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid in seen or uid not in valid_users:
                continue
            seen.add(uid)
            dst.add(CommentLike(comment_id=comment_id, user_id=uid))
            moved += 1
    dst.commit()
    return moved


def main() -> int:
    args = parse_args()
    source = Path(args.source)
    if not source.exists():
        print(f"[✗] 源 SQLite 不存在：{source}", file=sys.stderr)
        return 1

    target_url = args.target
    if target_url.startswith("sqlite"):
        print("[✗] 目标不是 PostgreSQL。请通过 --target 或 DATABASE_URL 指定 postgresql+psycopg://…", file=sys.stderr)
        return 1

    print(f"源(SQLite) : {source}")
    print(f"目标(PG)   : {target_url.split('@')[-1]}")
    print()

    # 1) 确保目标库有表（复用应用建表逻辑，PG 上 JSON 自动落为 JSONB）
    if DATABASE_URL != target_url:
        os.environ["DATABASE_URL"] = target_url
    pg_engine = create_engine(target_url, future=True, pool_pre_ping=True)
    Base.metadata.create_all(bind=pg_engine)
    print("[1/3] 目标表结构就绪")
    print()

    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row

    # 2) 迁移
    print("[2/3] 迁移数据")
    with Session(pg_engine) as dst:
        for table, model in TABLE_MODELS.items():
            if not table_exists(src, table):
                print(f"      {table:<14} 源无此表，跳过")
                continue
            src_count = row_count_sqlite(src, table)
            moved = copy_table(src, dst, table, model, JSON_COLUMNS.get(table, []))
            print(f"      {table:<14} 源 {src_count:>4} 行 → 写入 {moved:>4} 行")
        # 旧源库（comments.liked_by 仍在）→ 回填 comment_likes；源已升级则为空操作
        likes = backfill_comment_likes(src, dst)
        if likes:
            print(f"      {'comment_likes':<14} 从 liked_by 回填 {likes:>4} 行")

    print()

    # 3) 校验行数
    print("[3/3] 校验")
    ok = True
    with Session(pg_engine) as dst:
        for table, model in TABLE_MODELS.items():
            if not table_exists(src, table):
                print(f"      - {table:<14} 源无此表，跳过校验")
                continue
            src_count = row_count_sqlite(src, table)
            dst_count = len(dst.execute(select(model)).scalars().all())
            flag = "✓" if src_count == dst_count else "✗"
            if src_count != dst_count:
                ok = False
            print(f"      {flag} {table:<14} SQLite {src_count:>4} / PG {dst_count:>4}")

    src.close()
    pg_engine.dispose()

    print()
    print("迁移完成。" if ok else "迁移完成，但行数不一致，请检查上方输出。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())