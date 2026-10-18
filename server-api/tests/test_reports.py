"""E5 用户举报：核心逻辑 + 接口。

重点验五件事，其中前两条是「错了会出事」的：

1. **幂等去重**：同一用户对同一目标重复举报不新增行——这是达阈值自动处置
   防刷的地基（单用户最多贡献 1 次计数）；
2. **达阈值自动软隐藏**：去重后 pending 举报数 ≥ 阈值 → 目标 `hidden=True`，
   公开查询随之不可见；不足阈值**不**隐藏；
3. **目标不存在 / 理由非法**：分别 40409 / 42206，不落库；
4. **管理端处置**：`hidden` 确认隐藏、`kept` 驳回恢复，且写审计；
5. **鉴权**：举报必须登录（401），复核必须管理员（403）。

运行：
    cd server-api
    pytest tests/test_reports.py -q
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
os.environ["HOT_CRON_ENABLED"] = "false"
os.environ["ADMIN_PHONES"] = "13800138000"

import main  # noqa: E402
from app import config, reports  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Comment, News, Report, User, now_str  # noqa: E402


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _seed_user(phone="13900000001", admin=False) -> int:
    with SessionLocal() as db:
        u = User(phone=phone, name="甲", password_hash="x")
        db.add(u)
        db.commit()
        return u.id


def _seed_news(nid="n1", hidden=False) -> None:
    with SessionLocal() as db:
        db.add(News(id=nid, title="标题", summary="", source="t", hidden=hidden))
        db.commit()


def _seed_comment(cid="c1", hidden=False) -> None:
    with SessionLocal() as db:
        _seed_news()
        db.add(
            Comment(
                id=cid,
                news_id="n1",
                content="内容",
                seed="s",
                hidden=hidden,
                created_at=now_str(),
            )
        )
        db.commit()


def _login(client, phone="13900000001") -> dict:
    # 直接造 token：避免依赖登录接口的完整链路，聚焦举报本身
    from app.security import create_token

    uid = _seed_user(phone)
    token = create_token(uid)
    return {"Authorization": f"Bearer {token}"}


def _post_report(client, headers, target_type="news", target_id="n1", reason="色情低俗"):
    return client.post(
        "/api/reports",
        json={"targetType": target_type, "targetId": target_id, "reason": reason},
        headers=headers,
    )


class TestCreateReport:
    def test_requires_login(self, client):
        r = client.post(
            "/api/reports",
            json={"targetType": "news", "targetId": "n1", "reason": "色情低俗"},
        )
        assert r.status_code == 401

    def test_missing_target_is_40409(self, client):
        _seed_news()
        h = _login(client)
        r = _post_report(client, h, target_id="gone")
        assert r.status_code == 404
        assert r.json()["code"] == 40409

    def test_invalid_reason_is_42206(self, client):
        _seed_news()
        h = _login(client)
        r = _post_report(client, h, reason="不存在的理由")
        assert r.status_code == 422
        assert r.json()["code"] == 42206

    def test_invalid_target_type_is_42206(self, client):
        _seed_news()
        h = _login(client)
        r = _post_report(client, h, target_type="user")
        assert r.status_code == 422
        assert r.json()["code"] == 42206

    def test_report_lands(self, client):
        _seed_news()
        h = _login(client)
        r = _post_report(client, h)
        assert r.status_code == 200
        body = r.json()["data"]
        assert body == {"status": "pending", "autoHidden": False}
        with SessionLocal() as db:
            assert db.query(Report).count() == 1

    def test_idempotent_no_duplicate_row(self, client):
        """同一用户重复举报：更新 reason、不新增行（防刷地基）。"""
        _seed_news()
        h = _login(client)
        assert _post_report(client, h).status_code == 200
        assert _post_report(client, h, reason="广告营销").status_code == 200
        with SessionLocal() as db:
            rows = db.query(Report).all()
            assert len(rows) == 1
            assert rows[0].reason == "广告营销", "重复举报应更新 reason"


class TestAutoHide:
    def _n_reporters(self, client, n, target_type="news", target_id="n1"):
        for i in range(n):
            h = _login(client, phone=f"1390000000{i}")
            assert _post_report(client, h, target_type=target_type, target_id=target_id).status_code == 200

    def test_below_threshold_does_not_hide(self, client):
        _seed_news()
        self._n_reporters(client, config.REPORT_AUTO_HIDE_THRESHOLD - 1)
        with SessionLocal() as db:
            assert db.get(News, "n1").hidden is False

    def test_at_threshold_hides_news(self, client):
        _seed_news()
        self._n_reporters(client, config.REPORT_AUTO_HIDE_THRESHOLD)
        with SessionLocal() as db:
            n = db.get(News, "n1")
            assert n.hidden is True
            assert n.hidden_reason == "用户举报"

    def test_at_threshold_hides_comment(self, client):
        _seed_comment()
        self._n_reporters(client, config.REPORT_AUTO_HIDE_THRESHOLD, target_type="comment", target_id="c1")
        with SessionLocal() as db:
            assert db.get(Comment, "c1").hidden is True

    def test_duplicate_from_same_user_counts_once(self, client):
        """防刷核心：同一用户重复举报不能刷到阈值。"""
        _seed_news()
        h = _login(client)
        # 同一用户刷阈值次数，也不应触发自动隐藏
        for _ in range(config.REPORT_AUTO_HIDE_THRESHOLD):
            _post_report(client, h)
        with SessionLocal() as db:
            assert db.get(News, "n1").hidden is False

    def test_auto_hidden_flag_reported(self, client):
        _seed_news()
        self._n_reporters(client, config.REPORT_AUTO_HIDE_THRESHOLD - 1)
        h = _login(client, phone="13999999999")
        r = _post_report(client, h)
        assert r.json()["data"]["autoHidden"] is True


class TestAdminResolve:
    def _admin_headers(self, client) -> dict:
        from app.security import create_token

        with SessionLocal() as db:
            admin = User(phone="13800138000", name="管", password_hash="x")
            db.add(admin)
            db.commit()
            admin_id = admin.id  # 会话关闭前取出，避免 detached 访问触发 refresh
        token = create_token(admin_id)
        return {"Authorization": f"Bearer {token}"}

    def test_resolve_requires_admin(self, client):
        _seed_news()
        h = _login(client)
        _post_report(client, h)
        with SessionLocal() as db:
            rid = db.query(Report).first().id
        # 非管理员（普通用户）处置 → 403
        r = client.post(f"/api/admin/reports/{rid}/resolve", json={"resolution": "hidden"}, headers=h)
        assert r.status_code == 403

    def test_resolve_hidden_confirm_and_audit(self, client):
        _seed_news()
        h = _login(client)
        _post_report(client, h)
        with SessionLocal() as db:
            rid = db.query(Report).first().id
        ah = self._admin_headers(client)
        r = client.post(f"/api/admin/reports/{rid}/resolve", json={"resolution": "hidden"}, headers=ah)
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "resolved"
        with SessionLocal() as db:
            assert db.get(News, "n1").hidden is True
            from app.models import AdminAuditLog

            assert db.query(AdminAuditLog).filter(AdminAuditLog.action == "report.resolve").count() == 1

    def test_resolve_kept_restores_visibility(self, client):
        _seed_news()
        # 先达阈值自动隐藏
        for i in range(config.REPORT_AUTO_HIDE_THRESHOLD):
            h = _login(client, phone=f"1390000000{i}")
            _post_report(client, h)
        with SessionLocal() as db:
            assert db.get(News, "n1").hidden is True
            rid = db.query(Report).filter(Report.status == "pending").first().id
        ah = self._admin_headers(client)
        r = client.post(f"/api/admin/reports/{rid}/resolve", json={"resolution": "kept"}, headers=ah)
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "dismissed"
        with SessionLocal() as db:
            assert db.get(News, "n1").hidden is False, "驳回应恢复可见"

    def test_list_reports_default_pending(self, client):
        _seed_news()
        for i in range(2):
            h = _login(client, phone=f"1390000000{i}")
            _post_report(client, h)
        ah = self._admin_headers(client)
        r = client.get("/api/admin/reports", headers=ah)
        assert r.status_code == 200
        assert len(r.json()["data"]["list"]) == 2


class TestHiddenNewsFilteredFromPublic:
    def test_auto_hidden_news_disappears_from_list(self, client):
        """自动隐藏后，公开列表不再返回该资讯（软下架语义）。"""
        _seed_news()
        for i in range(config.REPORT_AUTO_HIDE_THRESHOLD):
            h = _login(client, phone=f"1390000000{i}")
            _post_report(client, h)
        r = client.get("/api/news?limit=50")
        ids = [n["id"] for n in r.json()["data"]["list"]]
        assert "n1" not in ids
