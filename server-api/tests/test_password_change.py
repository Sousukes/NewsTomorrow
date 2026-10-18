"""C4：管理员代重置密码 + 自助改密 + 强制改密的受限态。

三条主线：
1. **改密本身的两道证明**——`require_step_up`（令牌 + 密码）与 `oldPassword`，
   任缺其一都拒绝；改密后旧令牌全部失效（改密的语义就是作废旧凭证），
   同时调用方拿到**新的一对**，不被自己踢下线；
2. **受限态真的受限**——临时密码若能长期使用，「强制改密」就名存实亡。
   故受限账号发评论/收藏/改昵称/进管理端一律 40305，而**读**接口仍可用
   （锁死整个账号除了让人焦急，没有安全收益）；
3. **代重置的边界**——权限（二次校验）、不能重置自己、不泄露手机号是否注册、
   审计**不含明文密码**。

运行：
    cd server-api
    pytest tests/test_password_change.py -q
"""
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import AdminAuditLog, News, User, now_str  # noqa: E402
from app.routers import admin as admin_mod  # noqa: E402

ADMIN_PHONE = "13900000000"
VICTIM_PHONE = "13900000001"
PASSWORD = "c4-initial-pass"


def seed_news(news_id: str = "c4n1") -> str:
    """插一条真实资讯：收藏端点会先查资讯是否存在，缺它就只会拿到 404。"""
    with SessionLocal() as db:
        db.add(
            News(
                id=news_id,
                title="受限态测试用资讯",
                source="测试源",
                channel="tech",
                published_at=now_str(),
            )
        )
        db.commit()
    return news_id


@pytest.fixture(autouse=True)
def _admin_whitelist(monkeypatch):
    monkeypatch.setattr(admin_mod, "ADMIN_PHONES", [ADMIN_PHONE])


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def register(client, phone: str, password: str = PASSWORD, name: str = "用户") -> dict:
    r = client.post(
        "/api/auth/register", json={"phone": phone, "password": password, "name": name}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def login(client, phone: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def step_up(client, headers: dict, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/step-up", json={"password": password}, headers=headers)
    assert r.status_code == 200, r.text
    return {**headers, "X-Step-Up-Token": r.json()["data"]["stepUpToken"]}


def admin_headers(client) -> dict:
    register(client, ADMIN_PHONE, name="管理员")
    return auth(login(client, ADMIN_PHONE, PASSWORD)["token"])


def reset_password_of(client, admin_h: dict, phone: str) -> str:
    """管理员代重置，返回临时密码。"""
    r = client.post(
        f"/api/admin/users/{phone}/reset-password",
        headers=step_up(client, admin_h),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["tempPassword"]


class TestChangePassword:
    def test_requires_login(self, client):
        r = client.put("/api/auth/password", json={"oldPassword": PASSWORD, "newPassword": "x" * 10})
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_requires_step_up(self, client):
        """改密要过两道证明；先撞缺 step-up 的那一道。"""
        d = register(client, VICTIM_PHONE)
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": "brand-new-pass"},
            headers=auth(d["token"]),
        )
        assert r.status_code == 403
        assert r.json()["code"] == 40304

    def test_wrong_old_password(self, client):
        d = register(client, VICTIM_PHONE)
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": "not-it", "newPassword": "brand-new-pass"},
            headers=step_up(client, auth(d["token"])),
        )
        assert r.status_code == 401
        assert r.json()["code"] == 40101

    def test_same_password_rejected(self, client):
        d = register(client, VICTIM_PHONE)
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": PASSWORD},
            headers=step_up(client, auth(d["token"])),
        )
        assert r.status_code == 422
        assert r.json()["code"] == 42205

    def test_short_new_password_rejected(self, client):
        d = register(client, VICTIM_PHONE)
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": "12345"},
            headers=step_up(client, auth(d["token"])),
        )
        assert r.status_code == 422

    def test_success_revokes_old_tokens_and_returns_new_pair(self, client):
        """改密 = 作废旧凭证；但调用方不能被自己踢下线，所以下发新的一对。"""
        d = register(client, VICTIM_PHONE)
        old_headers = step_up(client, auth(d["token"]))
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": "brand-new-pass"},
            headers=old_headers,
        )
        assert r.status_code == 200, r.text
        new_pair = r.json()["data"]
        assert new_pair["token"] and new_pair["refreshToken"]

        # 旧 access 立即失效（token_version 自增）
        assert client.get("/api/me/favorites", headers=auth(d["token"])).status_code == 401
        # 新 access 可用
        assert client.get("/api/me/favorites", headers=auth(new_pair["token"])).status_code == 200
        # 旧密码不能再登录，新密码可以
        assert client.post(
            "/api/auth/login", json={"phone": VICTIM_PHONE, "password": PASSWORD}
        ).status_code == 401
        assert client.post(
            "/api/auth/login", json={"phone": VICTIM_PHONE, "password": "brand-new-pass"}
        ).status_code == 200

    def test_refresh_token_also_revoked(self, client):
        """改密后三类凭证一起作废：access、refresh、step-up。"""
        d = register(client, VICTIM_PHONE)
        client.put(
            "/api/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": "brand-new-pass"},
            headers=step_up(client, auth(d["token"])),
        )
        r = client.post("/api/auth/refresh", json={"refreshToken": d["refreshToken"]})
        assert r.status_code == 401


