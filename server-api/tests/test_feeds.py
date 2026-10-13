"""批次 M7 测试：RSS 条件请求（304）与时效清理（SQL 侧）。

运行：
    cd server-api
    pytest -q
"""
import os
from datetime import datetime, timedelta, timezone

# 隔离环境（必须在导入 app 之前）
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-32bytes")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.feeds import fetcher  # noqa: E402
from app.feeds.fetcher import NotModified, fetch_source  # noqa: E402
from app.feeds.ingest import _fetch_with_retry, prune_old_news  # noqa: E402
from app.models import Comment, Message, News  # noqa: E402

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>标题一</title><link>http://e.com/1</link><description>正文</description>
  <pubDate>Mon, 07 Oct 2026 10:00:00 +0800</pubDate></item>
</channel></rss>
"""


class FakeResp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("GET", "http://x"), response=self
            )


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls: list[dict] = []

    def get(self, url, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return self.resp


class TestConditionalRequest:
    def setup_method(self):
        fetcher._CONDITIONAL_CACHE.clear()

    def test_304_raises_not_modified(self):
        client = FakeClient(FakeResp(304))
        with pytest.raises(NotModified):
            fetch_source(client, {"name": "s", "key": "k1", "url": "http://x/rss"}, limit=10)

    def test_validators_cached_and_sent(self):
        client = FakeClient(
            FakeResp(200, RSS_SAMPLE, {"etag": '"abc"', "last-modified": "Tue, 07 Oct 2026 10:00:00 GMT"})
        )
        src = {"name": "s", "key": "k2", "url": "http://x/rss", "channel": "hot"}
        items = fetch_source(client, src, limit=10)
        assert len(items) == 1

        # 第二次应带上条件头；源返回 304 → NotModified
        client.resp = FakeResp(304)
        with pytest.raises(NotModified):
            fetch_source(client, src, limit=10)
        assert client.calls[-1]["headers"].get("If-None-Match") == '"abc"'
        assert client.calls[-1]["headers"].get("If-Modified-Since")

    def test_retry_loop_does_not_retry_on_304(self):
        calls = {"n": 0}

        class C:
            def get(self, url, headers=None):
                calls["n"] += 1
                return FakeResp(304)

        out = _fetch_with_retry(C(), {"name": "s", "key": "k3", "url": "http://x"}, 10)
        assert out == []
        assert calls["n"] == 1  # 未变更不应重试


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


class TestPruneOldNews:
    def test_deletes_old_and_unknown_respecting_keep_min(self, db):
        for nid, pub in [
            ("old1", _iso(40)),
            ("old2", _iso(35)),
            ("new1", _iso(1)),
            ("unk1", None),
            ("unk2", ""),
        ]:
            db.add(News(id=nid, title=nid, published_at=pub))
        db.commit()

        # 总数 5，keep_min 2 → budget 3；未知时间优先淘汰
        assert prune_old_news(db, keep_days=30, keep_min=2) == 3
        left = {r[0] for r in db.execute(select(News.id))}
        assert len(left) == 2
        assert "new1" in left  # 新数据必须保留
        assert "unk1" not in left and "unk2" not in left  # 未知时间优先淘汰

    def test_noop_when_below_keep_min(self, db):
        db.add(News(id="a", title="a", published_at=_iso(99)))
        db.commit()
        assert prune_old_news(db, keep_days=30, keep_min=200) == 0
        assert db.execute(select(News.id)).scalars().all() == ["a"]

    def test_keeps_recent_rows_only(self, db):
        db.add(News(id="new", title="new", published_at=_iso(0.5)))
        for i in range(3):
            db.add(News(id=f"old{i}", title=f"old{i}", published_at=_iso(60 + i)))
        db.commit()
        removed = prune_old_news(db, keep_days=30, keep_min=1)
        assert removed == 3
        assert db.execute(select(News.id)).scalars().all() == ["new"]

    def test_prune_cleans_children(self, db):
        db.add(News(id="old", title="old", published_at=_iso(60)))
        db.add(News(id="new", title="new", published_at=_iso(1)))
        db.add(Comment(id="c1", news_id="old", content="x", created_at=_iso(60)))
        db.add(Message(id="m1", title="t", news_id="old"))
        db.commit()

        assert prune_old_news(db, keep_days=30, keep_min=1) == 1
        # 评论随资讯删除；消息保留但解除关联
        assert db.execute(select(Comment.id)).scalars().all() == []
        m = db.get(Message, "m1")
        assert m is not None and m.news_id is None
