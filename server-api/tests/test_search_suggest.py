"""P1-70（E2 后端）：正文入召回 + `/api/search/suggest` 三源联想。

覆盖：
- `trigram_similarity`（纯函数，纠错的排序键）；
- 三源各自的行为与合并后的优先级 / 去重 / 排序；
- 接口契约（空输入、边界、不下发资讯内容、**不写 search_logs**、免登录）；
- 正文召回（含"标题命中仍排第一"与"fallback 路径刻意不含正文"）。

关于 `similarity()`：PG 路径的 `_search_ranked` 依赖 `func.similarity`，
而本项目的测试跑在 SQLite 上。为让这条路径在 CI 里**真被执行**，
`_install_sqlite_similarity` 在 SQLite 连接上注册一个同名的三元组函数
（与生产用的 `trigram_similarity` 同实现）。这是**测试脚手架**：
它验证的是"召回集合"与"层级顺序"，这两件事只依赖权重间隔（100/10/1），
不依赖相似度的精确取值——绝对值仍由 `scripts/verify_search_ranking.py`
在真实 PG 上核对。
"""
import os
from datetime import datetime, timedelta
from urllib.parse import quote

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, func  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News, SearchLog  # noqa: E402
from app.routers import news as news_mod  # noqa: E402
from app.routers.search import (  # noqa: E402
    SUGGEST_CORRECT_MIN_SIMILARITY,
    SUGGEST_LIMIT_MAX,
    SUGGEST_TITLE_MAX_LEN,
    edit_distance_at_most_one,
    normalize_term,
    suggest_terms,
    trigram_similarity,
)

NOW = datetime(2026, 10, 19, 12, 0, 0)

_SIMILARITY_INSTALLED = False


def _install_sqlite_similarity(eng) -> None:
    """在 SQLite 上注册 `similarity(a, b)`（幂等）。

    两个入口都要照顾到：`event` 监听管**未来**新建的连接，而对**已存在**的
    连接（StaticPool 会一直复用同一条）必须直接补装——否则本文件的首个用例
    拿不到这个函数，报的是"no such function"，看起来像代码缺陷。
    """

    def _apply(dbapi_conn):
        # 参数为 NULL 时 SQLite 传 None；pg_trgm 对空串同样返回 0
        dbapi_conn.create_function(
            "similarity", 2, lambda a, b: trigram_similarity(a or "", b or "")
        )

    global _SIMILARITY_INSTALLED
    if not _SIMILARITY_INSTALLED:
        event.listen(eng, "connect", lambda dbapi_conn, _rec: _apply(dbapi_conn))
        _SIMILARITY_INSTALLED = True
    with eng.connect() as conn:
        _apply(conn.connection.dbapi_connection)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def ranked_client(client, monkeypatch):
    """让 SQLite 也走 PG 的 `_search_ranked`：注册 similarity + 声明 trgm 可用。"""

    _install_sqlite_similarity(engine)
    monkeypatch.setattr(news_mod, "_trgm_available", lambda db: True)
    return client


def _mk_news(nid: str, title: str, summary: str = "", content: str | None = None) -> None:
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=title,
                summary=summary,
                content=content,
                source="测试",
                comment_count=0,
                time="1小时前",
                channel="tech",
                variant="text",
                published_at="2026-10-19T11:00:00",
                hidden=False,
            )
        )
        db.commit()


def _log_searches(client, terms: list[str]) -> None:
    """用**真实的搜索请求**造日志：落在真实时间窗口内，联想的 `days` 才吃得到。

    直接 `record_search(..., now=NOW)` 造的锚点时间对**接口**测试无效——
    接口的 cutoff 用的是真实当前时间。
    """
    for t in terms:
        r = client.get(f"/api/news/search?q={quote(t)}")
        assert r.status_code == 200


def _suggest(client, q: str, **params) -> list[dict]:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/search/suggest?q={quote(q)}" + (f"&{qs}" if qs else "")
    return client.get(url).json()["data"]["list"]


# ---------------------------------------------------------------- 三元组相似度


