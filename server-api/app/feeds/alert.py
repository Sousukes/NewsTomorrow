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

聚合（P1-17）：
    待告警源达到 `FEED_ALERT_AGGREGATE_MIN` 时合并成**一条汇总**，而不是一源一条。
    断网或对方站点整体挂掉时十几个源会同时异常，逐源发一条就是刷屏；
    而收件人对十几条消息也做不了逐个处置，他要的是"出事了、多少条、去看源管理"。
    低于阈值仍逐源发送：一两条时直接点开看名字比先读汇总更省事。

    汇总**按原因分组并按严重度排序**，而非罗列源名：消息中心的预览是单行省略号，
    尾部必然被截断，所以"最严重 + 最普遍"必须排在最前面。
    "超时未抓取 N 条"本身就在告诉收件人这是系统性问题（定时任务或网络），
    不需要再写一句"建议排查网络"——那句话大概率被省略号吃掉。

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
    FEED_ALERT_AGGREGATE_LIST,
    FEED_ALERT_AGGREGATE_MIN,
    FEED_ALERT_ENABLED,
    FEED_ALERT_MIN_INTERVAL_HOURS,
    FEED_ALERT_STALE_HOURS,
)
from ..models import AdminUser, Message, RssSource, User, now_str
from .fetcher import PERMANENT, TRANSIENT

logger = logging.getLogger("toutiao.alert")

# 原因分类：既是汇总文案的标签，也是**严重度顺序**。
# 抓取失败按 P1-18 的**失败性质**再细分一层：
#   - `error_permanent`：404 / robots 拒绝 / 解析无条目 —— 等不会好，必须人工处理；
#   - `error_transient`：超时 / 5xx / 429 —— 系统会自动重试，多半只是一次抖动。
# 对收件人来说这两者的对策完全不同，混在"抓取失败"里就只能一律去翻源列表。
# `error_unknown` 是存量数据（P1-18 之前落的错误没有性质）的兜底。
KIND_LABELS = {
    "error_permanent": "永久失败",
    "error_transient": "临时失败",
    "error_unknown": "抓取失败",
    "stale": "超时未抓取",
    "never": "从未成功",
}
_KIND_ORDER = ("error_permanent", "error_transient", "error_unknown", "stale", "never")
# 与 Message.summary 的列宽一致：超了会被数据库截断（MySQL 严格模式会直接报错）
_SUMMARY_LIMIT = 300


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
    """列出需要告警的源（只看**启用中**的——禁用源不抓是预期行为）。

    按 `weight` 降序返回：汇总消息会把靠前的源名列出来，位次即"谁更重要"，
    与抓取合并时的权重优先同一套语义。
    """
    now = now or datetime.now()
    cutoff = now - timedelta(hours=stale_hours)
    out: list[dict] = []
    rows = db.execute(
        select(RssSource)
        .where(RssSource.enabled.is_(True))
        .order_by(RssSource.weight.desc(), RssSource.key)
    ).scalars()
    for row in rows:
        if row.last_status == "error":
            kind, tail = "error_unknown", ""
            if row.failure_kind == PERMANENT:
                kind, tail = "error_permanent", "（永久性，需人工处理）"
            elif row.failure_kind == TRANSIENT:
                kind, tail = "error_transient", "（可重试，系统会自动继续尝试）"
            # 连续次数是"抖一下"还是"早就坏了"的唯一区分依据
            n = row.consecutive_failures or 0
            head = f"连续 {n} 次失败：" if n > 1 else "最近一次抓取失败："
            reason = f"{head}{(row.last_error or '未知原因')[:80]}{tail}"
        else:
            last = _parse(row.last_fetched_at)
            if last is None:
                kind = "never"
                reason = "从未成功抓取"
            elif last < cutoff:
                kind = "stale"
                reason = f"已超过 {stale_hours} 小时未成功抓取"
            else:
                continue
        out.append(
            {"key": row.key, "name": row.name, "kind": kind, "reason": reason, "row": row}
        )
    return out


def _short_name(name: str, limit: int = 10) -> str:
    s = (name or "").strip() or "未命名"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def compose_batch_summary(
    items: list[dict], list_limit: int = FEED_ALERT_AGGREGATE_LIST
) -> str:
    """把多条源异常压成一条摘要，形如「抓取失败 3 条（A、B、C）；超时未抓取 2 条（D、E）」。

    刻意**按原因分组**而不是逐个罗列：
    - 消息中心预览是单行省略号，尾部看不到，最严重 + 最普遍必须排最前；
    - "超时未抓取 8 条"本身就在说这是系统性问题（定时任务或网络），
      比罗列 8 个名字更能指导下一步动作。
    """
    parts: list[str] = []
    for kind in _KIND_ORDER:
        grp = [i for i in items if i.get("kind") == kind]
        if not grp:
            continue
        # 截断后去重：名字过长时被截成一模一样的前缀，并列出来是纯噪音
        # （真实场景常见于同一站点的多个频道，如"XX 科技/体育/财经"）
        names: list[str] = []
        for it in grp:
            s = _short_name(it["name"])
            if s not in names:
                names.append(s)
            if len(names) >= list_limit:
                break
        tail = " 等" if len(grp) > len(names) else ""
        parts.append(f"{KIND_LABELS[kind]} {len(grp)} 条（{'、'.join(names)}{tail}）")
    return "；".join(parts)[:_SUMMARY_LIMIT]


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
    if len(due) >= FEED_ALERT_AGGREGATE_MIN:
        # 合并成一条：收件人对 N 条独立消息做不了逐个处置，要的是"出事了、多少条"。
        # 数量放**标题**（预览区标题不受单行省略号挤压），摘要放分组明细。
        title = f"信源异常汇总（{len(due)} 条）"
        summary = compose_batch_summary(due)
        for uid in recipients:
            db.add(
                Message(
                    id=f"msg_alertbatch_{uid}_{stamp}",
                    type="system",
                    title=title,
                    summary=summary,
                    news_id=None,
                    user_id=uid,
                    time="刚刚",
                )
            )
            created += 1
    else:
        for item in due:
            # 截断到列宽：源名（100）+ 原因（含 80 字错误详情）理论上可以超过 300
            summary = f"信源「{item['name']}」异常：{item['reason']}"[:_SUMMARY_LIMIT]
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

    # 推进节流锚点：本轮已通知到人，窗口内不再重复。
    # 两个分支都要做——漏掉会让汇总消息每轮重发。
    for item in due:
        item["row"].last_alerted_at = now_str()
    db.commit()
    if created:
        logger.info(
            "源健康告警：%d 个源 → %d 条消息（%s）",
            len(due), created, "汇总" if len(due) >= FEED_ALERT_AGGREGATE_MIN else "逐源",
        )
    return created
