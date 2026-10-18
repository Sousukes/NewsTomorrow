"""E6 评论排序：`sort=latest`（默认）/ `sort=hot` + 游标携带 sort。

为什么值得单独一组测试：排序错了不会报错，只会「顺序看起来怪」——
而这恰恰是最难被发现、也最影响讨论区可用性的那类 bug。

重点验五件事：
1. `latest` 按 `created_at` 倒序，`hot` 按 `likes` 倒序；
2. 平手时用 `created_at` + `id` 兜底（顺序稳定，翻页不抖）；
3. **`likes` 为 NULL 视作 0**（历史行；否则 `likes < x` 会把零赞评论整体排除，
   表现为「翻到第二页，零赞评论凭空消失」）；
4. **游标携带 sort**：`hot` 游标去续 `latest` 必须作废（从第一页重来），
   而不是拿错位的键续出一页「看起来正常但缺几条」的数据；
5. v1 裸 id 游标仍兼容（只可能由 latest 产生）。

运行：
    cd server-api
    pytest tests/test_comment_sort.py -q
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
from app.models import Comment, News  # noqa: E402


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


def _seed(rows: list[dict]) -> None:
    """rows: `{id, likes, at, hidden, deleted}`；news 固定为 n1。"""
    with SessionLocal() as db:
        db.add(News(id="n1", title="标题", summary="", source="t"))
        for r in rows:
            db.add(
                Comment(
                    id=r["id"],
                    news_id="n1",
                    content=f"内容-{r['id']}",
                    seed="s",
                    created_at=r["at"],
                    likes=r.get("likes"),
                    hidden=r.get("hidden", False),
                    is_deleted=r.get("deleted", False),
                )
            )
        db.commit()


def _ids(client, **params) -> list[str]:
    r = client.get("/api/news/n1/comments", params=params)
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()["data"]["list"]]


def _page(client, **params) -> dict:
    r = client.get("/api/news/n1/comments", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# 三条主评论：c_old 最旧但赞最多，c_new 最新且零赞
_SEED = [
    {"id": "c_old", "likes": 9, "at": "2026-10-01T00:00:00+00:00"},
    {"id": "c_mid", "likes": 3, "at": "2026-10-02T00:00:00+00:00"},
    {"id": "c_new", "likes": 0, "at": "2026-10-03T00:00:00+00:00"},
]


class TestOrdering:
    def test_default_is_latest(self, client):
        """不传 sort = latest（老客户端行为不变，这条是兼容性的底线）。"""
        _seed(_SEED)
        assert _ids(client) == ["c_new", "c_mid", "c_old"]

    def test_hot_orders_by_likes(self, client):
        _seed(_SEED)
        assert _ids(client, sort="hot") == ["c_old", "c_mid", "c_new"]

    def test_hot_tie_breaks_by_time_then_id(self, client):
        """同赞数时新的在前；同赞同时间按 id 倒序兜底（顺序必须确定，否则翻页会抖）。"""
        _seed(
            [
                {"id": "a", "likes": 5, "at": "2026-10-01T00:00:00+00:00"},
                {"id": "b", "likes": 5, "at": "2026-10-02T00:00:00+00:00"},
                {"id": "c", "likes": 5, "at": "2026-10-02T00:00:00+00:00"},
            ]
        )
        assert _ids(client, sort="hot") == ["c", "b", "a"]

    def test_null_likes_counts_as_zero(self, client):
        """`likes` 允许为 NULL（历史行）：NULL 必须当 0，否则 `likes < x` 会把它整行排除。"""
        _seed(
            [
                {"id": "c_null", "likes": None, "at": "2026-10-01T00:00:00+00:00"},
                {"id": "c_one", "likes": 1, "at": "2026-10-02T00:00:00+00:00"},
            ]
        )
        assert _ids(client, sort="hot") == ["c_one", "c_null"]

    def test_hidden_and_deleted_are_filtered_in_both_sorts(self, client):
        _seed(
            [
                {"id": "c_ok", "likes": 1, "at": "2026-10-01T00:00:00+00:00"},
                {"id": "c_hid", "likes": 99, "at": "2026-10-02T00:00:00+00:00", "hidden": True},
                {"id": "c_del", "likes": 98, "at": "2026-10-03T00:00:00+00:00", "deleted": True},
            ]
        )
        assert _ids(client, sort="latest") == ["c_ok"]
        assert _ids(client, sort="hot") == ["c_ok"]

    def test_invalid_sort_is_42201(self, client):
        _seed(_SEED)
        r = client.get("/api/news/n1/comments", params={"sort": "bogus"})
        assert r.status_code == 422
        assert r.json()["code"] == 42201
        assert "排序方式不支持" in r.json()["message"]


class TestCursor:
    def test_latest_cursor_pages_without_overlap(self, client):
        _seed(_SEED)
        first = _page(client, limit=2)
        assert [c["id"] for c in first["list"]] == ["c_new", "c_mid"]
        assert first["hasMore"] is True
        second = _page(client, limit=2, cursor=first["nextCursor"])
        assert [c["id"] for c in second["list"]] == ["c_old"]
        assert second["hasMore"] is False
        assert second["nextCursor"] is None

    def test_hot_cursor_pages_without_overlap(self, client):
        _seed(_SEED)
        first = _page(client, limit=2, sort="hot")
        assert [c["id"] for c in first["list"]] == ["c_old", "c_mid"]
        second = _page(client, limit=2, sort="hot", cursor=first["nextCursor"])
        assert [c["id"] for c in second["list"]] == ["c_new"]

    def test_cursor_carries_sort(self, client):
        """游标对客户端不透明：解析后必须能看出它是哪个排序的键。"""
        import base64
        import json

        _seed(_SEED)
        cur = _page(client, limit=1, sort="hot")["nextCursor"]
        payload = json.loads(base64.urlsafe_b64decode(cur.encode()).decode())
        assert payload[0] == 2 and payload[1] == "hot"

    def test_hot_cursor_used_for_latest_starts_over(self, client):
        """跨排序误用游标 → 作废重来（第一页），而不是续出一页错位数据。"""
        _seed(_SEED)
        hot_cursor = _page(client, limit=1, sort="hot")["nextCursor"]
        got = _ids(client, sort="latest", cursor=hot_cursor)
        assert got == ["c_new", "c_mid", "c_old"], "必须退回第一页，不能错位续接"

    def test_legacy_id_cursor_still_works(self, client):
        """v1 裸 id 游标（P1-12 起的旧客户端）仍按 latest 语义续接。"""
        _seed(_SEED)
        assert _ids(client, limit=10, cursor="c_mid") == ["c_old"]

    def test_garbage_cursor_is_ignored(self, client):
        """损坏的游标不 500：当作没有游标，返回第一页。"""
        _seed(_SEED)
        assert _ids(client, cursor="!!!not-a-cursor!!!") == ["c_new", "c_mid", "c_old"]
