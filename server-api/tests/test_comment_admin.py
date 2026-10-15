"""P1-22：评论治理（软隐藏 / 恢复 / 计数联动 / 公开四路过滤 / 审计留痕）。

覆盖五条线：
1. **管理端列表**——筛选（visible/hidden/all）、关键词搜索、newsId 过滤、
   游标分页、已软删除一概不返回、主评论的可见回复数、单条详情；
2. **隐藏与恢复**——往返（理由/时间落库与清空）、理由必填（空/纯空白/超长）、
   trim、幂等（不覆盖原理由、不重复审计）、未知/已删除 40405；
3. **计数联动**——隐藏 -1（下限 0）、恢复 +1、作者删已隐藏评论不重复扣；
4. **公开四路过滤**——主列表（offset + 游标）/ 回复列表 / 预览与计数 /
   对隐藏内容的写操作（回复 40003、点赞 40401），以及输出不暴露隐藏字段；
5. **审计留痕**——comment.hide 带理由与内容片段、comment.restore、审计页可查。

环境由根目录 `conftest.py` 统一固定（SQLite 内存库 / 限流关 / 定时抓取关 /
热度重算关 / ADMIN_PHONES=13800138000）——本文件不再重复设置，
避免"谁先导入谁定稿"的顺序坑。
"""
import json

import pytest
from fastapi.testclient import TestClient

import main
from app.database import Base, SessionLocal, engine
from app.models import AdminAuditLog, Comment, News

ADMIN_PHONE = "13800138000"  # 根 conftest 固定的号段
AUTHOR_PHONE = "13800138001"


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
    client.post(
        "/api/auth/register", json={"phone": phone, "password": "abc123", "name": "作者"}
    )
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _seed_news(rows: list[tuple[str, str, int]]) -> None:
    """直接造资讯行：(id, title, comment_count)。

    comment_count 显式给定——计数联动用例要能钉住「显示数与可见条数一致」。
    """
    with SessionLocal() as db:
        for nid, title, count in rows:
            db.add(
                News(
                    id=nid,
                    title=title,
                    summary=f"{title}的摘要",
                    source="测试源",
                    comment_count=count,
                    time="1小时前",
                    channel="tech",
                    variant="text",
                    published_at="2026-10-01T00:00:00+00:00",
                )
            )
        db.commit()


def _seed_comments(rows: list[tuple]) -> None:
    """直接造评论行（不走发表接口）。

    元组：(id, news_id, content, created_at, user_name[, parent_id, root_id,
    likes, is_deleted])。created_at 用统一格式：游标是字符串 keyset 比较，
    格式不一致会错位。user_id 一律留空（历史无归属评论）——作者归属只在
    「删已隐藏评论」用例里经发表接口真实建立。
    """
    with SessionLocal() as db:
        for row in rows:
            cid, news_id, content, created_at, user_name = row[:5]
            parent_id = row[5] if len(row) > 5 else None
            root_id = row[6] if len(row) > 6 else None
            likes = row[7] if len(row) > 7 else 0
            is_deleted = row[8] if len(row) > 8 else False
            db.add(
                Comment(
                    id=cid,
                    news_id=news_id,
                    user_id=None,
                    user_name=user_name,
                    seed="seed",
                    content=content,
                    time="1小时前",
                    created_at=created_at,
                    likes=likes,
                    parent_id=parent_id,
                    root_id=root_id,
                    is_deleted=is_deleted,
                )
            )
        db.commit()


def _hide(client, h: dict, comment_id: str, reason: str = "涉及不实信息"):
    return client.post(f"/api/admin/comments/{comment_id}/hide", headers=h, json={"reason": reason})


def _restore(client, h: dict, comment_id: str):
    return client.post(f"/api/admin/comments/{comment_id}/restore", headers=h)


def _comment_count(news_id: str) -> int:
    with SessionLocal() as db:
        return db.get(News, news_id).comment_count or 0


def _audits_of(comment_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.target == comment_id)
            .order_by(AdminAuditLog.id)
            .all()
        )
        return [
            {"action": r.action, "detail": json.loads(r.detail), "actor": r.actor_phone}
            for r in rows
        ]


# ---------------------------------------------------------------- 管理端列表


