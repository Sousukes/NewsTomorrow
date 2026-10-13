"""RSS 新闻源配置（**可运行时配置**）。

演进：
    源原先硬编码在本文件的 `RSS_SOURCES` 列表——新增/停用/调权重都要改代码并重新部署。
    现在**实际抓取读库**（`rss_sources` 表，迁移 0013），运营可通过管理接口调整；
    内置的 `BUILTIN_SOURCES` 退化为两个用途：
    1. **播种**：表为空时写入，保证新环境开箱可用；
    2. **回退**：拿不到数据库会话时（单测/脚本）仍可工作。

全部源均为**免费公开、无需 API Key** 的源。
"""
import logging

from sqlalchemy.orm import Session

from ..models import RssSource, now_str

logger = logging.getLogger("toutiao.sources")

# 频道 → 后端 News.channel 取值（须与前端 ChannelTabs 的 key 对齐）
# 该列表此前是**死常量**（无任何引用）且比前端少了 `video`，现已投入使用：
# `schemas.SourceIn/UpdateIn` 用它校验运营填入的频道。改动时务必同步
# `mingri-toutiao-vue/src/constants/channels.ts`，否则前端标签会与实际数据脱节。
CHANNEL_KEYS = ["recommend", "hot", "video", "tech", "finance", "sports", "car"]

# 内置源：播种数据与无库回退。key 为内部标识，也是与库记录对齐的依据。
BUILTIN_SOURCES: list[dict] = [
    # 人民网：分类全、单源量大（100 条/次）
    {"key": "people_politics", "name": "人民网·时政", "channel": "recommend",
     "url": "http://www.people.com.cn/rss/politics.xml", "weight": 10, "enabled": True},
    {"key": "people_finance", "name": "人民网·财经", "channel": "finance",
     "url": "http://www.people.com.cn/rss/finance.xml", "weight": 8, "enabled": True},
    {"key": "people_scitech", "name": "人民网·科技", "channel": "tech",
     "url": "http://www.people.com.cn/rss/scitech.xml", "weight": 8, "enabled": True},
    {"key": "people_sports", "name": "人民网·体育", "channel": "sports",
     "url": "http://www.people.com.cn/rss/sports.xml", "weight": 6, "enabled": True},
    {"key": "people_society", "name": "人民网·社会", "channel": "hot",
     "url": "http://www.people.com.cn/rss/society.xml", "weight": 8, "enabled": True},
    {"key": "people_world", "name": "人民网·国际", "channel": "recommend",
     "url": "http://www.people.com.cn/rss/world.xml", "weight": 8, "enabled": True},
    # 新华网：单次300 条，作为主力与冗余
    {"key": "xinhua_politics", "name": "新华网·时政", "channel": "recommend",
     "url": "http://www.xinhuanet.com/politics/news_politics.xml", "weight": 10, "enabled": True},
    {"key": "xinhua_fortune", "name": "新华网·财经", "channel": "finance",
     "url": "http://www.xinhuanet.com/fortune/news_fortune.xml", "weight": 8, "enabled": True},
    # 中新网：滚动新闻，单次 30 条
    {"key": "chinanews_scroll", "name": "中新网·要闻", "channel": "hot",
     "url": "https://www.chinanews.com.cn/rss/scroll-news.xml", "weight": 6, "enabled": True},
    {"key": "chinanews_finance", "name": "中新网·财经", "channel": "finance",
     "url": "https://www.chinanews.com.cn/rss/finance.xml", "weight": 5, "enabled": True},
    {"key": "chinanews_sports", "name": "中新网·体育", "channel": "sports",
     "url": "https://www.chinanews.com.cn/rss/sports.xml", "weight": 4, "enabled": True},
    # AIHOT：AI 垂直资讯（RSS 2.0，单源 50 条）。归入 tech 频道。
    {"key": "aihot_picks", "name": "AIHOT·精选", "channel": "tech",
     "url": "https://aihot.news/feed.xml", "weight": 9, "enabled": True},
    # 探测不可用：连接超时/404，保留配置但不启用
    {"key": "thepaper", "name": "澎湃新闻", "channel": "recommend",
     "url": "https://feedx.net/rss/thepaper.xml", "weight": 6, "enabled": False},
    {"key": "zaobao", "name": "联合早报·中国", "channel": "recommend",
     "url": "https://plink.anyfeeder.com/zaobao/realtime/china", "weight": 5, "enabled": True},
]

