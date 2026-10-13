"""抓取编排：多源并发 + 重试 + 去重入库 + 关键词过滤 + 空数据兜底。"""
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import News
from .fetcher import USER_AGENT, FetchError, NotModified, RawItem, fetch_source
from .sources import all_enabled_sources, record_fetch_result, sources_for_channel

logger = logging.getLogger("toutiao.ingest")

# 退避重试参数
RETRY_TIMES = 2
BACKOFF_BASE = 0.6


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
    """带指数退避的抓取；重试仍失败则抛出 `FetchError`（由 `collect` 记为该源故障）。

    源返回 304（未变更）时直接返回空列表且**不重试**——那不是失败。
    """
    last_exc: Exception | None = None
    for attempt in range(RETRY_TIMES + 1):
        try:
            return fetch_source(client, source, limit=limit)
        except NotModified:
            return []
        except Exception as e:
            last_exc = e
            if attempt < RETRY_TIMES:
                # 指数退避 + 抖动，避免多源同时重试打爆对端
                delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.3)
                logger.info(
                    "重试 %s（第 %d 次）：%s，等待 %.2fs", source.get("name"), attempt + 1, e, delay
                )
                time.sleep(delay)
    raise last_exc if last_exc is not None else FetchError("抓取失败")


def collect(
    channel: str | None = None,
    keyword: str | None = None,
    limit_per_source: int = 30,
    timeout: float = 15.0,
    connect_timeout: float = 8.0,
    sources: list[dict] | None = None,
    stats: list[dict] | None = None,
) -> list[RawItem]:
    """按频道/ 关键词抓取多个源并合并去重。

    - `sources`：显式指定源列表（**优先**）。抓取流程会传入库中配置的源；
      不传则回退到内置源（`all_enabled_sources()`），保证无库场景仍可运行。
    - `stats`：可选出参，收集每个源的抓取结果 `{key, ok, count, error}`，
      供调用方回写健康状态。**单个源失败不影响其他源**。
    """
    if sources is not None:
        srcs = _dedupe_sources(sources)
    elif keyword:
        # 关键词检索覆盖全部启用源：部分源（如中新网）内容最新但条数少，
        # 限定少数源会漏掉结果，故不做源裁剪。
        srcs = all_enabled_sources()
    else:
        srcs = sources_for_channel(channel)

    collected: dict[str, RawItem] = {}
    with _client(timeout, connect_timeout) as client:
        for s in srcs:
            try:
                items = _fetch_with_retry(client, s, limit_per_source)
                if stats is not None:
                    stats.append({"key": s["key"], "ok": True, "count": len(items), "error": None})
            except Exception as e:  # 单源失败不得拖垮整轮抓取
                logger.warning("源 %s 抓取失败：%s", s.get("key"), e)
                if stats is not None:
                    stats.append(
                        {"key": s["key"], "ok": False, "count": 0, "error": str(e)[:300]}
                    )
                continue
            for it in items:
                if keyword and keyword not in it.title and keyword not in it.summary:
                    continue
                # 同 id 保留先到的一条（多源可能转载同一稿件）
                collected.setdefault(it.id, it)
    logger.info("抓取完成：%d 个源 → %d 条去重后", len(srcs), len(collected))
    return list(collected.values())


def _dedupe_sources(srcs: list[dict]) -> list[dict]:
    """按 key 去重并按 weight 降序（与库内顺序无关，保证高权重优先入库）。"""
    seen: dict[str, dict] = {}
    for s in srcs:
        seen.setdefault(s.get("key", ""), s)
    return sorted(seen.values(), key=lambda s: s.get("weight", 0), reverse=True)


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
        # 健康状态无论成败都回写：源挂了也要能在列表里看出来
        for st in stats:
            record_fetch_result(db, st["key"], st["ok"], st["count"], st["error"])

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