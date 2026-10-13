"""P1-15：运营审计日志。

覆盖三条线：
1. **留痕**——源增/改/删/重置、管理员增删都写入 admin_audit_logs，且内容可读
   （改只记真正变化的字段，无变化不留痕）；
2. **可查**——时间线倒序、游标分页、按对象筛选；
3. **与业务同事务**——审计随业务一并提交（不做静默降级）。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import AdminAuditLog  # noqa: E402
from app.routers import admin as admin_mod  # noqa: E402

ADMIN_PHONE = "13900000000"


@pytest.fixture(autouse=True)
def _admin_whitelist(monkeypatch):
    """同 test_sources.py：直接改模块绑定，避免 os.environ 对已定稿的模块级常量无效。"""
    monkeypatch.setattr(admin_mod, "ADMIN_PHONES", [ADMIN_PHONE])


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def _admin(client) -> dict:
    client.post(
        "/api/auth/register",
        json={"phone": ADMIN_PHONE, "password": "abc123", "name": "管理员"},
    )
    r = client.post("/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _user(client, phone: str) -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": "路人"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _add_source(client, h, key: str, **over):
    body = {
        "key": key,
        "name": "自定义源",
        "channel": "tech",
        "url": "https://example.com/feed.xml",
        "weight": 5,
        "enabled": True,
    }
    body.update(over)
    return client.post("/api/admin/sources", headers=h, json=body)


# ---------------------------------------------------------------- 鉴权


class TestAuditAuth:
    def test_requires_login(self, client):
        assert client.get("/api/admin/audit").status_code == 401

    def test_non_admin_is_forbidden(self, client):
        h = _user(client, "13900000001")
        r = client.get("/api/admin/audit", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40301


# ---------------------------------------------------------------- 留痕


class TestAuditRecords:
    def test_create_source_recorded(self, client):
        h = _admin(client)
        _add_source(client, h, "audit_src")
        data = client.get("/api/admin/audit", headers=h).json()["data"]
        top = data["list"][0]
        assert top["action"] == "source.create"
        assert top["target"] == "audit_src"
        assert top["actorPhone"] == ADMIN_PHONE
        # 快照要能回答「加进来的时候长什么样」
        snap = top["detail"]["snapshot"]
        assert snap["url"] == "https://example.com/feed.xml"
        assert snap["weight"] == 5
        assert snap["enabled"] is True

    def test_update_records_only_changed_fields(self, client):
        """PATCH 常因表单整体提交而带上一堆未变字段，只记真正变化的才有可读性。"""
        h = _admin(client)
        _add_source(client, h, "audit_src")
        client.patch("/api/admin/sources/audit_src", headers=h, json={"weight": 3, "enabled": False})
        top = client.get("/api/admin/audit", headers=h).json()["data"]["list"][0]
        assert top["action"] == "source.update"
        assert top["detail"]["changes"] == {"weight": [5, 3], "enabled": [True, False]}

    def test_no_op_update_records_nothing(self, client):
        h = _admin(client)
        _add_source(client, h, "audit_src")
        before = client.get("/api/admin/audit", headers=h).json()["data"]["list"]
        client.patch("/api/admin/sources/audit_src", headers=h, json={"weight": 5})
        after = client.get("/api/admin/audit", headers=h).json()["data"]["list"]
        assert [a["id"] for a in after] == [a["id"] for a in before]  # 未新增痕迹

    def test_delete_keeps_snapshot(self, client):
        """删除后行就没了，快照必须在删之前取——否则事后无从知道删掉的是什么。"""
        h = _admin(client)
        _add_source(client, h, "audit_src")
        client.delete("/api/admin/sources/audit_src", headers=h)
        top = client.get("/api/admin/audit", headers=h).json()["data"]["list"][0]
        assert top["action"] == "source.delete"
        assert top["detail"]["snapshot"]["name"] == "自定义源"

    def test_reset_recorded(self, client):
        h = _admin(client)
        client.post("/api/admin/sources/reset", headers=h)
        top = client.get("/api/admin/audit", headers=h).json()["data"]["list"][0]
        assert top["action"] == "source.reset"
        assert top["target"] == ""
        assert top["detail"]["seeded"] > 0

    def test_admin_add_and_remove_recorded(self, client):
        h = _admin(client)
        client.post("/api/admin/users", headers=h, json={"phone": "13900000002", "note": "运营-小红"})
        client.delete("/api/admin/users/13900000002", headers=h)
        actions = [a["action"] for a in client.get("/api/admin/audit", headers=h).json()["data"]["list"]]
        targets = [a["target"] for a in client.get("/api/admin/audit", headers=h).json()["data"]["list"]]
        # 新的在前：移除在前、添加在后
        assert actions == ["admin.remove", "admin.add"]
        assert targets == ["13900000002", "13900000002"]

    def test_audit_is_committed_with_business(self, client):
        """同事务：业务成功就必须有痕（不允许「改了但没留痕」）。"""
        h = _admin(client)
        _add_source(client, h, "audit_src")
        with SessionLocal() as db:
            assert db.query(AdminAuditLog).count() == 1


# ---------------------------------------------------------------- 查询


class TestAuditQuery:
    def test_newest_first(self, client):
        h = _admin(client)
        for i in range(3):
            _add_source(client, h, f"s{i}")
        data = client.get("/api/admin/audit", headers=h).json()["data"]
        assert [a["target"] for a in data["list"]] == ["s2", "s1", "s0"]

    def test_cursor_pagination(self, client):
        """游标用自增 id：created_at 是秒级精度，同秒多条会让时间戳游标抖动。"""
        h = _admin(client)
        for i in range(5):
            _add_source(client, h, f"s{i}")
        page1 = client.get("/api/admin/audit?limit=2", headers=h).json()["data"]
        assert len(page1["list"]) == 2
        assert page1["hasMore"] is True
        assert page1["nextCursor"]
        page2 = client.get(
            f"/api/admin/audit?limit=2&cursor={page1['nextCursor']}", headers=h
        ).json()["data"]
        assert len(page2["list"]) == 2
        # 无重复、无遗漏
        seen = [a["target"] for a in page1["list"]] + [a["target"] for a in page2["list"]]
        assert seen == ["s4", "s3", "s2", "s1"]
        page3 = client.get(
            f"/api/admin/audit?limit=2&cursor={page2['nextCursor']}", headers=h
        ).json()["data"]
        assert [a["target"] for a in page3["list"]] == ["s0"]
        assert page3["hasMore"] is False
        assert page3["nextCursor"] is None

    def test_filter_by_target(self, client):
        h = _admin(client)
        _add_source(client, h, "keep")
        _add_source(client, h, "other")
        client.patch("/api/admin/sources/keep", headers=h, json={"weight": 1})
        data = client.get("/api/admin/audit?target=keep", headers=h).json()["data"]
        assert [a["action"] for a in data["list"]] == ["source.update", "source.create"]
        assert all(a["target"] == "keep" for a in data["list"])

    def test_invalid_cursor_is_400(self, client):
        h = _admin(client)
        r = client.get("/api/admin/audit?cursor=abc", headers=h)
        assert r.status_code == 400
        assert r.json()["code"] == 40003