# 兼容旧引用：行为等价于内置源（无库环境使用）
RSS_SOURCES: list[dict] = BUILTIN_SOURCES


def _by_weight(srcs: list[dict]) -> list[dict]:
    """按 weight 降序：多源转载同一稿件时，权重高者先入库（collect 中先到先得）。"""
    return sorted(srcs, key=lambda s: s.get("weight", 0), reverse=True)


def _to_dict(row: RssSource) -> dict:
    """源 → 抓取用字典。

    健康/礼仪字段一并带出：`last_fetched_at` 用于**单源最小抓取间隔**判定
    （定时任务与手动触发叠加时不重复打同一个源），`respect_robots` 决定本轮
    是否先查对方 robots.txt（P1-16）。
    """
    return {
        "key": row.key,
        "name": row.name,
        "channel": row.channel,
        "url": row.url,
        "weight": row.weight,
        "enabled": bool(row.enabled),
        "respect_robots": bool(row.respect_robots),
        "last_fetched_at": row.last_fetched_at,
    }


def ensure_seeded(db: Session) -> int:
    """表为空时播种内置源，返回播种条数。

    只在**表为空**时播种：否则运营的停用/调权重会被下次启动覆盖。
    """
    if db.query(RssSource).count() > 0:
        return 0
    now = now_str()
    for s in BUILTIN_SOURCES:
        db.add(
            RssSource(
                key=s["key"],
                name=s["name"],
                channel=s["channel"],
                url=s["url"],
                weight=s["weight"],
                enabled=s["enabled"],
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    logger.info("已播种 %d 个内置 RSS 源", len(BUILTIN_SOURCES))
    return len(BUILTIN_SOURCES)


def _load(db: Session) -> list[dict]:
    """读取全部源（必要时先播种）。"""
    ensure_seeded(db)
    return [_to_dict(r) for r in db.query(RssSource).all()]


def all_enabled_sources(db: Session | None = None) -> list[dict]:
    """全部启用源（按 weight 降序）。

    `db` 为空时回落到内置源——保证单测与不连库的脚本仍可运行。
    """
    if db is None:
        # 无库回退：内置源补齐礼仪字段默认值，使下游判定无需到处 `.get(..., 默认)`
        return _by_weight(
            [
                {**s, "respect_robots": s.get("respect_robots", True), "last_fetched_at": None}
                for s in BUILTIN_SOURCES
                if s["enabled"]
            ]
        )
    return _by_weight([s for s in _load(db) if s["enabled"]])


def sources_for_channel(channel: str | None, db: Session | None = None) -> list[dict]:
    """按频道筛选启用源（按 weight 降序）。

    `recommend` 为空/None 时返回全部启用源；某频道无专属源时回退到全部，
    避免抓到空结果（宁可内容宽一些，也不要整页空白）。
    """
    active = all_enabled_sources(db)
    if not channel or channel == "recommend":
        return active
    return [s for s in active if s["channel"] == channel] or active


def record_fetch_result(db: Session, key: str, ok: bool, count: int, error: str | None) -> None:
    """回写单次抓取结果（健康状态）。失败静默——它只是排障信息。"""
    row = db.get(RssSource, key)
    if row is None:
        return
    row.last_status = "ok" if ok else "error"
    row.last_error = (error or "")[:300] or None
    row.last_item_count = count
    row.last_fetched_at = now_str()
    row.updated_at = row.last_fetched_at
    try:
        db.commit()
    except Exception as e:
        logger.warning("源健康状态回写失败（不影响抓取）: %s", e)
        db.rollback()