class TestAdminCommentList:
    def test_requires_login(self, client):
        assert client.get("/api/admin/comments").status_code == 401

    def test_non_admin_is_forbidden(self, client):
        h = _user(client, AUTHOR_PHONE)
        r = client.get("/api/admin/comments", headers=h)
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_list_defaults_to_all_with_hidden_fields(self, client):
        """默认 all：运营要能核验自己的处置，不需要多点一下筛选。"""
        _seed_news([("n1", "资讯一", 2)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n1", "评论二", "2026-10-02T00:00:00+00:00", "李四"),
            ]
        )
        h = _admin(client)
        assert _hide(client, h, "c2").json()["code"] == 0
        data = client.get("/api/admin/comments", headers=h).json()["data"]
        assert data["total"] == 2
        by_id = {c["id"]: c for c in data["list"]}
        assert by_id["c1"]["hidden"] is False
        assert by_id["c1"]["hiddenReason"] is None
        assert by_id["c2"]["hidden"] is True
        assert by_id["c2"]["hiddenReason"] == "涉及不实信息"
        assert by_id["c2"]["hiddenAt"]  # 恢复前一直有值
        # 治理认内容不认 id：行上带所属资讯标题与作者名
        assert by_id["c2"]["newsTitle"] == "资讯一"
        assert by_id["c2"]["userName"] == "李四"

    def test_status_filter_visible_and_hidden(self, client):
        _seed_news([("n1", "资讯一", 3)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n1", "评论二", "2026-10-02T00:00:00+00:00", "李四"),
                ("c3", "n1", "评论三", "2026-10-03T00:00:00+00:00", "王五"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "c2")
        vis = client.get("/api/admin/comments?status=visible", headers=h).json()["data"]
        hid = client.get("/api/admin/comments?status=hidden", headers=h).json()["data"]
        assert vis["total"] == 2
        assert all(c["hidden"] is False for c in vis["list"])
        assert hid["total"] == 1
        assert hid["list"][0]["id"] == "c2"

    def test_status_invalid_is_422(self, client):
        h = _admin(client)
        r = client.get("/api/admin/comments?status=foo", headers=h)
        assert r.status_code == 422
        assert r.json()["code"] == 42200

    def test_keyword_matches_content(self, client):
        _seed_news([("n1", "资讯一", 2)])
        _seed_comments(
            [
                ("c1", "n1", "独特的评论词甲", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n1", "普通内容", "2026-10-02T00:00:00+00:00", "李四"),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/comments?keyword=独特的评论词", headers=h).json()["data"]
        assert data["total"] == 1
        assert data["list"][0]["id"] == "c1"

    def test_keyword_percent_is_escaped(self, client):
        """`%` 必须被当字面量：不转义时搜一个百分号等于"匹配全部"。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "百分之百", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        data = client.get("/api/admin/comments?keyword=%25", headers=h).json()["data"]
        assert data["total"] == 0  # 内容里没有字面 %，不应命中

    def test_news_id_filter(self, client):
        _seed_news([("n1", "资讯一", 1), ("n2", "资讯二", 1)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n2", "评论二", "2026-10-02T00:00:00+00:00", "李四"),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/comments?newsId=n1", headers=h).json()["data"]
        assert data["total"] == 1
        assert data["list"][0]["id"] == "c1"

    def test_snake_case_query_is_silently_dropped(self, client):
        """焊死多词查询参数的驼峰缝：蛇形 `news_id` 被当未知参数静默忽略。

        若入参误写成蛇形，表现为「按资讯筛选永远不生效」——不过滤、不报错，
        且前后端各自单测都不会失败（前端只断言自己发出的 URL）。
        """
        _seed_news([("n1", "资讯一", 1), ("n2", "资讯二", 1)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n2", "评论二", "2026-10-02T00:00:00+00:00", "李四"),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/comments?news_id=n1", headers=h).json()["data"]
        assert data["total"] == 2  # 过滤没生效，但也不报错

    def test_deleted_comments_excluded(self, client):
        """已软删除是作者的不可逆行为，对治理视图同样不存在。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c_del", "n1", "删掉的评论", "2026-10-02T00:00:00+00:00", "李四", None, None, 0, True),
            ]
        )
        h = _admin(client)
        data = client.get("/api/admin/comments", headers=h).json()["data"]
        assert data["total"] == 1
        assert [c["id"] for c in data["list"]] == ["c1"]

    def test_cursor_pagination_no_repeats(self, client):
        _seed_news([("n1", "资讯一", 5)])
        _seed_comments(
            [
                (f"c{i}", "n1", f"评论{i}", f"2026-10-{i + 1:02d}T00:00:00+00:00", "张三")
                for i in range(5)
            ]
        )
        h = _admin(client)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            url = "/api/admin/comments?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            data = client.get(url, headers=h).json()["data"]
            seen.extend(c["id"] for c in data["list"])
            pages += 1
            cursor = data["nextCursor"] if data["hasMore"] else None
            if not cursor:
                break
        assert pages == 3  # 2 + 2 + 1
        assert sorted(seen) == [f"c{i}" for i in range(5)]  # 不重不漏
        assert len(set(seen)) == len(seen)

    def test_root_reply_count_counts_visible_only(self, client):
        """主评论行的 replyCount 只数**当前可见**回复（已隐藏不计）。"""
        _seed_news([("n1", "资讯一", 4)])
        _seed_comments(
            [
                ("c1", "n1", "主评论", "2026-10-01T00:00:00+00:00", "张三"),
                ("r1", "n1", "回复一", "2026-10-02T00:00:00+00:00", "李四", None, "c1"),
                ("r2", "n1", "回复二", "2026-10-03T00:00:00+00:00", "王五", None, "c1"),
                ("r3", "n1", "回复三", "2026-10-04T00:00:00+00:00", "赵六", None, "c1"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "r3")
        data = client.get("/api/admin/comments", headers=h).json()["data"]
        by_id = {c["id"]: c for c in data["list"]}
        assert by_id["c1"]["replyCount"] == 2  # r3 已隐藏，不计入

    def test_detail_returns_hidden_fields_and_title(self, client):
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        _hide(client, h, "c1", reason="侵权")
        data = client.get("/api/admin/comments/c1", headers=h).json()["data"]
        assert data["id"] == "c1"
        assert data["newsTitle"] == "资讯一"
        assert data["hidden"] is True
        assert data["hiddenReason"] == "侵权"
        assert data["hiddenAt"]
        assert data["content"] == "评论一"

    def test_detail_unknown_is_404(self, client):
        h = _admin(client)
        r = client.get("/api/admin/comments/nope", headers=h)
        assert r.status_code == 404
        assert r.json()["code"] == 40405


# ---------------------------------------------------------------- 隐藏与恢复


class TestHideRestore:
    def test_hide_then_restore_round_trip(self, client):
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        r = _hide(client, h, "c1")
        assert r.json()["code"] == 0
        assert r.json()["data"]["hidden"] is True
        with SessionLocal() as db:
            row = db.get(Comment, "c1")
            assert row.hidden is True
            assert row.hidden_at and row.hidden_reason == "涉及不实信息"
        r2 = _restore(client, h, "c1")
        assert r2.json()["data"]["hidden"] is False
        with SessionLocal() as db:
            row = db.get(Comment, "c1")
            assert row.hidden is False
            # 理由/时间一并清空：留着会让下一次隐藏的审计读起来张冠李戴
            assert row.hidden_at is None and row.hidden_reason is None

    def test_hide_requires_reason(self, client):
        """留痕是准入条件：空理由（含纯空白）与超长理由一律 422。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        for bad in ("", "   ", "长" * 301):
            r = _hide(client, h, "c1", reason=bad)
            assert r.status_code == 422, bad
            assert r.json()["code"] == 42200
        # 边界：恰好 300 字放行
        ok = _hide(client, h, "c1", reason="长" * 300)
        assert ok.json()["code"] == 0
        assert len(ok.json()["data"]["hiddenReason"]) == 300

    def test_hide_reason_is_trimmed(self, client):
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        data = _hide(client, h, "c1", reason="  涉及侵权  ").json()["data"]
        assert data["hiddenReason"] == "涉及侵权"

    def test_hide_unknown_is_404(self, client):
        h = _admin(client)
        r = _hide(client, h, "nope")
        assert r.status_code == 404
        assert r.json()["code"] == 40405

    def test_hide_deleted_comment_is_404(self, client):
        """作者已软删除的评论对治理视图同样不存在（没有可恢复的状态）。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments(
            [("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三", None, None, 0, True)]
        )
        h = _admin(client)
        r = _hide(client, h, "c1")
        assert r.status_code == 404
        assert r.json()["code"] == 40405

    def test_hide_idempotent_keeps_original_reason_and_audit(self, client):
        """重复隐藏不覆盖原理由、不重复记审计——网络重试/界面双击都不该再造痕迹。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        _hide(client, h, "c1", reason="第一次的理由")
        r = _hide(client, h, "c1", reason="第二次的理由")
        assert r.json()["data"]["hiddenReason"] == "第一次的理由"
        assert len(_audits_of("c1")) == 1

    def test_restore_idempotent(self, client):
        """对未隐藏的评论恢复：成功返回但不记审计。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        r = _restore(client, h, "c1")
        assert r.json()["code"] == 0
        assert r.json()["data"]["hidden"] is False
        assert _audits_of("c1") == []

    def test_restore_unknown_is_404(self, client):
        h = _admin(client)
        r = _restore(client, h, "nope")
        assert r.status_code == 404
        assert r.json()["code"] == 40405

    def test_new_comments_default_visible(self, client):
        """新评论默认可见（ORM default=False；server_default 由迁移守卫把守）。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        data = client.get("/api/admin/comments/c1", headers=h).json()["data"]
        assert data["hidden"] is False


# ---------------------------------------------------------------- 计数联动


class TestCountLinkage:
    def test_hide_decrements_and_restore_increments(self, client):
        """隐藏即从公开视图消失：计数必须同步回退，恢复加回。

        「显示 N 条却只看到 N-1 条」是 P1-12 修过的旧 bug，不能以另一种形式回来。
        """
        _seed_news([("n1", "资讯一", 2)])
        _seed_comments(
            [
                ("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三"),
                ("c2", "n1", "评论二", "2026-10-02T00:00:00+00:00", "李四"),
            ]
        )
        h = _admin(client)
        assert _comment_count("n1") == 2
        _hide(client, h, "c1")
        assert _comment_count("n1") == 1
        _restore(client, h, "c1")
        assert _comment_count("n1") == 2

    def test_hide_count_floor_zero(self, client):
        """下限 0：历史脏数据（计数已为 0）不该被扣成负数。"""
        _seed_news([("n1", "资讯一", 0)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        _hide(client, h, "c1")
        assert _comment_count("n1") == 0

    def test_author_delete_after_hide_does_not_double_decrement(self, client):
        """作者删已隐藏评论：隐藏时已扣过，删除不再重复扣（否则扣穿下限）。"""
        _seed_news([("n1", "资讯一", 0)])
        uh = _user(client, AUTHOR_PHONE)
        cid = client.post(
            "/api/news/n1/comments", json={"content": "作者的评论"}, headers=uh
        ).json()["data"]["id"]
        assert _comment_count("n1") == 1
        h = _admin(client)
        _hide(client, h, cid)  # 隐藏：扣到 0
        assert _comment_count("n1") == 0
        r = client.delete(f"/api/news/n1/comments/{cid}", headers=uh)
        assert r.json()["code"] == 0
        # 删除不重复扣：仍为 0（不是 -1），行也确实软删了
        assert _comment_count("n1") == 0
        with SessionLocal() as db:
            assert db.get(Comment, cid).is_deleted is True


# ---------------------------------------------------------------- 公开四路过滤


class TestPublicFiltering:
    def test_list_excludes_hidden_offset_and_cursor(self, client):
        _seed_news([("n1", "资讯一", 4)])
        _seed_comments(
            [
                (f"c{i}", "n1", f"评论{i}", f"2026-10-{i + 1:02d}T00:00:00+00:00", "张三")
                for i in range(4)
            ]
        )
        h = _admin(client)
        _hide(client, h, "c1")
        # offset 模式：隐藏的直接不在结果里
        data = client.get("/api/news/n1/comments").json()["data"]
        assert sorted(c["id"] for c in data["list"]) == ["c0", "c2", "c3"]
        # 游标模式：翻完全部也不应出现已隐藏
        seen: list[str] = []
        cursor = None
        while True:
            url = "/api/news/n1/comments?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            page = client.get(url).json()["data"]
            seen.extend(c["id"] for c in page["list"])
            cursor = page["nextCursor"] if page["hasMore"] else None
            if not cursor:
                break
        assert sorted(seen) == ["c0", "c2", "c3"]

    def test_replies_empty_when_root_hidden_and_back(self, client):
        """主评论被隐藏后其回复亦不可见（失去语境的回复不该继续裸露）。"""
        _seed_news([("n1", "资讯一", 2)])
        _seed_comments(
            [
                ("c1", "n1", "主评论", "2026-10-01T00:00:00+00:00", "张三"),
                ("r1", "n1", "回复一", "2026-10-02T00:00:00+00:00", "李四", None, "c1"),
            ]
        )
        h = _admin(client)
        assert client.get("/api/news/n1/comments/c1/replies").json()["data"]["list"] != []
        _hide(client, h, "c1")
        data = client.get("/api/news/n1/comments/c1/replies").json()["data"]
        assert data["list"] == []
        _restore(client, h, "c1")
        back = client.get("/api/news/n1/comments/c1/replies").json()["data"]
        assert [c["id"] for c in back["list"]] == ["r1"]

    def test_hidden_reply_excluded_from_count_and_preview(self, client):
        """隐藏的回复不计入 replyCount、不进预览——计数与所见必须一致。"""
        _seed_news([("n1", "资讯一", 3)])
        _seed_comments(
            [
                ("c1", "n1", "主评论", "2026-10-01T00:00:00+00:00", "张三"),
                ("r1", "n1", "回复一", "2026-10-02T00:00:00+00:00", "李四", None, "c1"),
                ("r2", "n1", "回复二", "2026-10-03T00:00:00+00:00", "王五", None, "c1"),
            ]
        )
        h = _admin(client)
        _hide(client, h, "r2")
        item = client.get("/api/news/n1/comments").json()["data"]["list"][0]
        assert item["id"] == "c1"
        assert item["replyCount"] == 1
        assert [r["id"] for r in item["replies"]] == ["r1"]

    def test_reply_to_hidden_comment_is_400(self, client):
        """对一条运营已处置的内容继续盖楼没有意义——像"不存在"一样拒绝。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "主评论", "2026-10-01T00:00:00+00:00", "张三")])
        uh = _user(client, AUTHOR_PHONE)
        h = _admin(client)
        _hide(client, h, "c1")
        r = client.post(
            "/api/news/n1/comments",
            headers=uh,
            json={"content": "还能回复吗", "parentId": "c1"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == 40003

    def test_like_hidden_comment_is_404(self, client):
        """已隐藏的评论不可再点赞（避免为不可见内容累加计数、点赞列表出幽灵）。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        uh = _user(client, AUTHOR_PHONE)
        h = _admin(client)
        _hide(client, h, "c1")
        r = client.patch(
            "/api/news/n1/comments/c1", headers=uh, json={"liked": True}
        )
        assert r.status_code == 404
        assert r.json()["code"] == 40401

    def test_public_out_has_no_hidden_fields(self, client):
        """公开侧一概不暴露隐藏三件套——隐藏的评论不该被读到"它被隐藏了"。"""
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        item = client.get("/api/news/n1/comments").json()["data"]["list"][0]
        assert "hidden" not in item
        assert "hiddenAt" not in item
        assert "hiddenReason" not in item


# ---------------------------------------------------------------- 审计留痕


class TestAuditTrail:
    def test_hide_records_reason_content_and_context(self, client):
        """审计记的是运营认的信息：理由 + 内容片段 + 资讯标题 + 作者名。"""
        _seed_news([("n1", "资讯一", 1)])
        long_content = "违规内容" * 20  # 超过 50 字，验证片段截断
        _seed_comments([("c1", "n1", long_content, "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        _hide(client, h, "c1", reason="含广告信息")
        audits = _audits_of("c1")
        assert len(audits) == 1
        assert audits[0]["action"] == "comment.hide"
        assert audits[0]["detail"]["reason"] == "含广告信息"
        assert audits[0]["detail"]["content"] == long_content[:50]
        assert audits[0]["detail"]["newsTitle"] == "资讯一"
        assert audits[0]["detail"]["userName"] == "张三"
        assert audits[0]["actor"] == ADMIN_PHONE
        # 审计页按 target 可查（前端入口）
        page = client.get("/api/admin/audit?target=c1", headers=h).json()["data"]
        assert page["list"][0]["action"] == "comment.hide"

    def test_restore_records_audit(self, client):
        _seed_news([("n1", "资讯一", 1)])
        _seed_comments([("c1", "n1", "评论一", "2026-10-01T00:00:00+00:00", "张三")])
        h = _admin(client)
        _hide(client, h, "c1")
        _restore(client, h, "c1")
        audits = _audits_of("c1")
        assert [a["action"] for a in audits] == ["comment.hide", "comment.restore"]
