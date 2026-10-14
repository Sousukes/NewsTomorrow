"""迁移链守卫：在**独立子进程 + 临时 SQLite 库**上跑通迁移链。

为什么要这个测试：
    schema 改为 Alembic 单轨管理后，启动不再 `create_all()`，
    **迁移链跑不通 = 应用起不来**。而 `config.py` 的默认 DATABASE_URL 就是 SQLite，
    一旦某个迁移用了 SQLite 不支持的操作（如裸 `op.drop_constraint`），
    默认配置会直接不可用——这类问题在 PostgreSQL 开发环境上完全看不出来。

关键安全约束（勿改）：
    `migrations/env.py` 从 `app.config.DATABASE_URL` 取连接，**不会**读
    alembic Config 里的 `sqlalchemy.url`。若在进程内调用 `command.upgrade()`，
    实际作用的是**真实数据库**（曾因此把开发库回滚到 base）。
    因此这里强制用**子进程 + 覆盖 DATABASE_URL 环境变量**来保证隔离。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text as sa_text

SERVER_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = (
    "users",
    "news",
    "comments",
    "comment_likes",
    "messages",
    "message_reads",
    "user_favorites",
    "user_history",
    "search_logs",
    "rss_sources",
    "admin_users",
    "admin_audit_logs",
)


def run_alembic(db_path: Path, *args: str) -> None:
    """在子进程里执行 alembic，强制指向临时 SQLite 库。"""
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(SERVER_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} 失败:\n{proc.stdout}\n{proc.stderr}"


def inspector_of(db_path: Path):
    return inspect(create_engine(f"sqlite:///{db_path.as_posix()}"))


@pytest.fixture(scope="module")
def chain_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("mig") / "chain.db"
    run_alembic(db, "upgrade", "head")
    yield db
    # 收尾：回滚并确认确实回到空库（同时验证 downgrade 可用）
    run_alembic(db, "downgrade", "base")


class TestMigrationChain:
    def test_target_is_sqlite_temp_db(self, chain_db):
        """安全网：确认迁移真的落在临时 SQLite 文件上，而非开发/生产库。"""
        assert chain_db.exists()
        assert "sqlite" in f"sqlite:///{chain_db.as_posix()}"

    def test_all_tables_created(self, chain_db):
        tables = set(inspector_of(chain_db).get_table_names())
        for t in EXPECTED_TABLES:
            assert t in tables, f"缺少表 {t}"

    def test_comment_ownership_column(self, chain_db):
        """0007：评论归属列（「删除自己的评论」的前置）。"""
        cols = {c["name"] for c in inspector_of(chain_db).get_columns("comments")}
        assert "user_id" in cols
        assert "created_at" in cols  # 0004：排序唯一依据

    def test_feed_index_created(self, chain_db):
        """0004：feed 排序复合索引。"""
        names = {i["name"] for i in inspector_of(chain_db).get_indexes("news")}
        assert "ix_news_feed" in names

    def test_token_version_column(self, chain_db):
        """0006：JWT 服务端吊销依赖该列。"""
        cols = {c["name"] for c in inspector_of(chain_db).get_columns("users")}
        assert "token_version" in cols

    def test_user_marks_tables(self, chain_db):
        """0010：收藏 / 浏览历史云端化的落库结构。"""
        insp = inspector_of(chain_db)
        fav_cols = {c["name"] for c in insp.get_columns("user_favorites")}
        hist_cols = {c["name"] for c in insp.get_columns("user_history")}
        assert {"user_id", "news_id", "created_at"} <= fav_cols
        assert {"user_id", "news_id", "viewed_at"} <= hist_cols
        # 复合主键保证「同一用户同一资讯仅一条」（幂等基础）
        fav_pk = insp.get_pk_constraint("user_favorites")["constrained_columns"]
        assert set(fav_pk) == {"user_id", "news_id"}
        # 倒序列表依赖的索引
        assert "ix_user_favorites_user_created" in {
            i["name"] for i in insp.get_indexes("user_favorites")
        }
        assert "ix_user_history_user_viewed" in {
            i["name"] for i in insp.get_indexes("user_history")
        }

    def test_search_logs_table(self, chain_db):
        """0012：搜索词日志（热词统计的原始来源）。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("search_logs")}
        assert {"id", "user_id", "term", "created_at"} <= cols
        # 热词按「词 + 时间窗口」聚合，索引必须覆盖这两列
        assert "ix_search_logs_term_created" in {
            i["name"] for i in insp.get_indexes("search_logs")
        }

    def test_news_hot_score_column(self, chain_db):
        """0011：热度分**落库**（而非查询时算），否则无法索引、也不能当游标键。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("news")}
        assert "hot_score" in cols
        assert "ix_news_hot" in {i["name"] for i in insp.get_indexes("news")}

    def test_rss_sources_table(self, chain_db):
        """0013：RSS 源可配置化——源配置由代码搬到了表里。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("rss_sources")}
        assert {
            "key", "name", "channel", "url", "weight", "enabled",
            "last_status", "last_error", "last_item_count", "last_fetched_at",
            "created_at", "updated_at",
        } <= cols
        # key 是业务主键：播种/回写/管理接口都以它对齐
        assert insp.get_pk_constraint("rss_sources")["constrained_columns"] == ["key"]
        # 抓取时按「频道 + 是否启用」取源
        assert "ix_rss_sources_channel_enabled" in {
            i["name"] for i in insp.get_indexes("rss_sources")
        }

    def test_comment_likes_table(self, chain_db):
        """0014：点赞关系表取代 liked_by JSON 数组（与 0004 read_by 同款处方）。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("comment_likes")}
        assert {"comment_id", "user_id", "created_at"} <= cols
        # 复合主键保证「同一用户对同一评论只点一次」（重复点赞幂等）
        pk = insp.get_pk_constraint("comment_likes")["constrained_columns"]
        assert set(pk) == {"comment_id", "user_id"}

    def test_comment_reply_columns(self, chain_db):
        """0014：两级评论结构（parent_id/root_id）+ liked_by 已删。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("comments")}
        assert {"parent_id", "root_id"} <= cols
        assert "liked_by" not in cols  # JSON 数组已搬进关系表
        # 回复列表按 root_id 查询，索引必须存在
        assert "ix_comments_root_id" in {
            i["name"] for i in insp.get_indexes("comments")
        }

    def test_fetch_politeness_columns(self, chain_db):
        """0017：抓取礼仪与告警节流字段（P1-16）。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("rss_sources")}
        assert "respect_robots" in cols  # 逐源 robots 开关
        assert "last_alerted_at" in cols  # 告警节流锚点（落库，多实例共享）
        # 存量行必须是「遵守」而非 NULL：NULL 会在判定里掉进"不遵守"分支
        col = next(c for c in insp.get_columns("rss_sources") if c["name"] == "respect_robots")
        assert col["nullable"] is False

    def test_fetch_failure_grading_columns(self, chain_db):
        """0018：失败分级与熔断字段（P1-18）。

        `consecutive_failures` 必须**非空**：NULL 参与阈值比较会静默失效，
        熔断就永远不会触发——而这类 bug 在功能测试里看不出来（计数走的是新建行）。
        """
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("rss_sources")}
        assert {"consecutive_failures", "failure_kind", "next_retry_at"} <= cols
        col = next(
            c for c in insp.get_columns("rss_sources") if c["name"] == "consecutive_failures"
        )
        assert col["nullable"] is False

    def test_admin_audit_logs_table(self, chain_db):
        """0016：运营审计——权限回答「谁能进」，审计回答「谁干了什么」。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("admin_audit_logs")}
        assert {"id", "actor_phone", "action", "target", "detail", "created_at"} <= cols
        # 自增 id 同时充当游标（单调，规避秒级时间戳同值导致的排序抖动）
        pk = insp.get_pk_constraint("admin_audit_logs")["constrained_columns"]
        assert pk == ["id"]
        names = {i["name"] for i in insp.get_indexes("admin_audit_logs")}
        # 全局时间线按时间倒序；「这个对象被谁动过」按 target + id
        assert "ix_admin_audit_logs_created_at" in names
        assert "ix_admin_audit_target_id" in names

    def test_admin_users_table(self, chain_db):
        """0015：管理员白名单入库（应用内管理「指定账号」）。"""
        insp = inspector_of(chain_db)
        cols = {c["name"] for c in insp.get_columns("admin_users")}
        assert {"phone", "note", "added_by", "created_at"} <= cols
        # 手机号即主键：预授权（未注册账号）也能先加入名单，故不设 users 外键
        pk = insp.get_pk_constraint("admin_users")["constrained_columns"]
        assert pk == ["phone"]
        fks = insp.get_foreign_keys("admin_users")
        assert fks == []  # 无外键是刻意设计（见模型注释）
        # 列表按添加时间排序，索引必须存在
        assert "ix_admin_users_created_at" in {
            i["name"] for i in insp.get_indexes("admin_users")
        }

    def test_downgrade_to_base_drops_tables(self, chain_db):
        """回滚到 base 后表应被清除（确保 downgrade 可用、非空操作）。"""
        run_alembic(chain_db, "downgrade", "base")
        assert "news" not in inspector_of(chain_db).get_table_names()
        # 复原，供后续用例使用
        run_alembic(chain_db, "upgrade", "head")
        assert "news" in inspector_of(chain_db).get_table_names()


