"""P1-9：RSS 源可配置化。

覆盖三条线：
1. **播种与读取**——内置源自动入库、运营改动不被覆盖、按频道/权重筛选；
2. **管理接口**——鉴权、增删改、冲突、协议白名单、重置；
3. **抓取健壮性**——单源失败不影响其他源、健康状态回写。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
# 关掉限流：本文件大量反复登录（`_admin`/`_user`），同一 testclient IP 很快触顶，
# 表现为 login 返回 data=None → 后续取值 TypeError（失败点会指向莫名其妙的行）。
os.environ["RATE_LIMIT_ENABLED"] = "false"
# 关掉定时抓取：`TestClient` 会触发 lifespan → 启动调度 → **真的去抓网络**并回写源表，
# 既慢又会污染本文件的断言（曾表现为播种条数与库中条数对不上）。
os.environ["FEED_CRON_ENABLED"] = "false"
# 注：ADMIN_PHONES 不需要（也不该）在这里用 os.environ 设置——
# 根目录 `conftest.py` 已统一固定测试环境；且 `app.config` 在首次 import 时即定稿，
# 模块级 env 赋值只对"第一个被导入的测试文件"有效，历来是顺序敏感的坑源。
# 本文件改用 `_admin_whitelist` fixture 直接改模块绑定：按测试隔离、结束自动还原。

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.routers import admin as admin_mod  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.feeds import ingest as ingest_mod  # noqa: E402
from app.feeds import sources as src_mod  # noqa: E402
from app.feeds.fetcher import FetchError, RawItem  # noqa: E402
from app.feeds.ingest import collect  # noqa: E402
from app.models import RssSource  # noqa: E402

ADMIN_PHONE = "13900000000"


@pytest.fixture(autouse=True)
def _admin_whitelist(monkeypatch):
    """把本文件的管理员号段装进白名单，并保证测试后还原。

    踩坑记录：`ADMIN_PHONES` 是 `app.config` 的**模块级常量**，在首次 import 时定稿。
    全量跑时 `test_api.py` 先导入（其 env 为 13800138000），因此本文件顶部再写
    `os.environ["ADMIN_PHONES"]` 完全无效——症状是整类管理接口用例返回 40301，
    而单独跑本文件却全绿。故直接改模块绑定（`_require_admin` 调用时才解析该全局名）。
    """
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
    r = client.post(
        "/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"}
    )
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _user(client, phone: str) -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": "路人"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


# ---------------------------------------------------------------- 播种与读取


class TestSeeding:
    def test_seeds_when_empty(self, client):
        with SessionLocal() as db:
            n = src_mod.ensure_seeded(db)
            assert n == len(src_mod.BUILTIN_SOURCES)
            assert db.query(RssSource).count() == n

    def test_does_not_overwrite_existing_config(self, client):
        """表非空时不播种——否则运营的停用/调权重会被下次启动覆盖。"""
        with SessionLocal() as db:
            src_mod.ensure_seeded(db)
            row = db.get(RssSource, "people_politics")
            row.enabled = False
            row.weight = 1
            db.commit()

            assert src_mod.ensure_seeded(db) == 0
            after = db.get(RssSource, "people_politics")
            assert after.enabled is False
            assert after.weight == 1

    def test_aihot_source_is_builtin_and_enabled(self, client):
        """本次新增的信源：AIHOT（AI 垂直资讯），归入 tech 频道。"""
        with SessionLocal() as db:
            src_mod.ensure_seeded(db)
            row = db.get(RssSource, "aihot_picks")
        assert row is not None
        assert row.enabled is True
        assert row.channel == "tech"
        assert row.url == "https://aihot.news/feed.xml"

    def test_all_enabled_excludes_disabled(self, client):
        with SessionLocal() as db:
            keys = [s["key"] for s in src_mod.all_enabled_sources(db)]
        assert "thepaper" not in keys  # 内置即停用的源
        assert "aihot_picks" in keys

    def test_ordered_by_weight_desc(self, client):
        with SessionLocal() as db:
            srcs = src_mod.all_enabled_sources(db)
        weights = [s["weight"] for s in srcs]
        assert weights == sorted(weights, reverse=True)

    def test_channel_filter_falls_back_to_all(self, client):
        """频道无专属源时回退全部：宁可内容宽一些，也不要整页空白。"""
        with SessionLocal() as db:
            # car 频道无专属源
            srcs = src_mod.sources_for_channel("car", db)
            assert len(srcs) == len(src_mod.all_enabled_sources(db))

    def test_channel_filter_selects_matching(self, client):
        with SessionLocal() as db:
            srcs = src_mod.sources_for_channel("tech", db)
        assert all(s["channel"] == "tech" for s in srcs)
        assert any(s["key"] == "aihot_picks" for s in srcs)

    def test_builtin_fallback_without_db(self):
        """无数据库会话时回落到内置源（单测/脚本可用）。"""
        srcs = src_mod.all_enabled_sources(None)
        assert srcs
        assert all("key" in s and "url" in s for s in srcs)


# ---------------------------------------------------------------- 健康状态


class TestHealth:
    def test_records_success(self, client):
        with SessionLocal() as db:
            src_mod.ensure_seeded(db)
            src_mod.record_fetch_result(db, "aihot_picks", True, 20, None)
            row = db.get(RssSource, "aihot_picks")
            assert row.last_status == "ok"
            assert row.last_item_count == 20
            assert row.last_error is None
            assert row.last_fetched_at

    def test_records_failure(self, client):
        with SessionLocal() as db:
            src_mod.ensure_seeded(db)
            src_mod.record_fetch_result(db, "aihot_picks", False, 0, "连接超时")
            row = db.get(RssSource, "aihot_picks")
            assert row.last_status == "error"
            assert row.last_error == "连接超时"

    def test_error_is_truncated_to_column_width(self, client):
        with SessionLocal() as db:
            src_mod.ensure_seeded(db)
            src_mod.record_fetch_result(db, "aihot_picks", False, 0, "长" * 500)
            assert len(db.get(RssSource, "aihot_picks").last_error) <= 300

    def test_unknown_key_is_ignored(self, client):
        with SessionLocal() as db:
            src_mod.record_fetch_result(db, "nope", True, 1, None)  # 不应抛错


# ---------------------------------------------------------------- 抓取健壮性


class TestCollectRobustness:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        """跳过退避等待：真实 sleep 会让本类测试白等数秒（每个失败源 2 次重试）。"""
        monkeypatch.setattr(ingest_mod.time, "sleep", lambda *_: None)

    def test_single_source_failure_does_not_break_others(self, client, monkeypatch):
        """一个源挂掉不应让整轮抓取空手而归，也不应让 collect 抛错。"""
        good = RawItem(id="ok_1", title="好源的头条", source="好源", channel="tech")

        def fake_fetch(_client, source, limit=30):
            if source["key"] == "broken":
                raise FetchError("连接被拒绝")
            return [good]

        monkeypatch.setattr(ingest_mod, "fetch_source", fake_fetch)
        stats: list[dict] = []
        items = collect(
            sources=[
                {"key": "broken", "name": "坏源", "channel": "tech",
                 "url": "https://example.com/nope.xml", "weight": 9, "enabled": True},
                {"key": "healthy", "name": "好源", "channel": "tech",
                 "url": "https://example.com/ok.xml", "weight": 5, "enabled": True},
            ],
            limit_per_source=5,
            timeout=2.0,
            connect_timeout=1.0,
            stats=stats,
        )
        assert [i.id for i in items] == ["ok_1"]  # 好源的内容照常返回
        by_key = {s["key"]: s for s in stats}
        assert by_key["broken"]["ok"] is False
        assert by_key["broken"]["error"]  # 必须带原因，否则排障时无从下手
        assert by_key["healthy"]["ok"] is True
        assert by_key["healthy"]["count"] == 1

    def test_real_broken_sources_recorded_as_failed(self, client):
        """真实不可达地址（连接拒绝 / 非法协议）应被记为故障，而不是静默 ok。"""
        stats: list[dict] = []
        items = collect(
            sources=[
                {"key": "broken", "name": "坏源", "channel": "tech",
                 "url": "http://127.0.0.1:1/nope.xml", "weight": 5, "enabled": True},
                {"key": "bad_scheme", "name": "非法协议", "channel": "tech",
                 "url": "file:///etc/passwd", "weight": 4, "enabled": True},
            ],
            limit_per_source=5,
            timeout=2.0,
            connect_timeout=1.0,
            stats=stats,
        )
        # 两个源都失败，但 collect 本身不抛错
        assert items == []
        assert len(stats) == 2
        assert all(s["ok"] is False for s in stats)
        assert all(s["error"] for s in stats)

    def test_sources_are_deduped_and_sorted(self, client):
        """显式传入的源按 key 去重、按 weight 降序（高权重先入库）。"""
        stats: list[dict] = []
        # 用不可达地址即可：只验证顺序与去重，不依赖真实网络
        collect(
            sources=[
                {"key": "a", "url": "http://127.0.0.1:1/a.xml", "weight": 1,
                 "name": "A", "channel": "tech", "enabled": True},
                {"key": "b", "url": "http://127.0.0.1:1/b.xml", "weight": 9,
                 "name": "B", "channel": "tech", "enabled": True},
                {"key": "a", "url": "http://127.0.0.1:1/a.xml", "weight": 1,
                 "name": "A", "channel": "tech", "enabled": True},
            ],
            limit_per_source=1,
            timeout=2.0,
            connect_timeout=1.0,
            stats=stats,
        )
        keys = [s["key"] for s in stats]
        assert keys == ["b", "a"]  # 去重后按权重降序


# ---------------------------------------------------------------- 管理接口


class TestAdminSources:
    def test_requires_login(self, client):
        assert client.get("/api/admin/sources").status_code == 401

    def test_non_admin_is_forbidden(self, client):
        h = _user(client, "13900000001")
        r = client.get("/api/admin/sources", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_admin_lists_sources(self, client):
        h = _admin(client)
        data = client.get("/api/admin/sources", headers=h).json()["data"]
        assert data["total"] == len(src_mod.BUILTIN_SOURCES)
        keys = [s["key"] for s in data["list"]]
        assert "aihot_picks" in keys

    def test_admin_creates_source(self, client):
        h = _admin(client)
        r = client.post(
            "/api/admin/sources",
            headers=h,
            json={"key": "custom_ai", "name": "自定义 AI", "channel": "tech",
                  "url": "https://example.com/feed.xml", "weight": 7},
        )
        assert r.json()["code"] == 0
        assert r.json()["data"]["key"] == "custom_ai"
        with SessionLocal() as db:
            assert db.get(RssSource, "custom_ai") is not None

    def test_duplicate_key_is_409(self, client):
        h = _admin(client)
        body = {"key": "dup", "name": "X", "channel": "tech", "url": "https://example.com/a.xml"}
        assert client.post("/api/admin/sources", headers=h, json=body).json()["code"] == 0
        r = client.post("/api/admin/sources", headers=h, json=body)
        assert r.status_code == 409
        # 40902 专指「源标识已存在」，与 40901「手机号已注册」区分
        assert r.json()["code"] == 40902

    def test_rejects_non_http_url(self, client):
        """抓取器会真的请求该地址，必须挡住 file:// 等协议。"""
        h = _admin(client)
        r = client.post(
            "/api/admin/sources",
            headers=h,
            json={"key": "evil", "name": "E", "channel": "tech", "url": "file:///etc/passwd"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == 40004

    def test_rejects_unknown_channel(self, client):
        """拼错的频道会让源匹配不到任何频道，进而靠回退污染所有频道——必须挡掉。"""
        h = _admin(client)
        r = client.post(
            "/api/admin/sources",
            headers=h,
            json={"key": "typo", "name": "T", "channel": "tecn",
                  "url": "https://example.com/t.xml"},
        )
        assert r.status_code == 422
        # 必须是统一契约而非 FastAPI 默认结构，也不能因自定义 validator 抛 ValueError 而变 500
        body = r.json()
        assert body["code"] == 42200
        assert isinstance(body["data"]["errors"], list)
        assert body["data"]["errors"][0]["loc"][-1] == "channel"

    def test_rejects_malformed_key(self, client):
        """key 是播种与更新对齐的依据，限定小写字母/数字/下划线。"""
        h = _admin(client)
        r = client.post(
            "/api/admin/sources",
            headers=h,
            json={"key": "Bad Key!", "name": "T", "channel": "tech",
                  "url": "https://example.com/t.xml"},
        )
        assert r.status_code == 422

    def test_rejects_bad_channel_on_update(self, client):
        h = _admin(client)
        r = client.patch("/api/admin/sources/aihot_picks", headers=h, json={"channel": "tecn"})
        assert r.status_code == 422

    def test_partial_update(self, client):
        h = _admin(client)
        client.patch("/api/admin/sources/aihot_picks", headers=h, json={"weight": 3})
        with SessionLocal() as db:
            row = db.get(RssSource, "aihot_picks")
            assert row.weight == 3
            # 未传入的字段不应被清空
            assert row.url == "https://aihot.news/feed.xml"

    def test_toggle_enabled(self, client):
        h = _admin(client)
        client.patch("/api/admin/sources/aihot_picks", headers=h, json={"enabled": False})
        with SessionLocal() as db:
            assert db.get(RssSource, "aihot_picks").enabled is False
            assert "aihot_picks" not in [s["key"] for s in src_mod.all_enabled_sources(db)]

    def test_update_missing_is_404(self, client):
        h = _admin(client)
        r = client.patch("/api/admin/sources/nope", headers=h, json={"weight": 1})
        assert r.status_code == 404

    def test_delete(self, client):
        h = _admin(client)
        assert client.delete("/api/admin/sources/aihot_picks", headers=h).json()["code"] == 0
        with SessionLocal() as db:
            assert db.get(RssSource, "aihot_picks") is None

    def test_delete_missing_is_404(self, client):
        h = _admin(client)
        assert client.delete("/api/admin/sources/nope", headers=h).status_code == 404

    def test_reset_restores_builtins(self, client):
        h = _admin(client)
        # 先改坏：停用一个源 + 加一个自定义源
        client.patch("/api/admin/sources/aihot_picks", headers=h, json={"enabled": False})
        client.post("/api/admin/sources", headers=h,
                    json={"key": "temp", "name": "T", "channel": "tech",
                          "url": "https://example.com/t.xml"})
        r = client.post("/api/admin/sources/reset", headers=h)
        assert r.json()["code"] == 0
        with SessionLocal() as db:
            assert db.get(RssSource, "temp") is None
            assert db.get(RssSource, "aihot_picks").enabled is True

    def test_admin_disabled_when_whitelist_empty(self, client, monkeypatch):
        """白名单为空 = 该能力不可用，避免生产因配置疏漏而暴露管理接口。"""
        h = _admin(client)
        monkeypatch.setattr(admin_mod, "ADMIN_PHONES", [])
        r = client.get("/api/admin/sources", headers=h)
        assert r.status_code == 403


# ------------------------------------------------- 管理能力标志（前端入口用）


class TestAdminFlagInAuthPayload:
    """登录/注册/续期响应下发 `user.isAdmin`，前端据此决定是否展示管理入口。

    只下发布尔值、不下发白名单本身——否则等于把管理员名单暴露给客户端。
    """

    def test_login_marks_whitelisted_phone(self, client):
        _admin(client)  # 注册 + 登录一次（返回的 token 即可用）
        r = client.post(
            "/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"}
        )
        assert r.json()["data"]["user"]["isAdmin"] is True

    def test_login_marks_ordinary_phone(self, client):
        _user(client, "13900000001")
        r = client.post(
            "/api/auth/login", json={"phone": "13900000001", "password": "abc123"}
        )
        assert r.json()["data"]["user"]["isAdmin"] is False

    def test_register_marks_admin(self, client):
        r = client.post(
            "/api/auth/register",
            json={"phone": ADMIN_PHONE, "password": "abc123", "name": "管理员"},
        )
        assert r.json()["data"]["user"]["isAdmin"] is True

    def test_refresh_keeps_flag(self, client):
        h = _admin(client)
        r = client.post("/api/auth/refresh", headers=h)
        assert r.json()["data"]["user"]["isAdmin"] is True

    def test_flag_follows_whitelist_change(self, client, monkeypatch):
        """白名单改动后，同一账号的标志应随之变化（判定不是写死在 token 里）。"""
        h = _admin(client)
        assert client.post("/api/auth/refresh", headers=h).json()["data"]["user"]["isAdmin"] is True
        monkeypatch.setattr(admin_mod, "ADMIN_PHONES", [])
        assert client.post("/api/auth/refresh", headers=h).json()["data"]["user"]["isAdmin"] is False
