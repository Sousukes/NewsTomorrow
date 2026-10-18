"""P1-24：用户昵称敏感词校验（防绕过归一化 / 形状边界 / 三级分级 / 注册与改名双入口 /
词库管理 / 命中日志 / 复核队列 / 引擎内部）。

覆盖十条线：
1. **归一化与防绕过**——空格/标点/全半角/大小写/重复压缩/数字插字/leetspeak/繁简
   （含日语新字体）/别名（拼音谐音）下同一名字得到同一判定；
2. **形状校验**——空/纯空白/单字/超长/emoji/控制字符/零宽/纯符号/代理字符一律
   42203，合法符号与边界长度放行；
3. **分级语义**——block（40006、不回显命中词、旧名不动）/ review（放行置位）/
   allow（完全包裹才豁免）/ pass（含干净昵称零误判）；
4. **注册接入**——block 不留账号可立即换名重试、review 置位并下发到 token、
   空值走默认名、形状不合法 42203；
5. **改名接口**——通过即清待复核标志、拦截不动旧名、同名重设一视同仁；
6. **热更新**——管理端增/停/删词后下一次保存即生效，reload 返回计数；
7. **词库管理**——CRUD、42204（主词与别名分别）、40906、40407、不改词本身、
   审计、非管理员 403；
8. **命中日志**——block 与 review 均留痕、matched 记别名、多形态命中只记一行、
   筛选与游标、hit_count 累加；
9. **复核队列**——flaggedName/flaggedMatched、改干净名即出队、幂等复核、
   40408、block 用户不入队；
10. **引擎内部**——撞车保留更严者且只警告一次、每形态最短长度把关、超限截断、
    压缩形态开关、未知动作降级 review、alias_list 脏数据、check 不写库。

环境由根目录 `conftest.py` 统一固定。本文件另外把词库缓存 TTL 压到 0：
单测里"改了词库立刻改昵称"必须稳定生效，不能依赖 30 秒的 TTL 恰好过期。

⚠️ 词库是**进程内单例缓存**：直接往库里插词的用例必须显式 `nick_engine.invalidate()`，
否则测的是上一轮的索引（走管理端接口新增则不需要——接口自己会失效缓存）。
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

import main
from app.config import NICKNAME_MAX_LENGTH, NICKNAME_MIN_LENGTH
from app.database import Base, SessionLocal, engine as db_engine
from app.models import AdminAuditLog, NicknameHit, NicknameWord, User, now_str
from app.nickname_moderation import (
    MESSAGE_REVIEW,
    RESULT_BLOCK,
    RESULT_PASS,
    RESULT_REVIEW,
    alias_list,
    engine as nick_engine,
    validate_nickname_shape,
)

ADMIN_PHONE = "13800138000"  # 根 conftest 固定的号段
USER_PHONE = "13800138001"
USER_PHONE2 = "13800138002"
PASSWORD = "abc123"


@pytest.fixture
def client(monkeypatch):
    # TTL=0：每次检测都回库查指纹，避免"改了词但缓存没到期"的偶发失败
    monkeypatch.setattr("app.nickname_moderation.NICKNAME_CACHE_TTL_SECONDS", 0)
    Base.metadata.drop_all(bind=db_engine)
    Base.metadata.create_all(bind=db_engine)
    nick_engine.invalidate()
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=db_engine)
    # 收尾必须再失效一次：引擎是进程内单例，跨测试文件共享。不清的话，
    # 后续文件注册用户时 TTL 已恢复 30s、_checked_at 却是本文件刚摸过的，
    # 指纹查询被跳过 → 拿本文件的词库去扫别人的昵称（静默串味）。
    nick_engine.invalidate()


def _headers(client, phone: str, name: str = "用户") -> dict:
    client.post(
        "/api/auth/register", json={"phone": phone, "password": PASSWORD, "name": name}
    )
    r = client.post("/api/auth/login", json={"phone": phone, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _step_up(client, headers: dict) -> dict:
    """二次校验（C10）：改名端点要求 `X-Step-Up-Token`，测试先换一张。"""
    r = client.post("/api/auth/step-up", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200, r.text
    return {**headers, "X-Step-Up-Token": r.json()["data"]["stepUpToken"]}


def _admin(client) -> dict:
    return _headers(client, ADMIN_PHONE, "管理员")


def _user_id(client, phone: str) -> int:
    r = client.post("/api/auth/login", json={"phone": phone, "password": PASSWORD})
    return r.json()["data"]["user"]["id"]


def _add_word(
    word: str,
    action: str = "review",
    category: str = "",
    aliases: list[str] | None = None,
    enabled: bool = True,
) -> None:
    """直接入库造词（绕过接口），随后必须 `nick_engine.invalidate()`。"""
    with SessionLocal() as db:
        db.add(
            NicknameWord(
                word=word,
                action=action,
                category=category,
                aliases=aliases or [],
                enabled=enabled,
                hit_count=0,
                created_by=ADMIN_PHONE,
                created_at=now_str(),
                updated_at=now_str(),
            )
        )
        db.commit()
    nick_engine.invalidate()


def _change(client, headers: dict, name: str):
    return client.put("/api/me/nickname", json={"name": name}, headers=_step_up(client, headers))


def _code(r) -> int:
    return r.json()["code"]


def _audit_detail(row) -> dict:
    """审计 detail 是 **JSON 字符串列**（String(1000)，不是 JSONType）——
    读出来必须 parse，直接下标会拿到字符串的字符。"""
    return json.loads(row.detail)


# ---------------------------------------------------------------- 1. 归一化与防绕过


@pytest.mark.parametrize(
    "variant",
    [
        "傻逼",  # 原样
        "傻 逼",  # 内部空格
        "傻　逼",  # 全角空格（NFKC 归一）
        "傻-逼",  # 连字符（昵称合法符号）
        "傻·逼",  # 间隔号
        "傻_逼",  # 下划线
        "傻.逼",  # 句点
        "傻・逼",  # 片假名中点
        "傻傻傻逼逼逼",  # 重复字符（collapsed 形态，子串已断裂）
        "SHABI",  # 大写（别名 shabi）
        "ShAbI",  # 混合大小写
        "5habi",  # 数字混淆（5→s）
        "5h4b1",  # 数字混淆（5→s、4→a、1→i）
    ],
)
def test_bypass_variants_all_blocked(client, variant):
    """同一个词的十三种写法必须得到同一个判定——归一化是防绕过的主力。

    零宽字符/控制字符不走这条线：它们过不了形状白名单（见第 2 线），
    在 HTTP 层就被 42203 挡掉，根本到不了匹配引擎。
    """
    _add_word("傻逼", action="block", aliases=["shabi"])
    h = _headers(client, USER_PHONE)
    r = _change(client, h, variant)
    assert _code(r) == 40006, variant
    # 提示**不回显**命中词：回显等于给绕过者一份对照表
    msg = r.json()["message"]
    assert "傻" not in msg and "逼" not in msg
    assert "shabi" not in msg.lower()


def test_fullwidth_and_case_normalized(client):
    """全角 + 大写 + 点号隔断：`ＦＵＣＫ` 与 `fuck` 同判。"""
    _add_word("fuck", action="block")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "ＦＵＣＫ")) == 40006
    assert _code(_change(client, h, "F.u.c.k")) == 40006
    assert _code(_change(client, h, "F u C k")) == 40006


def test_digit_insertion_blocked(client):
    """数字插字（博0彩）：n digits 形态把数字来源字符整体删掉。

    与 leetspeak 归并互补——leetspeak 把 0 当字母 o 用，本形态把 0 当噪声删掉。
    """
    _add_word("博彩", action="block")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "博0彩")) == 40006
    assert _code(_change(client, h, "博1彩")) == 40006


def test_traditional_and_shinjitai_variants_blocked(client):
    """繁体/日语新字体两条路：内置 curated 繁→简映射覆盖常用字；未覆盖的
    变体走词条别名（完整 OpenCC 表要数千项且随 Unicode 维护，这里零依赖）。"""
    _add_word("坏蛋", action="block")
    _add_word("广告", action="block", aliases=["廣告"])  # 廣 不在 curated 表内 → 别名兜底
    _add_word("干净", action="block")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "壞蛋哥")) == 40006  # 映射表覆盖（壞→坏）
    assert _code(_change(client, h, "廣告哥")) == 40006  # 别名兜底
    assert _code(_change(client, h, "干浄")) == 40006  # 日语新字体（浄→净）


def test_alias_homophone_flagged(client):
    """同音字替换（煞笔）没有字符级算法可解——词条别名是既定路线。"""
    _add_word("傻逼", action="review", aliases=["shabi", "煞笔"])
    h = _headers(client, USER_PHONE)
    r = _change(client, h, "煞笔哥")
    assert _code(r) == 0  # review 级：放行但留痕
    assert r.json()["data"]["result"] == RESULT_REVIEW
    admin = _admin(client)
    row = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]["list"][0]
    assert row["matched"] == "煞笔"


def test_clean_names_not_flagged(client):
    """正常昵称零误判——误杀是这套机制最大的风险，比漏放更贵。"""
    _add_word("傻逼", action="block", aliases=["shabi"])
    _add_word("广告", action="block")
    h = _headers(client, USER_PHONE)
    for name in ["小明同学", "努力搬砖人", "Tom2024", "推广小助手", "阿伟"]:
        r = _change(client, h, name)
        assert _code(r) == 0, name


# ---------------------------------------------------------------- 2. 形状校验


def test_shape_rejected_via_api(client):
    """形状不合法一律 42203，且消息即面向用户的提示（不是英文校验错误）。"""
    h = _headers(client, USER_PHONE)
    cases = [
        "",  # 空值
        "   ",  # 纯空白
        "甲",  # 单字（下限 NICKNAME_MIN_LENGTH=2）
        "a" * (NICKNAME_MAX_LENGTH + 1),  # 超长（上限 24）
        "😀小明",  # emoji（So）
        "小\x00明",  # 控制字符（Cc）
        "____",  # 纯符号
        "小\u200b明",  # 零宽字符（Cf，形状白名单不含）
    ]
    for name in cases:
        r = _change(client, h, name)
        assert _code(r) == 42203, repr(name)
        assert r.json()["message"]  # 面向用户的提示


def test_shape_boundaries_accepted(client):
    """边界长度与合法符号放行；首尾空白先 trim 再校验。"""
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "啊" * NICKNAME_MAX_LENGTH)) == 0
    assert _code(_change(client, h, "啊" * NICKNAME_MIN_LENGTH)) == 0
    assert _code(_change(client, h, "小_明-2024")) == 0
    assert _code(_change(client, h, "小 明")) == 0  # 内部空格
    assert _code(_change(client, h, "小·明")) == 0
    assert _code(_change(client, h, "小・明")) == 0
    assert _change(client, h, "  小明  ").json()["data"]["name"] == "小明"


def test_shape_rejects_lone_surrogate():
    """代理字符（异常编码）不在白名单。JSON 带不进来（UTF-8 编码就炸），
    故直接验函数——这是唯一必须绕开 HTTP 的边界。"""
    ok, msg = validate_nickname_shape("\ud800小明")
    assert ok is False
    assert msg


def test_shape_rejects_non_string():
    ok, _ = validate_nickname_shape(None)
    assert ok is False


# ---------------------------------------------------------------- 3. 分级语义


def test_block_rejected_and_old_name_kept(client):
    """block：40006 拒绝、提示不回显命中词、旧名原样不动。"""
    _add_word("傻逼", action="block", category="辱骂")
    h = _headers(client, USER_PHONE)
    _change(client, h, "原来名字")
    r = _change(client, h, "傻逼哥")
    assert _code(r) == 40006
    assert "傻" not in r.json()["message"]
    with SessionLocal() as db:
        assert db.query(User).filter(User.phone == USER_PHONE).first().name == "原来名字"


def test_review_passes_and_flags(client):
    """review：**放行** + 置待复核 + 提示说明需人工复核（机器的判断是概率性的）。"""
    _add_word("广告", action="review", category="广告")
    h = _headers(client, USER_PHONE)
    r = _change(client, h, "广告哥")
    assert _code(r) == 0
    d = r.json()["data"]
    assert d["result"] == RESULT_REVIEW
    assert d["pendingReview"] is True
    assert d["reason"] == "包含广告内容，需人工复核"
    assert r.json()["message"] == MESSAGE_REVIEW


def test_pass_clears_pending_flag(client):
    """改成干净名字即清标志：标志只描述**当前名字**，历史在命中日志里。"""
    _add_word("广告", action="review")
    h = _headers(client, USER_PHONE)
    assert _change(client, h, "广告哥").json()["data"]["pendingReview"] is True
    d = _change(client, h, "小明同学").json()["data"]
    assert d["result"] == RESULT_PASS
    assert d["pendingReview"] is False
    admin = _admin(client)
    assert client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["total"] == 0


def test_allow_exempts_wrapped_hit(client):
    """白名单是降误杀的逃生门：命中区间被 allow 区间**完全包裹**才豁免。

    「射手」单独出现要拦，「射手座」（星座昵称）是正常表达——为它配一条
    allow 词，比把「射手」从词库里删掉精确得多。
    """
    _add_word("射手", action="block")
    _add_word("射手座", action="allow")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "射手座")) == 0  # 被包裹 → 放行
    assert _code(_change(client, h, "射手")) == 40006  # 未包裹 → 拦截
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]
    # allow 命中不是"风险事件"，不污染日志；只有 block 那条
    assert hits["total"] == 1
    assert hits["list"][0]["word"] == "射手"


def test_multiple_words_worst_wins(client):
    """一次提交命中多个词：结果取最严（block 压过 review），每个词各记一行日志。"""
    _add_word("广告", action="review")
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "广告傻逼")) == 40006
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]
    assert hits["total"] == 2


def test_multi_form_hit_logged_once(client):
    """同一段被多个形态同时命中（base + collapsed）→ 合并为一次命中、一行日志。

    不合并会把同一段算成多个命中，日志与 reason 都虚高。
    """
    _add_word("傻逼", action="review")
    h = _headers(client, USER_PHONE)
    assert _change(client, h, "傻傻傻逼逼逼").json()["data"]["result"] == RESULT_REVIEW
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]
    assert hits["total"] == 1


def test_reset_same_name_allowed(client):
    """同名重设不做特殊分支：保存是显式动作，对提交值一视同仁。"""
    _add_word("广告", action="review")
    h = _headers(client, USER_PHONE)
    assert _change(client, h, "广告哥").json()["data"]["result"] == RESULT_REVIEW
    r = _change(client, h, "广告哥")
    assert _code(r) == 0
    assert r.json()["data"]["result"] == RESULT_REVIEW
    admin = _admin(client)
    # 两次提交各记一行（日志是事件，不是状态）
    assert client.get("/api/admin/nickname-hits", headers=admin).json()["data"]["total"] == 2


def test_change_requires_step_up(client):
    """C10：改名要二次校验。缺令牌 → 403 + 40304（不是 401：会话是好的）。"""
    h = _headers(client, USER_PHONE)
    r = client.put("/api/me/nickname", json={"name": "新名字"}, headers=h)
    assert r.status_code == 403
    assert r.json()["code"] == 40304
    # 名字没被改掉
    me = client.get("/api/me/favorites", headers=h)
    assert me.status_code == 200
    # 换取令牌后同一请求即通过（证明不是接口坏了）
    assert _change(client, h, "新名字").json()["code"] == 0


def test_step_up_token_is_single_purpose(client):
    """C10：step-up 令牌不能当 access 用（类型隔离，与 C1 同款）。"""
    h = _headers(client, USER_PHONE)
    su = _step_up(client, h)
    r = client.get("/api/me/favorites", headers={"Authorization": f"Bearer {su['X-Step-Up-Token']}"})
    assert r.status_code == 401


# ---------------------------------------------------------------- 4. 注册接入


def test_register_blocked_name_creates_no_account(client):
    """拦注册而非静默换默认名：手机号未落库，换个昵称可立即重试。"""
    _add_word("傻逼", action="block")
    r = client.post(
        "/api/auth/register", json={"phone": USER_PHONE, "password": PASSWORD, "name": "傻逼哥"}
    )
    assert _code(r) == 40006
    assert "傻" not in r.json()["message"]
    # 账号未落库 → 同号换名直接成功
    r2 = client.post(
        "/api/auth/register",
        json={"phone": USER_PHONE, "password": PASSWORD, "name": "正常用户"},
    )
    assert _code(r2) == 0
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]
    assert hits["total"] == 1  # 被拦的尝试同样留痕（block 不留痕 = 黑箱）
    row = hits["list"][0]
    assert row["userId"] is None  # 账号尚未落库，记 NULL
    assert row["source"] == "register"
    assert row["snippet"] == "傻逼哥"  # 昵称原文在 snippet 里，证据不丢


def test_register_review_name_flagged_and_logged(client):
    """注册昵称与改昵称走同一条链：review → 放行 + 置位 + 日志。"""
    _add_word("广告", action="review")
    r = client.post(
        "/api/auth/register", json={"phone": USER_PHONE, "password": PASSWORD, "name": "广告哥"}
    )
    assert _code(r) == 0
    uid = r.json()["data"]["user"]["id"]
    assert r.json()["data"]["user"]["namePendingReview"] is True
    # 刷新不失忆：再登录仍带标志（写而不读的状态迟早被当成没用的字段删掉）
    r2 = client.post("/api/auth/login", json={"phone": USER_PHONE, "password": PASSWORD})
    assert r2.json()["data"]["user"]["namePendingReview"] is True
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits?source=register", headers=admin).json()["data"]
    assert hits["total"] == 1
    row = hits["list"][0]
    assert row["userId"] == uid
    assert row["userName"] == "广告哥"
    assert row["matched"] == "广告"
    queue = client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]
    assert queue["total"] == 1
    assert queue["list"][0]["flaggedName"] == "广告哥"


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"name": "   "}])
def test_register_default_name(client, payload):
    """昵称是可选字段：None/空/纯空白走默认名——默认名是平台的，不过校验。"""
    body = {"phone": USER_PHONE, "password": PASSWORD, **payload}
    r = client.post("/api/auth/register", json=body)
    assert r.json()["data"]["user"]["name"] == "明日头条用户"


@pytest.mark.parametrize("name", ["甲", "😀", "a" * (NICKNAME_MAX_LENGTH + 1)])
def test_register_invalid_shape(client, name):
    """注册提供了非空白昵称 → 与改昵称同一套形状规则（两套规则必然漂移）。"""
    r = client.post(
        "/api/auth/register", json={"phone": USER_PHONE, "password": PASSWORD, "name": name}
    )
    assert _code(r) == 42203


# ---------------------------------------------------------------- 5. 命中日志


def test_update_block_hit_records_old_name(client):
    """snippet 记被拦下的新名字，userName 记账号当前昵称——运营要按人定位。"""
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    _change(client, h, "原来名字")
    assert _code(_change(client, h, "傻逼哥")) == 40006
    admin = _admin(client)
    row = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]["list"][0]
    assert row["snippet"] == "傻逼哥"
    assert row["userName"] == "原来名字"
    assert row["source"] == "update"


def test_hit_count_increments_via_sql(client):
    """词的命中次数累加（SQL 表达式自增，防并发丢更新）——高频词是热点还是
    误杀大户，运营都得看得见。"""
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    for _ in range(3):
        assert _code(_change(client, h, "傻逼哥")) == 40006
    admin = _admin(client)
    words = client.get("/api/admin/nickname-words", headers=admin).json()["data"]["list"]
    assert words[0]["hitCount"] == 3


def test_hits_filters_and_cursor(client):
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    for _ in range(3):
        _change(client, h, "傻逼哥")
    admin = _admin(client)
    hits = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]
    assert hits["total"] == 3
    page1 = client.get("/api/admin/nickname-hits?limit=2", headers=admin).json()["data"]
    assert len(page1["list"]) == 2 and page1["hasMore"] is True
    page2 = client.get(
        f"/api/admin/nickname-hits?limit=2&cursor={page1['nextCursor']}", headers=admin
    ).json()["data"]
    assert len(page2["list"]) == 1 and page2["hasMore"] is False
    # 筛选：word 匹配主词、action/source/userId 精确匹配
    assert client.get("/api/admin/nickname-hits?word=傻逼", headers=admin).json()["data"][
        "total"
    ] == 3
    assert client.get("/api/admin/nickname-hits?word=广告", headers=admin).json()["data"][
        "total"
    ] == 0
    assert client.get("/api/admin/nickname-hits?action=review", headers=admin).json()["data"][
        "total"
    ] == 0
    uid = _user_id(client, USER_PHONE)
    assert client.get(f"/api/admin/nickname-hits?userId={uid}", headers=admin).json()["data"][
        "total"
    ] == 3


def test_matched_records_alias(client):
    """matched 记**实际命中的模式**：回答"是哪条别名拦住的"，也是该补哪个别名的依据。"""
    client.post(
        "/api/admin/nickname-words",
        json={"word": "傻逼", "action": "review", "aliases": ["shabi", "煞笔"]},
        headers=_admin(client),
    )
    h = _headers(client, USER_PHONE)
    _change(client, h, "shabi哥")
    admin = _admin(client)
    row = client.get("/api/admin/nickname-hits", headers=admin).json()["data"]["list"][0]
    assert row["word"] == "傻逼"
    assert row["matched"] == "shabi"


# ---------------------------------------------------------------- 6. 热更新


def test_word_lifecycle_effective_immediately(client):
    """新增 → 拦截；停用 → 放行；删除 → 放行。保存即生效，不需要重启服务。"""
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "广告哥")) == 0  # 词库为空时放行
    admin = _admin(client)
    client.post("/api/admin/nickname-words", json={"word": "广告", "action": "block"}, headers=admin)
    assert _code(_change(client, h, "广告哥")) == 40006
    wid = client.get("/api/admin/nickname-words", headers=admin).json()["data"]["list"][0]["id"]
    client.patch(f"/api/admin/nickname-words/{wid}", json={"enabled": False}, headers=admin)
    assert _code(_change(client, h, "广告哥")) == 0
    client.delete(f"/api/admin/nickname-words/{wid}", headers=admin)
    assert _code(_change(client, h, "广告哥")) == 0


def test_reload_endpoint_returns_count(client):
    """reload 供集群下其他实例手动同步（本实例改词已自动失效缓存）。"""
    admin = _admin(client)
    client.post("/api/admin/nickname-words", json={"word": "广告"}, headers=admin)
    r = client.post("/api/admin/nickname-words/reload", headers=admin)
    assert r.json()["data"]["count"] == 1
    with SessionLocal() as db:
        assert (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nicknameWord.reload")
            .count()
        ) == 1


# ---------------------------------------------------------------- 7. 词库管理与权限


def test_create_word_with_aliases_and_audit(client):
    admin = _admin(client)
    r = client.post(
        "/api/admin/nickname-words",
        json={
            "word": "傻逼",
            "action": "block",
            "category": "辱骂",
            "aliases": ["shabi", " sb "],  # schema 已 strip、去重
        },
        headers=admin,
    )
    assert _code(r) == 0
    d = r.json()["data"]
    assert d["action"] == "block"
    assert d["category"] == "辱骂"
    assert d["aliases"] == ["shabi", "sb"]
    assert d["hitCount"] == 0
    assert d["enabled"] is True
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nicknameWord.create")
            .first()
        )
        assert audit.target == "傻逼"
        assert _audit_detail(audit)["aliases"] == ["shabi", "sb"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"word": "射"}, 42204),  # 1 字词误杀率不可接受（"射"会命中"射手"）
        ({"word": "***"}, 42204),  # 纯符号词归一化后为空
        ({"word": "傻逼", "aliases": ["s"]}, 42204),  # 别名逐个查，不只查主词
        ({"word": "傻逼", "aliases": ["shabi"]}, 0),
    ],
)
def test_create_word_length_gate(client, payload, expected):
    """42204 是新码：42202 已承载评论词库的「太短」语义，复用会让前端文案错配。"""
    admin = _admin(client)
    r = client.post("/api/admin/nickname-words", json=payload, headers=admin)
    assert _code(r) == expected


def test_create_word_duplicate_rejected(client):
    admin = _admin(client)
    client.post("/api/admin/nickname-words", json={"word": "广告"}, headers=admin)
    r = client.post("/api/admin/nickname-words", json={"word": "广告"}, headers=admin)
    assert _code(r) == 40906


def test_update_word_fields_and_audit(client):
    admin = _admin(client)
    created = client.post(
        "/api/admin/nickname-words", json={"word": "广告", "action": "review"}, headers=admin
    ).json()["data"]
    r = client.patch(
        f"/api/admin/nickname-words/{created['id']}",
        json={"action": "block", "category": "引流", "aliases": ["gg"], "enabled": False},
        headers=admin,
    )
    d = r.json()["data"]
    assert d["action"] == "block"
    assert d["category"] == "引流"
    assert d["aliases"] == ["gg"]
    assert d["enabled"] is False
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nicknameWord.update")
            .first()
        )
        assert set(_audit_detail(audit)["changes"]) == {"action", "category", "aliases", "enabled"}


def test_update_word_empty_patch_no_audit(client):
    """空 PATCH 无变化早退：没有新事实就不留新痕。"""
    admin = _admin(client)
    created = client.post(
        "/api/admin/nickname-words", json={"word": "广告"}, headers=admin
    ).json()["data"]
    r = client.patch(f"/api/admin/nickname-words/{created['id']}", json={}, headers=admin)
    assert _code(r) == 0
    with SessionLocal() as db:
        assert (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nicknameWord.update")
            .count()
        ) == 0


def test_update_word_cannot_change_word_itself(client):
    """**不允许改词本身**：改词等于删一条加一条（schema 不收 word 字段）。"""
    admin = _admin(client)
    created = client.post(
        "/api/admin/nickname-words", json={"word": "广告"}, headers=admin
    ).json()["data"]
    r = client.patch(
        f"/api/admin/nickname-words/{created['id']}", json={"word": "广告2"}, headers=admin
    )
    assert r.json()["data"]["word"] == "广告"


def test_update_word_clear_aliases(client):
    """aliases 传空数组 = 明确清空；不传 = 不动（两者必须可区分）。"""
    admin = _admin(client)
    created = client.post(
        "/api/admin/nickname-words",
        json={"word": "傻逼", "aliases": ["shabi"]},
        headers=admin,
    ).json()["data"]
    r = client.patch(
        f"/api/admin/nickname-words/{created['id']}", json={"aliases": []}, headers=admin
    )
    assert r.json()["data"]["aliases"] == []
    # 清空后别名不再拦截（热更新生效）
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "shabi哥")) == 0


def test_delete_word_snapshot_audit(client):
    """删除留快照：删错了要能知道原来是什么（停用通常比删除更合适）。"""
    admin = _admin(client)
    created = client.post(
        "/api/admin/nickname-words", json={"word": "广告", "action": "block"}, headers=admin
    ).json()["data"]
    r = client.delete(f"/api/admin/nickname-words/{created['id']}", headers=admin)
    assert _code(r) == 0
    assert r.json()["data"]["word"] == "广告"  # 返回快照
    with SessionLocal() as db:
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nicknameWord.delete")
            .first()
        )
        assert _audit_detail(audit)["snapshot"]["word"] == "广告"


def test_word_not_found(client):
    admin = _admin(client)
    assert (
        client.patch("/api/admin/nickname-words/9999", json={"action": "block"}, headers=admin).json()[
            "code"
        ]
        == 40407
    )
    assert client.delete("/api/admin/nickname-words/9999", headers=admin).json()["code"] == 40407


def test_nickname_admin_requires_admin(client):
    """词库与命中日志是高危配置：非管理员一律 403（读也禁止——词表本身是情报）。"""
    h = _headers(client, USER_PHONE)
    calls = [
        client.get("/api/admin/nickname-words", headers=h),
        client.post("/api/admin/nickname-words", json={"word": "广告"}, headers=h),
        client.post("/api/admin/nickname-words/reload", headers=h),
        client.get("/api/admin/nickname-hits", headers=h),
        client.get("/api/admin/nickname-reviews", headers=h),
        client.post("/api/admin/nickname-reviews/1", headers=h),
    ]
    for r in calls:
        assert _code(r) == 40301


def test_list_words_filters(client):
    admin = _admin(client)
    client.post("/api/admin/nickname-words", json={"word": "广告", "action": "block"}, headers=admin)
    client.post("/api/admin/nickname-words", json={"word": "傻逼", "action": "review"}, headers=admin)
    client.post(
        "/api/admin/nickname-words",
        json={"word": "加微信", "action": "review", "enabled": False},
        headers=admin,
    )
    allw = client.get("/api/admin/nickname-words", headers=admin).json()["data"]
    assert allw["total"] == 3
    blocked = client.get("/api/admin/nickname-words?action=block", headers=admin).json()["data"]
    assert blocked["total"] == 1 and blocked["list"][0]["word"] == "广告"
    enabled = client.get("/api/admin/nickname-words?enabled=true", headers=admin).json()["data"]
    assert enabled["total"] == 2
    kw = client.get("/api/admin/nickname-words?keyword=傻", headers=admin).json()["data"]
    assert kw["total"] == 1 and kw["list"][0]["word"] == "傻逼"


# ---------------------------------------------------------------- 8. 复核队列


def test_queue_shows_flagged_submission(client):
    """队列回答「哪个提交需要人看」：flaggedName/flaggedMatched 取最近一条
    review 命中，flaggedAt 同源——用户可能已经改过名字了。"""
    _add_word("广告", action="review", category="广告")
    h = _headers(client, USER_PHONE)
    _change(client, h, "广告哥")
    admin = _admin(client)
    queue = client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]
    assert queue["total"] == 1
    row = queue["list"][0]
    assert row["name"] == "广告哥"
    assert row["flaggedName"] == "广告哥"
    assert row["flaggedMatched"] == "广告"
    assert row["flaggedAt"]


def test_queue_flagged_matched_records_alias(client):
    client.post(
        "/api/admin/nickname-words",
        json={"word": "傻逼", "action": "review", "aliases": ["shabi"]},
        headers=_admin(client),
    )
    h = _headers(client, USER_PHONE)
    _change(client, h, "shabi哥")
    admin = _admin(client)
    row = client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["list"][0]
    assert row["flaggedName"] == "shabi哥"
    assert row["flaggedMatched"] == "shabi"


def test_clean_rename_leaves_queue(client):
    """改成干净名字即出队：不把历史提交一直挂在队列上。"""
    _add_word("广告", action="review")
    h = _headers(client, USER_PHONE)
    _change(client, h, "广告哥")
    admin = _admin(client)
    assert client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["total"] == 1
    _change(client, h, "小明同学")
    assert client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["total"] == 0


def test_review_nickname_clears_flag_and_audits(client):
    """复核只清标志、**不改昵称本身**——名字是用户自己起的，复核是「人看过了」
    这个事实，不是运营替他改名。"""
    _add_word("广告", action="review")
    h = _headers(client, USER_PHONE)
    _change(client, h, "广告哥")
    admin = _admin(client)
    uid = _user_id(client, USER_PHONE)
    r = client.post(f"/api/admin/nickname-reviews/{uid}", headers=admin)
    assert _code(r) == 0
    d = r.json()["data"]
    assert d["pendingReview"] is False
    assert d["reviewedAt"]
    with SessionLocal() as db:
        u = db.get(User, uid)
        assert u.name == "广告哥"  # 不改名字
        assert u.name_pending_review is False
        audit = (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nickname.review")
            .first()
        )
        assert audit.target == str(uid)
        assert _audit_detail(audit)["name"] == "广告哥"
    assert client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["total"] == 0


def test_review_nickname_idempotent(client):
    """重复复核不重复记审计（幂等，与评论复核同处方）。"""
    _add_word("广告", action="review")
    h = _headers(client, USER_PHONE)
    _change(client, h, "广告哥")
    admin = _admin(client)
    uid = _user_id(client, USER_PHONE)
    first = client.post(f"/api/admin/nickname-reviews/{uid}", headers=admin).json()["data"]
    second = client.post(f"/api/admin/nickname-reviews/{uid}", headers=admin).json()["data"]
    assert second["reviewedAt"] == first["reviewedAt"]
    with SessionLocal() as db:
        assert (
            db.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "nickname.review")
            .count()
        ) == 1


def test_review_missing_user(client):
    admin = _admin(client)
    assert client.post("/api/admin/nickname-reviews/9999", headers=admin).json()["code"] == 40408


def test_blocked_user_not_queued(client):
    """block 只留痕不置位——拦下的提交不进「待人工复核」队列（它不需要人看，
    需要人改；队列里混进拦截记录只会稀释真正待看的那几条）。"""
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    assert _code(_change(client, h, "傻逼哥")) == 40006
    admin = _admin(client)
    assert client.get("/api/admin/nickname-reviews", headers=admin).json()["data"]["total"] == 0


def test_queue_keyword_filter(client):
    _add_word("广告", action="review")
    h1 = _headers(client, USER_PHONE, "用户一")
    _change(client, h1, "广告哥")
    _headers(client, USER_PHONE2, "用户二")  # 未提交坏名字 → 不入队
    admin = _admin(client)
    # keyword 匹配**当前名**（name）或手机号；用户一改名后当前名是「广告哥」
    assert (
        client.get("/api/admin/nickname-reviews?keyword=广告", headers=admin).json()["data"][
            "total"
        ]
        == 1
    )
    assert (
        client.get("/api/admin/nickname-reviews?keyword=用户二", headers=admin).json()["data"][
            "total"
        ]
        == 0
    )
    assert (
        client.get(f"/api/admin/nickname-reviews?keyword={USER_PHONE}", headers=admin).json()[
            "data"
        ]["total"]
        == 1
    )


# ---------------------------------------------------------------- 9. 引擎内部


def _engine_check(name: str):
    with SessionLocal() as db:
        return nick_engine.check(db, name)


def test_pattern_collision_keeps_stricter(client, caplog):
    """模式串撞车（「傻 逼」与「傻逼」归一并到同一串）保留更严者并留日志——
    否则后加载的会静默覆盖先加载的，先建的词莫名失效，而这种失效在测试里
    几乎看不见。四个形态各撞一次，但同一对冲突只警告一行。"""
    with caplog.at_level(logging.WARNING, logger="toutiao.nickname"):
        _add_word("傻 逼", action="block")  # 先加载（id 小）
        _add_word("傻逼", action="review")  # 后加载：撞车，block 更严 → 保留 block
        v = _engine_check("傻逼")
    assert v.result == RESULT_BLOCK
    warnings = [r for r in caplog.records if "模式串冲突" in r.getMessage()]
    assert len(warnings) == 1


def test_stricter_word_overwrites_silently(client, caplog):
    """反方向：后加载的更严 → 直接覆盖，不需要警告（没有东西被静默吞掉）。"""
    with caplog.at_level(logging.WARNING, logger="toutiao.nickname"):
        _add_word("傻逼", action="review")
        _add_word("傻 逼", action="block")
        v = _engine_check("傻逼")
    assert v.result == RESULT_BLOCK
    assert [r for r in caplog.records if "模式串冲突" in r.getMessage()] == []


def test_min_word_len_gates_every_form(client):
    """最短词长对**每个形态**都把关：「约约」压缩成「约」（1 字符）被跳过，
    否则任何含「约」的昵称都会算成命中「约约」——那是运营不想要的误杀面。"""
    _add_word("约约", action="block")
    assert _engine_check("约约").result == RESULT_BLOCK  # base 2 字符 → 生效
    assert _engine_check("约").result == RESULT_PASS  # collapsed 1 字符 → 跳过
    assert _engine_check("约约约").result == RESULT_BLOCK


def test_collapse_switch_controls_form(client, monkeypatch):
    """压缩形态可单独关（出现误杀时的止血手段），其余形态不受影响。"""
    _add_word("abc", action="block")
    assert _engine_check("aabbcc").result == RESULT_BLOCK  # collapsed 命中
    monkeypatch.setattr("app.nickname_moderation.NICKNAME_COLLAPSE_REPEATS", False)
    nick_engine.invalidate()
    assert _engine_check("aabbcc").result == RESULT_PASS  # 只剩 base，子串断裂


def test_word_limit_truncates(client, monkeypatch):
    """词库超限截断并置 `truncated`（供健康检查观测）——静默丢词比报错难发现。"""
    monkeypatch.setattr("app.nickname_moderation.NICKNAME_MAX_WORDS", 3)
    for i in range(4):
        _add_word(f"坏蛋{'甲乙丙丁'[i]}", action="block")
    # truncated 只在重建时置位——先触发一次检测再断言
    assert _engine_check("坏蛋甲").result == RESULT_BLOCK  # 前 3 条生效
    assert nick_engine.truncated is True
    assert _engine_check("坏蛋丁").result == RESULT_PASS  # 第 4 条被截断


def test_unknown_action_falls_back_to_review(client):
    """库里的 action 缺失/未知 → 降级 review（放行 + 让人看一眼），
    不是 block（把用户的昵称直接吞掉）。"""
    with SessionLocal() as db:
        db.add(
            NicknameWord(
                word="坏蛋",
                action="weird",
                category="",
                aliases=[],
                enabled=True,
                hit_count=0,
                created_by="test",
                created_at=now_str(),
                updated_at=now_str(),
            )
        )
        db.commit()
    nick_engine.invalidate()
    assert _engine_check("坏蛋").result == RESULT_REVIEW


def test_switch_off_skips_check(client, monkeypatch):
    """一键止血：关掉开关后不再检测（大面积误杀时的应急手段，无需发版）。"""
    monkeypatch.setattr("app.nickname_moderation.NICKNAME_MODERATION_ENABLED", False)
    _add_word("傻逼", action="block")
    h = _headers(client, USER_PHONE)
    r = _change(client, h, "你这傻逼")
    assert _code(r) == 0
    assert r.json()["data"]["name"] == "你这傻逼"


def test_check_does_not_write(client):
    """check() 只读：写日志是路由的职责（record_hits），引擎不碰库。"""
    _add_word("傻逼", action="block")
    _engine_check("傻逼")
    with SessionLocal() as db:
        assert db.query(NicknameHit).count() == 0
        assert db.query(NicknameWord).first().hit_count == 0


def test_engine_handles_symbol_only_name(client):
    """纯符号名归一化后为空：不炸、不放行误杀（形状层另有一道闸）。"""
    _add_word("傻逼", action="block")
    assert _engine_check("___").result == RESULT_PASS


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("not json", []),  # 非 JSON 字符串
        ("5", []),  # 合法 JSON 但不是数组
        ('{"a": 1}', []),  # 对象也不行
        ('["shabi", " shabi ", "", 1, null, "' + "x" * 60 + '"]', ["shabi"]),  # 去空白/去重/限长
        (["shabi", "sb"], ["shabi", "sb"]),  # list 原样支持
    ],
)
def test_alias_list_dirty_data(raw, expected):
    """脏别名一律按空处理：引擎加载时静默跳过，前端拿到的永远是干净数组。"""
    assert alias_list(raw) == expected


def test_alias_list_capped(client):
    """单条词别名数超限截断（防误导入把索引撑爆）。"""
    raw = [f"alias{i:02d}" for i in range(25)]
    out = alias_list(raw)
    assert len(out) == 20