class TestRestrictedState:
    """受限态（must_change_password）必须真的拦得住写操作。"""

    @pytest.fixture
    def restricted(self, client):
        admin_h = admin_headers(client)
        register(client, VICTIM_PHONE)
        temp = reset_password_of(client, admin_h, VICTIM_PHONE)
        d = login(client, VICTIM_PHONE, temp)
        return admin_h, temp, d

    def test_login_response_carries_flag(self, restricted):
        _, _, d = restricted
        assert d["user"]["mustChangePassword"] is True

    def test_normal_login_has_flag_false(self, client):
        d = register(client, VICTIM_PHONE)
        assert d["user"]["mustChangePassword"] is False

    def test_write_actions_blocked(self, restricted, client):
        """写操作被拦，且拦在业务校验之前（否则会误报 404 内容不存在）。"""
        _, _, d = restricted
        h = auth(d["token"])
        seed_news()
        assert client.post("/api/me/favorites", json={"newsId": "c4n1"}, headers=h).json()[
            "code"
        ] == 40305
        assert client.post("/api/me/history", json={"newsId": "c4n1"}, headers=h).json()[
            "code"
        ] == 40305

    def test_favorites_blocked_but_readable(self, restricted, client):
        _, _, d = restricted
        h = auth(d["token"])
        assert client.get("/api/me/favorites", headers=h).status_code == 200
        assert client.post(
            "/api/me/favorites", json={"newsId": "n1"}, headers=h
        ).json()["code"] == 40305
        assert client.put("/api/me/nickname", json={"name": "新名"}, headers=h).json()[
            "code"
        ] == 40305

    def test_reading_news_still_works(self, restricted, client):
        _, _, d = restricted
        assert client.get("/api/news").status_code == 200

    def test_admin_access_blocked_while_restricted(self, restricted, client):
        """拿临时密码管后台 = 临时密码长期有效，「强制改密」名存实亡。"""
        admin_h, temp, d = restricted
        # 先把受害者加成管理员，再验证他进不去后台
        assert client.post(
            "/api/admin/users",
            json={"phone": VICTIM_PHONE, "note": "x"},
            headers=step_up(client, admin_h),
        ).status_code == 200
        # 注意：受限账号此刻的密码已经是**临时密码**（原密码已被作废）
        r = client.get("/api/admin/users", headers=step_up(client, auth(d["token"]), temp))
        assert r.status_code == 403
        assert r.json()["code"] == 40305

    def test_change_password_clears_restriction(self, restricted, client):
        _, temp, d = restricted
        r = client.put(
            "/api/auth/password",
            json={"oldPassword": temp, "newPassword": "brand-new-pass"},
            headers=step_up(client, auth(d["token"]), temp),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["user"]["mustChangePassword"] is False
        # 解除后写操作恢复（能收藏真的收藏成功，而不只是"不再被拦"）
        seed_news()
        h = auth(r.json()["data"]["token"])
        assert client.post("/api/me/favorites", json={"newsId": "c4n1"}, headers=h).json()[
            "code"
        ] == 0


class TestAdminReset:
    def test_non_admin_forbidden(self, client):
        register(client, VICTIM_PHONE)
        h = auth(login(client, VICTIM_PHONE, PASSWORD)["token"])
        r = client.post(f"/api/admin/users/{VICTIM_PHONE}/reset-password", headers=step_up(client, h))
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_requires_step_up(self, client):
        h = admin_headers(client)
        register(client, VICTIM_PHONE)
        r = client.post(f"/api/admin/users/{VICTIM_PHONE}/reset-password", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40304

    def test_cannot_reset_self(self, client):
        h = admin_headers(client)
        r = client.post(
            f"/api/admin/users/{ADMIN_PHONE}/reset-password", headers=step_up(client, h)
        )
        assert r.status_code == 400
        assert r.json()["code"] == 40007

    def test_unknown_phone_is_40400(self, client):
        """与公开接口同码：不能靠错误码区分「手机号是否已注册」。"""
        h = admin_headers(client)
        r = client.post(
            "/api/admin/users/13911111111/reset-password", headers=step_up(client, h)
        )
        assert r.status_code == 404
        assert r.json()["code"] == 40400

    def test_reset_revokes_old_sessions(self, client):
        h = admin_headers(client)
        d = register(client, VICTIM_PHONE)
        temp = reset_password_of(client, h, VICTIM_PHONE)
        # 旧 access 立即失效（密码换了就该作废旧凭证）
        assert client.get("/api/me/favorites", headers=auth(d["token"])).status_code == 401
        # 临时密码可登录，且被标记为待改密
        again = login(client, VICTIM_PHONE, temp)
        assert again["user"]["mustChangePassword"] is True

    def test_temp_password_is_readable_and_sane(self, client):
        """临时密码要能被口头/手写传达：长度够、无易混字符。"""
        from app.security import TEMP_PASSWORD_ALPHABET

        h = admin_headers(client)
        register(client, VICTIM_PHONE)
        temp = reset_password_of(client, h, VICTIM_PHONE)
        assert len(temp) == 12
        assert set(temp) <= set(TEMP_PASSWORD_ALPHABET)
        for ch in "01lIO":
            assert ch not in temp

    def test_audit_never_contains_plaintext(self, client):
        """审计表长期留存，写进明文等于给任何只读权限者发一份密码清单。"""
        h = admin_headers(client)
        register(client, VICTIM_PHONE)
        temp = reset_password_of(client, h, VICTIM_PHONE)
        with SessionLocal() as db:
            rows = db.query(AdminAuditLog).all()
            blob = " ".join((r.detail or "") + (r.action or "") for r in rows)
        assert temp not in blob
        assert any(r.action == "user.password.reset" for r in rows)

    def test_user_row_persisted(self, client):
        h = admin_headers(client)
        register(client, VICTIM_PHONE)
        reset_password_of(client, h, VICTIM_PHONE)
        with SessionLocal() as db:
            row = db.query(User).filter(User.phone == VICTIM_PHONE).one()
            assert row.must_change_password is True