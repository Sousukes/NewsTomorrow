"""P1-20：资讯审核与下架（软下架 / 恢复 / 置顶 / 审计留痕 / 公开过滤）。

覆盖五条线：
1. **管理端列表**——筛选（visible/hidden/all）、关键词搜索、频道过滤、游标分页；
2. **下架与恢复**——往返、理由必填（空/纯空白/超长）、幂等（不重复记审计、
   不覆盖原理由）、未知 id 404、鉴权；
3. **公开四路过滤**——列表（offset + 游标）/ 详情 / 搜索 / 相关推荐，以及发评论 404；
4. **审计留痕**——news.hide 带理由与标题、news.restore、news.update 记 isTop diff；
5. **置顶管理**——PATCH 开关、驼峰入参（蛇形被静默丢弃的缝要焊死）。

环境由根目录 `conftest.py` 统一固定（SQLite 内存库 / 限流关 / 定时抓取关 /
ADMIN_PHONES=13800138000）——本文件不再重复设置，避免"谁先导入谁定稿"的顺序坑。
"""
import json

import pytest
from fastapi.testclient import TestClient

import main
from app.database import Base, SessionLocal, engine
from app.models import AdminAuditLog, News

ADMIN_PHONE = "13800138000"  # 根 conftest 固定的号段


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
    r = client.post("/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _user(client, phone: str) -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": "路人"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _seed_news(rows: list[tuple[str, str, str, str]]) -> None:
    """直接造资讯行（不走抓取链路）：(id, title, published_at, channel)。

    published_at 用统一格式：游标是字符串 keyset 比较，格式不一致会错位。
    """
    with SessionLocal() as db:
        for nid, title, pub, channel in rows:
            db.add(
                News(
                    id=nid,
                    title=title,
                    summary=f"{title}的摘要",
                    source="测试源",
                    comment_count=0,
                    time="1小时前",
                    channel=channel,
                    variant="text",
                    published_at=pub,
                )
            )
        db.commit()


def _hide(client, h: dict, news_id: str, reason: str = "涉及不实信息"):
    return client.post(f"/api/admin/news/{news_id}/hide", headers=h, json={"reason": reason})


def _audits_of(news_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.target == news_id)
            .order_by(AdminAuditLog.id)
            .all()
        )
        return [
            {"action": r.action, "detail": json.loads(r.detail), "actor": r.actor_phone}
            for r in rows
        ]


# ---------------------------------------------------------------- 管理端列表


