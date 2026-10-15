"""P1-23：评论敏感词审核（归一化匹配 / 拦截 / 脱敏待复核 / 词库热更新 / 命中日志）。

覆盖八条线：
1. **归一化与防绕过**——全半角、大小写、空格、标点、零宽字符干扰下仍然命中；
2. **block**——40005 拒绝提交、不落库、提示不回显命中词、**拦截也留痕**；
3. **mask**——脱敏后照常发布、置待复核、计数照常、日志留原文片段；
4. **allow 白名单**——豁免区间内的命中（降误杀的主要手段）；
5. **昵称检测**——只脱敏展示名、不阻断发言（昵称不是用户此刻能改的东西）；
6. **热更新**——新增/停用词后下一次提交即生效，无需重启；
7. **词库管理与权限**——CRUD、太短 42202、重复 40905、审计、非管理员 403；
8. **待复核与复核通过**——pending 筛选、幂等复核、审计留痕。

环境由根目录 `conftest.py` 统一固定。本文件另外把词库缓存 TTL 压到 0：
单测里"改了词库立刻发评论"必须稳定生效，不能依赖 30 秒的 TTL 恰好过期。

⚠️ 词库是**进程内单例缓存**：直接往库里插词的用例必须显式 `engine.invalidate()`，
否则测的是上一轮的索引（走管理端接口新增则不需要——接口自己会失效缓存）。
"""
import pytest
from fastapi.testclient import TestClient

import main
from app.database import Base, SessionLocal, engine as db_engine
from app.models import AdminAuditLog, SensitiveHit, SensitiveWord, now_str
from app.moderation import engine as mod_engine

ADMIN_PHONE = "13800138000"  # 根 conftest 固定的号段
USER_PHONE = "13800138001"
NEWS_ID = "n1"


@pytest.fixture
def client(monkeypatch):
    # TTL=0：每次检测都回库查指纹，避免"改了词但缓存没到期"的偶发失败
    monkeypatch.setattr("app.moderation.MODERATION_CACHE_TTL_SECONDS", 0)
    Base.metadata.drop_all(bind=db_engine)
    Base.metadata.create_all(bind=db_engine)
    mod_engine.invalidate()
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=db_engine)


def _headers(client, phone: str, name: str = "用户") -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": name})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _admin(client) -> dict:
    return _headers(client, ADMIN_PHONE, "管理员")


def _seed_news() -> None:
    from app.models import News

    with SessionLocal() as db:
        db.add(
            News(
                id=NEWS_ID,
                title="资讯一",
                summary="摘要",
                source="测试源",
                comment_count=0,
                time="1小时前",
                channel="tech",
                variant="text",
                published_at="2026-10-01T00:00:00+00:00",
            )
        )
        db.commit()


def _add_word(word: str, action: str = "mask", enabled: bool = True) -> int:
    """直接入库造词（绕过接口），随后必须 `mod_engine.invalidate()`。"""
    with SessionLocal() as db:
        row = SensitiveWord(
            word=word,
            action=action,
            category="测试",
            enabled=enabled,
            hit_count=0,
            created_by=ADMIN_PHONE,
            created_at=now_str(),
            updated_at=now_str(),
        )
        db.add(row)
        db.commit()
        wid = row.id
    mod_engine.invalidate()
    return wid


def _post(client, headers: dict, content: str) -> dict:
    r = client.post(f"/api/news/{NEWS_ID}/comments", json={"content": content}, headers=headers)
    return r.json()


# ---------------------------------------------------------------- 1. 归一化与防绕过


@pytest.mark.parametrize(
    "variant",
    [
        "傻逼",  # 原样
        "傻 逼",  # 空格
        "傻-逼",  # 标点
        "傻\u200b逼",  # 零宽字符
        "傻\u3000逼",  # 全角空格
    ],
)
def test_bypass_variants_all_blocked(client, variant):
    """同一句话的五种写法必须得到同一个判定——归一化是防绕过的主力。"""
    _seed_news()
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, f"你这个{variant}")
    assert r["code"] == 40005
    # 提示**不回显**命中词：回显等于给绕过者一份对照表
    assert "傻" not in r["message"]


