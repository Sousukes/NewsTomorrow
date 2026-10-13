"""pytest：收藏 / 浏览历史云端同步（/api/me/*）。

运行：
    cd server-api
    pytest -q
"""
import os

import pytest

# 必须在导入 app 之前隔离环境
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-32bytes"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["TRUSTED_PROXIES"] = ""
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.security import hash_password  # noqa: E402

NEWS_COUNT = 60


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        for phone, name in (("13800138000", "甲用户"), ("13900139000", "乙用户")):
            conn.exec_driver_sql(
                "INSERT INTO users (phone, password_hash, name, created_at) VALUES (?,?,?,?)",
                (phone, hash_password("123456"), name, "2026-10-11"),
            )
        for i in range(NEWS_COUNT):
            nid = f"n{i:02d}"
            conn.exec_driver_sql(
                "INSERT INTO news (id,title,summary,source,comment_count,time,channel,variant,"
                "is_top,published_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nid, f"标题{i}", "摘要", "测试源", 0, "刚刚", "recommend", "text", 0,
                 "2026-10-11T10:00:00+00:00"),
            )

    with TestClient(main.app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def headers_for(client, phone: str) -> dict:
    r = client.post("/api/auth/login", json={"phone": phone, "password": "123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


@pytest.fixture(scope="module")
def h(client):
    """甲用户（13800138000）的鉴权头。"""
    return headers_for(client, "13800138000")


@pytest.fixture(scope="module")
def h2(client):
    """乙用户（13900139000）的鉴权头。"""
    return headers_for(client, "13900139000")


def ids_of(resp) -> list[str]:
    return resp.json()["data"]["ids"]


class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/me/favorites"),
            ("POST", "/api/me/favorites"),
            ("PUT", "/api/me/favorites"),
            ("GET", "/api/me/history"),
            ("POST", "/api/me/history"),
            ("DELETE", "/api/me/history"),
        ],
    )
    def test_requires_login(self, client, method, path):
        r = client.request(method, path, json={"newsId": "n00", "ids": []})
        assert r.status_code == 401, f"{method} {path} 应要求登录"


class TestFavorites:
    def test_add_list_remove(self, client, h):
        assert ids_of(client.get("/api/me/favorites", headers=h)) == []

        r = client.post("/api/me/favorites", json={"newsId": "n01"}, headers=h)
        assert r.json()["code"] == 0
        assert ids_of(r) == ["n01"]
        # 返回体带完整资讯对象，便于新设备直接渲染
        assert r.json()["data"]["list"][0]["id"] == "n01"

        client.post("/api/me/favorites", json={"newsId": "n02"}, headers=h)
        assert set(ids_of(client.get("/api/me/favorites", headers=h))) == {"n01", "n02"}

        r = client.delete("/api/me/favorites/n01", headers=h)
        assert ids_of(r) == ["n02"]

        # 幂等：重复删除仍成功且不报错
        assert client.delete("/api/me/favorites/n01", headers=h).json()["code"] == 0

    def test_add_is_idempotent(self, client, h):
        for _ in range(3):
            client.post("/api/me/favorites", json={"newsId": "n03"}, headers=h)
        assert ids_of(client.get("/api/me/favorites", headers=h)).count("n03") == 1

    def test_add_unknown_news_404(self, client, h):
        r = client.post("/api/me/favorites", json={"newsId": "does-not-exist"}, headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40400

    def test_merge_is_union_and_preserves_order(self, client, h):
        """PUT 为并集：只增不删；非法 id 静默跳过；顺序按传入先后（新的在前）。"""
        # 先清空，保证断言不受其它用例影响
        for nid in list(ids_of(client.get("/api/me/favorites", headers=h))):
            client.delete(f"/api/me/favorites/{nid}", headers=h)
        client.post("/api/me/favorites", json={"newsId": "n10"}, headers=h)

        merged = client.put(
            "/api/me/favorites",
            json={"ids": ["n11", "n10", "ghost-id", "n12"]},
            headers=h,
        )
        ids = ids_of(merged)
        assert set(ids) == {"n10", "n11", "n12"}  # ghost-id 被跳过，n10 不重复
        # 新并入的按传入顺序在前（n11 最早传入 → 时间最新）
        assert ids.index("n11") < ids.index("n12")
        assert ids.index("n12") < ids.index("n10")  # 云端已有条目排在并入之后

    def test_merge_does_not_delete_cloud_data(self, client, h):
        """本地为空时合并不得清空云端（这是"并集而非覆盖"的核心保证）。"""
        client.post("/api/me/favorites", json={"newsId": "n20"}, headers=h)
        r = client.put("/api/me/favorites", json={"ids": []}, headers=h)
        assert "n20" in ids_of(r)

    def test_isolated_between_users(self, client, h, h2):
        client.post("/api/me/favorites", json={"newsId": "n30"}, headers=h)
        assert "n30" not in ids_of(client.get("/api/me/favorites", headers=h2))


class TestHistory:
    def test_record_dedupes_and_moves_to_front(self, client, h):
        client.delete("/api/me/history", headers=h)
        client.post("/api/me/history", json={"newsId": "n01"}, headers=h)
        client.post("/api/me/history", json={"newsId": "n02"}, headers=h)
        assert set(ids_of(client.get("/api/me/history", headers=h))) == {"n01", "n02"}

        # 重复浏览 n01 只刷新时间，不产生第二行
        client.post("/api/me/history", json={"newsId": "n01"}, headers=h)
        ids = ids_of(client.get("/api/me/history", headers=h))
        assert ids.count("n01") == 1
        assert len(ids) == 2

    def test_record_unknown_news_is_silent(self, client, h):
        """被动副作用：资讯不存在时静默忽略，不向客户端抛错。"""
        before = ids_of(client.get("/api/me/history", headers=h))
        r = client.post("/api/me/history", json={"newsId": "ghost"}, headers=h)
        assert r.status_code == 200
        assert ids_of(r) == before

    def test_clear(self, client, h):
        client.post("/api/me/history", json={"newsId": "n05"}, headers=h)
        assert ids_of(client.delete("/api/me/history", headers=h)) == []
        assert ids_of(client.get("/api/me/history", headers=h)) == []

    def test_cap_at_limit(self, client, h):
        """超过上限时裁剪最旧记录，只保留最近 50 条。"""
        client.delete("/api/me/history", headers=h)
        # 传入 55 条（顺序即"由新到旧"），并入后应只留前 50 条
        payload = [f"n{i:02d}" for i in range(55)]
        r = client.put("/api/me/history", json={"ids": payload}, headers=h)
        ids = ids_of(r)
        assert len(ids) == 50
        assert ids[0] == "n00"
        assert "n54" not in ids  # 最旧的 5 条被裁掉

    def test_isolated_between_users(self, client, h, h2):
        client.delete("/api/me/history", headers=h2)
        client.post("/api/me/history", json={"newsId": "n07"}, headers=h)
        assert "n07" not in ids_of(client.get("/api/me/history", headers=h2))


class TestMarksCleanedWithNews:
    def test_prune_removes_marks(self, client, h):
        """资讯被时效清理时，其收藏/历史标记一并清除（SQLite 无外键级联，需显式清理）。"""
        from sqlalchemy.orm import sessionmaker

        from app.database import engine as app_engine
        from app.feeds.ingest import prune_old_news
        from app.models import News, UserFavorite, UserHistory

        client.post("/api/me/favorites", json={"newsId": "n40"}, headers=h)
        client.post("/api/me/history", json={"newsId": "n40"}, headers=h)

        Session = sessionmaker(bind=app_engine)
        db = Session()
        try:
            # 把 n40 的发布时间改到 60 天前，使其落入清理范围
            db.get(News, "n40").published_at = "2026-01-01T00:00:00+00:00"
            db.commit()
            prune_old_news(db, keep_days=30, keep_min=1)
            left_fav = db.query(UserFavorite).filter(UserFavorite.news_id == "n40").count()
            left_hist = db.query(UserHistory).filter(UserHistory.news_id == "n40").count()
        finally:
            db.close()

        assert left_fav == 0
        assert left_hist == 0
