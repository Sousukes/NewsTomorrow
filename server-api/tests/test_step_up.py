"""C10：高敏操作二次校验（step-up）。

覆盖四类风险：
1. **端点本身是密码 oracle**：`/auth/step-up` 复用登录的账号维度锁定
   （scope=`stepup`），连续失败同样会被锁；且 scope 隔离——二次校验失败
   不该把用户的登录也锁掉。
2. **令牌被冒用**：step-up 令牌与 access/refresh 同源签发但类型不同，
   三者互不通用；登出（`token_version` 自增）同时吊销它。
3. **顺序泄露**：未授权者访问管理端点拿到的是 40301（权限不足）而不是
   40304（要二次校验），不向外透露站内有哪类操作。
4. **覆盖的端点真的挡住了**：缺令牌时 5 个高敏端点全部 40304。

运行：
    cd server-api
    pytest tests/test_step_up.py -q
"""
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
# 管理员白名单：被测的 admin 端点需要它
os.environ["ADMIN_PHONES"] = "13800138000"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app import config, login_guard  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.ratelimit import FixedWindowLimiter  # noqa: E402
from app.security import (  # noqa: E402
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_token,
    decode_token_full,
    hash_password,
)

PASSWORD = "step-up-pw"
PHONE = "13800138000"
OTHER_PHONE = "13900002222"


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (phone, password_hash, name, created_at) VALUES (?,?,?,?)",
            (PHONE, hash_password(PASSWORD), "二次校验用户", "2026-10-18T00:00:00+00:00"),
        )
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def limited(monkeypatch):
    """打开限流（根 conftest 钉的是 false），并注入全新计数器。

    与 `test_login_guard.py` 同款：模块级限流器跨测试文件共享，复用会把
    上一个用例的计数带进来；`app.config` 首次 import 即定稿，故只能
    monkeypatch **运行期**开关（main 与 login_guard 两处都要改）。
    """
    monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(login_guard.config, "RATE_LIMIT_ENABLED", True)
    # IP 限流阈值抬到不影响本用例（我们测的是账号维度）
    monkeypatch.setattr(main, "LOGIN_RATE_LIMIT_PER_MINUTE", 100000)
    login_guard._limiter = FixedWindowLimiter(config.LOGIN_FAIL_WINDOW_SECONDS)
    yield
    login_guard._limiter = FixedWindowLimiter(config.LOGIN_FAIL_WINDOW_SECONDS)


