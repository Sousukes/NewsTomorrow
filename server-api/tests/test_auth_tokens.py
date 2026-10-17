"""C1 令牌双轨：access（短）/ refresh（长、只能换 access）的类型隔离与续期。

审查报告 C1 的原话是「access token 有效期 7 天」，真正的风险不是"7 天"这个数字，
而是**只有一张令牌、且没有任何缩短手段**：一旦泄露（XSS / 日志 / 共享设备），
攻击窗口就是整整 7 天。拆分后 access 默认 30 分钟，refresh 7 天且**不能被业务
接口接受**——后者是本文件最重要的断言组：类型校验一旦失效，"短 access"就只是
把 30 分钟后的登录态换成了另一张永久令牌。

运行：
    cd server-api
    pytest tests/test_auth_tokens.py -q
"""
import os

import jwt
import pytest

# 必须在导入 app 之前切换数据库/配置（app.config 首次 import 即定稿）
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app import config  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.security import (  # noqa: E402
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_token,
    decode_token_full,
    hash_password,
)

PHONE = "13800138000"
PASSWORD = "123456"


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (phone, password_hash, name, created_at) VALUES (?,?,?,?)",
            (PHONE, hash_password(PASSWORD), "令牌测试用户", "2026-10-18T00:00:00+00:00"),
        )
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def login(client) -> dict:
    r = client.post("/api/auth/login", json={"phone": PHONE, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestTokenShape:
    """纯 security 层：类型与有效期。"""

    def test_default_type_is_access(self):
        """不传类型时仍签 access：老调用点（含测试）零改动。"""
        token = create_token(1, 0)
        assert decode_token_full(token) is not None
        assert decode_token_full(token, expect_type=TOKEN_TYPE_REFRESH) is None

    def test_refresh_rejected_as_access(self):
        token = create_token(1, 0, token_type=TOKEN_TYPE_REFRESH)
        assert decode_token_full(token) is None
        assert decode_token_full(token, expect_type=TOKEN_TYPE_REFRESH) is not None

    def test_legacy_token_without_typ_is_access(self):
        """迁移期兼容：没有 `typ` 的历史令牌按 access 处理（它们最多再活到自身过期）。"""
        payload = {"sub": "1", "ver": 0, "jti": "x", "iat": 0, "exp": 4_102_444_800}
        token = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)
        assert decode_token_full(token) is not None

    def test_lifetimes_access_shorter_than_refresh(self):
        """双轨的意义：access 必须**显著短于** refresh。"""
        assert config.JWT_ACCESS_EXPIRE_MINUTES < config.JWT_REFRESH_EXPIRE_MINUTES

        def span(token: str) -> int:
            p = jwt.decode(
                token, config.JWT_SECRET, algorithms=[config.JWT_ALG], options={"verify_exp": False}
            )
            return int(p["exp"]) - int(p["iat"])

        assert span(create_token(1, 0, token_type=TOKEN_TYPE_ACCESS)) == (
            config.JWT_ACCESS_EXPIRE_MINUTES * 60
        )
        assert span(create_token(1, 0, token_type=TOKEN_TYPE_REFRESH)) == (
            config.JWT_REFRESH_EXPIRE_MINUTES * 60
        )


class TestLoginIssuesPair:
    def test_login_returns_token_pair(self, client):
        d = login(client)
        assert d["token"]
        assert d["refreshToken"]
        # 两张令牌必须不同：相同的串意味着 refresh 分支根本没生效
        assert d["token"] != d["refreshToken"]
        # expiresIn 让前端**提前**续期，而不是等撞上 401
        assert d["expiresIn"] == config.JWT_ACCESS_EXPIRE_MINUTES * 60

    def test_register_returns_token_pair(self, client):
        r = client.post(
            "/api/auth/register", json={"phone": "13900002222", "password": "abc123"}
        )
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["token"] and d["refreshToken"]


class TestTypeIsolation:
    """最重要的一组：refresh 令牌不能当 access 用。"""

    def test_refresh_token_rejected_by_business_endpoint(self, client):
        d = login(client)
        r = client.get("/api/me/favorites", headers=bearer(d["refreshToken"]))
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_access_token_accepted_by_business_endpoint(self, client):
        """对照组：access 能过。没有这条，上一条的 401 可能只是接口本身坏了。"""
        d = login(client)
        assert client.get("/api/me/favorites", headers=bearer(d["token"])).status_code == 200

    def test_access_token_rejected_as_refresh(self, client):
        """把 access 塞进 refreshToken 字段同样拒绝：类型校验是双向的。"""
        d = login(client)
        r = client.post("/api/auth/refresh", json={"refreshToken": d["token"]})
        assert r.status_code == 401

    def test_garbage_refresh_token_rejected(self, client):
        r = client.post("/api/auth/refresh", json={"refreshToken": "not-a-token"})
        assert r.status_code == 401


class TestRefreshFlow:
    def test_refresh_with_refresh_token_issues_usable_access(self, client):
        d = login(client)
        r = client.post("/api/auth/refresh", json={"refreshToken": d["refreshToken"]})
        assert r.status_code == 200, r.text
        new_access = r.json()["data"]["token"]
        assert new_access and new_access != d["token"]
        # 关键：换出来的**是 access**（refresh 不能被业务接口接受，故能过即证明类型正确）
        assert client.get("/api/me/favorites", headers=bearer(new_access)).status_code == 200

    def test_refresh_rotates_refresh_token(self, client):
        """滑动续期：每次换发一对，活跃用户不会被 7 天硬登出。"""
        d = login(client)
        r = client.post("/api/auth/refresh", json={"refreshToken": d["refreshToken"]})
        assert r.status_code == 200
        assert r.json()["data"]["refreshToken"]
        # 新 refresh 可继续使用（未做一次性重放检测，见 README「已知取舍」）
        r2 = client.post("/api/auth/refresh", json={"refreshToken": r.json()["data"]["refreshToken"]})
        assert r2.status_code == 200

    def test_refresh_falls_back_to_access(self, client):
        """老客户端 / 契约脚本：只带 Authorization，不带 body。"""
        d = login(client)
        r = client.post("/api/auth/refresh", headers=bearer(d["token"]))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["token"]

    def test_refresh_requires_something(self, client):
        assert client.post("/api/auth/refresh").status_code == 401

    def test_expired_access_cannot_refresh(self, client):
        """过期的 access 换不出新令牌——这正是需要 refresh 令牌的原因。"""
        d = login(client)
        user_id, ver = decode_token_full(d["token"])
        expired = create_token(user_id, ver, token_type=TOKEN_TYPE_ACCESS, expires_minutes=-1)
        assert client.get("/api/me/favorites", headers=bearer(expired)).status_code == 401
        # 回退路径也走不通：refresh 令牌无效时**不**回退到 access
        assert client.post("/api/auth/refresh", headers=bearer(expired)).status_code == 401

    def test_expired_access_but_valid_refresh_still_renews(self, client):
        """真实场景：access 过期、refresh 仍有效 → 静默换发，用户不掉线。"""
        d = login(client)
        user_id, ver = decode_token_full(d["token"])
        expired = create_token(user_id, ver, token_type=TOKEN_TYPE_ACCESS, expires_minutes=-1)
        r = client.post("/api/auth/refresh", json={"refreshToken": d["refreshToken"]})
        assert r.status_code == 200
        assert client.get("/api/me/favorites", headers=bearer(r.json()["data"]["token"])).status_code == 200
        assert expired  # 仅为可读性保留：过期令牌在此仅作对照


class TestRevocation:
    def test_logout_revokes_both_tokens(self, client):
        """登出自增 token_version：两类令牌**同时**失效（吊销不分类型）。"""
        d = login(client)
        assert client.post("/api/auth/logout", headers=bearer(d["token"])).json()["code"] == 0
        assert client.get("/api/me/favorites", headers=bearer(d["token"])).status_code == 401
        r = client.post("/api/auth/refresh", json={"refreshToken": d["refreshToken"]})
        assert r.status_code == 401
