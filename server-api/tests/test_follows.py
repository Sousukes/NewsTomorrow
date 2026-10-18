"""E6b 关注来源：来源目录 + 关注关系 + 关注流。

重点验六件事，其中第 1 条是本设计存在的**唯一理由**：

1. **运营改名后关注仍然有效**——关注挂在 `rss_sources.key` 上，不是名称。
   若按名称关注，改名后关注流会静默变空，界面却仍显示「已关注」；
2. 关注/取关幂等（复合主键兜底），并返回**完整集合**（服务端即事实源）；
3. 来源目录只返回 `enabled` 源、只暴露四个字段（不含 `url`/健康状态）；
4. 关注流：只含已关注来源、过滤已下架、未登录 401、无关注返回空页而非 404；
5. 关注上限 200（42207）；
6. 源被删除后关注行保留但不出现在列表里（不连带丢历史）。

运行：
    cd server-api
    pytest tests/test_follows.py -q
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
os.environ["HOT_CRON_ENABLED"] = "false"

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News, RssSource, User, UserFollow  # noqa: E402


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


def _seed_source(key="s1", name="新华社", channel="recommend", enabled=True) -> None:
    with SessionLocal() as db:
        db.add(
            RssSource(
                key=key,
                name=name,
                channel=channel,
                url=f"https://example.com/{key}.xml",
                weight=5,
                enabled=enabled,
            )
        )
        db.commit()


def _seed_news(nid: str, source_key="s1", source="新华社", hidden=False, at="2026-10-01T00:00:00+00:00", top=False):
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=f"标题-{nid}",
                summary="",
                source=source,
                source_key=source_key,
                channel="recommend",
                published_at=at,
                is_top=top,
                hidden=hidden,
            )
        )
        db.commit()


def _login(phone="13900000001") -> dict:
    from app.security import create_token

    with SessionLocal() as db:
        u = User(phone=phone, name="甲", password_hash="x")
        db.add(u)
        db.commit()
        uid = u.id
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _user_id(phone="13900000001") -> int:
    with SessionLocal() as db:
        return db.query(User).filter(User.phone == phone).one().id


class TestSourceDirectory:
    def test_public_and_shape(self, client):
        _seed_source("s1", "新华社")
        _seed_source("s2", "停用源", enabled=False)
        _seed_news("n1", "s1")
        _seed_news("n2", "s1", hidden=True)

        r = client.get("/api/sources")
        assert r.status_code == 200
        items = r.json()["data"]["list"]
        assert [i["key"] for i in items] == ["s1"], "停用源不应出现在可关注列表"
        assert set(items[0]) == {"key", "name", "channel", "newsCount"}
        # 只算可见资讯：已下架不计入
        assert items[0]["newsCount"] == 1

    def test_channel_filter(self, client):
        _seed_source("s1", "科技源", channel="tech")
        _seed_source("s2", "体育源", channel="sports")
        keys = [i["key"] for i in client.get("/api/sources?channel=tech").json()["data"]["list"]]
        assert keys == ["s1"]


class TestFollows:
    def test_requires_login(self, client):
        assert client.get("/api/me/follows").status_code == 401
        assert client.post("/api/me/follows", json={"sourceKey": "s1"}).status_code == 401

    def test_follow_returns_full_set(self, client):
        _seed_source("s1", "新华社")
        h = _login()
        r = client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["keys"] == ["s1"]
        assert data["list"][0]["name"] == "新华社"
        assert data["list"][0]["enabled"] is True

    def test_follow_is_idempotent(self, client):
        _seed_source("s1", "新华社")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        with SessionLocal() as db:
            assert db.query(UserFollow).count() == 1

    def test_unfollow_is_idempotent(self, client):
        _seed_source("s1", "新华社")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        r1 = client.delete("/api/me/follows/s1", headers=h)
        r2 = client.delete("/api/me/follows/s1", headers=h)
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["data"]["keys"] == []

    def test_unknown_source_is_40410(self, client):
        h = _login()
        r = client.post("/api/me/follows", json={"sourceKey": "nope"}, headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40410

    def test_disabled_source_is_40410(self, client):
        """停用源与不存在同码：对外不暴露「它存在但被停用了」。"""
        _seed_source("s2", "停用源", enabled=False)
        h = _login()
        r = client.post("/api/me/follows", json={"sourceKey": "s2"}, headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40410

    def test_follow_limit(self, client):
        _seed_source("s1", "新华社")
        h = _login()
        uid = _user_id()
        with SessionLocal() as db:
            for i in range(200):
                db.add(UserFollow(user_id=uid, source_key=f"x{i}", created_at="2026-10-01T00:00:00+00:00"))
            db.commit()
        r = client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        assert r.status_code == 422
        assert r.json()["code"] == 42207

    def test_deleted_source_drops_from_list_but_row_survives(self, client):
        _seed_source("s1", "新华社")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        with SessionLocal() as db:
            db.delete(db.get(RssSource, "s1"))
            db.commit()
        assert client.get("/api/me/follows", headers=h).json()["data"]["keys"] == []
        with SessionLocal() as db:
            assert db.query(UserFollow).count() == 1, "源删除不该连带丢关注历史"


class TestFollowingFeed:
    def test_requires_login(self, client):
        r = client.get("/api/news?feed=following")
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_empty_follows_returns_empty_page(self, client):
        h = _login()
        data = client.get("/api/news?feed=following", headers=h).json()["data"]
        assert data["list"] == [] and data["hasMore"] is False and data["nextCursor"] is None

    def test_only_followed_sources(self, client):
        _seed_source("s1", "新华社")
        _seed_source("s2", "别的源")
        _seed_news("n1", "s1")
        _seed_news("n2", "s2")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        ids = [n["id"] for n in client.get("/api/news?feed=following", headers=h).json()["data"]["list"]]
        assert ids == ["n1"]

    def test_hidden_news_filtered(self, client):
        _seed_source("s1", "新华社")
        _seed_news("n1", "s1")
        _seed_news("n2", "s1", hidden=True)
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        ids = [n["id"] for n in client.get("/api/news?feed=following", headers=h).json()["data"]["list"]]
        assert ids == ["n1"]

    def test_keeps_sort_contract(self, client):
        """置顶优先 → published_at 倒序 → id 倒序（复用列表契约）。"""
        _seed_source("s1", "新华社")
        _seed_news("n_old", "s1", at="2026-10-01T00:00:00+00:00")
        _seed_news("n_new", "s1", at="2026-10-03T00:00:00+00:00")
        _seed_news("n_top", "s1", at="2026-09-01T00:00:00+00:00", top=True)
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        ids = [n["id"] for n in client.get("/api/news?feed=following", headers=h).json()["data"]["list"]]
        assert ids == ["n_top", "n_new", "n_old"]

    def test_channel_is_ignored(self, client):
        """前端从别的 tab 切过来会带着上一次的 channel：忽略而不是报错。"""
        _seed_source("s1", "新华社", channel="recommend")
        _seed_news("n1", "s1")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)
        r = client.get("/api/news?feed=following&channel=tech", headers=h)
        assert r.status_code == 200
        assert [n["id"] for n in r.json()["data"]["list"]] == ["n1"]

    def test_invalid_feed_is_422(self, client):
        r = client.get("/api/news?feed=bogus")
        assert r.status_code == 422
        assert r.json()["code"] == 42200

    def test_follow_survives_source_rename(self, client):
        """**本设计的核心验收**：改名后关注仍有效，关注流不空。

        关注挂在 `key` 上；`news.source` 只是名称快照，改名后旧行仍是旧名。
        若按名称关注，这里会拿到空列表——而界面上仍显示「已关注」。
        """
        _seed_source("s1", "人民网·时政")
        _seed_news("n1", "s1", source="人民网·时政")
        h = _login()
        client.post("/api/me/follows", json={"sourceKey": "s1"}, headers=h)

        with SessionLocal() as db:
            db.get(RssSource, "s1").name = "人民网·要闻"
            db.commit()

        # 改名后新抓的内容（source 是新名，source_key 不变）
        _seed_news("n2", "s1", source="人民网·要闻", at="2026-10-02T00:00:00+00:00")

        data = client.get("/api/news?feed=following", headers=h).json()["data"]
        assert [n["id"] for n in data["list"]] == ["n2", "n1"]
        # 列表里也带稳定标识，前端据此判断"已关注"
        assert all(n["sourceKey"] == "s1" for n in data["list"])