class TestTrigramSimilarity:
    def test_identical_is_one(self):
        assert trigram_similarity("新能源汽车", "新能源汽车") == 1.0

    def test_disjoint_is_zero(self):
        assert trigram_similarity("新能源汽车", "楼市调控") == 0.0

    def test_empty_side_is_zero(self):
        """空串不能靠"交集为空/并集为空"算出 1.0 或除零。"""
        assert trigram_similarity("", "新能源汽车") == 0.0
        assert trigram_similarity("新", "") == 0.0

    def test_one_char_diff_is_above_correction_threshold(self):
        """差一个字（纠错的核心场景）要能越过 0.3 的门槛。"""
        sim = trigram_similarity("新能源汽吃", "新能源汽车")
        assert 0.3 <= sim < 1.0

    def test_short_strings_do_not_crash(self):
        """短于三字符时没有三元组可用，退化为整串比较而非抛错。"""
        assert trigram_similarity("雨", "雨") == 1.0
        assert trigram_similarity("雨", "雪") == 0.0

    def test_boundary_padding_keeps_short_words_comparable(self):
        """补齐是必需的而非细节：不补齐时"近期词"与"近期祠"三元组完全不相交。

        这条断言钉死的是**边界补齐**这一行为——去掉补齐它就会红。
        """
        sim = trigram_similarity("近期词", "近期祠")
        assert 0 < sim < 1.0

    def test_similarity_drops_fast_for_short_words(self):
        """4 字词错一个字只有 2/8：单靠相似度当门槛会漏掉最常见的中文错字。

        这条断言是"为什么还要编辑距离"的依据，不是缺陷报告。
        """
        assert trigram_similarity("暴雨蓝色", "暴雨篮色") < SUGGEST_CORRECT_MIN_SIMILARITY


class TestEditDistance:
    def test_substitution(self):
        assert edit_distance_at_most_one("暴雨篮色", "暴雨蓝色")

    def test_insertion_and_deletion(self):
        assert edit_distance_at_most_one("暴雨色", "暴雨蓝色")
        assert edit_distance_at_most_one("暴雨蓝色", "暴雨色")

    def test_two_edits_are_rejected(self):
        """差两个字不是错字：这正是"中国人民 / 中国银行"要被排除的原因。"""
        assert not edit_distance_at_most_one("中国人民", "中国银行")

    def test_length_gap_over_one_is_rejected(self):
        assert not edit_distance_at_most_one("暴雨", "暴雨蓝色")

    def test_identical_and_empty(self):
        assert edit_distance_at_most_one("暴雨", "暴雨")
        assert edit_distance_at_most_one("", "")

    def test_trailing_extra_char(self):
        """循环扫完后长出来的尾部也要算一次编辑，不能漏判。"""
        assert edit_distance_at_most_one("暴雨蓝", "暴雨蓝色")
        assert not edit_distance_at_most_one("暴雨蓝", "暴雨蓝色预")


# ---------------------------------------------------------------- 三源


class TestHotSource:
    def test_prefix_of_searched_term_is_suggested(self, client):
        _log_searches(client, ["新能源汽车", "新能源汽车", "楼市"])
        items = _suggest(client, "新能源")
        assert items and items[0] == {"term": "新能源汽车", "kind": "hot", "hitCount": 2}

    def test_non_prefix_is_not_hot(self, client):
        """热搜源只认前缀：中间匹配的词交给纠错源，否则两源会重复给同一批词。"""
        _log_searches(client, ["今日新能源汽车"])
        assert _suggest(client, "新能源") == []

    def test_same_kind_orders_by_hit_count_desc(self, client):
        _log_searches(client, ["新能源车", "新能源车", "新能源车", "新能源政策", "新能源政策"])
        items = _suggest(client, "新能源")
        assert [i["term"] for i in items] == ["新能源车", "新能源政策"]
        assert [i["hitCount"] for i in items] == [3, 2]


