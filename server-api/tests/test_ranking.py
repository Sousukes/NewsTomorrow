"""P1-7：热度排序与个性化。

覆盖两条线：
1. **纯函数**（时间可注入，不依赖真实时钟）——热度分与衰减的计算正确性；
2. **接口行为**——三模式排序、置顶优先、游标续接、以及本次顺带修复的
   `comment_count` 计数维护缺陷。
"""
import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News  # noqa: E402
from app.ranking import compute_hot_score, recompute_hot_scores, time_decay  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, 0)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def _mk_news(
    nid: str,
    channel: str = "tech",
    hot: float = 0.0,
    published_at: str | None = None,
    is_top: bool = False,
    comments: int = 0,
) -> None:
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=f"标题-{nid}",
                summary="",
                source="测试",
                comment_count=comments,
                time="1小时前",
                channel=channel,
                variant="text",
                is_top=is_top,
                published_at=published_at,
                hot_score=hot,
            )
        )
        db.commit()


def _set_hot(nid: str, value: float) -> None:
    """直接写入 hot_score。

    注意：收藏/评论会触发 `bump_hot_score` 重算该条，覆盖测试预设值。
    因此凡是要精确控制分值的用例，都应在**建立互动之后**再调用本函数设定分数。
    """
    with SessionLocal() as db:
        n = db.query(News).filter(News.id == nid).first()
        n.hot_score = value
        db.commit()


def _set_anchor(nid: str, value: str | None) -> None:
    """直接写 `hot_score_at` 锚点，模拟「上一轮重算发生在何时」。"""
    with SessionLocal() as db:
        n = db.query(News).filter(News.id == nid).first()
        n.hot_score_at = value
        db.commit()


def _register(client, phone: str, name: str) -> dict:
    client.post(
        "/api/auth/register", json={"phone": phone, "password": "abc123", "name": name}
    )
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


# ---------------------------------------------------------------- 热度纯函数


class TestHotScore:
    def test_more_interaction_means_higher_score(self):
        base = dict(favorite_count=0, play_count=0, published_at=None, now=NOW)
        assert compute_hot_score(comment_count=10, **base) > compute_hot_score(
            comment_count=1, **base
        )
        assert compute_hot_score(comment_count=0, favorite_count=5, play_count=0,
                                 published_at=None, now=NOW) > compute_hot_score(
            comment_count=0, favorite_count=1, play_count=0, published_at=None, now=NOW
        )

    def test_comment_weighs_more_than_favorite(self):
        """评论是更强的互动信号：同数量下评论分更高。"""
        one_comment = compute_hot_score(1, 0, 0, None, NOW)
        one_fav = compute_hot_score(0, 1, 0, None, NOW)
        assert one_comment > one_fav

    def test_older_content_scores_lower(self):
        recent = "2026-10-13T11:00:00+00:00"
        old = "2026-10-01T11:00:00+00:00"
        assert compute_hot_score(5, 0, 0, recent, NOW) > compute_hot_score(5, 0, 0, old, NOW)

    def test_decay_is_one_at_zero_age(self):
        assert time_decay("2026-10-13T12:00:00+00:00", NOW) == 1.0

    def test_decay_halves_at_half_life(self):
        """48 小时半衰：恰好 48h 时为 0.5。"""
        at = (NOW - timedelta(hours=48)).isoformat()
        assert round(time_decay(at, NOW), 4) == 0.5

    def test_unparsable_published_at_uses_neutral_decay(self):
        """无法解析时不按"最新"优待（会霸榜），取中性值。"""
        assert time_decay("不是时间", NOW) == 0.5
        assert time_decay(None, NOW) == 0.5

    def test_future_published_at_is_not_amplified(self):
        """时钟偏移/脏数据产生未来时间时，视作最新即可，不能让衰减 > 1 放大分数。"""
        future = (NOW + timedelta(hours=10)).isoformat()
        assert time_decay(future, NOW) == 1.0

    def test_mixed_tz_aware_and_naive_do_not_raise(self):
        """回归：published_at 带时区而 now 为 naive 时曾抛 TypeError。"""
        aware = NOW.replace(tzinfo=timezone.utc).isoformat()
        naive = NOW.isoformat()
        assert compute_hot_score(1, 0, 0, aware, NOW) == compute_hot_score(1, 0, 0, naive, NOW)

    def test_zero_interaction_scores_zero(self):
        assert compute_hot_score(0, 0, 0, "2026-10-13T11:00:00", NOW) == 0.0


