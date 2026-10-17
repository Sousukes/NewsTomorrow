"""命中日志留存治理（C7，合规）。

`sensitive_hits` / `nickname_hits` 的 `snippet` 存的是**用户原文**：脱敏不可逆，
它是判断"是否误杀"和受理申诉的唯一证据。但证据价值随时间衰减——
无限期保存用户原话本身就是合规风险（数据最小化原则）。

策略：

- 按保留期删除（`HIT_LOG_KEEP_DAYS`，默认 **90 天**）；
- **不按条数保底**（与 `prune_old_news` 的 `keep_min` 不同）：保留期就是它的全部价值，
  保底会留下一批最旧的用户原话，反而违背"最小留存"；
- `keep_days=0` 显式关闭清理（仅取证期临时使用，不建议长期如此）。

与累计计数的关系：`nickname_words.hit_count` 是**聚合值**，不随日志删除而回退——
"这个词历史上命中过多少次"与"还能看到多少条原文证据"是两个不同的问题，
前者用于发现高频词/误杀大户，后者是申诉材料。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import HIT_LOG_KEEP_DAYS
from .models import NicknameHit, SensitiveHit


def _cutoff(days: int, now: datetime | None = None) -> str:
    """保留期截止时间（UTC aware ISO 串）。

    A3 之后所有时间列同为 UTC aware，**字典序即时间序**；用 naive 本地串比较会整段错位。
    """
    base = now or datetime.now(timezone.utc)
    return (base - timedelta(days=days)).isoformat(timespec="seconds")


def prune_hit_logs(
    db: Session, keep_days: int | None = None, now: datetime | None = None
) -> dict[str, int]:
    """删除超过保留期的命中日志，返回各表删除条数。

    @param keep_days 保留天数；`None` 取配置，`0` 表示不清理
    @param now       基准时间（测试注入用）
    """
    days = HIT_LOG_KEEP_DAYS if keep_days is None else keep_days
    if days <= 0:
        return {"sensitive": 0, "nickname": 0}

    cutoff = _cutoff(days, now)
    sensitive = (
        db.execute(
            SensitiveHit.__table__.delete().where(SensitiveHit.created_at < cutoff)
        ).rowcount
        or 0
    )
    nickname = (
        db.execute(
            NicknameHit.__table__.delete().where(NicknameHit.created_at < cutoff)
        ).rowcount
        or 0
    )
    db.commit()
    return {"sensitive": int(sensitive), "nickname": int(nickname)}