def test_fullwidth_and_case_normalized(client):
    """全角 + 大写 + 点号隔断：`Ｆ．Ｕ．Ｃ．Ｋ` 与 `fuck` 同判。"""
    _seed_news()
    _add_word("fuck", action="block")
    h = _headers(client, USER_PHONE)
    assert _post(client, h, "ＦＵＣＫ you")["code"] == 40005
    assert _post(client, h, "f.u.c.k you")["code"] == 40005
    assert _post(client, h, "F u C k")["code"] == 40005


def test_block_does_not_create_comment(client):
    """拦截后库里不留评论（计数也不动）——否则"拦了"只是假象。"""
    _seed_news()
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    assert _post(client, h, "你这个傻逼")["code"] == 40005
    listing = client.get(f"/api/news/{NEWS_ID}/comments").json()["data"]
    assert listing["list"] == []


def test_block_still_logs_hit(client):
    """拦截必须留痕：否则"拦了多少人"是黑箱，也认不出反复试探的行为。"""
    _seed_news()
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    _post(client, h, "你这个傻逼")
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]
    assert hits["total"] == 1
    row = hits["list"][0]
    assert row["word"] == "傻逼"
    assert row["action"] == "block"
    assert row["field"] == "content"
    # 没写成评论 → comment_id 为空
    assert row["commentId"] is None
    # 原文片段：脱敏不可逆，复盘/申诉全靠它
    assert "傻逼" in row["snippet"]


def test_hit_count_increments(client):
    """词的命中次数累加——高频词是热点还是误杀大户，运营得看得见。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    _post(client, h, "广告一")
    _post(client, h, "广告二")
    words = client.get("/api/admin/words", headers=_admin(client)).json()["data"]["list"]
    assert words[0]["hitCount"] == 2
    # 两次提交、同一个词 → 两行日志（各行对应一条评论）
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]
    assert hits["total"] == 2


# ---------------------------------------------------------------- 2. mask 与待复核


def test_mask_replaces_and_publishes(client):
    """mask：脱敏后**照常发布**（不是"吞掉用户的话"），并置待复核。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, "这是广告内容")
    assert r["code"] == 0
    assert r["data"]["content"] == "这是**内容"
    assert r["data"]["pendingReview"] is True


def test_mask_length_follows_word_not_variant(client):
    """脱敏长度取**词长**，不随变形方式漂移（否则等于告诉对方绕过去几个字符）。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    assert _post(client, h, "看广 告")["data"]["content"] == "看**"
    assert _post(client, h, "看广告")["data"]["content"] == "看**"


def test_masked_comment_is_visible_and_counted(client):
    """脱敏评论仍然可见且计入评论数——复核前不该让它凭空消失。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    _post(client, h, "这是广告内容")
    listing = client.get(f"/api/news/{NEWS_ID}/comments").json()["data"]["list"]
    assert len(listing) == 1
    from app.models import News

    with SessionLocal() as db:
        assert db.query(News).filter(News.id == NEWS_ID).first().comment_count == 1