# ---------------------------------------------------------------- 排序模式


class TestSortModes:
    def test_latest_orders_by_published_at(self, client):
        _mk_news("old", published_at="2026-10-01T10:00:00")
        _mk_news("new", published_at="2026-10-13T10:00:00")
        ids = [n["id"] for n in client.get("/api/news?sort=latest").json()["data"]["list"]]
        assert ids.index("new") < ids.index("old")

    def test_hot_orders_by_score_not_time(self, client):
        """热度排序：分高的在前，即使发布时间更晚的分数更低。"""
        _mk_news("low", published_at="2026-10-13T11:00:00", hot=10.0)
        _mk_news("high", published_at="2026-10-01T11:00:00", hot=900.0)
        ids = [n["id"] for n in client.get("/api/news?sort=hot").json()["data"]["list"]]
        assert ids[0] == "high"

    def test_top_stays_first_even_in_hot(self, client):
        _mk_news("hot", hot=900.0)
        _mk_news("pinned", hot=1.0, is_top=True)
        ids = [n["id"] for n in client.get("/api/news?sort=hot").json()["data"]["list"]]
        assert ids[0] == "pinned"

    def test_invalid_sort_is_422(self, client):
        r = client.get("/api/news?sort=bogus")
        assert r.status_code == 422

    def test_response_echoes_sort(self, client):
        assert client.get("/api/news?sort=hot").json()["data"]["sort"] == "hot"

    def test_default_sort_is_latest(self, client):
        assert client.get("/api/news").json()["data"]["sort"] == "latest"


