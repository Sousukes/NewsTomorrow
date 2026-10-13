"""源健康告警（P1-16）：让"源挂了"主动找到人，而不是等人去翻列表。

背景：
    健康字段（`last_status` / `last_fetched_at`）早已落库，界面上也能看到「异常」，
    但那是**被动**的——源悄悄烂掉时没人被通知，只有下次有人打开源管理页才发现。

判定：
    - **异常源**：`last_status == 'error'`；
    - **超时未成功**：`last_fetched_at` 为空（从未抓过）或早于 `FEED_ALERT_STALE_HOURS`。
      后者覆盖"定时抓取根本没跑到这个源"的情况（如源被禁用前的残留、抓取锁竞争导致跳过）。

节流：
    同一源两次告警至少间隔 `FEED_ALERT_MIN_INTERVAL_HOURS`，锚点落在
    `rss_sources.last_alerted_at`——**落库而非进程内变量**，多实例部署时共享同一窗口
    （定时抓取有 advisory lock，但手动触发不走锁），进程重启也不会重新刷屏。

收件人：
    管理能力来自 `ADMIN_PHONES` ∪ `admin_users` 表（与 `routers.admin.is_admin` 同源），
    但消息中心按 `user_id` 投递，故需把手机号解析成**已注册**的用户；
    未注册（预授权）的管理员收不到消息不报错——他们还没账号可收。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from ..config import (
    ADMIN_PHONES,
    FEED_ALERT_ENABLED,
    FEED_ALERT_MIN_INTERVAL_HOURS,
    FEED_ALERT_STALE_HOURS,
)
from ..models import AdminUser, Message, RssSource, User, now_str

logger = logging.getLogger("toutiao.alert")


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def unhealthy_sources(
    db,
    stale_hours: int = FEED_ALERT_STALE_HOURS,
    now: datetime | None = None,
) -> list[dict]:
    """列出需要告警的源（只看**启用中**的——禁用源不抓是预期行为）。"""
    now = now or datetime.now()
    cutoff = now - timedelta(hours=stale_hours)
    out: list[dict] = []
    for row in db.execute(
        select(RssSource).where(RssSource.enabled.is_(True))
    ).scalars():
        if row.last_status == "error":
            reason = f"最近一次抓取失败：{(row.last_error or '未知原因')[:80]}"
        else:
            last = _parse(row.last_fetched_at)
            if last is None:
                reason = "从未成功抓取"
            elif last < cutoff:
                reason = f"已超过 {stale_hours} 小时未成功抓取"
            else:
                continue
        out.append({"key": row.key, "name": row.name, "reason": reason, "row": row})
    return out


def _admin_user_ids(db) -> list[int]:
    """把管理能力（env ∪ 库白名单）解析成**已注册**用户 id。"""
    phones = {p for p in ADMIN_PHONES if p}
    phones |= {r[0] for r in db.execute(select(AdminUser.phone)).all()}
    if not phones:
        return []
    rows = db.execute(select(User.id).where(User.phone.in_(phones))).scalars()
    return [int(uid) for uid in rows]


def unhealthy_count(db, stale_hours: int = FEED_ALERT_STALE_HOURS) -> int:
    """不健康源数量（供 `/api/metrics` 暴露，便于接监控告警）。"""
    try:
        return len(unhealthy_sources(db, stale_hours=stale_hours))
    except Exception:
        return -1  # 查询失败时用一个不可能的计数表达"未知"，而不是假装健康


def notify_unhealthy_sources(db) -> int:
    """检查源健康并按需通知管理员，返回产生的消息条数。

    失败不影响主流程：告警是旁路逻辑，由调用方吞掉异常。
    """
    if not FEED_ALERT_ENABLED:
        return 0
    now = datetime.now()
    due: list[dict] = []
    for item in unhealthy_sources(db, now=now):
        row = item["row"]
        last = _parse(row.last_alerted_at)
        if last and (now - last) < timedelta(hours=FEED_ALERT_MIN_INTERVAL_HOURS):
            continue  # 节流窗口内，不重复打扰
        due.append(item)
    if not due:
        return 0

    recipients = _admin_user_ids(db)
    if not recipients:
        # 没有管理员账号（未注册/白名单为空）：不是错误，只是无人可收。
        # 刻意**不推进**节流锚点：没通知到人就不该占用窗口，
        # 否则管理员一注册就得白等一个节流周期才收到第一条告警。
        logger.info("源健康告警：无已注册的管理员账号，跳过通知（%d 个源需要关注）", len(due))
        return 0

    created = 0
    stamp = now.strftime("%Y%m%d%H%M%S")
    for item in due:
        summary = f"信源「{item['name']}」异常：{item['reason']}"
        for uid in recipients:
            db.add(
                Message(
                    id=f"msg_alert_{item['key']}_{uid}_{stamp}",
                    type="system",
                    title="信源异常提醒",
                    summary=summary,
                    news_id=None,
                    user_id=uid,
                    time="刚刚",
                )
            )
            created += 1
        # 推进节流锚点：本条已通知到人，窗口内不再重复
        item["row"].last_alerted_at = now_str()
    db.commit()
    if created:
        logger.info("源健康告警：%d 个源 → %d 条消息", len(due), created)
    return created
