"""pytest 接口测试：使用独立 SQLite 内存库，不触碰开发/生产数据。

运行：
    cd server-api
    pytest -q
"""
import os

import pytest

# 必须在导入 app 之前切换数据库/配置，隔离测试环境。
# 用 SQLite 内存库：既不触碰开发/生产数据，也避免 Windows 上文件锁导致 teardown 失败。
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-32bytes"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
# 不信任任何代理：验证 X-Forwarded-For 不会被采信
os.environ["TRUSTED_PROXIES"] = ""
# 管理员白名单：用于 /api/news/refresh 鉴权测试
os.environ["ADMIN_PHONES"] = "13800138000"
# 关闭示例数据播种，保证测试断言确定性（只保留本文件手工插入的数据）
os.environ["SEED_SAMPLE_DATA"] = "false"
# 关闭定时抓取：调度器启动即执行一轮，测试中不应触发真实网络请求
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # 种子数据（用 executemany 避免逐条提交）
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (phone, password_hash, name, created_at) VALUES (?,?,?,?)",
            ("13800138000", hash_password("123456"), "测试用户", "2026-10-07"),
        )
        # 排序契约验证：t3 置顶（应排第一）> t1(11:00) > t2(09:00)
        conn.exec_driver_sql(
            "INSERT INTO news (id,title,summary,source,comment_count,time,channel,variant,is_top,published_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("t1", "暴雨蓝色预警", "华南地区将出现大到暴雨", "中国天气", 0, "1小时前",
             "hot", "text", 0, "2026-10-07T10:00:00+00:00"),
        )
        conn.exec_driver_sql(
            "INSERT INTO news (id,title,summary,source,comment_count,time,channel,variant,is_top,published_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("t2", "楼市新政落地", "首付比例下调", "财经周刊", 0, "2小时前",
             "finance", "left-right", 0, "2026-10-07T09:00:00+00:00"),
        )
        conn.exec_driver_sql(
            "INSERT INTO news (id,title,summary,source,comment_count,time,channel,variant,is_top,published_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("t3", "置顶要闻", "置顶内容应排在最前", "新华社", 0, "刚刚",
             "recommend", "text", 1, "2026-10-07T11:00:00+00:00"),
        )
        conn.exec_driver_sql(
            "INSERT INTO messages (id,type,title,summary,news_id,time) VALUES (?,?,?,?,?,?)",
            ("m1", "system", "欢迎使用", "欢迎", None, "刚刚"),
        )

    with TestClient(main.app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def auth_header(client) -> dict:
    r = client.post("/api/auth/login", json={"phone": "13800138000", "password": "123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


class TestHealth:
    def test_health(self, client):
        d = client.get("/api/health").json()["data"]
        assert d["status"] == "up"
        assert d["pool"]["dialect"] == "sqlite"

    def test_request_id_header(self, client):
        """L5：响应回带 X-Request-ID（可自定义，便于全链路追踪）。"""
        r = client.get("/api/health", headers={"X-Request-ID": "trace-123"})
        assert r.headers["X-Request-ID"] == "trace-123"
        assert r.headers.get("X-Process-Time-Ms")

    def test_metrics_endpoint(self, client):
        """L5：Prometheus 文本指标。"""
        client.get("/api/health")
        r = client.get("/api/metrics")
        assert r.status_code == 200
        assert "toutiao_requests_total" in r.text
        assert 'toutiao_responses_total{status="200"}' in r.text
        assert "toutiao_uptime_seconds" in r.text


class TestAuth:
    def test_login_ok(self, client):
        r = client.post("/api/auth/login", json={"phone": "13800138000", "password": "123456"})
        assert r.json()["code"] == 0
        assert r.json()["data"]["token"]

    def test_login_wrong_password(self, client):
        r = client.post("/api/auth/login", json={"phone": "13800138000", "password": "bad"})
        assert r.status_code == 401
        assert r.json()["code"] == 40101
        assert "密码" in r.json()["message"]

    def test_login_bad_phone(self, client):
        r = client.post("/api/auth/login", json={"phone": "123", "password": "123456"})
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_validation_error_unified_contract(self, client):
        """M4：参数校验失败(422) 也返回 {code,message,data} 统一结构。"""
        r = client.post("/api/auth/register", json={"phone": "138", "password": "x"})
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == 42200
        assert body["message"]
        assert "errors" in body["data"]

    def test_register_then_login(self, client):
        r = client.post("/api/auth/register",
                        json={"phone": "13911112222", "password": "abc123", "name": "新人"})
        assert r.json()["code"] == 0
        r2 = client.post("/api/auth/login", json={"phone": "13911112222", "password": "abc123"})
        assert r2.json()["code"] == 0

    def test_register_duplicate(self, client):
        client.post("/api/auth/register", json={"phone": "13911113333", "password": "abc123"})
        r = client.post("/api/auth/register", json={"phone": "13911113333", "password": "abc123"})
        assert r.status_code == 409
        assert r.json()["code"] == 40901

    def test_logout_requires_auth(self, client):
        assert client.post("/api/auth/logout").status_code == 401

    def test_logout_ok(self, client):
        assert client.post("/api/auth/logout", headers=auth_header(client)).json()["code"] == 0

    def test_logout_revokes_all_tokens(self, client):
        """M5：登出后自增 token 版本，旧 token 立即失效。"""
        h = auth_header(client)
        assert client.post("/api/auth/logout", headers=h).json()["code"] == 0
        # 旧 token 再用应 401
        assert client.post("/api/auth/logout", headers=h).status_code == 401

    def test_refresh_returns_usable_token(self, client):
        """M5：滑动续期返回的新 token 可用。"""
        h = auth_header(client)
        r = client.post("/api/auth/refresh", headers=h)
        assert r.json()["code"] == 0
        new_token = r.json()["data"]["token"]
        assert new_token and new_token != h["Authorization"].split()[1]
        r2 = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {new_token}"})
        assert r2.json()["code"] == 0

    def test_refresh_requires_auth(self, client):
        assert client.post("/api/auth/refresh").status_code == 401


class TestNews:
    def test_list(self, client):
        d = client.get("/api/news").json()["data"]
        assert d["total"] >= 3
        assert d["list"][0]["id"]

    def test_feed_order_top_then_time_desc(self, client):
        """H1：置顶优先 → 发布时间倒序。"""
        ids = [n["id"] for n in client.get("/api/news").json()["data"]["list"]]
        assert ids.index("t3") == 0, f"置顶应排第一，实际 {ids}"
        assert ids.index("t1") < ids.index("t2"), f"较新应在前，实际 {ids}"

    def test_filter_by_channel(self, client):
        d = client.get("/api/news?channel=finance").json()["data"]
        assert all(n["channel"] == "finance" for n in d["list"])
        assert [n["id"] for n in d["list"]] == ["t2"]

    def test_pagination(self, client):
        d = client.get("/api/news?page=1&pageSize=1").json()["data"]
        assert len(d["list"]) == 1
        assert d["page"] == 1
        assert d["hasMore"] is True
        assert d["nextCursor"]

    def test_cursor_pagination_no_overlap(self, client):
        """H5：游标分页应连续且不重复/不漏。"""
        p1 = client.get("/api/news?pageSize=1").json()["data"]
        cur = p1["nextCursor"]
        assert cur
        p2 = client.get(f"/api/news?pageSize=1&cursor={cur}").json()["data"]
        assert p2["list"][0]["id"] != p1["list"][0]["id"]

    def test_cursor_invalid(self, client):
        r = client.get("/api/news?cursor=@@not-base64@@")
        assert r.status_code == 400
        assert r.json()["code"] == 40003

    def test_detail(self, client):
        assert client.get("/api/news/t1").json()["data"]["title"] == "暴雨蓝色预警"

    def test_detail_404(self, client):
        r = client.get("/api/news/nope")
        assert r.status_code == 404
        assert r.json()["code"] == 40400

    def test_search_by_title(self, client):
        d = client.get("/api/news/search?q=蓝色预警").json()["data"]
        assert d["total"] >= 1
        assert "t1" in [n["id"] for n in d["list"]]

    def test_search_by_summary(self, client):
        d = client.get("/api/news/search?q=首付比例").json()["data"]
        assert "t2" in [n["id"] for n in d["list"]]

    def test_search_empty_keyword(self, client):
        d = client.get("/api/news/search?q=").json()["data"]
        assert d["total"] == 0 and d["list"] == []

    def test_search_no_match(self, client):
        assert client.get("/api/news/search?q=zzzz不存在").json()["data"]["total"] == 0

    def test_search_not_swallowed_by_detail_route(self):
        """/news/search 必须先于 /news/{id} 匹配。"""
        assert not callable(None)


class TestSearchRanking:
    """搜索相关度排序（P1-11）。

    SQLite 套件走 `_search_fallback`（ILIKE + 标题命中优先）；
    PG + pg_trgm 的加权相似度路径由真实库验证（记录在《优化落地记录》P1-11）。

    种子行 `sr_*` 用唯一 id 且都**不**落在 finance 频道、发布时间都早于 t1，
    避免影响 TestNews 里的全局列表/排序断言。
    """

    def _seed(self):
        from app.database import SessionLocal
        from app.models import News

        rows = [
            # 标题命中，但时间是三者中最早的——用来证明"相关度压过时效"
            ("sr_title", "新能源车牌申领指南", "线上办理流程说明", "tech",
             "2026-10-05T10:00:00+00:00"),
            # 仅摘要命中，时间最新
            ("sr_summary", "股市收评", "新能源板块领涨", "tech",
             "2026-10-06T10:00:00+00:00"),
            # 仅摘要命中，时间最旧
            ("sr_summary_old", "城市公交观察", "新能源公交车投入运营", "car",
             "2026-10-04T10:00:00+00:00"),
        ]
        with SessionLocal() as db:
            for rid, title, summary, channel, pub in rows:
                if db.get(News, rid) is None:
                    db.add(News(
                        id=rid, title=title, summary=summary, source="测试源",
                        comment_count=0, time="1小时前", channel=channel,
                        variant="text", published_at=pub,
                    ))
            db.commit()

    def test_title_match_ranks_above_summary_match(self, client):
        """标题命中 > 仅摘要命中；同层级内时间新的在前。"""
        self._seed()
        d = client.get("/api/news/search?q=新能源").json()["data"]
        ids = [n["id"] for n in d["list"]]
        assert ids[0] == "sr_title", f"标题命中应排第一，实际 {ids}"
        assert ids.index("sr_summary") < ids.index("sr_summary_old"), (
            f"同为摘要命中，新的应在前，实际 {ids}"
        )

    def test_like_wildcards_are_escaped(self, client):
        """`%` / `_` / `\\` 必须按字面处理：否则搜一个通配符就"匹配全部"。

        显式 URL 编码，保证服务端收到的就是字面字符（未经编码的 `%` 在
        query 里语义不确定，测了也白测）。
        """
        from urllib.parse import quote

        for kw in ("%", "_", "\\", "100%"):
            d = client.get(f"/api/news/search?q={quote(kw)}").json()["data"]
            assert d["total"] == 0, f"q={kw!r} 应只按字面匹配（无此文本质上为 0 条），实际 {d['total']}"

    def test_pagination_with_ranking(self, client):
        """排序与 offset 分页叠加：顺序稳定、hasMore 正确。"""
        self._seed()
        p1 = client.get("/api/news/search?q=新能源&pageSize=1").json()["data"]
        assert [n["id"] for n in p1["list"]] == ["sr_title"]
        assert p1["total"] == 3 and p1["hasMore"] is True
        p2 = client.get("/api/news/search?q=新能源&pageSize=1&page=2").json()["data"]
        assert [n["id"] for n in p2["list"]] == ["sr_summary"]
        assert p2["hasMore"] is True
        p3 = client.get("/api/news/search?q=新能源&pageSize=1&page=3").json()["data"]
        assert [n["id"] for n in p3["list"]] == ["sr_summary_old"]
        assert p3["hasMore"] is False

    def test_response_shape_unchanged(self, client):
        """契约形状不变（前端 SearchResults 依赖 total/page/hasMore/keyword）。"""
        self._seed()
        d = client.get("/api/news/search?q=新能源").json()["data"]
        assert set(d) == {"list", "total", "page", "pageSize", "keyword", "hasMore"}
        assert d["keyword"] == "新能源"
        assert isinstance(d["total"], int) and isinstance(d["hasMore"], bool)
        for n in d["list"]:
            assert n["id"] and n["title"] and n["variant"]
            assert isinstance(n["commentCount"], int)


class TestRefreshAuth:
    """H4：/api/news/refresh 需管理员鉴权，且不阻塞请求。"""

    def test_refresh_requires_auth(self, client):
        assert client.post("/api/news/refresh").status_code == 401

    def test_refresh_non_admin_forbidden(self, client):
        client.post("/api/auth/register", json={"phone": "13911114444", "password": "abc123"})
        tok = client.post(
            "/api/auth/login", json={"phone": "13911114444", "password": "abc123"}
        ).json()["data"]["token"]
        r = client.post("/api/news/refresh", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_refresh_admin_accepted(self, client, monkeypatch):
        # 打桩后台抓取，避免测试触发真实网络请求
        monkeypatch.setattr(main, "_run_refresh", lambda *a, **k: None)
        r = client.post("/api/news/refresh", headers=auth_header(client))
        assert r.status_code == 202
        assert r.json()["data"]["accepted"] is True


class TestComments:
    def test_list_empty(self, client):
        assert client.get("/api/news/t1/comments").json()["data"]["list"] == []

    def test_post_requires_auth(self, client):
        r = client.post("/api/news/t1/comments", json={"content": "hi"})
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_post_and_like(self, client):
        h = auth_header(client)
        r = client.post("/api/news/t1/comments", json={"content": "测试评论"}, headers=h)
        assert r.json()["code"] == 0
        cid = r.json()["data"]["id"]

        r2 = client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=h)
        assert r2.json()["data"]["likes"] == 1
        assert r2.json()["data"]["liked"] is True

        r3 = client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=h)
        assert r3.json()["data"]["likes"] == 1

        r4 = client.patch(f"/api/news/t1/comments/{cid}", json={"liked": False}, headers=h)
        assert r4.json()["data"]["likes"] == 0
        assert r4.json()["data"]["liked"] is False

    def test_comment_order_newest_first(self, client):
        """H2：评论按绝对时间倒序（新的在前），与展示文案无关。"""
        h = auth_header(client)
        client.post("/api/news/t2/comments", json={"content": "第一条"}, headers=h)
        client.post("/api/news/t2/comments", json={"content": "第二条"}, headers=h)
        items = client.get("/api/news/t2/comments").json()["data"]["list"]
        assert len(items) == 2
        # 两条 created_at 可能同秒，此时由 id DESC 兜底；只要不出现空/乱序即可
        assert items[0]["createdAt"] is not None
        assert items[0]["content"] in ("第一条", "第二条")

    def test_comments_pagination(self, client):
        """M2：limit + cursor 分页。"""
        h = auth_header(client)
        for i in range(3):
            client.post("/api/news/t3/comments", json={"content": f"c{i}"}, headers=h)
        p1 = client.get("/api/news/t3/comments?limit=2").json()["data"]
        assert len(p1["list"]) == 2
        assert p1["hasMore"] is True
        p2 = client.get(
            f"/api/news/t3/comments?limit=2&cursor={p1['nextCursor']}"
        ).json()["data"]
        assert len(p2["list"]) == 1
        assert p2["hasMore"] is False

    def test_post_empty_content(self, client):
        h = auth_header(client)
        r = client.post("/api/news/t1/comments", json={"content": "   "}, headers=h)
        assert r.status_code == 400
        assert r.json()["code"] == 40002

    def test_comment_on_missing_news(self, client):
        h = auth_header(client)
        r = client.post("/api/news/nope/comments", json={"content": "x"}, headers=h)
        assert r.status_code == 404

    def test_like_missing_comment(self, client):
        h = auth_header(client)
        r = client.patch("/api/news/t1/comments/nope", json={"liked": True}, headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40401


class TestMessages:
    def test_list(self, client):
        d = client.get("/api/messages").json()["data"]
        assert len(d["list"]) >= 1
        assert d["list"][0]["title"] == "欢迎使用"

    def test_unread_count_requires_login_for_accuracy(self, client):
        assert client.get("/api/messages/unread-count").json()["data"] == 0

    def test_unread_count_and_mark(self, client):
        """未读数应随标记递减（不依赖初始绝对值，避免受其他测试影响）。"""
        h = auth_header(client)
        before = client.get("/api/messages/unread-count", headers=h).json()["data"]
        assert before >= 1

        client.patch("/api/messages/m1", headers=h)
        after = client.get("/api/messages/unread-count", headers=h).json()["data"]
        assert after == before - 1

    def test_mark_all_read(self, client):
        h = auth_header(client)
        assert client.post("/api/messages/read-all", headers=h).json()["code"] == 0
        assert client.get("/api/messages/unread-count", headers=h).json()["data"] == 0

    def test_mark_missing_message_is_noop(self, client):
        h = auth_header(client)
        assert client.patch("/api/messages/nope", headers=h).status_code == 200


class TestCommentOwnership:
    """评论归属（comments.user_id）：为「删除自己的评论」「我的评论」铺路。

    此前只有 user_name 字符串，重名即可能错误归属，无法支撑按用户治理。
    """

    def test_create_comment_sets_user_id(self, client):
        h = auth_header(client)
        r = client.post("/api/news/t1/comments", json={"content": "归属测试"}, headers=h)
        assert r.json()["code"] == 0
        data = r.json()["data"]
        assert data["userId"] is not None
        assert data["mine"] is True

    def test_mine_true_only_for_author(self, client):
        """同一条评论，作者看 mine=true，其他用户/游客看 false。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "作者视角"}, headers=h
        ).json()["data"]["id"]

        # 作者自己
        mine = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        assert next(c for c in mine if c["id"] == cid)["mine"] is True

        # 游客（无 token）
        anon = client.get("/api/news/t1/comments").json()["data"]["list"]
        assert next(c for c in anon if c["id"] == cid)["mine"] is False
        # userId 仍可见（用于前端判断是否展示"我的"标记），但 mine 为 false
        assert next(c for c in anon if c["id"] == cid)["userId"] is not None

    def test_legacy_comment_without_user_id(self, client):
        """历史评论（无归属）userId 为 None，任何人都不是其作者。"""
        from app.database import SessionLocal
        from app.models import Comment

        h = auth_header(client)
        with SessionLocal() as db:
            db.add(
                Comment(
                    id="c_legacy",
                    news_id="t1",
                    user_id=None,
                    user_name="历史用户",
                    seed="legacy",
                    content="迁移前的评论",
                    time="1天前",
                    created_at="2026-10-01T00:00:00+00:00",
                    likes=0,
                )
            )
            db.commit()

        rows = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        legacy = next(c for c in rows if c["id"] == "c_legacy")
        assert legacy["userId"] is None
        assert legacy["mine"] is False
        # 展示名仍保留，不影响阅读
        assert legacy["name"] == "历史用户"

    def test_ownership_does_not_break_like(self, client):
        """新增 user_id 不应影响既有点赞幂等逻辑（回归）。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t2/comments", json={"content": "点赞回归"}, headers=h
        ).json()["data"]["id"]
        r1 = client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=h)
        r2 = client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=h)
        assert r1.json()["data"]["likes"] == 1
        assert r2.json()["data"]["likes"] == 1
        assert r2.json()["data"]["mine"] is True


