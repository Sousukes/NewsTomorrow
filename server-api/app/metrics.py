"""运营指标聚合（E7-a）：管理端大盘的单端点数据源。

设计见 `docs/后端/E7运营数据看板设计.md`。五条硬约束：

1. **单一时间口径**：所有窗口类板块共用同一个 `days`（由端点层收敛），
   否则页面会出现"总量按 7 天、趋势按 30 天"的割裂——而这种割裂在运营眼里
   就是"数字对不上"。
2. **不缓存**（D5）：管理端大盘低频（一天看几次），缓存收益极小；
   而审核量是运营**决策依据**，陈旧比慢更糟。与 P1-62「热门列表评估后
   刻意不做」同款决策。
3. **按天分桶走 SQL 侧 `substr(col, 1, 10)`**：本模块涉及的时间列
   （`sensitive_hits.created_at` / `nickname_hits.created_at`）都是
   `String(30)` 存 UTC ISO，PG 与 SQLite 的 `substr` 行为一致（都 1 起始），
   且写入侧是 `now_str()`，**不涉及会话时区**。
   全库唯一的例外是 `rss_source_health_events.at`（DateTime），它由
   `feeds/health.py` 自己处理，不走这里。
   **D1-2 打算把 `hit_logs.*` 迁成 DateTime**——届时这条查询会在 PG 上
   报 `substr(timestamptz, …) does not exist`（P1-74 同款事故），
   护栏见 `tests/test_admin_dashboard.py::TestDashboardOnPostgres`。
4. **不引入 N+1**：源健康复用 `summarize_many`（单条 GROUP BY），
   最近失败时间只对**选出来的 Top N** 再查一次（有界，不是逐行）。
5. **空库不炸**：所有板块在无数据时给 0 / 空列表，而不是报错——
   新环境第一眼看到的大盘就该是这个样子。

时间比较的口径：这些列是 `String(30)` 存 UTC aware ISO，
`created_at >= cutoff` 是**字符串**比较（A3 之后字典序即时间序，
见 `search_stats._iso` 的说明）。cutoff 一律由注入的 `now` 推导，
保证测试可回放。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import (
    Comment,
    News,
    NicknameHit,
    Report,
    RssSource,
    RssSourceHealthEvent,
    SearchLog,
    SensitiveHit,
    User,
    UserDislike,
    UserExposure,
    UserFavorite,
    UserFollow,
    UserHistory,
    iso_utc,
)
from .search_stats import hot_terms
from .feeds.health import since, summarize_many

logger = logging.getLogger("toutiao.metrics")

# 大盘展示的"最差源"条数：概览不是报表，要看全量去源管理页
WORST_SOURCES_LIMIT = 5
# 搜索热词条数（与搜索页默认一致，运营看到的与用户看到的是同一份）
HOT_TERMS_LIMIT = 10
# 日期前缀长度：ISO 串 `2026-10-20T06:00:00+00:00` 前 10 位即 UTC 日期
_DATE_LEN = 10


def _cutoff(days: int, now: datetime | None = None) -> str:
    """窗口截止时间（UTC aware ISO 秒精度串）。

    与 `hit_logs._cutoff` / `search_stats._iso` 同一口径：**字典序即时间序**
    只在同格式下成立，所以 cutoff 与列值必须同为 UTC aware ISO。
    """
    base = now or datetime.now(timezone.utc)
    return (base - timedelta(days=days)).isoformat(timespec="seconds")


def _count(db: Session, model, *conditions) -> int:
    """单表计数（条件可选）。固定次数的小查询，不是逐行的 N+1。"""
    q = select(func.count()).select_from(model)
    for c in conditions:
        q = q.where(c)
    return int(db.execute(q).scalar() or 0)


# ----------------------------- 存量规模 -----------------------------


def content_counts(db: Session) -> dict:
    """内容与账号的**存量**规模（不随 `days` 变，故不吃窗口参数）。

    `news` 与 `newsHidden` 必须分开数：只数可见数的话，"下架了多少"
    永远看不出来——而那是运营最常问的问题之一。
    """
    total_news, visible_news = db.execute(
        select(
            func.count(News.id),
            func.sum(case((News.hidden.is_(False), 1), else_=0)),
        ).select_from(News)
    ).one()
    # RssSource 的主键是 `key`（业务自然键），不是 `id`
    total_src, enabled_src = db.execute(
        select(
            func.count(RssSource.key),
            func.sum(case((RssSource.enabled.is_(True), 1), else_=0)),
        ).select_from(RssSource)
    ).one()
    return {
        "news": int(total_news or 0),
        "newsHidden": int(total_news or 0) - int(visible_news or 0),
        "comments": _count(db, Comment),
        "users": _count(db, User),
        "sources": int(total_src or 0),
        "sourcesEnabled": int(enabled_src or 0),
    }


# ----------------------------- 待办队列 -----------------------------


def todo_counts(db: Session) -> dict:
    """"系统在等人"的队列——大盘上最该被看见的几个数字。

    三条队列的共同点：**不是运营主动发起的动作，而是机器兜住后留给人看一眼**。
    漏掉任何一条，大盘就不是"待办概览"而是"统计报表"。
    """
    return {
        # E5：用户举报待复核。只数 pending——自动软隐藏也只算 pending
        # （见 app/reports.py），已处置/驳回不该继续占着待办数
        "reports": _count(db, Report, Report.status == "pending"),
        # P1-23：命中敏感词被脱敏后发布的评论，"机器先兜住、人再看一眼"
        "commentReviews": _count(db, Comment, Comment.pending_review.is_(True)),
        # P1-24：被放行但待人工复核的昵称
        "nicknameReviews": _count(db, User, User.name_pending_review.is_(True)),
    }


# ----------------------------- 互动量（窗口内） -----------------------------


def engagement_counts(db: Session, days: int, now: datetime | None = None) -> dict:
    """窗口内的互动量。各表都有 `created_at` / `viewed_at` 等 UTC ISO 列。"""
    cutoff = _cutoff(days, now)
    return {
        "searches": _count(db, SearchLog, SearchLog.created_at >= cutoff),
        "favorites": _count(db, UserFavorite, UserFavorite.created_at >= cutoff),
        "history": _count(db, UserHistory, UserHistory.viewed_at >= cutoff),
        "follows": _count(db, UserFollow, UserFollow.created_at >= cutoff),
        "exposures": _count(db, UserExposure, UserExposure.exposed_at >= cutoff),
        "dislikes": _count(db, UserDislike, UserDislike.created_at >= cutoff),
    }


# ----------------------------- 源健康 -----------------------------


def source_health_block(db: Session, days: int) -> dict:
    """窗口内最值得关注的源。

    复用 `summarize_many`（单条 GROUP BY，P1-19 专门为防 N+1 写的），
    在 Python 侧按失败率排序取 Top N。源数量级是十位数，全量取回再排序
    没有压力；新写一份聚合就会与 `/api/admin/sources` 的口径漂移。

    "不健康"的定义：**有尝试且有失败**。没有尝试的源不是不健康，是空闲
    （按礼仪跳过 / 熔断冷却 / 未启用）——把它们算进 unhealthy 会让数字失去意义。
    """
    rows = db.execute(select(RssSource.key, RssSource.name)).all()
    summary = summarize_many(db, [r.key for r in rows], days)
    names = {r.key: r.name for r in rows}

    worst = []
    for key, s in summary.items():
        attempts = int(s.get("attempts") or 0)
        ok = int(s.get("ok") or 0)
        failed = max(0, attempts - ok)
        if not attempts or not failed:
            continue
        worst.append(
            {
                "key": key,
                "name": names.get(key) or key,
                "attempts": attempts,
                "ok": ok,
                "failed": failed,
                "rate": s.get("rate"),
                "avgItems": s.get("avgItems"),
            }
        )
    # 失败率低的在前；并列时尝试次数多的在前（样本多，信号更可信）
    worst.sort(key=lambda r: (r["rate"] if r["rate"] is not None else 1.0, -r["attempts"]))

    top = worst[:WORST_SOURCES_LIMIT]
    if top:
        # 只给选出来的 Top N 补一次"最近失败时间"：**有界的一次查询**，
        # 不是逐源查（那才是 N+1）。
        # cutoff 用 `since()`（datetime 对象）而不是 ISO 串：`at` 是 DateTime 列
        # （D1-1 迁移 0030），PG 上 `timestamptz >= varchar` 直接报
        # "operator does not exist"——而 SQLite 不报，单测一路绿灯
        # （P1-74 同款事故，由 TestDashboardOnPostgres 抓到）。
        last_fail = dict(
            db.execute(
                select(
                    RssSourceHealthEvent.source_key,
                    func.max(
                        case(
                            (RssSourceHealthEvent.ok.is_(False), RssSourceHealthEvent.at),
                        )
                    ),
                )
                .where(
                    RssSourceHealthEvent.source_key.in_([r["key"] for r in top]),
                    RssSourceHealthEvent.at >= since(days),
                )
                .group_by(RssSourceHealthEvent.source_key)
            ).all()
        )
        for r in top:
            r["lastFailAt"] = iso_utc(last_fail.get(r["key"]))

    return {
        "windowDays": days,
        "unhealthy": len(worst),
        "worst": top,
    }


# ----------------------------- 审核与命中 -----------------------------


def moderation_block(db: Session, days: int, now: datetime | None = None) -> dict:
    """窗口内的治理量 + 命中趋势。

    下架/隐藏量按 `hidden_at` 过滤（隐藏动作会写 `now_str()`，恢复时置回
    NULL，见 `routers/admin/news.py` / `comments.py`）——**不是按当前是否隐藏**：
    后者会把"历史上架过的"也算进来，而且恢复过又隐藏的会被漏掉。

    命中趋势按天分桶（SQL 侧 substr，理由见模块 docstring 第 3 条）。
    两个序列合并输出：同一天只有一个点，缺的那条补 0——趋势图缺一天
    比补 0 更难读（补 0 表示"那天真的没有命中"，与"没有数据"是两件事故，
    但这里按天连续扫窗口，窗口内的每一天都有定义）。
    """
    cutoff = _cutoff(days, now)
    day = func.substr(SensitiveHit.created_at, 1, _DATE_LEN)
    nick_day = func.substr(NicknameHit.created_at, 1, _DATE_LEN)

    # 两条查询都复用**同一个表达式对象**（`day` / `nick_day`）：PG 要求 SELECT 与
    # GROUP BY 里的表达式逐字相同才认"已分组"，分开写两次会报
    # `column "…created_at" must appear in the GROUP BY clause`——
    # SQLite 不报这个错，只有真实 PG 会暴露（TestDashboardOnPostgres 抓到过）。
    sensitive_rows = db.execute(
        select(day, func.count(SensitiveHit.id))
        .where(SensitiveHit.created_at >= cutoff)
        .group_by(day)
        .order_by(day)
    ).all()
    nickname_rows = db.execute(
        select(nick_day, func.count(NicknameHit.id))
        .where(NicknameHit.created_at >= cutoff)
        .group_by(nick_day)
        .order_by(nick_day)
    ).all()

    trend: dict[str, dict] = {}
    for date, n in sensitive_rows:
        trend.setdefault(str(date), {"sensitive": 0, "nickname": 0})["sensitive"] = int(n or 0)
    for date, n in nickname_rows:
        trend.setdefault(str(date), {"sensitive": 0, "nickname": 0})["nickname"] = int(n or 0)

    return {
        "newsHidden": _count(db, News, News.hidden.is_(True), News.hidden_at >= cutoff),
        "commentsHidden": _count(db, Comment, Comment.hidden.is_(True), Comment.hidden_at >= cutoff),
        "hits": {
            "sensitive": sum(p["sensitive"] for p in trend.values()),
            "nickname": sum(p["nickname"] for p in trend.values()),
        },
        "hitsTrend": [
            {"date": d, "sensitive": p["sensitive"], "nickname": p["nickname"]}
            for d, p in sorted(trend.items())
        ],
    }


# ----------------------------- 组装 -----------------------------


def build_dashboard(db: Session, days: int, now: datetime | None = None) -> dict:
    """组装大盘响应（纯函数：只吃 session 与注入的 `now`，便于直接单测）。

    `now` 作用于 `engagement` / `moderation` 的窗口截止与 `generatedAt`。
    **`sourceHealth` 不吃它**——该板块复用 `summarize_many`，与
    `/api/admin/sources` 同一口径（锚定真实当前时间）；为它单独传 `now`
    只会让同一个板块里出现两个时间基准，比不传更难排查。
    """
    return {
        "days": days,
        # 必须经 iso_utc：SQLite 的 DateTime 读回不带时区，直接出响应会让
        # 双端格式不一致（P1-19 立过的规矩）
        "generatedAt": iso_utc(now or datetime.now(timezone.utc)),
        "content": content_counts(db),
        "todo": todo_counts(db),
        "engagement": engagement_counts(db, days, now),
        "sourceHealth": source_health_block(db, days),
        "moderation": moderation_block(db, days, now),
        # 热词复用搜索页同一份口径（含它自己的 60s 缓存）：运营看到的与
        # 用户看到的应当是同一批词，否则"用户在搜什么"会有两个答案。
        # `now` 显式传下去时会绕过缓存（D6 铁律：显式 now 的注入点必须绕过），
        # 生产环境不传，照旧吃缓存
        "search": {"hot": hot_terms(db, limit=HOT_TERMS_LIMIT, days=days, now=now)},
    }