def test_mask_hit_log_keeps_original(client):
    """脱敏不可逆，命中日志必须留原文片段，否则误杀无法复盘。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    created = _post(client, h, "这是广告内容")["data"]
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]["list"]
    assert hits[0]["snippet"] == "这是广告内容"
    assert hits[0]["commentId"] == created["id"]


def test_multiple_words_single_submission(client):
    """一次提交命中多个词：每个词一行日志，正文按区间全部脱敏。"""
    _seed_news()
    _add_word("广告")
    _add_word("加微信")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, "广告内容，加微信详谈")
    assert r["data"]["content"] == "**内容，***详谈"
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]
    assert hits["total"] == 2


def test_same_word_twice_one_log_row(client):
    """同一词在同一条评论里出现两次 → 只记一行（运营看的是"命中了什么"）。"""
    _seed_news()
    _add_word("广告")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, "广告广告")
    assert r["data"]["content"] == "****"
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]
    assert hits["total"] == 1


def test_overlapping_words_merge(client):
    """重叠命中（「傻逼」与「傻」）合并为一段，不重复替换。"""
    _seed_news()
    _add_word("傻逼", action="mask")
    _add_word("傻", action="block")
    h = _headers(client, USER_PHONE)
    # 区间重叠时取**最严**：block 优先
    assert _post(client, h, "你这个傻逼")["code"] == 40005


# ---------------------------------------------------------------- 3. allow 白名单（降误杀）


def test_allow_exempts_longer_phrase(client):
    """白名单是降误杀的主要手段：「射」命中「射击」时，后者豁免。"""
    _seed_news()
    _add_word("射", action="mask")
    _add_word("射击", action="allow")
    h = _headers(client, USER_PHONE)
    ok = _post(client, h, "射击比赛很好看")
    assert ok["code"] == 0
    assert ok["data"]["content"] == "射击比赛很好看"
    assert ok["data"]["pendingReview"] is False
    # 单独出现「射」仍然命中
    assert _post(client, h, "射得准")["data"]["content"] == "*得准"


def test_allow_word_not_logged(client):
    """白名单命中不是"风险事件"，不该污染命中日志。"""
    _seed_news()
    _add_word("射击", action="allow")
    h = _headers(client, USER_PHONE)
    _post(client, h, "射击比赛")
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]
    assert hits["total"] == 0


# ---------------------------------------------------------------- 4. 昵称检测


def test_name_hit_masks_display_name_only(client):
    """昵称命中：只脱敏这条评论的展示名，**不阻断发言**。

    昵称是账号级属性，用户没法在评论框里改它——为昵称拒绝发言等于无声禁言。
    """
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE, name="广告哥")
    r = _post(client, h, "正常的一句话")
    assert r["code"] == 0
    assert r["data"]["name"] == "**哥"
    assert r["data"]["pendingReview"] is True
    hits = client.get("/api/admin/moderation/hits", headers=_admin(client)).json()["data"]["list"]
    assert hits[0]["field"] == "name"


def test_name_block_word_degrades_to_mask(client):
    """昵称命中 block 词也只脱敏：绝不因昵称把人挡在评论区外。"""
    _seed_news()
    _add_word("广告", action="block")
    h = _headers(client, USER_PHONE, name="广告哥")
    r = _post(client, h, "正常的一句话")
    assert r["code"] == 0
    assert r["data"]["name"] == "**哥"


def test_name_check_can_be_disabled(client, monkeypatch):
    """昵称检测可关（昵称的语言组合远比正文随机，误杀敏感的场景需要这个开关）。"""
    monkeypatch.setattr("app.moderation.MODERATION_CHECK_NAME", False)
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE, name="广告哥")
    r = _post(client, h, "正常的一句话")
    assert r["data"]["name"] == "广告哥"
    assert r["data"]["pendingReview"] is False


# ---------------------------------------------------------------- 5. 热更新


def test_new_word_effective_immediately(client):
    """加词后下一次提交即生效（保存即生效，不需要重启服务）。"""
    _seed_news()
    h = _headers(client, USER_PHONE)
    assert _post(client, h, "广告内容")["code"] == 0  # 词库为空时放行
    admin = _admin(client)
    r = client.post("/api/admin/words", json={"word": "广告", "action": "block"}, headers=admin)
    assert r.json()["code"] == 0
    assert _post(client, h, "广告内容")["code"] == 40005  # 立刻生效


def test_disabling_word_takes_effect(client):
    """停用词（而非删除）同样立即生效——删错了要能一秒回滚。"""
    _seed_news()
    h = _headers(client, USER_PHONE)
    admin = _admin(client)
    created = client.post(
        "/api/admin/words", json={"word": "广告", "action": "block"}, headers=admin
    ).json()["data"]
    assert _post(client, h, "广告内容")["code"] == 40005
    client.patch(f"/api/admin/words/{created['id']}", json={"enabled": False}, headers=admin)
    assert _post(client, h, "广告内容")["code"] == 0


def test_reload_endpoint_returns_count(client):
    """reload 供集群下其他实例手动同步（本实例改词已自动失效缓存）。"""
    _seed_news()
    admin = _admin(client)
    client.post("/api/admin/words", json={"word": "广告"}, headers=admin)
    r = client.post("/api/admin/words/reload", headers=admin)
    assert r.json()["data"]["count"] == 1
    with SessionLocal() as db:
        acts = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "word.reload")
            .all()
        )
        assert len(acts) == 1


# ---------------------------------------------------------------- 6. 词库管理与权限


def test_create_word_and_audit(client):
    _seed_news()
    admin = _admin(client)
    r = client.post(
        "/api/admin/words",
        json={"word": "加微信", "action": "block", "category": "引流"},
        headers=admin,
    )
    assert r.json()["code"] == 0
    data = r.json()["data"]
    assert data["action"] == "block"
    assert data["category"] == "引流"
    assert data["hitCount"] == 0
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "word.create")
            .first()
        )
        assert audit.target == "加微信"
        assert audit.detail  # 留痕非空


def test_create_word_too_short_rejected(client):
    """1 字词误杀率不可接受（正常表达里遍地都是），挡在入库这一步。"""
    admin = _admin(client)
    r = client.post("/api/admin/words", json={"word": "射"}, headers=admin)
    assert r.json()["code"] == 42202
    # 纯符号词归一化后为空，同样挡住（否则运营会以为它生效了）
    r2 = client.post("/api/admin/words", json={"word": "***"}, headers=admin)
    assert r2.json()["code"] == 42202


def test_create_word_duplicate_rejected(client):
    admin = _admin(client)
    client.post("/api/admin/words", json={"word": "广告"}, headers=admin)
    r = client.post("/api/admin/words", json={"word": "广告"}, headers=admin)
    assert r.json()["code"] == 40905


def test_update_and_delete_word(client):
    admin = _admin(client)
    created = client.post("/api/admin/words", json={"word": "广告"}, headers=admin).json()["data"]
    assert created["action"] == "mask"  # 默认 mask：机器判断是概率性的
    upd = client.patch(
        f"/api/admin/words/{created['id']}", json={"action": "block"}, headers=admin
    ).json()
    assert upd["data"]["action"] == "block"
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "word.update")
            .first()
        )
        assert "action" in audit.detail
    deleted = client.delete(f"/api/admin/words/{created['id']}", headers=admin)
    assert deleted.json()["code"] == 0
    assert client.get("/api/admin/words", headers=admin).json()["data"]["total"] == 0


def test_word_not_found(client):
    admin = _admin(client)
    assert client.patch("/api/admin/words/9999", json={"action": "block"}, headers=admin).json()[
        "code"
    ] == 40406
    assert client.delete("/api/admin/words/9999", headers=admin).json()["code"] == 40406


def test_words_require_admin(client):
    """词库是高危配置：非管理员既不能读也不能改（读也禁止——词表本身是情报）。"""
    h = _headers(client, USER_PHONE)
    assert client.get("/api/admin/words", headers=h).json()["code"] == 40301
    assert client.post("/api/admin/words", json={"word": "广告"}, headers=h).json()["code"] == 40301
    assert client.get("/api/admin/moderation/hits", headers=h).json()["code"] == 40301
    assert client.post("/api/admin/words/reload", headers=h).json()["code"] == 40301


def test_list_words_filters(client):
    admin = _admin(client)
    client.post("/api/admin/words", json={"word": "广告", "action": "block"}, headers=admin)
    client.post("/api/admin/words", json={"word": "傻逼", "action": "mask"}, headers=admin)
    client.post(
        "/api/admin/words", json={"word": "加微信", "action": "mask", "enabled": False},
        headers=admin,
    )
    allw = client.get("/api/admin/words", headers=admin).json()["data"]
    assert allw["total"] == 3
    blocked = client.get("/api/admin/words?action=block", headers=admin).json()["data"]
    assert blocked["total"] == 1 and blocked["list"][0]["word"] == "广告"
    enabled_only = client.get("/api/admin/words?enabled=true", headers=admin).json()["data"]
    assert enabled_only["total"] == 2


def test_hits_filters(client):
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE, name="广告哥")
    _post(client, h, "广告内容")
    admin = _admin(client)
    assert client.get("/api/admin/moderation/hits?field=name", headers=admin).json()["data"][
        "total"
    ] == 1
    assert client.get("/api/admin/moderation/hits?action=block", headers=admin).json()["data"][
        "total"
    ] == 0
    assert client.get("/api/admin/moderation/hits?word=广告", headers=admin).json()["data"][
        "total"
    ] == 2


# ---------------------------------------------------------------- 7. 待复核与复核通过


def test_pending_filter_and_review(client):
    """待复核队列：脱敏评论能被筛出来，人工确认后清除标记并留痕。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    created = _post(client, h, "广告内容")["data"]
    _post(client, h, "完全正常的一句话")
    admin = _admin(client)
    pending = client.get("/api/admin/comments?status=pending", headers=admin).json()["data"]
    assert pending["total"] == 1
    assert pending["list"][0]["id"] == created["id"]
    assert pending["list"][0]["pendingReview"] is True

    r = client.post(f"/api/admin/comments/{created['id']}/review", headers=admin)
    assert r.json()["code"] == 0
    assert r.json()["data"]["pendingReview"] is False
    assert r.json()["data"]["reviewedAt"]
    after = client.get("/api/admin/comments?status=pending", headers=admin).json()["data"]
    assert after["total"] == 0
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "comment.review")
            .first()
        )
        assert audit.target == created["id"]


