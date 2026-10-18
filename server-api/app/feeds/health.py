"""源健康历史（P1-19）：记录每轮抓取结果，并聚合成趋势。

P1-16~P1-18 把「当前状态」做到了位，但那只是**一个点**：
- 告警只看"现在是否不健康"，答不了"它是今天才开始烂的，还是已经烂了两周"；
- `last_item_count` 只看"最近一次抓到几条"，答不了"这个源是不是在缓慢退化"；
- 熔断只看"连续失败几次"，答不了"改了配置之后有没有好转"。

本模块补上时间维度。两类故障只有历史能看见：

1. **规律性抖动**：每天凌晨固定失败几轮——多半是对方维护窗口，不是我们的锅，
   看见规律就不会去瞎改配置；
2. **成功但越来越少**：一直 `ok`，条数从 30 条掉到 3 条。
   它永远是绿的，其实已经废了——**这是当前健康字段结构性看不见的一类**。

存储用**原始事件**（每轮一行）而非每日聚合表，理由与 `SearchLog` 同源：
聚合表只能表达"历史累计"，事后想换个窗口（7 天 / 30 天）重看就没数据了。
代价是需要清理（见 `prune`）。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import FEED_HEALTH_KEEP_DAYS
from ..models import RssSourceHealthEvent, iso_utc, now_dt, now_str

logger = logging.getLogger("toutiao.feeds.health")

# 日期前缀长度：`at` 是 ISO 串（2026-10-15T03:24:35），前 10 位即日期。
# 用 substr 而不是数据库的日期函数：SQLite 与 PostgreSQL 行为不一，
# 而这里只需要"按天分桶"，字符串前缀足够且两边通用。
_DATE_LEN = 10


def record_event(
    db: Session,
    key: str,
    ok: bool,
    count: int,
    error: str | None = None,
    kind: str | None = None,
) -> None:
    """记一轮抓取的结果。

    **只记真实尝试**（ok / failed）。按礼仪或熔断跳过的源本轮根本没发请求，
    留一行会伪造出"尝试过"的痕迹——与「跳过的源不刷新健康状态」同一条口径。
    因此趋势图上的空白 = 那时没抓它，不是失败。

    失败静默：这是旁路排障信息，写不进去不该影响抓取结果。
    """
    try:
        db.add(
            RssSourceHealthEvent(
                source_key=key,
                ok=bool(ok),
                item_count=int(count or 0),
                # strip 后再截断：纯空白不是错误，存进去前端 `v-if="error"` 会渲染空条
                error=(error or "").strip()[:300] or None,
                kind=kind,
                at=now_dt(),
            )
        )
        db.commit()
    except Exception as e:
        logger.warning("源健康事件写入失败（不影响抓取）: %s", e)
        db.rollback()


def prune(db: Session, keep_days: int | None = None) -> int:
    """清理过旧的健康事件，返回删除条数。

    **按时间删，不按条数保底**（与 `prune_old_news` 的 keep_min 不同）：
    健康历史是排障取证用的，保底条数会留下一堆最旧的噪音，
    而"最近 N 天"才是它的全部价值——过期即无意义。
    """
    days = FEED_HEALTH_KEEP_DAYS if keep_days is None else keep_days
    # A3：cutoff 与 `at` 列同为 UTC aware 口径（写入侧 now_str / 存量经
    # 迁移 0027 回填）。此前 cutoff 是 naive 本地串，与 aware 的 `at`
    # 做字符串比较会整段错位——字典序只在同格式下才是时间序。
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat(timespec="seconds")
    try:
        n = (
            db.execute(
                RssSourceHealthEvent.__table__.delete().where(RssSourceHealthEvent.at < cutoff)
            ).rowcount
            or 0
        )
        db.commit()
        if n:
            logger.info("清理过期源健康事件 %d 条（保留 %d 天）", n, days)
        return n
    except Exception as e:
        logger.warning("清理过期源健康事件失败（不影响抓取）: %s", e)
        db.rollback()
        return 0


def _since(days: int) -> datetime:
    """N 天前的 UTC aware 锚点（`DateTime` 列的**参数绑定值**，不是字符串）。

    D1-1：`at` 列改成 `DateTime` 后，这里返回 datetime 对象由 SQLAlchemy
    参数绑定。此前返回 ISO 串、让数据库做**字符串比较**——那依赖"同格式
    字典序 == 时间序"这条契约；列有了真实类型后，继续拼字符串等于主动放弃
    类型带来的好处，还多一层隐式转换的出错面。
    """
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


def _rate(ok: int, total: int) -> float | None:
    """成功率。无尝试时返回 **None 而不是 0**——"没抓过"和"全挂了"是两回事，
    前端据此才能区分「近期无数据」与「近期全失败」。"""
    if not total:
        return None
    return round(ok / total, 4)


def summarize(db: Session, key: str, days: int) -> dict:
    """单源在窗口内的汇总。

    `avgItems` 是**成功轮次的平均条数**而不是全部轮次的：失败轮次天然是 0 条，
    混进来会让"条数退化"被失败率掩盖——这是本模块要抓的第二类隐形故障。
    """
    cutoff = _since(days)
    row = (
        db.execute(
            select(
                func.count(RssSourceHealthEvent.id),
                func.sum(case((RssSourceHealthEvent.ok.is_(True), 1), else_=0)),
                func.sum(RssSourceHealthEvent.item_count),
                func.max(case((RssSourceHealthEvent.ok.is_(True), RssSourceHealthEvent.at))),
                func.max(case((RssSourceHealthEvent.ok.is_(False), RssSourceHealthEvent.at))),
            ).where(
                RssSourceHealthEvent.source_key == key, RssSourceHealthEvent.at >= cutoff
            )
        )
        .one()
        or None
    )
    total = int(row[0] or 0) if row else 0
    ok = int(row[1] or 0) if row else 0
    items = int(row[2] or 0) if row else 0
    return {
        "attempts": total,
        "ok": ok,
        "failed": max(0, total - ok),
        "rate": _rate(ok, total),
        "items": items,
        # 成功轮次的平均条数：看"源是不是在变空"用的就是这个
        "avgItems": round(items / ok, 1) if ok else None,
        # 必须经 iso_utc：SQLite 的 DateTime 读回不带时区，直接出响应会
        # 让双端格式不一致（PG 带 +00:00、SQLite 裸串）。
        "lastOkAt": iso_utc(row[3] if row else None),
        "lastFailAt": iso_utc(row[4] if row else None),
    }


def summarize_many(db: Session, keys: list[str], days: int) -> dict[str, dict]:
    """一次查多源的窗口汇总（供源列表用）。

    刻意**单条 GROUP BY 查询**而不是逐源查 N 次：源列表是管理端首屏，
    14 个源各跑一次会让首屏变成 N+1。
    """
    if not keys:
        return {}
    cutoff = _since(days)
    rows = (
        db.execute(
            select(
                RssSourceHealthEvent.source_key,
                func.count(RssSourceHealthEvent.id),
                func.sum(case((RssSourceHealthEvent.ok.is_(True), 1), else_=0)),
                func.sum(RssSourceHealthEvent.item_count),
            )
            .where(
                RssSourceHealthEvent.source_key.in_(keys), RssSourceHealthEvent.at >= cutoff
            )
            .group_by(RssSourceHealthEvent.source_key)
        )
        .all()
    )
    out: dict[str, dict] = {}
    for key, total, ok, items in rows:
        total = int(total or 0)
        ok = int(ok or 0)
        items = int(items or 0)
        out[key] = {
            "attempts": total,
            "ok": ok,
            "rate": _rate(ok, total),
            "avgItems": round(items / ok, 1) if ok else None,
        }
    return out


def daily_points(db: Session, key: str, days: int) -> list[dict]:
    """按天分桶（升序）。

    **缺的天不补零**：空白表示"那天没抓它"（跳过 / 未启用 / 服务没跑），
    补零会把它伪装成"抓了但全失败"——这两种情况的处置完全不同。
    前端按 `days` 铺好格子再按日期落点，空白自然显示为无数据。
    """
    cutoff = _since(days)
    day = func.substr(RssSourceHealthEvent.at, 1, _DATE_LEN)
    rows = (
        db.execute(
            select(
                day.label("date"),
                func.count(RssSourceHealthEvent.id),
                func.sum(case((RssSourceHealthEvent.ok.is_(True), 1), else_=0)),
                func.sum(RssSourceHealthEvent.item_count),
            )
            .where(RssSourceHealthEvent.source_key == key, RssSourceHealthEvent.at >= cutoff)
            .group_by(day)
            .order_by(day)
        )
        .all()
    )
    points = []
    for date, total, ok, items in rows:
        total = int(total or 0)
        ok = int(ok or 0)
        points.append(
            {
                "date": str(date),
                "total": total,
                "ok": ok,
                "failed": max(0, total - ok),
                "items": int(items or 0),
                "rate": _rate(ok, total),
            }
        )
    return points


def recent_failures(db: Session, key: str, days: int, limit: int = 20) -> list[dict]:
    """窗口内最近的失败（新的在前）。

    只列失败：成功在趋势图里已经看得到，这里要回答的是"它到底为什么挂"。
    """
    cutoff = _since(days)
    rows = (
        db.execute(
            select(RssSourceHealthEvent)
            .where(
                RssSourceHealthEvent.source_key == key,
                RssSourceHealthEvent.at >= cutoff,
                RssSourceHealthEvent.ok.is_(False),
            )
            .order_by(RssSourceHealthEvent.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "at": iso_utc(r.at),
            "error": r.error,
            "kind": r.kind,
            "items": r.item_count,
        }
        for r in rows
    ]


def window_days(requested: int | None) -> int:
    """把请求的天数收敛到保留期内：超出保留期的窗口只会得到一片空白，
    与其让前端渲染 90 个空格子，不如直接给一个与数据等宽的窗口。"""
    if not requested or requested <= 0:
        return FEED_HEALTH_KEEP_DAYS
    return int(min(requested, FEED_HEALTH_KEEP_DAYS))