class TestCorrectSource:
    def test_typo_gets_the_real_term(self, client):
        """验收标准之一：输错一个字，靠**别人真实搜过的词**给出正确写法。"""
        _log_searches(client, ["新能源汽车", "新能源汽吃"])
        items = _suggest(client, "新能源汽吃")
        assert {"term": "新能源汽车", "kind": "correct", "hitCount": 1} in items

    def test_query_term_itself_is_not_a_correction(self, client):
        """与自己完全一样的词不是纠错：它会被当成"你已经打对了"。"""
        _log_searches(client, ["新能源汽车"])
        items = _suggest(client, "新能源汽车")
        assert all(i["term"] != "新能源汽车" or i["kind"] != "correct" for i in items)

    def test_short_word_typo_is_corrected(self, client):
        """4 字词错一个字（相似度判不出来，靠编辑距离）——最常见的一类中文错字。"""
        _log_searches(client, ["暴雨蓝色"])
        items = _suggest(client, "暴雨篮色")
        assert {"term": "暴雨蓝色", "kind": "correct", "hitCount": 1} in items

    def test_typo_with_one_char_missing(self, client):
        """打漏一个字同样算错字（长度差 1）。"""
        _log_searches(client, ["暴雨蓝色"])
        assert [i["term"] for i in _suggest(client, "暴雨色")] == ["暴雨蓝色"]

    def test_related_but_not_a_typo_is_rejected(self, client):
        """共享前缀但差两个字 → 不是错字。

        这是 0.3 门槛与"编辑距离 ≤1"共同挡住的那类误报：
        "中国人民" 与 "中国银行" 相似度 0.25、距离 2，两条判据都不该放行。
        """
        _log_searches(client, ["中国银行"])
        assert _suggest(client, "中国人民") == []

    def test_length_gap_too_large_is_rejected(self, client):
        """长度差过大不是打错（"暴雨" 不会是 "新能源汽车" 的错词）。

        没有这条约束，相似度排序会被"越长的词共享越多三元组"带偏。
        """
        _log_searches(client, ["新能源汽车"])
        assert _suggest(client, "暴") == []


class TestTitleSource:
    def test_title_prefix_is_suggested(self, client):
        _mk_news("t1", "暴雨蓝色预警发布")
        items = _suggest(client, "暴雨")
        assert {"term": "暴雨蓝色预警发布", "kind": "title", "hitCount": 0} in items

    def test_too_long_title_is_not_suggested(self, client):
        """长标题不适合当下拉项，且搜整条标题基本只回自己那一篇。"""
        title = "暴雨蓝色预警发布后多地启动应急响应并提醒市民减少不必要的外出活动"
        _mk_news("t2", title)
        assert len(title) > SUGGEST_TITLE_MAX_LEN  # 否则这条断言是空的
        assert _suggest(client, "暴雨") == []

    def test_title_at_the_length_limit_is_kept(self, client):
        """边界：正好等于上限的标题要保留（差一字就是两种行为）。"""
        title = "暴" * SUGGEST_TITLE_MAX_LEN
        _mk_news("t4", title)
        assert [i["term"] for i in _suggest(client, "暴")] == [title]

    def test_hidden_news_is_not_a_source(self, client):
        """P1-20：已下架内容不参与联想——搜得到却点不开是更差的体验。"""
        _mk_news("t3", "暴雨蓝色预警发布")
        with SessionLocal() as db:
            db.get(News, "t3").hidden = True
            db.commit()
        assert _suggest(client, "暴雨") == []


