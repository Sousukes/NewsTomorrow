"""P1-72（E3 后端）：负反馈 + 曝光去重。

覆盖：
- 曝光：批量上报幂等（只刷新时间不累积行）、保留期过滤、清理；
- 负反馈：单条与来源两档、幂等、取消幂等、参数二选一校验（42207）、目标不存在；
- 排序效果：单条负反馈**对所有排序**生效；来源衰减只作用 for-you 且**不归零**；
- 曝光去重：只作用 for-you、latest/hot 不过滤；
- 探底保护：已曝光占比超阈值时放弃去重并回传 `dedupRelaxed: true`；
- 游标一致性：中途踩来源后翻页不重不漏（penalty 参与游标键）。

关于 `similarity()`：for-you 在 SQLite 上没有该函数，但 E3 的断言只依赖
`hot_score` 与权重间隔，不依赖相似度取值（与 `test_search_suggest.py` 同一处理：
PG 专属路径的绝对值由 `scripts/verify_search_ranking.py` 在真实 PG 上核对）。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.feedback import (  # noqa: E402
    SOURCE_DISLIKE_FACTOR,
    exposed_news_ids,
    prune_exposures,
    record_exposures,
    set_dislike,
)
from app.models import News, RssSource, User, UserDislike, UserExposure  # noqa: E402
from app.security import create_token  # noqa: E402


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


def _user(phone="13900000001") -> int:
    with SessionLocal() as db:
        u = User(phone=phone, name="甲", password_hash="x")
        db.add(u)
        db.commit()
        return u.id


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _source(key: str = "src_1", name: str = "新华社", enabled: bool = True) -> None:
    with SessionLocal() as db:
        db.add(
            RssSource(
                key=key,
                name=name,
                channel="recommend",
                url=f"https://example.com/{key}.xml",
                weight=5,
                enabled=enabled,
            )
        )
        db.commit()


def _news(
    nid: str,
    source_key: str | None = "src_1",
    source: str = "新华社",
    hot: float = 10.0,
    hidden: bool = False,
) -> None:
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=f"标题-{nid}",
                summary="",
                source=source,
                source_key=source_key,
                channel="recommend",
                hot_score=hot,
                published_at="2026-10-01T00:00:00+00:00",
                hidden=hidden,
            )
        )
        db.commit()


class TestExposures:
    def test_batch_report_is_idempotent(self, client):
        uid = _user()
        r = client.post("/api/me/exposures", json={"ids": ["n1", "n2"]}, headers=_auth(uid))
        assert r.status_code == 200
        assert r.json()["data"]["accepted"] == 2

        # 重复上报：不新增行，只刷新时间
        client.post("/api/me/exposures", json={"ids": ["n1"]}, headers=_auth(uid))
        with SessionLocal() as db:
            assert db.query(UserExposure).count() == 2

    def test_empty_batch_is_422(self, client):
        uid = _user()
        r = client.post("/api/me/exposures", json={"ids": []}, headers=_auth(uid))
        assert r.status_code == 422

    def test_over_limit_is_422(self, client):
        uid = _user()
        r = client.post(
            "/api/me/exposures", json={"ids": [f"n{i}" for i in range(51)]}, headers=_auth(uid)
        )
        assert r.status_code == 422, "上限要拒绝而不是静默截断（截断会让调用方以为全成功）"

    def test_requires_login(self, client):
        assert client.post("/api/me/exposures", json={"ids": ["n1"]}).status_code == 401

    def test_retention_window(self):
        uid = _user()
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            record_exposures(db, uid, ["n1"], now=now)
            # 造一条 30 天前的曝光（超出默认 7 天保留期）
            db.add(
                UserExposure(
                    user_id=uid,
                    news_id="old",
                    exposed_at=(now - timedelta(days=30)).isoformat(timespec="seconds"),
                )
            )
            db.commit()
            assert exposed_news_ids(db, uid) == {"n1"}, "超期曝光不该参与去重"

    def test_prune_keeps_dislikes(self):
        uid = _user()
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            record_exposures(db, uid, ["n1"], now=now - timedelta(days=30))
            set_dislike(db, uid, "news", "n9")
            deleted = prune_exposures(db)
            assert deleted == 1
            # 负反馈是明确表达，没有"过期"一说
            assert db.query(UserDislike).count() == 1


class TestDislikes:
    def test_news_dislike_roundtrip(self, client):
        uid = _user()
        _news("n1")
        r = client.post("/api/me/dislikes", json={"newsId": "n1"}, headers=_auth(uid))
        assert r.status_code == 200
        assert "n1" in r.json()["data"]["newsIds"]

        got = client.get("/api/me/dislikes", headers=_auth(uid)).json()["data"]
        assert "n1" in got["newsIds"]
        assert got["list"][0]["id"] == "n1"

        r2 = client.delete("/api/me/dislikes/news/n1", headers=_auth(uid))
        assert r2.status_code == 200
        assert r2.json()["data"]["newsIds"] == []

    def test_source_dislike_roundtrip(self, client):
        uid = _user()
        _source("src_1")
        r = client.post("/api/me/dislikes", json={"sourceKey": "src_1"}, headers=_auth(uid))
        assert r.status_code == 200
        assert r.json()["data"]["sourceKeys"] == ["src_1"]

        r2 = client.delete("/api/me/dislikes/sources/src_1", headers=_auth(uid))
        assert r2.json()["data"]["sourceKeys"] == []

    def test_both_and_neither_are_42207(self, client):
        uid = _user()
        _news("n1")
        _source("src_1")
        for body in ({"newsId": "n1", "sourceKey": "src_1"}, {}):
            r = client.post("/api/me/dislikes", json=body, headers=_auth(uid))
            assert r.status_code == 422
            assert r.json()["code"] == 42207, "二选一是意图判定，不能只报通用校验错"

    def test_missing_news_target_is_42207(self, client):
        uid = _user()
        r = client.post("/api/me/dislikes", json={"newsId": "gone"}, headers=_auth(uid))
        assert r.status_code == 422
        assert r.json()["code"] == 42207

    def test_disabled_source_target_is_42207(self, client):
        uid = _user()
        _source("src_off", "停用源", enabled=False)
        r = client.post("/api/me/dislikes", json={"sourceKey": "src_off"}, headers=_auth(uid))
        assert r.status_code == 422
        assert r.json()["code"] == 42207

    def test_unfollow_is_idempotent(self, client):
        uid = _user()
        _news("n1")
        client.post("/api/me/dislikes", json={"newsId": "n1"}, headers=_auth(uid))
        r1 = client.delete("/api/me/dislikes/news/n1", headers=_auth(uid))
        r2 = client.delete("/api/me/dislikes/news/n1", headers=_auth(uid))
        assert r1.status_code == r2.status_code == 200


class TestFeedFiltering:
    def test_disliked_news_filtered_from_every_sort(self, client):
        """单条负反馈是显式表达：latest 也不该再出现它。"""
        uid = _user()
        _news("n1", hot=100.0)
        _news("n2", hot=50.0)
        client.post("/api/me/dislikes", json={"newsId": "n1"}, headers=_auth(uid))

        for sort in ("latest", "hot", "for-you"):
            ids = [n["id"] for n in client.get(f"/api/news?sort={sort}", headers=_auth(uid)).json()["data"]["list"]]
            assert "n1" not in ids, f"{sort} 仍返回已踩内容"

    def test_exposure_dedup_only_in_for_you(self, client):
        uid = _user()
        _news("n1", hot=100.0)
        _news("n2", hot=50.0)
        client.post("/api/me/exposures", json={"ids": ["n1"]}, headers=_auth(uid))

        latest = [n["id"] for n in client.get("/api/news?sort=latest", headers=_auth(uid)).json()["data"]["list"]]
        assert "n1" in latest, "时间流必须完整（缺一条比重复一条更难解释）"

        for_you = [
            n["id"] for n in client.get("/api/news?sort=for-you", headers=_auth(uid)).json()["data"]["list"]
        ]
        assert "n1" not in for_you and "n2" in for_you

    def test_exposure_dedup_ignores_guests(self, client):
        """未登录没有账号可挂载曝光，服务端不猜（前端走本地匿名作用域）。"""
        _news("n1")
        ids = [n["id"] for n in client.get("/api/news?sort=for-you").json()["data"]["list"]]
        assert "n1" in ids

    def test_relax_when_exposure_ratio_too_high(self, client):
        """探底保护：几乎全看过了就放弃去重，别给小库用户一张空页。"""
        uid = _user()
        for i in range(4):
            _news(f"n{i}", hot=10.0 - i)
        client.post("/api/me/exposures", json={"ids": ["n0", "n1", "n2"]}, headers=_auth(uid))
        # 4 条可见里 3 条已曝光 = 75% > 60% → 放弃去重
        data = client.get("/api/news?sort=for-you", headers=_auth(uid)).json()["data"]
        assert data["dedupRelaxed"] is True
        assert [n["id"] for n in data["list"]][:1] == ["n0"]

    def test_dedup_relaxed_flag_false_normally(self, client):
        uid = _user()
        for i in range(10):
            _news(f"n{i}", hot=10.0 - i)
        client.post("/api/me/exposures", json={"ids": ["n0"]}, headers=_auth(uid))
        data = client.get("/api/news?sort=for-you", headers=_auth(uid)).json()["data"]
        assert data["dedupRelaxed"] is False
        assert "n0" not in [n["id"] for n in data["list"]]

    def test_source_dislike_decays_but_keeps_visible(self, client):
        """来源负反馈是衰减不是硬归零：该来源仍要出现（否则用户感知不到屏蔽了多少）。"""
        uid = _user()
        _source("src_a", "源A")
        _source("src_b", "源B")
        # 同分：衰减后源B 应仍可出现在结果里
        _news("na", "src_a", "源A", hot=10.0)
        _news("nb", "src_b", "源B", hot=10.0)
        client.post("/api/me/dislikes", json={"sourceKey": "src_b"}, headers=_auth(uid))

        ids = [n["id"] for n in client.get("/api/news?sort=for-you", headers=_auth(uid)).json()["data"]["list"]]
        assert ids[0] == "na", "被衰减的来源应排到同分的其他来源之后"
        assert "nb" in ids, f"衰减系数 {SOURCE_DISLIKE_FACTOR} 不该把来源整个抹掉"

    def test_source_dislike_not_applied_to_latest(self, client):
        """latest 是时间流，来源衰减不该介入（否则时间顺序被个人偏好打乱）。"""
        uid = _user()
        _source("src_b", "源B")
        _news("na", "src_a", "源A", hot=1.0)
        _news("nb", "src_b", "源B", hot=100.0)
        client.post("/api/me/dislikes", json={"sourceKey": "src_b"}, headers=_auth(uid))
        ids = [n["id"] for n in client.get("/api/news?sort=latest", headers=_auth(uid)).json()["data"]["list"]]
        assert ids[0] == "nb"


class TestCursorConsistency:
    def test_paging_consistent_with_stable_feedback(self, client):
        """惩罚集合不变时翻页：不重不漏（penalty 必须同时进游标键与 keyset 比较）。"""
        uid = _user()
        _source("src_a", "源A")
        _source("src_b", "源B")
        for i in range(6):
            src = "src_a" if i % 2 == 0 else "src_b"
            _news(f"n{i}", src, "源A" if src == "src_a" else "源B", hot=60.0 - i)

        h = _auth(uid)
        client.post("/api/me/dislikes", json={"sourceKey": "src_b"}, headers=h)

        seen: list[str] = []
        cursor = None
        for _ in range(4):
            url = "/api/news?sort=for-you&pageSize=3" + (f"&cursor={cursor}" if cursor else "")
            data = client.get(url, headers=h).json()["data"]
            seen += [n["id"] for n in data["list"]]
            cursor = data["nextCursor"]
            if not cursor:
                break

        assert len(seen) == len(set(seen)) == 6, f"翻页重/漏：{seen}"

    def test_paging_after_mid_session_dislike_keeps_everything(self, client):
        """中途踩来源 → 顺序必然重排，已展示项可能"掉"回游标之后。

        这不是缺陷：任何排序键在翻页途中变化都会如此。防线有两条——
        ① 游标键与 keyset 比较都含 penalty（同一边界，见上一个用例）；
        ② 前端 `loadMore` 按 id 去重（`stores/news.ts` 的 mergeUnique 同款逻辑）。
        此处断言的是**最终不漏**：去重后的并集仍覆盖全部内容。
        """
        uid = _user()
        _source("src_a", "源A")
        _source("src_b", "源B")
        for i in range(6):
            src = "src_a" if i % 2 == 0 else "src_b"
            _news(f"n{i}", src, "源A" if src == "src_a" else "源B", hot=60.0 - i)

        h = _auth(uid)
        p1 = client.get("/api/news?sort=for-you&pageSize=3", headers=h).json()["data"]
        first_ids = [n["id"] for n in p1["list"]]

        client.post("/api/me/dislikes", json={"sourceKey": "src_b"}, headers=h)

        # 继续翻到尾：重排后末项可能落到第三页，只翻一页会误判"漏项"
        collected: list[str] = []
        cursor = p1["nextCursor"]
        for _ in range(5):
            data = client.get(
                f"/api/news?sort=for-you&pageSize=3&cursor={cursor}", headers=h
            ).json()["data"]
            collected += [n["id"] for n in data["list"]]
            cursor = data["nextCursor"]
            if not cursor:
                break

        # 前端按 id 去重后的并集：必须覆盖全部 6 条（不允许漏）
        merged: list[str] = []
        for nid in first_ids + collected:
            if nid not in merged:
                merged.append(nid)
        assert sorted(merged) == [f"n{i}" for i in range(6)], f"翻页漏项：{merged}"