class TestPersonalization:
    def test_for_you_boosts_preferred_channel(self, client):
        """偏好频道的内容被提升，足以反超分数更高的非偏好内容。"""
        _mk_news("tech_item", channel="tech", hot=90.0, published_at="2026-10-13T11:00:00")
        _mk_news("finance_item", channel="finance", hot=100.0, published_at="2026-10-13T11:00:00")

        h = _register(client, "13940000001", "偏好科技")
        client.post("/api/me/favorites", json={"newsId": "tech_item"}, headers=h)
        # 收藏会 bump 重算 tech_item，故在其后重新设定分值以获得确定的比较基线
        _set_hot("tech_item", 90.0)
        _set_hot("finance_item", 100.0)

        hot_ids = [n["id"] for n in client.get("/api/news?sort=hot").json()["data"]["list"]]
        for_you = [
            n["id"]
            for n in client.get("/api/news?sort=for-you", headers=h).json()["data"]["list"]
        ]
        # 纯热度：finance(100) 在前；加偏好后：tech 反超
        assert hot_ids[0] == "finance_item"
        assert for_you[0] == "tech_item"

    def test_for_you_without_login_degrades_to_hot(self, client):
        """未登录无偏好数据 → 加分恒为 0，等价于纯热度排序（不报错、不空列表）。"""
        _mk_news("a", channel="tech", hot=90.0)
        _mk_news("b", channel="finance", hot=100.0)
        r = client.get("/api/news?sort=for-you")
        assert r.json()["code"] == 0
        assert [n["id"] for n in r.json()["data"]["list"]][0] == "b"

    def test_boost_cannot_beat_real_heat(self, client):
        """偏好只提升次序，不足以让零互动内容压过真正热门的内容。"""
        _mk_news("empty_but_preferred", channel="tech", hot=0.0)
        _mk_news("truly_hot", channel="finance", hot=5000.0)
        h = _register(client, "13940000002", "只看科技")
        client.post("/api/me/favorites", json={"newsId": "empty_but_preferred"}, headers=h)
        ids = [
            n["id"]
            for n in client.get("/api/news?sort=for-you", headers=h).json()["data"]["list"]
        ]
        assert ids[0] == "truly_hot"

    def test_history_also_builds_preference(self, client):
        """浏览历史同样是偏好信号（弱于收藏）。"""
        _mk_news("sports_item", channel="sports", hot=50.0, published_at="2026-10-13T11:00:00")
        _mk_news("other", channel="tech", hot=80.0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000003", "看体育")
        for _ in range(3):
            client.post("/api/me/history", json={"newsId": "sports_item"}, headers=h)
        ids = [
            n["id"]
            for n in client.get("/api/news?sort=for-you", headers=h).json()["data"]["list"]
        ]
        assert ids[0] == "sports_item"


# ---------------------------------------------------------------- 游标


class TestHotCursor:
    def test_paging_has_no_duplicates_or_gaps(self, client):
        for i in range(5):
            _mk_news(f"n{i}", hot=float(50 - i), published_at=f"2026-10-1{i}T10:00:00")

        seen, cursor, pages = [], None, 0
        while pages < 10:
            url = "/api/news?sort=hot&pageSize=2" + (f"&cursor={cursor}" if cursor else "")
            data = client.get(url).json()["data"]
            seen += [n["id"] for n in data["list"]]
            pages += 1
            if not data["hasMore"] or data["nextCursor"] is None:
                break
            cursor = data["nextCursor"]

        assert len(seen) == len(set(seen)), f"出现重复：{seen}"
        assert set(seen) == {f"n{i}" for i in range(5)}, f"出现遗漏：{seen}"

    def test_v1_cursor_still_pages(self, client):
        """升级前下发的 v1 游标仍可按 latest 续接。"""
        import base64
        import json

        for i in range(3):
            _mk_news(f"v{i}", published_at=f"2026-10-1{i}T10:00:00")
        raw = base64.urlsafe_b64encode(json.dumps([0, "2026-10-12T10:00:00", "v2"]).encode()).decode()
        r = client.get(f"/api/news?cursor={raw}")
        assert r.json()["code"] == 0

    def test_invalid_cursor_is_400(self, client):
        r = client.get("/api/news?sort=hot&cursor=not-a-cursor")
        assert r.status_code == 400


# ---------------------------------------------------------------- 互动 → 计数/热度


class TestInteractionFeedback:
    def test_comment_increments_count(self, client):
        """回归：`comment_count` 此前从未被维护，评论增删都不计数。"""
        _mk_news("c1", comments=0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000004", "评论者")
        before = client.get("/api/news/c1").json()["data"]["commentCount"]
        client.post("/api/news/c1/comments", json={"content": "第一条"}, headers=h)
        after = client.get("/api/news/c1").json()["data"]["commentCount"]
        assert after == before + 1

    def test_delete_comment_decrements_count(self, client):
        _mk_news("c2", comments=0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000005", "删评者")
        cid = client.post(
            "/api/news/c2/comments", json={"content": "待删"}, headers=h
        ).json()["data"]["id"]
        assert client.get("/api/news/c2").json()["data"]["commentCount"] == 1
        client.delete(f"/api/news/c2/comments/{cid}", headers=h)
        assert client.get("/api/news/c2").json()["data"]["commentCount"] == 0

    def test_count_never_goes_negative(self, client):
        """历史脏数据（计数为 0 却仍有评论）删除时不算成负数。"""
        _mk_news("c3", comments=0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000006", "边界")
        cid = client.post(
            "/api/news/c3/comments", json={"content": "x"}, headers=h
        ).json()["data"]["id"]
        client.delete(f"/api/news/c3/comments/{cid}", headers=h)
        # 再次删除走幂等分支，不应再减
        assert client.get("/api/news/c3").json()["data"]["commentCount"] == 0

    def test_favorite_raises_hot_score(self, client):
        _mk_news("f1", hot=0.0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000007", "收藏者")
        client.post("/api/me/favorites", json={"newsId": "f1"}, headers=h)
        with SessionLocal() as db:
            assert db.query(News).filter(News.id == "f1").first().hot_score > 0

    def test_unfavorite_lowers_hot_score(self, client):
        _mk_news("f2", hot=0.0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000008", "取消收藏")
        client.post("/api/me/favorites", json={"newsId": "f2"}, headers=h)
        with SessionLocal() as db:
            raised = db.query(News).filter(News.id == "f2").first().hot_score
        client.delete("/api/me/favorites/f2", headers=h)
        with SessionLocal() as db:
            assert db.query(News).filter(News.id == "f2").first().hot_score < raised


class TestRecompute:
    def test_recompute_sets_scores_and_returns_count(self, client):
        _mk_news("r1", comments=5, published_at="2026-10-13T11:00:00")
        _mk_news("r2", comments=0, published_at="2026-10-13T11:00:00")
        with SessionLocal() as db:
            n = recompute_hot_scores(db, now=NOW)
            assert n == 2
            scores = {x.id: x.hot_score for x in db.query(News).all()}
        # r2 零互动 → 分数为 0（不是"未计算"）；r1 有评论 → 正分
        assert scores["r1"] > 0
        assert scores["r2"] == 0.0

    def test_recompute_on_empty_table_is_safe(self, client):
        with SessionLocal() as db:
            assert recompute_hot_scores(db, now=NOW) == 0


# ------------------------------------------------- P1-21：增量重算与锚点


class TestIncrementalRecompute:
    """`only_stale=True` 只重算过期行；锚点 `hot_score_at` 随之落库/刷新。

    要防的两类回归：
    1. 增量逻辑失效退化成全表重写（写入放大回来）——用「未过期行
       分数与锚点都未被触动」钉住；
    2. 增量把算法也改了——同一 `now` 下分数必须与全量一致。
    """

    def test_only_stale_skips_fresh_rows(self, client):
        _mk_news("s1", comments=5, published_at="2026-10-13T11:00:00")
        _mk_news("s2", comments=3, published_at="2026-10-13T11:00:00")
        _mk_news("s3", comments=1, published_at="2026-10-13T11:00:00")
        with SessionLocal() as db:
            assert recompute_hot_scores(db, now=NOW) == 3  # 全量：一并补锚点
            fresh = {x.id: (x.hot_score, x.hot_score_at) for x in db.query(News).all()}
        # s2 锚点过期（90 分钟前 > 60 分钟窗口）、s3 锚点缺失、s1 刚算过
        _set_anchor("s2", (NOW - timedelta(minutes=90)).isoformat(timespec="seconds"))
        _set_anchor("s3", None)
        with SessionLocal() as db:
            n = recompute_hot_scores(db, now=NOW, only_stale=True, stale_minutes=60)
            assert n == 2
            after = {x.id: (x.hot_score, x.hot_score_at) for x in db.query(News).all()}
        # 未过期的行：分数和锚点都没被动过（否则就是全表重写）
        assert after["s1"] == fresh["s1"]
        # 过期 / 缺失的行：锚点刷新为本次重算时间（naive、秒精度，与 now_str 同格式）
        assert after["s2"][1] == NOW.isoformat(timespec="seconds")
        assert after["s3"][1] == NOW.isoformat(timespec="seconds")
        assert after["s2"][0] > 0 and after["s3"][0] > 0

    def test_row_inside_window_is_skipped(self, client):
        """ staleness 窗口内的行不重算——这是「每行每窗口最多写一次」的另一半。"""
        _mk_news("w1", comments=5, published_at="2026-10-13T11:00:00")
        _set_anchor("w1", (NOW - timedelta(minutes=30)).isoformat(timespec="seconds"))
        with SessionLocal() as db:
            assert recompute_hot_scores(db, now=NOW, only_stale=True, stale_minutes=60) == 0

    def test_full_mode_ignores_staleness(self, client):
        """`me.py` 批量合并收藏走全量重算：默认模式不看 staleness，行行都算。"""
        _mk_news("a1", comments=5, published_at="2026-10-13T11:00:00")
        with SessionLocal() as db:
            assert recompute_hot_scores(db, now=NOW) == 1
            assert recompute_hot_scores(db, now=NOW) == 1  # 锚点刚刷新，仍重算

    def test_incremental_score_matches_full(self, client):
        """增量只改「扫哪些行」，不改算法：同一 now 下分数与全量一致。"""
        _mk_news("m1", comments=7, published_at="2026-10-12T11:00:00")
        with SessionLocal() as db:
            recompute_hot_scores(db, now=NOW)
            full = db.query(News).filter(News.id == "m1").first().hot_score
        _set_anchor("m1", None)  # 打回「从未算过」
        with SessionLocal() as db:
            recompute_hot_scores(db, now=NOW, only_stale=True)
            inc = db.query(News).filter(News.id == "m1").first().hot_score
        assert inc == full

    def test_bump_refreshes_anchor(self, client):
        """互动 bump 立即重算并刷新锚点——否则刚互动的行会被下一轮增量重复扫到。"""
        _mk_news("b1", comments=0, published_at="2026-10-13T11:00:00")
        h = _register(client, "13940000009", "锚点")
        client.post("/api/news/b1/comments", json={"content": "触发 bump"}, headers=h)
        with SessionLocal() as db:
            n = db.query(News).filter(News.id == "b1").first()
            assert n.hot_score > 0
            assert n.hot_score_at is not None
            # 锚点就是「刚刚」：秒级 naive ISO 串，且距当前时间在分钟级以内
            at = datetime.fromisoformat(n.hot_score_at)
            assert at.tzinfo is None
            assert abs((datetime.now() - at).total_seconds()) < 60
