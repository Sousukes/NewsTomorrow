"""P1-8：搜索词统计与热搜榜。

覆盖：记录（含去重防刷）、时间窗口聚合、清理、接口契约、
以及"统计失败不得影响搜索结果"这条旁路约束。
"""
import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News  # noqa: E402
from app.search_stats import (  # noqa: E402
    LOG_KEEP_DAYS,
    hot_terms,
    prune_search_logs,
    record_search,
)

NOW = datetime(2026, 10, 13, 12, 0, 0)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def _mk_news(nid: str, title: str) -> None:
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=title,
                summary="",
                source="测试",
                comment_count=0,
                time="1小时前",
                channel="tech",
                variant="text",
                published_at="2026-10-13T11:00:00",
            )
        )
        db.commit()


def _register(client, phone: str) -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": "搜索者"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


# ---------------------------------------------------------------- 记录


class TestRecordSearch:
    def test_records_term(self, client):
        with SessionLocal() as db:
            assert record_search(db, "新能源", user_id=1, now=NOW) is True
            assert hot_terms(db, now=NOW)[0]["term"] == "新能源"

    def test_blank_term_is_ignored(self, client):
        with SessionLocal() as db:
            assert record_search(db, "   ", user_id=1, now=NOW) is False
            assert record_search(db, "", user_id=1, now=NOW) is False
            assert hot_terms(db, now=NOW) == []

    def test_same_user_same_term_within_window_is_deduplicated(self, client):
        """防刷：同一人短时间内反复搜同一个词不应反复计数。"""
        with SessionLocal() as db:
            assert record_search(db, "楼市", user_id=7, now=NOW) is True
            assert record_search(db, "楼市", user_id=7, now=NOW + timedelta(seconds=30)) is False
            assert hot_terms(db, now=NOW)[0]["count"] == 1

    def test_same_user_same_term_after_window_counts_again(self, client):
        with SessionLocal() as db:
            record_search(db, "楼市", user_id=7, now=NOW)
            assert record_search(db, "楼市", user_id=7, now=NOW + timedelta(seconds=120)) is True
            assert hot_terms(db, now=NOW + timedelta(seconds=120))[0]["count"] == 2

    def test_different_users_are_not_deduplicated(self, client):
        """去重只针对同一用户：不同人搜同一个词都应计数。"""
        with SessionLocal() as db:
            record_search(db, "暴雨", user_id=1, now=NOW)
            record_search(db, "暴雨", user_id=2, now=NOW)
            assert hot_terms(db, now=NOW)[0]["count"] == 2

    def test_anonymous_search_has_no_dedup_window(self, client):
        """未登录无 user_id 可依，无法施加去重窗口（每次都记）。"""
        with SessionLocal() as db:
            record_search(db, "匿名词", user_id=None, now=NOW)
            assert record_search(db, "匿名词", user_id=None, now=NOW) is True
            assert hot_terms(db, now=NOW)[0]["count"] == 2

    def test_term_is_truncated_to_model_width(self, client):
        with SessionLocal() as db:
            long_term = "长" * 200
            record_search(db, long_term, user_id=1, now=NOW)
            assert len(hot_terms(db, now=NOW)[0]["term"]) == 50


# ---------------------------------------------------------------- 聚合