def test_review_idempotent(client):
    """重复复核不重复记审计（幂等，与 hide/restore 同一处方）。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    created = _post(client, h, "广告内容")["data"]
    admin = _admin(client)
    client.post(f"/api/admin/comments/{created['id']}/review", headers=admin)
    client.post(f"/api/admin/comments/{created['id']}/review", headers=admin)
    with SessionLocal() as db:
        assert (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "comment.review")
            .count()
        ) == 1


def test_review_missing_comment(client):
    admin = _admin(client)
    assert client.post("/api/admin/comments/c_not_exist/review", headers=admin).json()[
        "code"
    ] == 40405


# ---------------------------------------------------------------- 8. 总开关与边界


def test_switch_off_skips_check(client, monkeypatch):
    """一键止血：关掉开关后不再检测（大面积误杀时的应急手段，无需发版）。"""
    monkeypatch.setattr("app.moderation.MODERATION_ENABLED", False)
    monkeypatch.setattr("app.routers.comments.MODERATION_ENABLED", False)
    _seed_news()
    _add_word("广告", action="block")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, "广告内容")
    assert r["code"] == 0
    assert r["data"]["content"] == "广告内容"  # 未脱敏


def test_reply_also_checked(client):
    """回复走同一个写入口，同样受检（否则"换个地方发"就是免费绕过）。"""
    _seed_news()
    h = _headers(client, USER_PHONE)
    parent = _post(client, h, "主评论")["data"]
    _add_word("广告", action="block")
    r = client.post(
        f"/api/news/{NEWS_ID}/comments",
        json={"content": "广告回复", "parentId": parent["id"]},
        headers=h,
    )
    assert r.json()["code"] == 40005


def test_clean_comment_untouched(client):
    """正常评论零影响：既不脱敏也不待复核（误杀是这套机制最大的风险）。"""
    _seed_news()
    _add_word("广告", action="mask")
    h = _headers(client, USER_PHONE)
    r = _post(client, h, "这篇文章写得很好，感谢分享")
    assert r["data"]["content"] == "这篇文章写得很好，感谢分享"
    assert r["data"]["pendingReview"] is False
    with SessionLocal() as db:
        assert db.query(SensitiveHit).count() == 0