def login(client, phone: str = PHONE, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def headers_of(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def step_up(client, headers: dict, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/step-up", json={"password": password}, headers=headers)
    assert r.status_code == 200, r.text
    return {**headers, "X-Step-Up-Token": r.json()["data"]["stepUpToken"]}


class TestStepUpEndpoint:
    def test_requires_login(self, client):
        """已登录才谈得上二次校验：未登录 → 40100（不是 40304）。"""
        r = client.post("/api/auth/step-up", json={"password": PASSWORD})
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_success_issues_short_token(self, client):
        d = login(client)
        r = client.post("/api/auth/step-up", json={"password": PASSWORD}, headers=headers_of(d["token"]))
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["stepUpToken"]
        # 刻意很短：它是「此刻是本人」的证明，不是长期通行证
        assert body["expiresIn"] == config.STEP_UP_TOKEN_MINUTES * 60
        assert config.STEP_UP_TOKEN_MINUTES <= 10

    def test_wrong_password(self, client):
        d = login(client)
        r = client.post("/api/auth/step-up", json={"password": "wrong"}, headers=headers_of(d["token"]))
        assert r.status_code == 401
        # 与登录同文案：不透露「密码错」以外的任何信息
        assert r.json()["code"] == 40101

    def test_empty_password_rejected_by_schema(self, client):
        d = login(client)
        r = client.post("/api/auth/step-up", json={"password": ""}, headers=headers_of(d["token"]))
        assert r.status_code == 422


class TestLockout:
    def test_lockout_after_limit(self, client, limited):
        d = login(client)
        for _ in range(config.LOGIN_FAIL_LIMIT):
            r = client.post(
                "/api/auth/step-up", json={"password": "wrong"}, headers=headers_of(d["token"])
            )
            assert r.status_code == 401
        r = client.post(
            "/api/auth/step-up", json={"password": PASSWORD}, headers=headers_of(d["token"])
        )
        # 密码已经正确，但账号在 stepup scope 上被锁：先锁后验，与登录同序
        assert r.status_code == 429
        assert r.json()["code"] == 42902
        assert "二次校验" in r.json()["message"]

    def test_stepup_failures_do_not_lock_login(self, client, limited):
        """scope 隔离：二次校验失败不该把用户锁在登录之外（两件事）。"""
        d = login(client)
        for _ in range(config.LOGIN_FAIL_LIMIT):
            client.post("/api/auth/step-up", json={"password": "wrong"}, headers=headers_of(d["token"]))
        assert client.post(
            "/api/auth/login", json={"phone": PHONE, "password": PASSWORD}
        ).status_code == 200

    def test_login_failures_do_not_lock_stepup(self, client, limited):
        """反向隔离：登录被锁不影响已登录会话做二次校验。"""
        d = login(client)
        for _ in range(config.LOGIN_FAIL_LIMIT):
            client.post("/api/auth/login", json={"phone": PHONE, "password": "wrong"})
        r = client.post("/api/auth/step-up", json={"password": PASSWORD}, headers=headers_of(d["token"]))
        assert r.status_code == 200


class TestGuard:
    def test_missing_token_on_nickname(self, client):
        d = login(client)
        r = client.put("/api/me/nickname", json={"name": "新名字"}, headers=headers_of(d["token"]))
        assert r.status_code == 403
        assert r.json()["code"] == 40304

    def test_missing_token_on_admin_users(self, client):
        d = login(client)
        r = client.post(
            "/api/admin/users",
            json={"phone": OTHER_PHONE, "note": "测试"},
            headers=headers_of(d["token"]),
        )
        assert r.status_code == 403
        assert r.json()["code"] == 40304
        # 没有真的授权出去
        assert client.get("/api/admin/users", headers=headers_of(d["token"])).json()["data"]["total"] == 0

    def test_missing_token_on_source_ops(self, client):
        d = login(client)
        h = headers_of(d["token"])
        assert client.delete("/api/admin/sources/nope", headers=h).status_code == 403
        assert client.post("/api/admin/sources/reset", headers=h).status_code == 403

    def test_garbage_token(self, client):
        d = login(client)
        h = {**headers_of(d["token"]), "X-Step-Up-Token": "not-a-token"}
        assert client.put("/api/me/nickname", json={"name": "新名字"}, headers=h).json()["code"] == 40304

    def test_access_token_cannot_act_as_step_up(self, client):
        """类型隔离：access 令牌不能当二次校验凭证（与 C1 同款断言）。"""
        d = login(client)
        h = {**headers_of(d["token"]), "X-Step-Up-Token": d["token"]}
        assert client.put("/api/me/nickname", json={"name": "新名字"}, headers=h).json()["code"] == 40304

    def test_refresh_token_cannot_act_as_step_up(self, client):
        d = login(client)
        h = {**headers_of(d["token"]), "X-Step-Up-Token": d["refreshToken"]}
        assert client.put("/api/me/nickname", json={"name": "新名字"}, headers=h).json()["code"] == 40304

    def test_valid_token_unlocks_nickname(self, client):
        d = login(client)
        h = step_up(client, headers_of(d["token"]))
        assert client.put("/api/me/nickname", json={"name": "新名字"}, headers=h).json()["code"] == 0

    def test_valid_token_unlocks_admin_users(self, client):
        d = login(client)
        h = step_up(client, headers_of(d["token"]))
        r = client.post(
            "/api/admin/users", json={"phone": OTHER_PHONE, "note": "测试"}, headers=h
        )
        assert r.status_code == 200, r.text
        # 回收也要二次校验（同样敏感）
        assert (
            client.delete(f"/api/admin/users/{OTHER_PHONE}", headers=h).status_code == 200
        )

    def test_logout_invalidates_step_up(self, client):
        """登出自增 token_version → step-up 令牌同时失效（吊销不分类型）。"""
        d = login(client)
        h = step_up(client, headers_of(d["token"]))
        assert client.post("/api/auth/logout", headers=headers_of(d["token"])).json()["code"] == 0
        # 整个会话被吊销：旧 access 同样失效（401 而不是 40304）
        assert (
            client.put("/api/me/nickname", json={"name": "新名字"}, headers=h).status_code == 401
        )
        # 重新登录拿到**新** access，但旧 step-up 令牌仍然无效——它绑定的是
        # 旧 token_version，不是「看起来没过期」就行
        d2 = login(client)
        stale = {**headers_of(d2["token"]), "X-Step-Up-Token": h["X-Step-Up-Token"]}
        assert client.put("/api/me/nickname", json={"name": "新名字"}, headers=stale).json()[
            "code"
        ] == 40304

    def test_non_admin_gets_permission_error_not_step_up(self, client):
        """顺序：未授权者看到 40301，不被告知「这里还要二次校验」。"""
        other = login(client, phone=OTHER_PHONE, password=PASSWORD) if _register(client, OTHER_PHONE) else None
        assert other is not None
        h = step_up(client, headers_of(other["token"]))
        r = client.post("/api/admin/users", json={"phone": "13900003333", "note": "x"}, headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40301


def _register(client, phone: str) -> bool:
    r = client.post("/api/auth/register", json={"phone": phone, "password": PASSWORD})
    return r.status_code == 200