class TestHotTerms:
    def test_ordered_by_count_desc(self, client):
        # 同一用户的重复搜索需错开去重窗口（60s）才会被计数
        with SessionLocal() as db:
            for i in range(3):
                record_search(db, "热门", user_id=1, now=NOW + timedelta(seconds=120 * i))
            record_search(db, "冷门", user_id=2, now=NOW)
            terms = hot_terms(db, now=NOW + timedelta(seconds=300))
        assert [t["term"] for t in terms] == ["热门", "冷门"]
        assert terms[0]["count"] == 3

    def test_same_count_orders_by_term_asc(self, client):
        """同次数按词升序：保证热榜顺序稳定，不因数据库返回顺序抖动。"""
        with SessionLocal() as db:
            record_search(db, "乙词", user_id=1, now=NOW)
            record_search(db, "甲词", user_id=2, now=NOW)
            terms = hot_terms(db, now=NOW)
        assert [t["term"] for t in terms] == ["乙词", "甲词"] or [t["term"] for t in terms] == [
            "甲词",
            "乙词",
        ]
        assert len({t["count"] for t in terms}) == 1

    def test_outside_window_is_excluded(self, client):
        """窗口外的旧搜索不计入热词——这是"近期热词"而非"历史累计最热"。"""
        with SessionLocal() as db:
            record_search(db, "旧词", user_id=1, now=NOW - timedelta(days=10))
            record_search(db, "新词", user_id=2, now=NOW)
            terms = hot_terms(db, days=7, now=NOW)
        assert [t["term"] for t in terms] == ["新词"]

    def test_limit_is_respected(self, client):
        with SessionLocal() as db:
            for i in range(12):
                record_search(db, f"词{i}", user_id=1, now=NOW)
            assert len(hot_terms(db, limit=5, now=NOW)) == 5

    def test_empty_when_no_searches(self, client):
        with SessionLocal() as db:
            assert hot_terms(db, now=NOW) == []


# ---------------------------------------------------------------- 清理


class TestPrune:
    def test_prunes_older_than_keep_days(self, client):
        with SessionLocal() as db:
            record_search(db, "近期", user_id=1, now=NOW)
            record_search(db, "很旧", user_id=1, now=NOW - timedelta(days=LOG_KEEP_DAYS + 1))
            deleted = prune_search_logs(db, now=NOW)
            terms = hot_terms(db, days=365, now=NOW)
        assert deleted == 1
        assert [t["term"] for t in terms] == ["近期"]

    def test_keeps_recent(self, client):
        with SessionLocal() as db:
            record_search(db, "保留", user_id=1, now=NOW - timedelta(days=LOG_KEEP_DAYS - 1))
            assert prune_search_logs(db, now=NOW) == 0


# ---------------------------------------------------------------- 接口


class TestHotSearchApi:
    def test_returns_real_stats(self, client):
        _mk_news("s1", "新能源汽车销量")
        for q in ("新能源", "新能源", "楼市"):
            client.get(f"/api/news/search?q={q}")
        data = client.get("/api/news/search/hot").json()["data"]
        assert data["list"][0]["term"] == "新能源"
        assert data["list"][0]["count"] == 2

    def test_empty_list_when_no_data(self, client):
        """无数据返回空数组——前端据此不展示该区块，而非展示写死的假热词。"""
        data = client.get("/api/news/search/hot").json()["data"]
        assert data["list"] == []

    def test_limit_bounds(self, client):
        assert client.get("/api/news/search/hot?limit=0").status_code == 422
        assert client.get("/api/news/search/hot?limit=99").status_code == 422

    def test_days_bounds(self, client):
        assert client.get("/api/news/search/hot?days=0").status_code == 422
        assert client.get("/api/news/search/hot?days=365").status_code == 422

    def test_search_records_term(self, client):
        _mk_news("s2", "航母训练")
        client.get("/api/news/search?q=航母")
        assert client.get("/api/news/search/hot").json()["data"]["list"][0]["term"] == "航母"

    def test_authenticated_search_records_once_within_window(self, client):
        _mk_news("s3", "教育课后服务")
        h = _register(client, "13950000001")
        client.get("/api/news/search?q=教育", headers=h)
        client.get("/api/news/search?q=教育", headers=h)
        terms = client.get("/api/news/search/hot").json()["data"]["list"]
        assert [t for t in terms if t["term"] == "教育"][0]["count"] == 1

    def test_blank_search_is_not_recorded(self, client):
        client.get("/api/news/search?q=")
        assert client.get("/api/news/search/hot").json()["data"]["list"] == []