class TestAdminNewsList:
    def test_requires_login(self, client):
        assert client.get("/api/admin/news").status_code == 401

    def test_non_admin_is_forbidden(self, client):
        h = _user(client, "13800138001")
        r = client.get("/api/admin/news", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_list_defaults_to_all_with_hidden_fields(self, client):
        """默认 all：运营要能核验自己的下架操作，不需要多点一下筛选。"""
        _seed_news(
            [
                ("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance"),
                ("n2", "资讯二", "2026-10-11T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        assert _hide(client, h, "n2").json()["code"] == 0
        data = client.get("/api/admin/news", headers=h).json()["data"]
        assert data["total"] == 2
        by_id = {n["id"]: n for n in data["list"]}
        assert by_id["n1"]["hidden"] is False
        assert by_id["n1"]["hiddenReason"] is None
        assert by_id["n2"]["hidden"] is True
        assert by_id["n2"]["hiddenReason"] == "涉及不实信息"
        assert by_id["n2"]["hiddenAt"]  # 恢复前一直有值

    def test_status_filter_visible_and_hidden(self, client):
        _seed_news(
            [
                ("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance"),
                ("n2", "资讯二", "2026-10-11T00:00:00+00:00", "tech"),
                ("n3", "资讯三", "2026-10-12T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "n2")
        vis = client.get("/api/admin/news?status=visible", headers=h).json()["data"]
        hid = client.get("/api/admin/news?status=hidden", headers=h).json()["data"]
        assert vis["total"] == 2
        assert all(n["hidden"] is False for n in vis["list"])
        assert hid["total"] == 1
        assert hid["list"][0]["id"] == "n2"

    def test_status_invalid_is_422(self, client):
        h = _admin(client)
        r = client.get("/api/admin/news?status=foo", headers=h)
        assert r.status_code == 422
        assert r.json()["code"] == 42200

    def test_keyword_matches_title_and_summary(self, client):
        _seed_news(
            [
                ("n1", "台风蓝色预警发布", "2026-10-10T00:00:00+00:00", "finance"),
                ("n2", "普通财经新闻", "2026-10-11T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/news?keyword=台风", headers=h).json()["data"]
        assert data["total"] == 1
        assert data["list"][0]["id"] == "n1"
        # 摘要命中同样要能搜到（关键词只出现在 summary 里）
        data2 = client.get("/api/admin/news?keyword=摘要", headers=h).json()["data"]
        assert data2["total"] == 2

    def test_keyword_percent_is_escaped(self, client):
        """`%` 必须被当字面量：不转义时搜一个百分号等于"匹配全部"。"""
        _seed_news([("n1", "百分之百", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        data = client.get("/api/admin/news?keyword=%25", headers=h).json()["data"]
        assert data["total"] == 0  # 标题里没有字面 %，不应命中

    def test_channel_filter_and_validation(self, client):
        _seed_news(
            [
                ("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance"),
                ("n2", "资讯二", "2026-10-11T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/news?channel=tech", headers=h).json()["data"]
        assert data["total"] == 1
        assert data["list"][0]["id"] == "n2"
        # 拼错的频道必须显式报错，而不是静默返回空列表
        r = client.get("/api/admin/news?channel=tecn", headers=h)
        assert r.status_code == 422
        assert r.json()["code"] == 42201

    def test_cursor_pagination_no_repeats(self, client):
        _seed_news(
            [
                (f"n{i}", f"资讯{i}", f"2026-10-{i + 1:02d}T00:00:00+00:00", "tech")
                for i in range(5)
            ]
        )
        h = _admin(client)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            url = "/api/admin/news?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            data = client.get(url, headers=h).json()["data"]
            seen.extend(n["id"] for n in data["list"])
            pages += 1
            cursor = data["nextCursor"] if data["hasMore"] else None
            if not cursor:
                break
        assert pages == 3  # 2 + 2 + 1
        assert sorted(seen) == [f"n{i}" for i in range(5)]  # 不重不漏
        assert len(set(seen)) == len(seen)

    def test_invalid_cursor_is_400(self, client):
        h = _admin(client)
        r = client.get("/api/admin/news?cursor=not-a-cursor", headers=h)
        assert r.status_code == 400
        assert r.json()["code"] == 40003

    def test_detail_returns_hidden_fields(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        _hide(client, h, "n1", reason="侵权")
        data = client.get("/api/admin/news/n1", headers=h).json()["data"]
        assert data["id"] == "n1"
        assert data["hidden"] is True
        assert data["hiddenReason"] == "侵权"
        assert data["publishedAt"] == "2026-10-10T00:00:00+00:00"
        assert data["hotScore"] == 0

    def test_detail_unknown_is_404(self, client):
        h = _admin(client)
        r = client.get("/api/admin/news/nope", headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40404


# ---------------------------------------------------------------- 下架与恢复


class TestHideRestore:
    def test_hide_then_restore_round_trip(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        r = _hide(client, h, "n1")
        assert r.json()["code"] == 0
        assert r.json()["data"]["hidden"] is True
        with SessionLocal() as db:
            row = db.get(News, "n1")
            assert row.hidden is True
            assert row.hidden_at and row.hidden_reason == "涉及不实信息"
        r2 = client.post("/api/admin/news/n1/restore", headers=h)
        assert r2.json()["data"]["hidden"] is False
        with SessionLocal() as db:
            row = db.get(News, "n1")
            assert row.hidden is False
            # 理由/时间一并清空：留着会让下一次下架的审计读起来张冠李戴
            assert row.hidden_at is None and row.hidden_reason is None

    def test_hide_requires_reason(self, client):
        """留痕是准入条件：空理由（含纯空白）与超长理由一律 422。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        for bad in ("", "   ", "长" * 301):
            r = _hide(client, h, "n1", reason=bad)
            assert r.status_code == 422, bad
            assert r.json()["code"] == 42200
        # 边界：恰好 300 字放行
        ok = _hide(client, h, "n1", reason="长" * 300)
        assert ok.json()["code"] == 0
        assert len(ok.json()["data"]["hiddenReason"]) == 300

    def test_hide_reason_is_trimmed(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        data = _hide(client, h, "n1", reason="  涉及侵权  ").json()["data"]
        assert data["hiddenReason"] == "涉及侵权"

    def test_hide_unknown_is_404(self, client):
        h = _admin(client)
        r = _hide(client, h, "nope")
        assert r.status_code == 404
        assert r.json()["code"] == 40404

    def test_hide_idempotent_keeps_original_reason_and_audit(self, client):
        """重复下架不覆盖原理由、不重复记审计——网络重试/界面双击都不该再造痕迹。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        _hide(client, h, "n1", reason="第一次的理由")
        r = _hide(client, h, "n1", reason="第二次的理由")
        assert r.json()["data"]["hiddenReason"] == "第一次的理由"
        audits = _audits_of("n1")
        assert len(audits) == 1

    def test_restore_idempotent(self, client):
        """对未下架的资讯恢复：成功返回但不记审计。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        r = client.post("/api/admin/news/n1/restore", headers=h)
        assert r.json()["code"] == 0
        assert r.json()["data"]["hidden"] is False
        assert _audits_of("n1") == []

    def test_restore_unknown_is_404(self, client):
        h = _admin(client)
        r = client.post("/api/admin/news/nope/restore", headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40404

    def test_new_rows_default_visible(self, client):
        """新入库行默认可见（ORM default=False；server_default 由迁移守卫把守）。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "finance")])
        h = _admin(client)
        data = client.get("/api/admin/news/n1", headers=h).json()["data"]
        assert data["hidden"] is False


# ---------------------------------------------------------------- 公开四路过滤


class TestPublicFiltering:
    def test_list_excludes_hidden_offset_and_cursor(self, client):
        _seed_news(
            [
                ("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech"),
                ("n2", "资讯二", "2026-10-11T00:00:00+00:00", "tech"),
                ("n3", "资讯三", "2026-10-12T00:00:00+00:00", "tech"),
                ("n4", "资讯四", "2026-10-13T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "n2")
        # offset 兼容模式（B2 起需显式传 page 才走这条）：total 也必须是过滤后的
        data = client.get("/api/news?channel=tech&page=1").json()["data"]
        assert data["total"] == 3
        assert all(n["id"] != "n2" for n in data["list"])
        # 默认路径（keyset）：列表同样不得出现已下架；total 按约定为 null
        default_page = client.get("/api/news?channel=tech").json()["data"]
        assert default_page["total"] is None
        assert all(n["id"] != "n2" for n in default_page["list"])
        # 游标模式：翻完全部也不应出现已下架
        seen: list[str] = []
        cursor = None
        while True:
            url = "/api/news?channel=tech&pageSize=2"
            if cursor:
                url += f"&cursor={cursor}"
            page = client.get(url).json()["data"]
            seen.extend(n["id"] for n in page["list"])
            cursor = page["nextCursor"] if page["hasMore"] else None
            if not cursor:
                break
        assert sorted(seen) == ["n1", "n3", "n4"]

    def test_detail_404_for_hidden_and_back(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        assert client.get("/api/news/n1").json()["data"]["id"] == "n1"
        _hide(client, h, "n1")
        r = client.get("/api/news/n1")
        assert r.status_code == 404
        # 与「不存在」同码：对外不暴露"它存在但被下了"
        assert r.json()["code"] == 40400
        client.post("/api/admin/news/n1/restore", headers=h)
        assert client.get("/api/news/n1").json()["data"]["id"] == "n1"

    def test_search_excludes_hidden(self, client):
        _seed_news(
            [
                ("n1", "独特的搜索词甲", "2026-10-10T00:00:00+00:00", "tech"),
                ("n2", "独特的搜索词乙", "2026-10-11T00:00:00+00:00", "tech"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "n2")
        data = client.get("/api/news/search?q=独特的搜索词").json()["data"]
        assert [n["id"] for n in data["list"]] == ["n1"]
        assert data["total"] == 1

    def test_related_excludes_hidden_and_404s_on_hidden_target(self, client):
        _seed_news(
            [
                ("target", "目标资讯", "2026-10-10T00:00:00+00:00", "finance"),
                ("hidden_one", "相关但已下架", "2026-10-11T00:00:00+00:00", "finance"),
                ("visible_one", "相关且可见", "2026-10-12T00:00:00+00:00", "finance"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "hidden_one")
        data = client.get("/api/news/target/related").json()["data"]
        ids = [n["id"] for n in data["list"]]
        assert "visible_one" in ids
        assert "hidden_one" not in ids
        # 已下架的资讯没有详情页，"看了又看"也不该有入口
        assert client.get("/api/news/hidden_one/related").status_code == 404

    def test_comments_404_for_hidden(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        uh = _user(client, "13800138001")
        h = _admin(client)
        _hide(client, h, "n1")
        # 评论列表与发表评论都走 _get_news_or_404
        assert client.get("/api/news/n1/comments").status_code == 404
        r = client.post(
            "/api/news/n1/comments", headers=uh, json={"content": "还能评论吗"}
        )
        assert r.status_code == 404
        assert r.json()["code"] == 40400


# ---------------------------------------------------------------- 审计留痕


class TestAuditTrail:
    def test_hide_records_reason_and_title(self, client):
        """审计记的是运营认的信息：标题 + 理由（target 只有 id，认不出来）。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        _hide(client, h, "n1", reason="来源不可靠")
        audits = _audits_of("n1")
        assert len(audits) == 1
        assert audits[0]["action"] == "news.hide"
        assert audits[0]["detail"]["reason"] == "来源不可靠"
        assert audits[0]["detail"]["title"] == "资讯一"
        assert audits[0]["actor"] == ADMIN_PHONE
        # 审计页按 target 可查（前端入口）
        page = client.get(f"/api/admin/audit?target=n1", headers=h).json()["data"]
        assert page["list"][0]["action"] == "news.hide"

    def test_restore_records_audit(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        _hide(client, h, "n1")
        client.post("/api/admin/news/n1/restore", headers=h)
        audits = _audits_of("n1")
        assert [a["action"] for a in audits] == ["news.hide", "news.restore"]

    def test_update_records_is_top_diff(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        r = client.patch("/api/admin/news/n1", headers=h, json={"isTop": True})
        assert r.json()["data"]["isTop"] is True
        audits = _audits_of("n1")
        assert len(audits) == 1
        assert audits[0]["action"] == "news.update"
        assert audits[0]["detail"]["changes"] == {"isTop": [False, True]}

    def test_update_without_change_records_nothing(self, client):
        """无变化的 PATCH 不该灌进时间线（前端常因表单整体提交而触发）。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        r = client.patch("/api/admin/news/n1", headers=h, json={"isTop": False})
        assert r.json()["code"] == 0
        assert _audits_of("n1") == []

    def test_update_unknown_is_404(self, client):
        h = _admin(client)
        r = client.patch("/api/admin/news/nope", headers=h, json={"isTop": True})
        assert r.status_code == 404
        assert r.json()["code"] == 40404


# ---------------------------------------------------------------- 置顶管理


class TestTopToggle:
    def test_patch_is_top_round_trip(self, client):
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        client.patch("/api/admin/news/n1", headers=h, json={"isTop": True})
        with SessionLocal() as db:
            assert db.get(News, "n1").is_top is True
        # 公开列表同步可见（置顶优先）
        data = client.get("/api/news?channel=tech").json()["data"]
        assert data["list"][0]["isTop"] is True
        # 再关回去
        client.patch("/api/admin/news/n1", headers=h, json={"isTop": False})
        with SessionLocal() as db:
            assert db.get(News, "n1").is_top is False

    def test_patch_empty_body_keeps_state(self, client):
        """不传即不改（对齐 SourceUpdateIn 的局部更新语义）。"""
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        client.patch("/api/admin/news/n1", headers=h, json={"isTop": True})
        r = client.patch("/api/admin/news/n1", headers=h, json={})
        assert r.json()["data"]["isTop"] is True

    def test_snake_case_field_is_silently_dropped(self, client):
        """焊死多词入参的驼峰缝：蛇形 `is_top` 会被 pydantic 静默丢弃。

        若模型/入参误写成蛇形，表现为「开关看得见、点得动，但永远不生效」，
        且前后端各自单测都不会失败（前端只断言自己发出的 body）。
        """
        _seed_news([("n1", "资讯一", "2026-10-10T00:00:00+00:00", "tech")])
        h = _admin(client)
        r = client.patch("/api/admin/news/n1", headers=h, json={"is_top": True})
        assert r.json()["data"]["isTop"] is False
        with SessionLocal() as db:
            assert db.get(News, "n1").is_top is False