class TestMerge:
    def test_priority_hot_beats_title_on_same_term(self, client):
        """同一词同时在热搜与标题里 → 只出现一次，且标记为 hot（优先级更高）。"""
        _log_searches(client, ["暴雨蓝色预警发布"])
        _mk_news("m1", "暴雨蓝色预警发布")
        items = _suggest(client, "暴雨")
        assert len(items) == 1
        assert items[0]["kind"] == "hot"

    def test_case_different_terms_dedupe_to_one(self, client):
        """去重按小写比较，避免下拉里出现"AI新闻 / ai新闻"两条看起来一样的。"""
        _log_searches(client, ["ai新闻", "ai新闻", "AI新闻"])
        items = _suggest(client, "ai")
        assert len(items) == 1
        # 次数多的那条胜出（同次数时顺序取决于数据库排序，故这里用不同次数钉死）
        assert items[0]["term"] == "ai新闻"

    def test_hot_comes_before_correct_before_title(self, client):
        _log_searches(client, ["暴雨蓝色", "暴雨蓝se"])
        _mk_news("m2", "暴雨蓝色预警")
        items = _suggest(client, "暴雨")
        kinds = [i["kind"] for i in items]
        # 去掉重复 kind 后必须是 hot → correct → title 的子序列
        order = []
        for k in kinds:
            if k not in order:
                order.append(k)
        rank = {"hot": 0, "correct": 1, "title": 2}
        assert [rank[k] for k in order] == sorted(rank[k] for k in order)

    def test_limit_is_respected(self, client):
        for i in range(12):
            _log_searches(client, [f"新能源{i}号"])
        assert len(_suggest(client, "新能源", limit=5)) == 5


# ---------------------------------------------------------------- 接口契约


class TestSuggestApi:
    def test_empty_query_returns_empty_list(self, client):
        data = client.get("/api/search/suggest?q=").json()["data"]
        assert data == {"list": [], "keyword": ""}

    def test_blank_query_returns_empty_list(self, client):
        data = client.get("/api/search/suggest?q=%20%20").json()["data"]
        assert data == {"list": [], "keyword": ""}

    def test_keyword_is_normalized(self, client):
        data = client.get("/api/search/suggest?q=%20%E6%9A%B4%E9%9B%A8%20").json()["data"]
        assert data["keyword"] == "暴雨"

    def test_items_carry_no_news_payload(self, client):
        """联想只给词：把标题当结果返回会让"选词"和"看结果"互相干扰。"""
        _log_searches(client, ["新能源汽车"])
        items = _suggest(client, "新能源")
        assert items
        for item in items:
            assert set(item) == {"term", "kind", "hitCount"}

    def test_suggest_does_not_write_search_logs(self, client):
        """热搜榜不能被"边打字"污染——只有真正提交搜索才计数。"""
        _log_searches(client, ["新能源汽车"])
        with SessionLocal() as db:
            before = db.query(func.count(SearchLog.id)).scalar()
        for q in ("新能源", "新能源汽", "新能源汽⻋"):
            _suggest(client, q)
        with SessionLocal() as db:
            after = db.query(func.count(SearchLog.id)).scalar()
        assert after == before

    def test_works_without_token(self, client):
        """未登录也可用：热搜是公开统计，相似词来自公共日志且不含用户字段。"""
        _log_searches(client, ["新能源汽车"])
        r = client.get("/api/search/suggest?q=%E6%96%B0%E8%83%BD%E6%BA%90")
        assert r.status_code == 200
        assert r.json()["data"]["list"]

    def test_limit_bounds(self, client):
        assert client.get("/api/search/suggest?q=a&limit=0").status_code == 422
        assert client.get(f"/api/search/suggest?q=a&limit={SUGGEST_LIMIT_MAX + 1}").status_code == 422

    def test_days_bounds(self, client):
        assert client.get("/api/search/suggest?q=a&days=0").status_code == 422
        assert client.get("/api/search/suggest?q=a&days=365").status_code == 422

    def test_no_query_param_still_200(self, client):
        """前端可能在聚焦输入框时就不带 q 调一次：那不该是 422。"""
        assert client.get("/api/search/suggest").status_code == 200


# ---------------------------------------------------------------- 入参/窗口