@pytest.fixture(scope="class")
def backfill_db(tmp_path_factory) -> Path:
    """0013 基线 + 历史点赞数据，用于验证 0014 的回填与可逆性。"""
    db = tmp_path_factory.mktemp("backfill") / "backfill.db"
    run_alembic(db, "upgrade", "0013_rss_sources")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                "INSERT INTO users (id, phone, password_hash, name, created_at) "
                "VALUES (1, '22644212509', 'hash', '用户甲', '2026-01-01T00:00:00+00:00')"
            )
        )
        conn.execute(sa_text("INSERT INTO news (id, title) VALUES ('n1', '测试资讯')"))
        # c1：有效 1 + 数组内重复 1 + 悬空 99 + 非整数 'x'；likes=3 已漂移
        conn.execute(
            sa_text(
                "INSERT INTO comments (id, news_id, user_id, content, created_at, likes, liked_by) "
                "VALUES ('c1', 'n1', 1, '评论一', '2026-01-02T00:00:00+00:00', 3, :lk)"
            ),
            {"lk": json.dumps([1, 1, 99, "x"])},
        )
        # c2：liked_by 为空但 likes=5（计数漂移的另一极）
        conn.execute(
            sa_text(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c2', 'n1', '评论二', '2026-01-03T00:00:00+00:00', 5, NULL)"
            )
        )
        # c3：空数组对照；c4：脏数据（非 JSON 文本）对照
        conn.execute(
            sa_text(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c3', 'n1', '评论三', '2026-01-04T00:00:00+00:00', 0, '[]')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO comments (id, news_id, content, created_at, likes, liked_by) "
                "VALUES ('c4', 'n1', '评论四', '2026-01-05T00:00:00+00:00', 0, 'not-json')"
            )
        )
    engine.dispose()
    yield db
    run_alembic(db, "downgrade", "base")