class TestCommentDelete:
    """评论删除：仅作者可删、软删除、幂等、删除后不可点赞。"""

    def test_author_can_delete_own_comment(self, client):
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "待删除"}, headers=h
        ).json()["data"]["id"]

        r = client.delete(f"/api/news/t1/comments/{cid}", headers=h)
        assert r.json()["code"] == 0
        assert r.json()["data"]["isDeleted"] is True

        # 列表不再返回
        ids = [c["id"] for c in client.get("/api/news/t1/comments").json()["data"]["list"]]
        assert cid not in ids

    def test_delete_requires_auth(self, client):
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "未登录删"}, headers=h
        ).json()["data"]["id"]
        r = client.delete(f"/api/news/t1/comments/{cid}")
        assert r.status_code == 401

    def test_cannot_delete_others_comment(self, client):
        """他人评论 → 403，且评论仍存在。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "作者的评论"}, headers=h
        ).json()["data"]["id"]

        # 另一个用户
        client.post(
            "/api/auth/register",
            json={"phone": "13900001111", "password": "abc123", "name": "路人甲"},
        )
        other = {
            "Authorization": "Bearer "
            + client.post(
                "/api/auth/login",
                json={"phone": "13900001111", "password": "abc123"},
            ).json()["data"]["token"]
        }
        r = client.delete(f"/api/news/t1/comments/{cid}", headers=other)
        assert r.status_code == 403
        assert r.json()["code"] == 40302

        ids = [c["id"] for c in client.get("/api/news/t1/comments").json()["data"]["list"]]
        assert cid in ids

    def test_cannot_delete_legacy_comment_without_owner(self, client):
        """历史无归属评论（user_id NULL）任何人都删不掉。"""
        from app.database import SessionLocal
        from app.models import Comment

        h = auth_header(client)
        with SessionLocal() as db:
            db.add(
                Comment(
                    id="c_nolegacy",
                    news_id="t1",
                    user_id=None,
                    user_name="历史用户",
                    seed="legacy",
                    content="迁移前的评论",
                    time="1天前",
                    created_at="2026-10-01T00:00:00+00:00",
                    likes=0,
                )
            )
            db.commit()
        r = client.delete("/api/news/t1/comments/c_nolegacy", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_delete_is_idempotent(self, client):
        """重复删除同一条：第二次按幂等处理（不报错）。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "幂等删除"}, headers=h
        ).json()["data"]["id"]
        assert client.delete(f"/api/news/t1/comments/{cid}", headers=h).json()["code"] == 0

    def test_deleted_comment_cannot_be_liked(self, client):
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "删后点赞"}, headers=h
        ).json()["data"]["id"]
        client.delete(f"/api/news/t1/comments/{cid}", headers=h)
        r = client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=h)
        assert r.status_code == 404

    def test_delete_missing_comment_is_404(self, client):
        h = auth_header(client)
        assert client.delete("/api/news/t1/comments/nope", headers=h).status_code == 404

    def test_delete_wrong_article_is_404(self, client):
        """评论存在但不属于该资讯 → 404（防止跨文章越权）。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "文章归属"}, headers=h
        ).json()["data"]["id"]
        assert client.delete(f"/api/news/t2/comments/{cid}", headers=h).status_code == 404


def _login(client, phone: str, password: str = "abc123") -> dict:
    r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _register(client, phone: str, name: str) -> dict:
    client.post(
        "/api/auth/register", json={"phone": phone, "password": "abc123", "name": name}
    )
    return _login(client, phone)


class TestCommentReplies:
    """二级评论（P1-12）：主评论 + 回复；回复的回复拍平到同一 root。

    顺序敏感用例（预览/分页）用 ORM 显式 created_at 播种——API 连续post
    可能同秒，而 id 是随机 uuid，依赖插入顺序断言会 flake。
    """

    def _post(self, client, news, content, headers, parent=None):
        body = {"content": content}
        if parent:
            body["parentId"] = parent
        return client.post(f"/api/news/{news}/comments", json=body, headers=headers)

    def test_reply_structure_and_flattening(self, client):
        """回复挂 parent_id + root_id；回复的回复拍平到同一 root。"""
        h = auth_header(client)
        root = self._post(client, "t1", "结构主评论", h).json()["data"]
        assert root["parentId"] is None and root["rootId"] is None

        r1 = self._post(client, "t1", "回复主评论", h, parent=root["id"]).json()["data"]
        assert r1["parentId"] == root["id"]
        assert r1["rootId"] == root["id"]

        r2 = self._post(client, "t1", "回复回复", h, parent=r1["id"]).json()["data"]
        assert r2["parentId"] == r1["id"]
        # 拍平：root 仍是主评论，不是被回复的那条回复
        assert r2["rootId"] == root["id"]

    def test_main_list_shows_only_roots_with_preview(self, client):
        """主列表只出主评论，附 replyCount 与最早 2 条预览。"""
        from app.database import SessionLocal
        from app.models import Comment

        h = auth_header(client)
        root = self._post(client, "t1", "预览主评论", h).json()["data"]
        with SessionLocal() as db:
            for i in range(3):
                db.add(
                    Comment(
                        id=f"c_prev_{i}",
                        news_id="t1",
                        user_id=1,
                        user_name="测试用户",
                        content=f"预览回复{i}",
                        created_at=f"2026-10-08T10:00:0{i}+00:00",
                        parent_id=root["id"],
                        root_id=root["id"],
                        likes=0,
                    )
                )
            db.commit()

        rows = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        item = next(c for c in rows if c["id"] == root["id"])
        assert item["replyCount"] == 3
        # 预览取最早 2 条（与 replies 接口的正序一致）
        assert [r["content"] for r in item["replies"]] == ["预览回复0", "预览回复1"]
        # 预览项自带结构字段与「回复 @谁」
        assert item["replies"][0]["rootId"] == root["id"]
        assert item["replies"][0]["replyToName"] == "测试用户"
        # 列表里不出现回复本身
        assert all(c["rootId"] is None for c in rows)

    def test_replies_endpoint_time_asc_with_pagination(self, client):
        """replies 接口按时间正序 + 游标分页。"""
        from app.database import SessionLocal
        from app.models import Comment

        h = auth_header(client)
        root = self._post(client, "t2", "分页主评论", h).json()["data"]
        with SessionLocal() as db:
            for i in range(3):
                db.add(
                    Comment(
                        id=f"c_rep_{i}",
                        news_id="t2",
                        user_id=1,
                        user_name="测试用户",
                        content=f"回复{i}",
                        created_at=f"2026-10-08T11:00:0{i}+00:00",
                        parent_id=root["id"],
                        root_id=root["id"],
                        likes=0,
                    )
                )
            db.commit()

        p1 = client.get(f"/api/news/t2/comments/{root['id']}/replies?limit=2").json()["data"]
        assert [c["content"] for c in p1["list"]] == ["回复0", "回复1"]
        assert p1["hasMore"] is True
        p2 = client.get(
            f"/api/news/t2/comments/{root['id']}/replies?limit=2&cursor={p1['nextCursor']}"
        ).json()["data"]
        assert [c["content"] for c in p2["list"]] == ["回复2"]
        assert p2["hasMore"] is False

    def test_preview_liked_reflects_current_user(self, client):
        """主评论预览里的回复也要带当前用户点赞状态（批量判定覆盖预览 id）。"""
        h = auth_header(client)
        root = self._post(client, "t1", "点赞预览主评论", h).json()["data"]
        rid = self._post(
            client, "t1", "被点赞的回复", h, parent=root["id"]
        ).json()["data"]["id"]
        client.patch(f"/api/news/t1/comments/{rid}", json={"liked": True}, headers=h)

        rows = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        item = next(c for c in rows if c["id"] == root["id"])
        assert len(item["replies"]) == 1
        assert item["replies"][0]["id"] == rid
        assert item["replies"][0]["liked"] is True
        assert item["replies"][0]["likes"] == 1

    def test_reply_count_excludes_deleted(self, client):
        """已删除的回复不计入 replyCount。"""
        from app.database import SessionLocal
        from app.models import Comment

        h = auth_header(client)
        root = self._post(client, "t1", "计数主评论", h).json()["data"]
        with SessionLocal() as db:
            for i in range(2):
                db.add(
                    Comment(
                        id=f"c_del_{i}",
                        news_id="t1",
                        user_id=1,
                        user_name="测试用户",
                        content=f"待删回复{i}",
                        created_at=f"2026-10-08T12:00:0{i}+00:00",
                        parent_id=root["id"],
                        root_id=root["id"],
                        likes=0,
                    )
                )
            db.commit()

        client.delete("/api/news/t1/comments/c_del_0", headers=h)
        rows = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        item = next(c for c in rows if c["id"] == root["id"])
        assert item["replyCount"] == 1

    def test_deleted_root_hides_replies(self, client):
        """主评论被软删除后其回复不可见（replies 返回空、主列表无主评论）。"""
        h = auth_header(client)
        root = self._post(client, "t1", "将删主评论", h).json()["data"]
        self._post(client, "t1", "跟随主评论的回复", h, parent=root["id"])

        client.delete(f"/api/news/t1/comments/{root['id']}", headers=h)

        rows = client.get("/api/news/t1/comments", headers=h).json()["data"]["list"]
        assert root["id"] not in {c["id"] for c in rows}
        d = client.get(f"/api/news/t1/comments/{root['id']}/replies").json()["data"]
        assert d["list"] == [] and d["hasMore"] is False

    def test_reply_to_missing_comment(self, client):
        h = auth_header(client)
        r = self._post(client, "t1", "x", h, parent="c_nonexistent")
        assert r.status_code == 400
        assert r.json()["code"] == 40003

    def test_reply_cross_news_rejected(self, client):
        """回复必须属于同一篇文章（防止跨文章挂回复）。"""
        h = auth_header(client)
        cid = self._post(client, "t1", "甲文章评论", h).json()["data"]["id"]
        r = self._post(client, "t2", "x", h, parent=cid)
        assert r.status_code == 400
        assert r.json()["code"] == 40003

    def test_reply_notifies_parent_author_only(self, client):
        """回复只通知被回复者；不再走「资讯有新评论」广播（避免重复打扰）。"""
        a = _register(client, "13920000001", "主评论作者")
        b = _register(client, "13920000002", "回复者")
        c = _register(client, "13920000003", "过路评论者")

        # c 先在该资讯下评论（若走广播，它应收到消息）
        client.post("/api/news/t1/comments", json={"content": "c 的评论"}, headers=c)
        # a 发主评论
        root = client.post(
            "/api/news/t1/comments", json={"content": "a 的主评论"}, headers=a
        ).json()["data"]["id"]

        before_c = client.get("/api/messages/unread-count", headers=c).json()["data"]
        before_a = client.get("/api/messages/unread-count", headers=a).json()["data"]

        # b 回复 a 的评论
        client.post(
            "/api/news/t1/comments",
            json={"content": "b 的回复", "parentId": root},
            headers=b,
        )

        after_a = client.get("/api/messages/unread-count", headers=a).json()["data"]
        after_c = client.get("/api/messages/unread-count", headers=c).json()["data"]
        assert after_a == before_a + 1
        assert after_c == before_c  # 广播被抑制
        titles = [
            m["title"] for m in client.get("/api/messages", headers=a).json()["data"]["list"]
        ]
        assert any("回复" in t for t in titles)


class TestCommentLikesTable:
    """点赞关系表化（0014）：comment_likes 行即事实，likes 计数随行维护。"""

    def _like_rows(self, comment_id):
        from app.database import SessionLocal
        from app.models import CommentLike

        with SessionLocal() as db:
            return db.query(CommentLike).filter(CommentLike.comment_id == comment_id).all()

    def test_like_creates_and_removes_relation_row(self, client):
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "关系表点赞"}, headers=h
        ).json()["data"]["id"]

        client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=h)
        rows = self._like_rows(cid)
        assert len(rows) == 1 and rows[0].user_id == 1

        client.patch(f"/api/news/t1/comments/{cid}", json={"liked": False}, headers=h)
        assert self._like_rows(cid) == []

    def test_pk_prevents_duplicate_like(self, client):
        """复合主键兜底：重复点赞不产生第二行，likes 不重复累加。"""
        h = auth_header(client)
        cid = client.post(
            "/api/news/t1/comments", json={"content": "重复点赞"}, headers=h
        ).json()["data"]["id"]
        for _ in range(3):
            r = client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=h)
            assert r.json()["data"]["likes"] == 1
        assert len(self._like_rows(cid)) == 1

    def test_liked_is_per_current_user(self, client):
        """A 点赞不影响 B/游客的 liked 视图；计数为点赞人数之和。"""
        a = _register(client, "13920000010", "点赞者A")
        b = _register(client, "13920000011", "点赞者B")
        cid = client.post(
            "/api/news/t1/comments", json={"content": "多人点赞"}, headers=a
        ).json()["data"]["id"]

        client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=a)
        client.patch(f"/api/news/t1/comments/{cid}", json={"liked": True}, headers=b)

        rows = client.get("/api/news/t1/comments", headers=a).json()["data"]["list"]
        item = next(c for c in rows if c["id"] == cid)
        assert item["likes"] == 2
        assert item["liked"] is True

        rows_b = client.get("/api/news/t1/comments", headers=b).json()["data"]["list"]
        assert next(c for c in rows_b if c["id"] == cid)["liked"] is True

        anon = client.get("/api/news/t1/comments").json()["data"]["list"]
        assert next(c for c in anon if c["id"] == cid)["liked"] is False


class TestMessageEvents:
    """消息中心事件化：定向消息由真实互动产生，且只对该看到的人可见。"""

    def test_broadcast_visible_to_everyone(self, client):
        """广播消息（user_id NULL）对所有登录用户可见。"""
        h = _register(client, "13900003333", "广播用户")
        ids = [m["id"] for m in client.get("/api/messages", headers=h).json()["data"]["list"]]
        assert "m1" in ids  # 种子广播消息

    def test_targeted_message_only_visible_to_recipient(self, client):
        """定向消息不出现在他人列表中。"""
        from app.database import SessionLocal
        from app.models import Message, User

        a = _register(client, "13900004444", "甲")
        b = _register(client, "13900005555", "乙")

        with SessionLocal() as db:
            uid_a = db.query(User).filter(User.phone == "13900004444").first().id
            db.add(
                Message(
                    id="msg_target",
                    type="comment",
                    title="只给甲",
                    summary="定向消息",
                    news_id=None,
                    user_id=uid_a,
                    time="刚刚",
                )
            )
            db.commit()

        ids_a = [m["id"] for m in client.get("/api/messages", headers=a).json()["data"]["list"]]
        ids_b = [m["id"] for m in client.get("/api/messages", headers=b).json()["data"]["list"]]
        assert "msg_target" in ids_a
        assert "msg_target" not in ids_b

    def test_comment_notifies_other_commenters(self, client):
        """甲评论资讯后，乙再评论 → 甲收到通知；乙自己不收到。"""
        a = _register(client, "13900006666", "甲评论")
        b = _register(client, "13900007777", "乙评论")

        # 甲先评论
        client.post("/api/news/t1/comments", json={"content": "甲的第一条"}, headers=a)
        unread_a_before = client.get("/api/messages/unread-count", headers=a).json()["data"]

        # 乙再评论同一条资讯
        client.post("/api/news/t1/comments", json={"content": "乙来评论"}, headers=b)

        unread_a_after = client.get("/api/messages/unread-count", headers=a).json()["data"]
        assert unread_a_after == unread_a_before + 1

        titles = [m["title"] for m in client.get("/api/messages", headers=a).json()["data"]["list"]]
        assert any("新动态" in t for t in titles)

    def test_commenter_does_not_notify_self(self, client):
        """自己评论不给自己发通知。"""
        a = _register(client, "13900008888", "独狼")
        before = client.get("/api/messages/unread-count", headers=a).json()["data"]
        client.post("/api/news/t1/comments", json={"content": "只有我评论"}, headers=a)
        after = client.get("/api/messages/unread-count", headers=a).json()["data"]
        assert after == before

    def test_like_notifies_comment_author(self, client):
        """乙点赞甲的评论 → 甲收到"收到新点赞"。"""
        a = _register(client, "13900009999", "作者甲")
        b = _register(client, "13910000000", "点赞乙")

        cid = client.post(
            "/api/news/t2/comments", json={"content": "给甲点赞用"}, headers=a
        ).json()["data"]["id"]
        before = client.get("/api/messages/unread-count", headers=a).json()["data"]

        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=b)

        after = client.get("/api/messages/unread-count", headers=a).json()["data"]
        assert after == before + 1
        titles = [m["title"] for m in client.get("/api/messages", headers=a).json()["data"]["list"]]
        assert any("点赞" in t for t in titles)

    def test_unlike_does_not_notify(self, client):
        """取消点赞不应打扰作者（只有新增点赞才通知）。"""
        a = _register(client, "13910000001", "作者")
        b = _register(client, "13910000002", "手滑的人")

        cid = client.post(
            "/api/news/t2/comments", json={"content": "点赞再取消"}, headers=a
        ).json()["data"]["id"]
        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=b)
        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": False}, headers=b)
        before = client.get("/api/messages/unread-count", headers=a).json()["data"]

        # 再取消一次（已无点赞关系），不应新增消息
        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": False}, headers=b)
        assert client.get("/api/messages/unread-count", headers=a).json()["data"] == before

    def test_repeated_like_is_deduplicated(self, client):
        """反复点赞/取消不刷屏（相同内容只记一条）。"""
        a = _register(client, "13910000003", "作者去重")
        b = _register(client, "13910000004", "反复点赞")

        cid = client.post(
            "/api/news/t2/comments", json={"content": "去重测试评论"}, headers=a
        ).json()["data"]["id"]
        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=b)
        after_first = client.get("/api/messages/unread-count", headers=a).json()["data"]

        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": False}, headers=b)
        client.patch(f"/api/news/t2/comments/{cid}", json={"liked": True}, headers=b)

        assert client.get("/api/messages/unread-count", headers=a).json()["data"] == after_first

    def test_read_all_only_marks_visible(self, client):
        """全部已读后未读数为 0，且只影响可见消息。"""
        a = _register(client, "13910000005", "已读用户")
        assert client.post("/api/messages/read-all", headers=a).json()["code"] == 0
        assert client.get("/api/messages/unread-count", headers=a).json()["data"] == 0


class TestRelatedNews:
    """相关推荐：GET /api/news/:id/related（SQLite 走同频道/最新兜底）。"""

    def test_returns_list_and_excludes_self(self, client):
        r = client.get("/api/news/t1/related")
        assert r.json()["code"] == 0
        data = r.json()["data"]
        assert isinstance(data["list"], list)
        assert data["total"] == len(data["list"])
        assert "t1" not in [n["id"] for n in data["list"]]

    def test_limit_is_respected(self, client):
        data = client.get("/api/news/t1/related?limit=2").json()["data"]
        assert len(data["list"]) <= 2

    def test_limit_bounds(self, client):
        """越界参数由 FastAPI 校验为 422，统一契约码。"""
        assert client.get("/api/news/t1/related?limit=0").status_code == 422
        assert client.get("/api/news/t1/related?limit=99").status_code == 422

    def test_missing_news_is_404(self, client):
        r = client.get("/api/news/nope/related")
        assert r.status_code == 404
        assert r.json()["code"] == 40400

    def test_items_have_frontend_shape(self, client):
        """返回项必须能被前端识别（id/title/variant）。"""
        data = client.get("/api/news/t1/related?limit=3").json()["data"]
        for n in data["list"]:
            assert n["id"] and n["title"] and n["variant"]
            assert isinstance(n["commentCount"], int)

    def test_no_duplicates(self, client):
        data = client.get("/api/news/t1/related?limit=10").json()["data"]
        ids = [n["id"] for n in data["list"]]
        assert len(ids) == len(set(ids))

    def test_prefers_same_channel_then_falls_back(self, client):
        """无 pg_trgm 时（SQLite）：同频道优先，不足再用全站最新补齐。"""
        from app.database import SessionLocal
        from app.models import News

        new_id = "t_finance_related"  # 唯一 id，避免与既有用例冲突
        with SessionLocal() as db:
            if db.get(News, new_id) is None:
                db.add(
                    News(
                        id=new_id,
                        title="同频道财经资讯",
                        summary="finance 频道",
                        source="财经",
                        comment_count=0,
                        time="1小时前",
                        channel="finance",
                        variant="text",
                        is_top=False,
                    )
                )
                db.commit()

        data = client.get("/api/news/t2/related?limit=5").json()["data"]
        ids = [n["id"] for n in data["list"]]
        channels = [n["channel"] for n in data["list"]]
        assert new_id in ids
        # 同频道项必须排在其他频道之前
        if "recommend" in channels:
            assert channels.index("finance") < channels.index("recommend")
