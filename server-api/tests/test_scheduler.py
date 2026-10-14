"""P1-21：热度重算独立 cron 的调度行为。

核心回归：**抓取被关停（FEED_CRON_ENABLED=false）时热度照跑**——
此前热度寄生在抓取任务里，关停抓取等于让 hot / for-you 排序冻结。

其余钉住：
- 抓取单轮不再顺带重算热度（旧寄生关系已拆除，结构性断言）；
- 热度失败不杀循环（每轮都抛异常，循环仍按周期继续）；
- `/api/health` 暴露 hotRecompute（抓取关停时运维判断热度活着的唯一信号）；
- `hot_status()` 返回副本（health 每次调用拿新快照）。

所有用例用桩替换 DB/网络依赖，不跑真实调度——conftest 已在测试环境
关停两条 cron（否则 lifespan 的首轮「启动即执行」会与测试主线程并发写分）。

运行：
    cd server-api
    pytest -q tests/test_scheduler.py
"""
import asyncio
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-32bytes")
os.environ.setdefault("FEED_CRON_ENABLED", "false")
os.environ.setdefault("HOT_CRON_ENABLED", "false")

import pytest  # noqa: E402

import main  # noqa: E402
import app.scheduler as sched  # noqa: E402
from app.feeds.ingest import FetchReport  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_scheduler_handles():
    """兜底：用例异常中断也要清干净模块级任务句柄。

    每个场景都跑在自己的 `asyncio.run` 里，循环关闭即取消残留任务；
    这里只保证 `_task` / `_hot_task` 两个全局句柄不留脏值，
    否则后续文件里 `start_scheduler` 的幂等判断会失效。
    """
    yield
    sched._task = None
    sched._hot_task = None


class TestCronIndependence:
    def test_hot_runs_when_feed_cron_disabled(self, monkeypatch):
        """核心回归：抓取关停 → 热度任务照建、照跑、抓取一次都不执行。"""
        monkeypatch.setattr(sched, "FEED_CRON_ENABLED", False)
        monkeypatch.setattr(sched, "HOT_CRON_ENABLED", True)
        calls = {"hot": 0, "feed": 0}

        def fake_hot():
            calls["hot"] += 1
            return 0

        def fake_feed(channel=None, keyword=None):
            calls["feed"] += 1
            return FetchReport()

        monkeypatch.setattr(sched, "_run_hot_once", fake_hot)
        monkeypatch.setattr(sched, "_run_once", fake_feed)

        async def scenario():
            await sched.start_scheduler()
            assert sched._task is None  # 抓取任务根本没有创建
            assert sched._hot_task is not None
            for _ in range(100):  # 等首轮（启动即执行一次，无需等一个周期）
                if calls["hot"]:
                    break
                await asyncio.sleep(0.02)
            await sched.stop_scheduler()

        asyncio.run(scenario())
        assert calls["hot"] >= 1
        assert calls["feed"] == 0
        assert sched._task is None and sched._hot_task is None


class TestFeedHotDecoupled:
    def test_run_once_no_longer_recomputes_hot(self, monkeypatch):
        """结构性解耦：抓取单轮不再顺带重算热度（旧寄生关系已拆除）。"""
        recompute_calls = []
        monkeypatch.setattr(
            sched, "recompute_hot_scores", lambda *a, **k: recompute_calls.append(1) or 0
        )
        monkeypatch.setattr(
            sched, "run_ingest", lambda db, channel=None, keyword=None: FetchReport()
        )
        # 清理逻辑打桩：本用例只关心「抓取轮次是否碰热度」
        monkeypatch.setattr(sched, "prune_old_news", lambda db: 0)
        monkeypatch.setattr(sched, "prune_search_logs", lambda db: 0)
        monkeypatch.setattr(sched, "prune_health_events", lambda db: 0)

        report = sched._run_once()
        assert isinstance(report, FetchReport)
        assert recompute_calls == []


class TestHotLoopResilience:
    def test_loop_survives_repeated_failures(self, monkeypatch):
        """每轮都抛异常，循环仍按周期继续——热度是派生数据，失败只记录不终止。"""
        calls = []

        def always_fail():
            calls.append(1)
            raise RuntimeError("db down")

        monkeypatch.setattr(sched, "_run_hot_once", always_fail)

        async def scenario():
            # 0.001 分钟 ≈ 0.06s 一个周期，1 秒内应跑过多轮
            try:
                await asyncio.wait_for(sched._hot_loop(0.001), timeout=1.0)
            except asyncio.TimeoutError:
                pass

        asyncio.run(scenario())
        assert len(calls) >= 3  # 首轮 + 多个周期都跑到了：异常没有杀死循环

    def test_job_records_error_then_recovers(self, monkeypatch):
        """`_hot_job`：失败轮只落 last_error 不抛出；恢复轮清零并记录条数。"""
        seq = [RuntimeError("boom"), 3]

        def flaky():
            value = seq.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(sched, "_run_hot_once", flaky)

        async def scenario():
            await sched._hot_job()  # 失败：不向上抛
            assert sched.hot_status()["last_error"]
            await sched._hot_job()  # 恢复：错误清零、计数落账
            st = sched.hot_status()
            assert st["last_error"] is None
            assert st["last_count"] == 3
            assert st["last_at"]

        asyncio.run(scenario())


class TestHealthExposure:
    def test_health_exposes_hot_recompute(self):
        """`hotRecompute` 四键齐——抓取关停时这是热度任务仍活着的唯一信号。"""
        r = main.health()
        assert r.status_code == 200
        hot = json.loads(r.body)["data"]["hotRecompute"]
        assert set(hot) == {"enabled", "lastAt", "lastCount", "lastError"}
        assert isinstance(hot["enabled"], bool)
        assert isinstance(hot["lastCount"], int)

    def test_hot_status_returns_a_copy(self):
        """改快照不污染内部状态（health 每次调用都应拿到新副本）。"""
        snap = sched.hot_status()
        snap["last_at"] = "mutated"
        snap["last_count"] = 999
        assert sched.hot_status()["last_at"] != "mutated"
        assert sched.hot_status()["last_count"] != 999
