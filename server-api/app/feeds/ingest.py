"""抓取编排：多源并发 + 重试 + 去重入库 + 关键词过滤 + 空数据兜底。

P1-16 抓取治理：
- **并发化**：多源并行抓取（线程池），源变多后一轮不再线性变慢；
- **两阶段**：先并发抓取，再**按权重顺序串行合并**——"多源转载同一稿件时
  权重高者先入库"是先到先得的语义，若直接并发合并会让入库顺序随机化；
- **礼貌抓取**：按 host 分组（同站串行 + 间隔）、单源最小抓取间隔、
  遵守 robots.txt（逐源可关，见 `respect_robots`）。

P1-18 失败分级：
- 只有**可重试**失败（超时 / 连接错误 / 5xx / 429）才退避重试；
  4xx / robots 拒绝 / 解析无条目是永久性的，重试多少次结果都一样；
- 连续失败**落库计数**（`consecutive_failures`），达阈值进入**熔断**
  （冷却期内整轮跳过，且不动健康状态）；
- 失败**性质**（`failure_kind`）一路带回主线程——异常跨线程常被 `str(e)` 抹平。
"""
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    FEED_CIRCUIT_COOLDOWN_MINUTES,
    FEED_CIRCUIT_THRESHOLD,
    FEED_HOST_GAP_SECONDS,
    FEED_MAX_CONCURRENCY,
    FEED_MIN_INTERVAL_MINUTES,
    FEED_RETRY_BACKOFF,
    FEED_RETRY_TRANSIENT,
)
from ..models import News
from .fetcher import (
    PERMANENT,
    TRANSIENT,
    USER_AGENT,
    FetchError,
    NotModified,
    RawItem,
    fetch_source,
    robots_check,
)
from .sources import all_enabled_sources, record_fetch_result, sources_for_channel

logger = logging.getLogger("toutiao.ingest")

# 熔断跳过的标记（与"按礼仪跳过"区分，仅用于日志可解释）
SKIP_INTERVAL = "interval"
SKIP_CIRCUIT = "circuit"


@dataclass
class FetchOutcome:
    """单源抓取结果（取代原先的 4 元组）。

    `kind` 只在失败时有值（`transient` / `permanent`）：异常对象在线程池里被
    `str(e)` 抹成字符串后分类就没了，必须**结构化地带回**主线程，
    否则 `record_fetch_result` 又退化成"所有失败一视同仁"。
    """

    key: str
    items: list | None = None
    error: str | None = None
    skipped: bool = False
    kind: str | None = None
    skip_reason: str | None = None


@dataclass
class FetchReport:
    """一次抓取的执行结果（供接口返回与日志）。"""

    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    ok_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.fetched


def _client(timeout: float, connect: float) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=connect),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _fetch_with_retry(client: httpx.Client, source: dict, limit: int) -> list[RawItem]:
    """带指数退避的抓取，但**只对可重试失败**退避。

    原实现对所有失败一律重试 N 次：404 / robots 拒绝 / 解析无条目这类**永久性**失败，
    重试再多次结果都一样，只是白白多打 N 个请求——对自己是浪费，对对方站点是负担。

    源返回 304（未变更）时直接返回空列表且**不重试**——那不是失败。
    """
    name = source.get("name") or source.get("key", "")
    last_exc: Exception | None = None
    for attempt in range(FEED_RETRY_TRANSIENT + 1):
        try:
            return fetch_source(client, source, limit=limit)
        except NotModified:
            return []
        except Exception as e:
            last_exc = e
            # 认不出的异常按"可重试"处理，保守保留旧行为（不至于因为分类缺失丢掉重试）
            retryable = getattr(e, "kind", TRANSIENT) == TRANSIENT
            if not retryable:
                logger.info("永久失败，不重试 [%s]：%s（需改配置或换源）", name, e)
                break
            if attempt >= FEED_RETRY_TRANSIENT:
                break
            # 指数退避 + 抖动，避免多源同时重试打爆对端
            delay = FEED_RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 0.3)
            logger.info(
                "重试 %s（第 %d 次）：%s，等待 %.2fs", name, attempt + 1, e, delay
            )
            time.sleep(delay)
    if last_exc is None:  # pragma: no cover - 循环至少执行一次
        raise FetchError("抓取失败", PERMANENT)
    raise last_exc


