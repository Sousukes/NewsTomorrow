"""排序与个性化：热度分计算、频道偏好、排序键构造。

设计要点：
    1. **热度分落库**（`news.hot_score`），查询时只做列排序——查询时计算需要 JOIN
       聚合收藏数，既慢又无法建索引，且游标分页要求排序键是稳定标量。
    2. **时间可注入**：所有计算接受 `now` 参数，避免测试依赖真实时钟（否则断言会漂移）。
    3. **个性化用 boost 而非配额**：配额式"偏好频道多给几条"要在应用层组装，
       与 keyset 游标分页不兼容；改为给偏好频道的候选**加权重分**，
       排序键仍是单一标量，游标语义不变。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .models import News, UserFavorite, UserHistory

logger = logging.getLogger("toutiao.ranking")

# ---- 热度权重（可解释、可调；改动需同步 README 与测试）----
W_COMMENT = 3.0  # 评论：最强互动信号
W_FAVORITE = 2.0  # 收藏：次强
# 播放量级远大于评论，先缩小。取 1000 而非 100：实测若取 100，
# 10 万播放（÷100 = 1000）会压过数百条评论（512×3 = 1536 量级），
# 导致"零评论的高播放视频"霸榜热榜，与"热议"语义不符。
# 取 1000 后 10 万播放 ≈ 33 条评论的热度，传播度仍计入但不主导。
PLAY_DIVISOR = 1000.0
SCORE_SCALE = 100.0  # 放大到便于阅读与比较的量级

# ---- 时间衰减 ----
HALF_LIFE_HOURS = 48.0  # 48 小时后衰减为一半
# 无法解析发布时间时的中性衰减：既不按"最新"优待（会霸榜），也不直接归零
DECAY_UNKNOWN = 0.5

# ---- 个性化 ----
FAVORITE_SIGNAL = 3.0  # 收藏比浏览更能表达偏好
HISTORY_SIGNAL = 1.0
# 偏好频道的最大加分。量级刻意压在"约 10 条评论"之下：
# 偏好只能**提升**同热度内容的次序，不足以让零互动内容压过热门内容。
CHANNEL_BOOST_MAX = 300.0

# 排序模式
SORT_LATEST = "latest"
SORT_HOT = "hot"
SORT_FOR_YOU = "for-you"
SORT_MODES = (SORT_LATEST, SORT_HOT, SORT_FOR_YOU)


def _parse_dt(value: str | None) -> datetime | None:
    """解析 ISO 时间字符串；失败返回 None（调用方按"未知"处理）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _to_naive_utc(dt: datetime) -> datetime:
    """统一成"UTC 的 naive datetime"。

    `published_at` 存的是 ISO 字符串，实际数据中既有带时区偏移的（RSS/`now_str()`
    在部分环境带 offset），也有纯 naive 的（种子数据）。两者直接相减会抛
    `can't subtract offset-naive and offset-aware datetimes`，故先归一。
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def time_decay(published_at: str | None, now: datetime) -> float:
    """时间衰减因子：越新越接近 1，随时间按半衰期衰减。"""
    dt = _parse_dt(published_at)
    if dt is None:
        return DECAY_UNKNOWN
    age_hours = (_to_naive_utc(now) - _to_naive_utc(dt)).total_seconds() / 3600.0
    # 未来时间（时钟偏移/数据错误）视作最新，避免出现负衰减放大分数
    if age_hours <= 0:
        return 1.0
    return 1.0 / (1.0 + age_hours / HALF_LIFE_HOURS)


def compute_hot_score(
    comment_count: int,
    favorite_count: int,
    play_count: int | None,
    published_at: str | None,
    now: datetime,
) -> float:
    """热度分 = 互动加权 × 时间衰减 × 放大系数（保留 4 位小数）。"""
    raw = (
        float(comment_count or 0) * W_COMMENT
        + float(favorite_count or 0) * W_FAVORITE
        + float(play_count or 0) / PLAY_DIVISOR
    )
    score = raw * time_decay(published_at, now) * SCORE_SCALE
    return round(score, 4)


def _favorite_counts(db: Session) -> dict[str, int]:
    rows = db.query(UserFavorite.news_id, func.count(UserFavorite.news_id)).group_by(
        UserFavorite.news_id
    )
    return {news_id: int(c) for news_id, c in rows}


def score_for(db: Session, news: News, fav_counts: dict[str, int] | None = None, now=None) -> float:
    """计算单条资讯的热度分（供重算与增量 bump 共用）。"""
    if fav_counts is None:
        fav_counts = _favorite_counts(db)
    now = now or datetime.now()
    return compute_hot_score(
        news.comment_count or 0,
        fav_counts.get(news.id, 0),
        news.play_count,
        news.published_at,
        now,
    )


def bump_hot_score(db: Session, news_id: str) -> None:
    """单条重算：评论/收藏等互动发生后立即生效，不必等定时任务。"""
    news = db.query(News).filter(News.id == news_id).first()
    if news is None:
        return
    news.hot_score = score_for(db, news)
    try:
        db.commit()
    except Exception as e:  # 热度是派生数据，失败不得影响主流程（评论/收藏）
        logger.warning("热度分更新失败（已回滚，不影响主流程）: %s", e)
        db.rollback()


def recompute_hot_scores(db: Session, now: datetime | None = None) -> int:
    """全量重算热度分（含时间衰减），供定时任务调用。返回更新条数。

    用 `bulk_update_mappings` 批量提交，避免逐行 UPDATE。
    """
    now = now or datetime.now()
    fav_counts = _favorite_counts(db)
    rows = db.query(News.id, News.comment_count, News.play_count, News.published_at).all()
    mappings = [
        {
            "id": nid,
            "hot_score": compute_hot_score(
                comment_count or 0,
                fav_counts.get(nid, 0),
                play_count,
                published_at,
                now,
            ),
        }
        for nid, comment_count, play_count, published_at in rows
    ]
    if not mappings:
        return 0
    db.bulk_update_mappings(News, mappings)
    db.commit()
    return len(mappings)


def channel_preferences(db: Session, user_id: int) -> dict[str, float]:
    """用户的频道偏好权重（0~1，1 为最偏好）。

    收藏信号强于浏览信号（收藏权重 3、浏览 1）。归一化用最大计数而非总数，
    使"只看一个频道"的用户该频道权重为 1，而不是被总浏览数摊薄。
    """
    fav_rows = (
        db.query(News.channel, func.count(News.channel))
        .join(UserFavorite, UserFavorite.news_id == News.id)
        .filter(UserFavorite.user_id == user_id)
        .group_by(News.channel)
    )
    hist_rows = (
        db.query(News.channel, func.count(News.channel))
        .join(UserHistory, UserHistory.news_id == News.id)
        .filter(UserHistory.user_id == user_id)
        .group_by(News.channel)
    )

    scores: dict[str, float] = {}
    for channel, cnt in fav_rows:
        scores[channel] = scores.get(channel, 0.0) + float(cnt) * FAVORITE_SIGNAL
    for channel, cnt in hist_rows:
        scores[channel] = scores.get(channel, 0.0) + float(cnt) * HISTORY_SIGNAL

    if not scores:
        return {}
    top = max(scores.values())
    if top <= 0:
        return {}
    return {ch: round(v / top, 4) for ch, v in scores.items()}


def channel_boost_expr(weights: dict[str, float]):
    """频道偏好 → SQL CASE 表达式（未命中频道为 0）。

    权重为空时返回字面量 0，避免生成空 CASE（部分方言不接受）。
    """
    if not weights:
        from sqlalchemy import literal

        return literal(0.0)
    # 数值字面量内联：权重来自服务端计算，非用户输入，但仍强制 float 避免类型意外
    whens = [(News.channel == ch, float(w) * CHANNEL_BOOST_MAX) for ch, w in weights.items()]
    return case(*whens, else_=0.0)


def rank_key(sort: str, weights: dict[str, float] | None = None):
    """排序键表达式（越大越靠前）。

    - latest：不用分值，返回 None（调用方用 published_at 排序）
    - hot：hot_score
    - for-you：hot_score + 频道偏好加分
    """
    if sort == SORT_LATEST:
        return None
    if sort == SORT_FOR_YOU:
        return News.hot_score + channel_boost_expr(weights or {})
    return News.hot_score
