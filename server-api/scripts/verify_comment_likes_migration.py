# -*- coding: utf-8 -*-
"""P1-12 真实 PostgreSQL 迁移验证（影子模式，一次性脚本，可重复执行）。

在**临时影子模式**（mig_check，用完即毁）上复现 0014 的完整生命周期：
  1) 从 0013 基线造历史数据（liked_by 含重复/悬空/非整数/非数组脏数据）；
  2) upgrade head：回填 comment_likes（去重、跳过悬空、跳过非整数）、
     likes 计数与关系表对齐、liked_by 删列、parent_id/root_id 自引用外键落库；
  3) 自引用外键 ON DELETE CASCADE 端到端：删主评论带走其回复；
  4) downgrade 0013：liked_by 还原（PG JSONB CAST 路径）、关系表与父子列拆除。

隔离方式：PGOPTIONS/options 设 search_path=mig_check，迁移链建的所有表
都落在该模式内，public 里的开发数据分毫未动。为何不用独立影子库：
toutiao 角色无 CREATEDB 权限，而同库同版本的影子模式验证结论等价。

用法：
    cd server-api
    PYTHONPATH=. <venv python> scripts/verify_comment_likes_migration.py
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.stdout.reconfigure(encoding="utf-8")

import psycopg
from psycopg.types.json import Json

from app.config import DATABASE_URL

SERVER_ROOT = Path(__file__).resolve().parent.parent
SHADOW_SCHEMA = "mig_check"
# psycopg 直连串（SQLAlchemy 的 +psycopg 标记去掉即可）
APP_URL = DATABASE_URL.replace("+psycopg", "")
# 隔离方式：search_path 首放 mig_check，链上新建表全部落在该模式；带上 public
# 仅为让 pg_trgm 的 gin_trgm_ops 等已装扩展对象可解析（新建对象仍优先生成于
# mig_check，public 里的开发数据分毫未动）。
SEARCH_PATH_OPT = f"-c search_path={SHADOW_SCHEMA},public"


def run_alembic(*args: str) -> None:
    """子进程跑 alembic；PGOPTIONS 把整条链重定向到影子模式。"""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(SERVER_ROOT),
        env={**os.environ, "DATABASE_URL": DATABASE_URL, "PGOPTIONS": SEARCH_PATH_OPT},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} 失败:\n{proc.stdout}\n{proc.stderr}"


def shadow_connect():
    return psycopg.connect(APP_URL, options=SEARCH_PATH_OPT)


def reset_shadow_schema(create: bool) -> None:
    """重建影子模式；create 时预建空 alembic_version。

    为何必须预建：search_path 含 public 时，alembic 读未限定模式名的
    alembic_version 会落到 public 的版本表（开发库已是 0013），整条链被
    误判为「无需迁移」而 no-op。预建 mig_check.alembic_version 后版本
    解析优先命中影子模式，链才会真正在影子模式里从 base 跑起。
    """
    with psycopg.connect(APP_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SHADOW_SCHEMA} CASCADE")
        if create:
            conn.execute(f"CREATE SCHEMA {SHADOW_SCHEMA}")
            conn.execute(
                f"CREATE TABLE {SHADOW_SCHEMA}.alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )


def main() -> int:
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
        if not cond:
            ok = False

    reset_shadow_schema(create=True)
    print(f"--- 影子模式 {SHADOW_SCHEMA} 就绪，从 0013 基线开始 ---")

    try:
        # 1. 0013 基线 + 历史数据（PG 的 JSONB 列存得下「非数组」这种脏数据）
        run_alembic("upgrade", "0013_rss_sources")
        with shadow_connect() as conn:
            conn.execute(
                "INSERT INTO users (id, phone, password_hash, name, created_at) "
                "VALUES (1, '22644212509', 'hash', '用户甲', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute("INSERT INTO news (id, title) VALUES ('n1', '测试资讯')")
            conn.execute(
                "INSERT INTO comments (id, news_id, user_id, content, created_at, likes, liked_by) "
                "VALUES ('c1','n1',1,'评论一','2026-01-02T00:00:00+00:00',3,%s)",
                (Json([1, 1, 99, "x"]),),  # 有效 1 + 重复 1 + 悬空 99 + 非整数 'x'
            )
            conn.execute(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c2','n1','评论二','2026-01-03T00:00:00+00:00',5,NULL)"
            )
            conn.execute(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c3','n1','评论三','2026-01-04T00:00:00+00:00',0,%s)",
                (Json([]),),
            )
            conn.execute(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c4','n1','评论四','2026-01-05T00:00:00+00:00',0,%s)",
                (Json({"bad": 1}),),  # 非数组脏数据
            )

        # 2. upgrade head：回填 + 结构 + 外键
        run_alembic("upgrade", "head")
        print("--- upgrade head 后 ---")
        with shadow_connect() as conn:
            likes = conn.execute(
                "SELECT comment_id, user_id FROM comment_likes ORDER BY 1"
            ).fetchall()
            counts = dict(
                conn.execute("SELECT id, likes FROM comments ORDER BY id").fetchall()
            )
            has_liked_by = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name='comments' AND column_name='liked_by'"
            ).fetchone()
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name='comments'"
                ).fetchall()
            }
            fk_names = {
                r[0]
                for r in conn.execute(
                    "SELECT conname FROM pg_constraint WHERE contype='f' "
                    "AND conrelid IN ('comments'::regclass, 'comment_likes'::regclass)"
                ).fetchall()
            }
        check("回填去重/跳过悬空/跳过非整数：只剩 (c1, 1)", likes == [("c1", 1)], str(likes))
        check(
            "likes 与关系表行数对齐（3→1 下调、5→0 清零）",
            counts == {"c1": 1, "c2": 0, "c3": 0, "c4": 0},
            str(counts),
        )
        check("liked_by 列已删除", has_liked_by is None)
        check("parent_id/root_id 列已添加", {"parent_id", "root_id"} <= cols)
        check(
            "自引用外键已建（CASCADE）",
            {"fk_comments_parent_id_comments", "fk_comments_root_id_comments"} <= fk_names,
            str(sorted(fk_names)),
        )
        check(
            "comment_likes 外键已建（内联声明）",
            {"comment_likes_comment_id_fkey", "comment_likes_user_id_fkey"} <= fk_names,
        )

        # 3. 自引用外键 CASCADE 端到端：删主评论带走回复
        with shadow_connect() as conn:
            conn.execute(
                "INSERT INTO comments (id, news_id, content, created_at, likes) "
                "VALUES ('c5','n1','主评论','2026-01-06T00:00:00+00:00',0)"
            )
            conn.execute(
                "INSERT INTO comments (id, news_id, parent_id, root_id, content, created_at, likes) "
                "VALUES ('r1','n1','c5','c5','回复','2026-01-06T00:00:01+00:00',0)"
            )
            conn.execute("DELETE FROM comments WHERE id='c5'")
            left = conn.execute(
                "SELECT id FROM comments WHERE id IN ('c5','r1') ORDER BY id"
            ).fetchall()
        check("删主评论级联删除其回复", left == [], str(left))

        # 4. downgrade 0013：可逆性（含 PG JSONB CAST 路径）
        run_alembic("downgrade", "0013_rss_sources")
        print("--- downgrade 0013 后 ---")
        with shadow_connect() as conn:
            rows = dict(
                conn.execute("SELECT id, liked_by FROM comments ORDER BY id").fetchall()
            )
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                ).fetchall()
            }
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name='comments'"
                ).fetchall()
            }
            rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        check("liked_by 还原且 c1 的点赞从关系表找回", rows.get("c1") == [1], str(rows.get("c1")))
        check("无点赞的评论 liked_by 为 NULL", rows.get("c2") is None)
        check("comment_likes 表已删除", "comment_likes" not in tables)
        check(
            "parent_id/root_id 列已删除",
            "parent_id" not in cols and "root_id" not in cols,
        )
        check("版本回到 0013_rss_sources", rev == "0013_rss_sources", rev)
    finally:
        reset_shadow_schema(create=False)
        print(f"--- 影子模式 {SHADOW_SCHEMA} 已销毁 ---")

    print("\n==== %s ====" % ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
