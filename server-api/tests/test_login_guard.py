"""C2：登录失败锁定（账号维度）——与 IP 限流互补。

IP 限流（`main.py` 中间件，键 `login:<ip>`）防得住「一个 IP 猛刷」，
防不住「换 IP 打同一个账号」：每个 IP 都是首次访问，IP 维度干干净净，
而受害账号被分布式爆破。本文件测账号维度的失败计数与临时锁定。

环境注意：根 `conftest.py` 把 `RATE_LIMIT_ENABLED` 钉为 false（多数文件
要反复登录）。`app.config` 在**首次 import 时**读取环境变量，模块顶部
再写 `os.environ` 已无效（正是 conftest docstring 写明的坑）——故本文件
在 fixture 里 monkeypatch 运行期开关：`main.RATE_LIMIT_ENABLED`（中间件）
与 `login_guard.config.RATE_LIMIT_ENABLED`（锁定判定，动态读 config）。
"""
import time

import pytest
from fastapi.testclient import TestClient

import main
from app import login_guard
from app.database import Base, engine
from app.ratelimit import FixedWindowLimiter
from app.security import hash_password

# 专用测试号段：与 test_api 等文件错开，避免跨文件共享计数器
PHONE = "13800090001"
PHONE_GHOST = "13800090002"  # 从未注册——验证「不存在的账号同样计数」
PASSWORD = "123456"


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (phone, password_hash, name, created_at) VALUES (?,?,?,?)",
            (PHONE, hash_password(PASSWORD), "锁定测试用户", "2026-10-07"),
        )
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _guard_enabled(monkeypatch):
    """打开限流总开关，并给每个用例一个**全新的**失败计数器。

    全新计数器是关键：模块级 `_limiter` 是跨用例共享的，若复用，
    上一个用例锁定的手机号会把下一个用例锁死（900s 窗口）。
    同时把 IP 限流阈值调高——本文件一个用例要打十几次登录，
    默认 10 次/分的 IP 阈值会掺入 42901 噪声，干扰账号锁定断言。
    """
    monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(login_guard.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(main, "LOGIN_RATE_LIMIT_PER_MINUTE", 10000)
    fresh = FixedWindowLimiter(window_seconds=login_guard.config.LOGIN_FAIL_WINDOW_SECONDS)
    monkeypatch.setattr(login_guard, "_limiter", fresh)
    yield fresh


def _login(client, phone: str, password: str):
    return client.post("/api/auth/login", json={"phone": phone, "password": password})


class TestLoginLockout:
    def test_locks_after_five_consecutive_failures(self, client):
        """连续失败达上限后，**即使密码正确**也锁——锁定先于验密。"""
        for _ in range(5):
            r = _login(client, PHONE, "wrong")
            assert r.status_code == 401
            assert r.json()["code"] == 40101

        r = _login(client, PHONE, PASSWORD)  # 密码是对的，但账号已锁
        assert r.status_code == 429
        body = r.json()
        assert body["code"] == 42902
        assert "分钟" in body["message"]
        # Retry-After 必须活过异常处理器（header 透传，C2 踩过就不会再丢）
        assert r.headers.get("Retry-After") == str(login_guard.config.LOGIN_FAIL_WINDOW_SECONDS)

    def test_lock_covers_unknown_account_too(self, client):
        """不存在的账号同样计数——否则「同号有时 429 有时 401」
        就是账号枚举 oracle（响应差异本身就是信息）。"""
        for _ in range(5):
            assert _login(client, PHONE_GHOST, "wrong").status_code == 401
        r = _login(client, PHONE_GHOST, "whatever")
        assert r.status_code == 429
        assert r.json()["code"] == 42902

    def test_success_resets_failure_count(self, client):
        """成功登录即清零：失败计数只反映「连续」失败。

        不清零的话，4 次手滑 + 1 次成功 + 1 次手滑 = 6 次累计即锁，
        而用户刚刚证明过自己知道密码。
        """
        for _ in range(4):
            assert _login(client, PHONE, "wrong").status_code == 401
        assert _login(client, PHONE, PASSWORD).json()["code"] == 0
        for _ in range(4):
            assert _login(client, PHONE, "wrong").status_code == 401
        # 第 5 次「连续」失败——若计数未清零，这里应该已经被锁
        assert _login(client, PHONE, PASSWORD).json()["code"] == 0

    def test_lock_expires_after_window(self, client, monkeypatch):
        """窗口过后锁定自然解除（注入 1 秒窗口的计数器）。"""
        monkeypatch.setattr(login_guard, "_limiter", FixedWindowLimiter(window_seconds=1))
        for _ in range(5):
            assert _login(client, PHONE, "wrong").status_code == 401
        assert _login(client, PHONE, PASSWORD).status_code == 429

        time.sleep(1.1)
        assert _login(client, PHONE, PASSWORD).json()["code"] == 0

    def test_no_lock_when_rate_limit_disabled(self, client, monkeypatch):
        """总开关关闭时不锁定（测试环境的默认态，也是运维的手动刹车）。"""
        monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", False)
        monkeypatch.setattr(login_guard.config, "RATE_LIMIT_ENABLED", False)
        for _ in range(6):
            assert _login(client, PHONE, "wrong").status_code == 401
        assert _login(client, PHONE, PASSWORD).json()["code"] == 0

    def test_lock_does_not_block_other_accounts(self, client):
        """锁定按手机号隔离：A 被锁不影响 B（粒度是账号，不是全局）。"""
        for _ in range(5):
            _login(client, PHONE, "wrong")
        assert _login(client, PHONE, PASSWORD).status_code == 429
        # 另一个已注册账号不受影响
        assert _login(client, PHONE_GHOST, "x").status_code == 401  # 不存在 → 40101
        assert _login(client, "13800090003", "x").status_code == 401