class TestCommentLikesBackfill:
    """0014 数据迁移往返：liked_by JSON → comment_likes → liked_by。"""

    def test_upgrade_backfills_and_normalizes(self, backfill_db):
        run_alembic(backfill_db, "upgrade", "head")
        engine = create_engine(f"sqlite:///{backfill_db.as_posix()}")
        with engine.connect() as conn:
            likes = conn.execute(
                sa_text("SELECT comment_id, user_id FROM comment_likes ORDER BY comment_id")
            ).fetchall()
            counts = dict(conn.execute(sa_text("SELECT id, likes FROM comments")).fetchall())
            cols = {c["name"] for c in inspect(engine).get_columns("comments")}
        engine.dispose()
        # 去重（数组内两个 1）、跳过悬空 99、跳过非整数 'x'：只剩一条有效点赞
        assert likes == [("c1", 1)]
        # likes 与关系表行数对齐（3→1 下调，5→0 清零）
        assert counts == {"c1": 1, "c2": 0, "c3": 0, "c4": 0}
        assert "liked_by" not in cols

    def test_downgrade_restores_liked_by(self, backfill_db):
        run_alembic(backfill_db, "downgrade", "0013_rss_sources")
        engine = create_engine(f"sqlite:///{backfill_db.as_posix()}")
        with engine.connect() as conn:
            rows = dict(conn.execute(sa_text("SELECT id, liked_by FROM comments")).fetchall())
            tables = set(inspect(engine).get_table_names())
            cols = {c["name"] for c in inspect(engine).get_columns("comments")}
        engine.dispose()
        assert json.loads(rows["c1"]) == [1]  # 点赞数据从关系表还原
        assert rows["c2"] is None  # 无点赞行 → 保持 NULL
        assert rows["c3"] is None  # 原 '[]' 无对应行，还原为 NULL（空数组 ≡ 无点赞）
        assert "comment_likes" not in tables
        assert "parent_id" not in cols and "root_id" not in cols