def _dedupe_sources(srcs: list[dict]) -> list[dict]:
    """按 key 去重并按 weight 降序（与库内顺序无关，保证高权重优先入库）。"""
    seen: dict[str, dict] = {}
    for s in srcs:
        seen.setdefault(s.get("key", ""), s)
    return sorted(seen.values(), key=lambda s: s.get("weight", 0), reverse=True)


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _group_by_host(srcs: list[dict]) -> list[list[dict]]:
    """按 host 分组：**保持 `srcs` 的相对顺序**（权重降序），同站源归到一组。

    组间并发（不同站点互不影响）、组内串行（同一站点一次只发一个请求）——
    并发的意义是省掉等待时间，不是把同一站点打 N 遍。
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for s in srcs:
        h = _host_of(s.get("url", "")) or s.get("key", "")
        if h not in groups:
            groups[h] = []
            order.append(h)
        groups[h].append(s)
    return [groups[h] for h in order]


def _too_soon(source: dict, min_interval_minutes: int) -> bool:
    """是否刚抓过（在最小间隔内）。`last_fetched_at` 由 `sources._to_dict` 带出。"""
    last = source.get("last_fetched_at")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(str(last))
    except (TypeError, ValueError):
        return False
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return (now - dt) < timedelta(minutes=min_interval_minutes)


def _circuit_open(source: dict, now: datetime | None = None) -> bool:
    """熔断判定：连续失败达阈值后，冷却期内不再打这个源。

    意义：一个已经连挂 N 轮的源，不需要每轮再打 `FEED_RETRY_TRANSIENT + 1` 次
    ——既省自己的时间，也不去烦一个正在故障的站点。

    自愈：冷却到期后自动恢复尝试，成功即清零计数（`record_fetch_result`），
    无需人工干预。
    """
    if not FEED_CIRCUIT_THRESHOLD:
        return False
    if (source.get("consecutive_failures") or 0) < FEED_CIRCUIT_THRESHOLD:
        return False
    until = source.get("next_retry_at")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(str(until))
    except (TypeError, ValueError):
        return False
    ref = now or (datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now())
    return dt > ref


def _fetch_one(client, source: dict, limit: int, min_interval_minutes: int) -> FetchOutcome:
    """抓一个源。单个源的失败/跳过**不抛**：并发下异常要带回主线程归类，而不是炸掉整组。"""
    key = source.get("key") or source.get("url", "")
    if min_interval_minutes and _too_soon(source, min_interval_minutes):
        return FetchOutcome(key, skipped=True, skip_reason=SKIP_INTERVAL)
    if _circuit_open(source):
        # 与"按礼仪跳过"同口径：本轮没抓，就**不刷新健康状态**。
        # 该源仍然是不健康的（last_status 保持 error），只是不去打扰它。
        return FetchOutcome(key, skipped=True, skip_reason=SKIP_CIRCUIT)
    if source.get("respect_robots", True):
        try:
            allowed, delay, reason = robots_check(client, source["url"])
        except Exception as e:  # robots 判定本身异常不应拖垮抓取
            logger.info("robots 检查异常[%s]，按放行处理: %s", key, e)
            allowed, delay, reason = True, None, ""
        if not allowed:
            return FetchOutcome(key, error=reason or "robots.txt 禁止抓取", kind=PERMANENT)
        if delay:
            # 尊重 Crawl-delay，但封顶 5s：对方若写了极大值，不能把整轮拖死
            time.sleep(min(float(delay), 5.0))
    try:
        return FetchOutcome(key, items=_fetch_with_retry(client, source, limit))
    except Exception as e:
        logger.warning("源 %s 抓取失败：%s", key, e)
        return FetchOutcome(key, error=str(e), kind=getattr(e, "kind", TRANSIENT))


def _fetch_group(client, group: list[dict], limit: int, gap: float, min_interval: int):
    """同一 host 的多个源**串行**抓取，源之间留 `gap` 秒喘息。"""
    out = []
    for i, s in enumerate(group):
        if i and gap:
            time.sleep(gap)
        out.append(_fetch_one(client, s, limit, min_interval))
    return out


def _collect_all(
    srcs: list[dict],
    client,
    keyword: str | None,
    limit_per_source: int,
    stats: list[dict] | None,
    workers: int,
    gap: float,
    interval: int,
) -> list[RawItem]:
    """两阶段：并发抓取 → 按权重顺序串行合并（实现见 `collect` 的说明）。"""
    groups = _group_by_host(srcs)
    fetched: dict[str, FetchOutcome] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as pool:
        futures = [
            pool.submit(_fetch_group, client, g, limit_per_source, gap, interval)
            for g in groups
        ]
        for f in as_completed(futures):
            # 组内已逐个兜住异常；这里再兜一层，避免线程池本身的意外炸掉整轮
            try:
                results = f.result()
            except Exception as e:  # pragma: no cover - 防御性
                logger.warning("抓取分组异常: %s", e)
                continue
            for oc in results:
                fetched[oc.key] = oc

    collected: dict[str, RawItem] = {}
    for s in srcs:
        oc = fetched.get(s["key"]) or FetchOutcome(s["key"], error="未执行", kind=TRANSIENT)
        if oc.skipped:
            if stats is not None:
                stats.append(
                    {
                        "key": s["key"],
                        "ok": True,
                        "count": 0,
                        "error": None,
                        "skipped": True,
                        "skip_reason": oc.skip_reason,
                    }
                )
            if oc.skip_reason == SKIP_CIRCUIT:
                logger.info(
                    "源 %s 处于熔断冷却期，本轮跳过（已连续失败 %s 次）",
                    s["key"], s.get("consecutive_failures"),
                )
            continue
        if oc.error is not None or oc.items is None:
            if stats is not None:
                stats.append(
                    {
                        "key": s["key"],
                        "ok": False,
                        "count": 0,
                        "error": (oc.error or "抓取失败")[:300],
                        "skipped": False,
                        "kind": oc.kind,
                        "skip_reason": None,
                    }
                )
            continue
        if stats is not None:
            stats.append(
                {
                    "key": s["key"],
                    "ok": True,
                    "count": len(oc.items),
                    "error": None,
                    "skipped": False,
                    "kind": None,
                    "skip_reason": None,
                }
            )
        for it in oc.items:
            if keyword and keyword not in it.title and keyword not in it.summary:
                continue
            # 同 id 保留先到的一条（多源可能转载同一稿件）
            collected.setdefault(it.id, it)
    logger.info("抓取完成：%d 个源 → %d 条去重后", len(srcs), len(collected))
    return list(collected.values())


def collect(
    channel: str | None = None,
    keyword: str | None = None,
    limit_per_source: int = 30,
    timeout: float = 15.0,
    connect_timeout: float = 8.0,
    sources: list[dict] | None = None,
    stats: list[dict] | None = None,
    max_concurrency: int | None = None,
    host_gap: float | None = None,
    min_interval_minutes: int | None = None,
    client=None,
) -> list[RawItem]:
    """按频道/关键词抓取多个源并合并去重。

    - `sources`：显式指定源列表（**优先**）。抓取流程会传入库中配置的源；
      不传则回退到内置源（`all_enabled_sources()`），保证无库场景仍可运行。
    - `stats`：可选出参，收集每个源的抓取结果
      `{key, ok, count, error, skipped, kind, skip_reason}`，供调用方回写健康状态。
      **单个源失败不影响其他源**；`skipped=True` 表示本轮未抓取（按礼仪或熔断跳过），
      调用方**不应刷新其健康状态**——刷新会让"多久没成功"永远归零。
      `kind` 是失败性质（transient / permanent），异常跨线程会被 `str(e)` 抹平，
      必须结构化带回；`skip_reason` 区分 `interval` / `circuit`，仅用于日志可解释。
    - `client`：注入 HTTP 客户端（测试用）。生产调用不传，由本函数按超时配置创建。

    并发只用于**等待网络**；合并仍按 `srcs`（权重降序）串行进行——
    `collected.setdefault` 是先到先得，若合并顺序随机化，"高权重源优先入库"
    的语义就没了。
    """
    if sources is not None:
        srcs = _dedupe_sources(sources)
    elif keyword:
        # 关键词检索覆盖全部启用源：部分源（如中新网）内容最新但条数少，
        # 限定少数源会漏掉结果，故不做源裁剪。
        srcs = all_enabled_sources()
    else:
        srcs = sources_for_channel(channel)

    if not srcs:
        return []

    workers = max(1, max_concurrency or FEED_MAX_CONCURRENCY)
    gap = FEED_HOST_GAP_SECONDS if host_gap is None else host_gap
    interval = FEED_MIN_INTERVAL_MINUTES if min_interval_minutes is None else min_interval_minutes

    if client is not None:
        return _collect_all(srcs, client, keyword, limit_per_source, stats, workers, gap, interval)
    with _client(timeout, connect_timeout) as c:
        return _collect_all(srcs, c, keyword, limit_per_source, stats, workers, gap, interval)


def upsert_news(db: Session, items: list[RawItem]) -> FetchReport:
    """写入数据库：按 id 判定新增/ 更新（增量更新，幂等）。"""
    rep = FetchReport(fetched=len(items))
    if not items:
        return rep

    existing = {
        row.id: row
        for row in db.execute(select(News).where(News.id.in_([i.id for i in items]))).scalars()
    }

    for it in items:
        row = existing.get(it.id)
        if row is None:
            db.add(
                News(
                    id=it.id,
                    title=it.title,
                    summary=it.summary,
                    source=it.source,
                    comment_count=0,
                    time=it.time_text or "刚刚",
                    images=it.images or None,
                    cover=it.cover or None,
                    video_url=None,
                    play_count=None,
                    is_top=bool(it.is_top),
                    channel=it.channel,
                    variant=it.variant,
                    published_at=it.published_at.isoformat() if it.published_at else None,
                )
            )
            rep.inserted += 1
        else:
            # 增量：只刷新易变字段，保留已积累的互动数据
            row.title = it.title
            row.summary = it.summary
            row.source = it.source
            if it.time_text:
                row.time = it.time_text
            if it.images:
                row.images = it.images
                row.cover = it.cover or row.cover
            if it.variant:
                row.variant = it.variant
            if it.published_at:
                row.published_at = it.published_at.isoformat()
            rep.updated += 1

    db.commit()
    logger.info("入库完成：新增 %d / 更新 %d", rep.inserted, rep.updated)
    return rep


def run_ingest(
    db: Session,
    channel: str | None = None,
    keyword: str | None = None,
    limit_per_source: int = 30,
    fallback_seed: bool = True,
) -> FetchReport:
    """完整抓取流程：抓取 → 入库；空结果时可选回退到种子数据。"""
    rep = FetchReport()
    stats: list[dict] = []
    try:
        # 源来自库中配置（可运营调整）；关键字检索覆盖全部启用源，不做频道裁剪
        srcs = all_enabled_sources(db) if keyword else sources_for_channel(channel, db)
        items = collect(
            channel=channel,
            keyword=keyword,
            limit_per_source=limit_per_source,
            sources=srcs,
            stats=stats,
        )
    except Exception as e:  # 网络层整体异常也不应让定时任务崩掉
        logger.exception("抓取阶段异常: %s", e)
        items = []
    finally:
        # 健康状态无论成败都回写：源挂了也要能在列表里看出来。
        # 但**按礼仪跳过**的源本轮没抓取，刷新它会让"多久没成功"永远归零。
        for st in stats:
            if st.get("skipped"):
                continue
            record_fetch_result(
                db, st["key"], st["ok"], st["count"], st["error"], kind=st.get("kind")
            )
        # 告警是本轮的收尾动作：源挂了要有人被通知，而不是等人去翻列表。
        # 放在 finally 里，抓取阶段异常时也能基于已有健康状态发出告警。
        try:
            from .alert import notify_unhealthy_sources

            notify_unhealthy_sources(db)
        except Exception as e:
            logger.warning("源健康告警失败（不影响抓取结果）: %s", e)

    if items:
        rep = upsert_news(db, items)
        return rep

    # 空数据兜底：保证库中始终有可展示内容。
    if not fallback_seed:
        rep.fetched = 0
        return rep

    # 关键：先结束当前会话事务，避免与 init_db() 自开会话互相阻塞（此前在
    # 同一会话内调用 init_db 存在锁等待隐患）。
    db.rollback()
    from ..database import SessionLocal
    from ..seed import init_db

    init_db()
    with SessionLocal() as probe:
        count = probe.execute(select(func.count()).select_from(News)).scalar() or 0
    if count:
        logger.warning("抓取为空，已确保基础数据存在（当前 %s 条）", count)
    rep.fetched = 0
    return rep


def prune_old_news(db: Session, keep_days: int = 30, keep_min: int = 200) -> int:
    """按绝对发布时间清理过旧数据（**SQL 侧完成**，不把全表载入内存）。

    规则：
    1. 删除 `published_at` 早于 cutoff 的行；
    2. 时间未知（NULL/空串）的行视为最旧，优先淘汰；
    3. 删除量不超过 `总数 - keep_min`，保证库中至少保留 keep_min 条。

    返回删除条数。
    """
    from datetime import timedelta

    from sqlalchemy import and_, case, or_

    from ..models import Comment, Message, UserFavorite, UserHistory

    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
    total = db.execute(select(func.count()).select_from(News)).scalar() or 0
    budget = total - keep_min
    if budget <= 0:
        return 0

    is_unknown = or_(News.published_at.is_(None), News.published_at == "")
    is_expired = and_(
        News.published_at.isnot(None), News.published_at != "", News.published_at < cutoff
    )

    ids = (
        db.execute(
            select(News.id)
            .where(or_(is_expired, is_unknown))
            # 未知时间优先淘汰，其余按时间从旧到新
            .order_by(case((is_unknown, 1), else_=0).desc(), News.published_at.asc())
            .limit(budget)
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0

    # 资讯将被删除：先解除消息关联、清理其下评论与用户标记，避免外键约束报错。
    # 策略：消息保留（news_id 置空）；评论、收藏、浏览历史随资讯一并删除
    #（PG 有 ON DELETE CASCADE，但 SQLite 默认不强制外键，故统一显式清理）。
    db.execute(Message.__table__.update().where(Message.news_id.in_(ids)).values(news_id=None))
    db.execute(Comment.__table__.delete().where(Comment.news_id.in_(ids)))
    db.execute(UserFavorite.__table__.delete().where(UserFavorite.news_id.in_(ids)))
    db.execute(UserHistory.__table__.delete().where(UserHistory.news_id.in_(ids)))
    db.execute(News.__table__.delete().where(News.id.in_(ids)))
    db.commit()
    logger.info("清理过期资讯 %d 条（保留 ≥%d）", len(ids), keep_min)
    return len(ids)