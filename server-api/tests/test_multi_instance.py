"""批次 M6 测试：限流器工厂回退与调度器多实例互斥锁。

运行：
    cd server-api
    pytest -q
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-32bytes")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ratelimit import FixedWindowLimiter, build_limiter  # noqa: E402
from app.scheduler import (  # noqa: E402
    _ADVISORY_LOCK_KEY,
    _HOT_ADVISORY_LOCK_KEY,
    _release_lock,
    _try_lock,
)


class TestBuildLimiter:
    def test_default_is_in_memory(self):
        assert isinstance(build_limiter(60, ""), FixedWindowLimiter)

    def test_redis_unavailable_falls_back_to_memory(self):
        """Redis 未安装 / 连不上时必须回退，不能因限流组件故障导致启动失败。"""
        limiter = build_limiter(60, "redis://127.0.0.1:6399/0")
        assert isinstance(limiter, FixedWindowLimiter)


class TestAdvisoryLock:
    def test_sqlite_treats_as_acquired(self):
        engine = create_engine("sqlite:///:memory:")
        with sessionmaker(bind=engine)() as db:
            assert _try_lock(db, _ADVISORY_LOCK_KEY) is True
            _release_lock(db, _ADVISORY_LOCK_KEY)  # 非 PG 应为 no-op，不应抛错

    def test_two_crons_use_distinct_lock_keys(self):
        """P1-21：抓取锁与热度锁**键不同**。

        两把锁键相同会让热度任务因为另一实例正在抓取而整轮跳过——
        那等于把拆回寄生关系。SQLite 下都视为获取，故这里只钉键的区分。
        """
        assert _ADVISORY_LOCK_KEY != _HOT_ADVISORY_LOCK_KEY
        engine = create_engine("sqlite:///:memory:")
        with sessionmaker(bind=engine)() as db:
            assert _try_lock(db, _HOT_ADVISORY_LOCK_KEY) is True
            _release_lock(db, _HOT_ADVISORY_LOCK_KEY)
