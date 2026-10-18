"""B5：后台任务与 API 的**连接池隔离**。

为什么这件事值得单独测：
    抓取周期会**整段持有**一条连接（advisory lock 绑在会话上，中途不能关），
    而一个周期里要发 N 个 HTTP 请求、各带超时。与 API 共用 5+10 的池时，
    抓取 + 热度重算能长期吃掉 2 条，请求峰值就在 `pool_timeout` 上排队——
    症状是"偶发接口变慢"，而根因在调度器。**这类问题不会报错，只会让尾延迟变差。**

    共享池不是"省资源"，而是把两种完全不同的占用时长混在一起：请求是毫秒级，
    抓取是分钟级。混池的后果是后者决定前者的尾延迟。

运行：
    cd server-api
    pytest tests/test_db_pools.py -q
"""
import os

import pytest
import sqlalchemy as sa

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import database  # noqa: E402
from app.config import FEED_DB_APP_NAME, FEED_DB_MAX_OVERFLOW, FEED_DB_POOL_SIZE  # noqa: E402


class TestSchedulerPool:
    def test_sqlite_reuses_the_same_engine(self):
        """SQLite 必须复用同一 engine。

        内存库靠 `StaticPool` 共享同一条连接才有数据可言；另建 engine 会得到
        一个**空库**，症状是"表不存在"，而根因是池隔离——这条断言就是为了
        防止有人"顺手"把 SQLite 也隔离了。
        """
        if not database.DATABASE_URL.startswith("sqlite"):
            pytest.skip("当前不是 SQLite")
        assert database.scheduler_engine is database.engine

    def test_postgres_gets_a_separate_small_pool(self):
        """服务端库：独立池，且容量明显小于 API 池。"""
        kwargs = database._scheduler_engine_kwargs(
            "postgresql+psycopg://u:p@localhost:5432/db"
        )
        assert kwargs["pool_size"] == FEED_DB_POOL_SIZE
        assert kwargs["max_overflow"] == FEED_DB_MAX_OVERFLOW
        # 后台任务只需要"抓取 + 热度重算"两个并发，给 API 那么大的池毫无意义
        assert kwargs["pool_size"] < database._engine_kwargs(
            "postgresql+psycopg://u:p@localhost:5432/db"
        )["pool_size"]

    def test_scheduler_has_its_own_application_name(self):
        """`pg_stat_activity` 里要能一眼分出哪条连接是后台任务。

        没有这个，线上看到连接数偏高时无法回答"是请求打上来的，还是抓取占着"。
        """
        kwargs = database._scheduler_engine_kwargs(
            "postgresql+psycopg://u:p@localhost:5432/db"
        )
        assert kwargs["connect_args"]["application_name"] == FEED_DB_APP_NAME
        assert FEED_DB_APP_NAME != database._engine_kwargs(
            "postgresql+psycopg://u:p@localhost:5432/db"
        )["connect_args"]["application_name"]

    def test_scheduler_pool_keeps_the_other_pool_settings(self):
        """隔离的是容量与名字，其他参数（回收/探活/超时）应与 API 一致。

        漏掉 `pool_pre_ping` 就会在服务端断开后拿到死连接——那种"偶发一条请求失败"
        最难查，不该因为拆池而丢掉。
        """
        url = "postgresql+psycopg://u:p@localhost:5432/db"
        sched = database._scheduler_engine_kwargs(url)
        api = database._engine_kwargs(url)
        for key in ("pool_timeout", "pool_recycle", "pool_pre_ping", "pool_use_lifo"):
            assert sched[key] == api[key], f"{key} 在拆池时丢失或被改动"


class TestDispose:
    def test_dispose_releases_both_pools(self, monkeypatch):
        """进程退出要释放**两个**池。

        只 dispose API 池会留下后台任务的连接，PG 端表现为 idle 连接慢慢堆积。
        """
        disposed: list[str] = []

        class Fake:
            def __init__(self, name: str) -> None:
                self.name = name

            def dispose(self) -> None:
                disposed.append(self.name)

        api_engine, sched_engine = Fake("api"), Fake("scheduler")
        monkeypatch.setattr(database, "engine", api_engine)
        monkeypatch.setattr(database, "scheduler_engine", sched_engine)

        database.dispose()

        assert disposed == ["api", "scheduler"]

    def test_dispose_does_not_double_dispose_when_shared(self, monkeypatch):
        """SQLite 下两个名字指向**同一个** engine，不能 dispose 两次。"""
        disposed: list[str] = []

        class Fake:
            def dispose(self) -> None:
                disposed.append("x")

        shared = Fake()
        monkeypatch.setattr(database, "engine", shared)
        monkeypatch.setattr(database, "scheduler_engine", shared)

        database.dispose()
        assert len(disposed) == 1


class TestSchedulerUsesSchedulerSession:
    def test_scheduler_module_imports_the_scheduler_session(self):
        """调度器必须用 `SchedulerSessionLocal`——用错就白拆了池。

        这条读模块属性即可，不必真跑一次抓取（那要打网络）。
        """
        from app import scheduler

        assert scheduler.SchedulerSessionLocal is database.SchedulerSessionLocal
        # 反向：不能残留对 API 会话的引用
        assert not hasattr(scheduler, "SessionLocal")

    def test_scheduler_session_binds_scheduler_engine(self):
        from app import scheduler

        assert scheduler.SchedulerSessionLocal.kw["bind"] is database.scheduler_engine


class TestPoolSettingsAreSane:
    def test_scheduler_pool_covers_both_jobs(self):
        """2 条够不够：抓取与热度重算是**两个**独立 advisory lock 的任务。

        池比任务数小会让第二个任务在 `pool_timeout` 上白等一整轮——
        等于把它饿死，而不是"省下一条连接"。
        """
        assert FEED_DB_POOL_SIZE >= 2

    def test_real_engines_have_the_expected_pool_sizes(self):
        """真的建一次 engine（不连接）验池容量——只断言字典形状不够硬。

        `create_engine` 是惰性的：不在建 engine 时连库，所以这条能在没有 PG 的
        机器上跑，同时验证"参数真的被接受"（拼错的关键字会被 SQLAlchemy 拒）。
        """
        url = "postgresql+psycopg://u:p@localhost:5432/db"
        api_engine = sa.create_engine(url, future=True, **database._engine_kwargs(url))
        sched_engine = sa.create_engine(
            url, future=True, **database._scheduler_engine_kwargs(url)
        )
        try:
            assert api_engine.pool.size() == database.DB_POOL_SIZE
            assert sched_engine.pool.size() == FEED_DB_POOL_SIZE
            assert sched_engine.pool.size() < api_engine.pool.size()
            # 两个池是不同对象——混池的根因就在"同一个池"
            assert sched_engine.pool is not api_engine.pool
        finally:
            api_engine.dispose()
            sched_engine.dispose()