class TestSuggestUnit:
    def test_normalize_term_strips_and_truncates(self):
        assert normalize_term("  暴雨  ") == "暴雨"
        assert normalize_term("") == ""
        assert normalize_term(None) == ""
        assert len(normalize_term("雨" * 200)) == 50

    def test_explicit_now_bypasses_real_clock(self, client):
        """`suggest_terms` 接受 `now`，窗口断言不依赖真实时钟。"""
        with SessionLocal() as db:
            from app.search_stats import record_search

            record_search(db, "窗口内", user_id=1, now=NOW)
            record_search(db, "窗口外", user_id=2, now=NOW - timedelta(days=10))
            items = suggest_terms(db, "窗口", now=NOW, days=7)
        assert [i["term"] for i in items] == ["窗口内"]

    def test_days_narrows_the_pool(self, client):
        """窗口外的词不进候选池——联想与热搜榜共用同一口径（同一份查询）。

        两条候选都与输入只差一个字，故"它们该不该出现"完全由窗口决定，
        不受相似度/编辑距离判据的干扰。
        """
        with SessionLocal() as db:
            from app.search_stats import record_search

            record_search(db, "近期词", user_id=1, now=NOW)
            record_search(db, "远期词", user_id=2, now=NOW - timedelta(days=10))
            narrow = suggest_terms(db, "近期祠", now=NOW, days=7)
            wide = suggest_terms(db, "近期祠", now=NOW, days=365)
        assert [i["term"] for i in narrow] == ["近期词"]
        # 远期词与"近期祠"差两个字，不是错字关系（长度容差内但编辑距离为 2）
        assert [i["term"] for i in wide] == ["近期词"]


# ---------------------------------------------------------------- 正文召回


class TestContentRecall:
    def test_content_only_keyword_is_recalled(self, ranked_client):
        """验收标准：只出现在正文里的关键词能召回这篇。"""
        _mk_news("c1", "今日天气综述", summary="晴转多云", content="局部地区有暴雨")
        data = ranked_client.get("/api/news/search?q=%E6%9A%B4%E9%9B%A8").json()["data"]
        assert [n["id"] for n in data["list"]] == ["c1"]
        assert data["total"] == 1

    def test_title_hit_still_ranks_first(self, ranked_client):
        """正文权重最低的意义：标题命中的那篇必须仍在正文命中的之前。"""
        _mk_news("r1", "无关标题", summary="", content="暴雨来袭")
        _mk_news("r2", "暴雨蓝色预警", summary="", content="")
        data = ranked_client.get("/api/news/search?q=%E6%9A%B4%E9%9B%A8").json()["data"]
        assert [n["id"] for n in data["list"]] == ["r2", "r1"]

    def test_summary_hit_ranks_above_content_hit(self, ranked_client):
        _mk_news("s1", "无关标题", summary="暴雨摘要", content="")
        _mk_news("s2", "无关标题2", summary="", content="暴雨正文")
        data = ranked_client.get("/api/news/search?q=%E6%9A%B4%E9%9B%A8").json()["data"]
        assert [n["id"] for n in data["list"]] == ["s1", "s2"]

    def test_hidden_news_is_not_recalled_by_content(self, ranked_client):
        _mk_news("h1", "无关标题", summary="", content="暴雨来袭")
        with SessionLocal() as db:
            db.get(News, "h1").hidden = True
            db.commit()
        data = ranked_client.get("/api/news/search?q=%E6%9A%B4%E9%9B%A8").json()["data"]
        assert data["list"] == []

    def test_fallback_path_does_not_scan_content(self, client):
        """D2 定案的边界：**没打桩时**（SQLite / 无 trgm）正文不参与召回。

        这条断言是"刻意不做"的护栏——哪天有人为了让测试通过把正文加进
        `_search_fallback`，这里会立刻红。
        """
        _mk_news("f1", "今日天气综述", summary="晴转多云", content="局部地区有暴雨")
        data = client.get("/api/news/search?q=%E6%9A%B4%E9%9B%A8").json()["data"]
        assert data["list"] == []

    def test_like_escape_still_holds_with_content(self, ranked_client):
        """正文扩了召回面，通配符注入的面也跟着变大——转义必须仍然生效。"""
        _mk_news("e1", "百分号测试", summary="", content="含%的正文")
        data = ranked_client.get("/api/news/search?q=%25").json()["data"]
        assert [n["id"] for n in data["list"]] == ["e1"]
