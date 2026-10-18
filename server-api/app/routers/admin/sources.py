"""管理端 · RSS 源管理：列表 / 健康 / 增删改 / 重置。读取即播种（`ensure_seeded`）。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ...database import get_db
from urllib.parse import urlparse

from ...config import FEED_HEALTH_KEEP_DAYS, FEED_HEALTH_LIST_DAYS
from ...feeds.health import (
    daily_points,
    recent_failures,
    summarize,
    summarize_many,
    window_days,
)
from ...feeds.sources import ensure_seeded
from ...models import RssSource, User, now_str
from ...response import ok
from ...schemas import SourceIn, SourceUpdateIn
from ...step_up import require_step_up
from . import require_admin
from ._shared import (
    record_audit,
    ALLOWED_SCHEMES,
    AUDIT_SOURCE_CREATE,
    AUDIT_SOURCE_DELETE,
    AUDIT_SOURCE_RESET,
    AUDIT_SOURCE_UPDATE,
)

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

def _validate_url(url: str) -> None:
    """仅允许 http/https：抓取器会真的去请求这个地址，必须挡住 file:// 等协议。"""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise HTTPException(
            status_code=400, detail={"code": 40004, "message": "源地址必须为 http/https URL"}
        )


def _out(row: RssSource, health: dict | None = None) -> dict:
    return {
        "key": row.key,
        "name": row.name,
        "channel": row.channel,
        "url": row.url,
        "weight": row.weight,
        "enabled": bool(row.enabled),
        "lastStatus": row.last_status,
        "lastError": row.last_error,
        "lastItemCount": row.last_item_count,
        "lastFetchedAt": row.last_fetched_at,
        # P1-16：抓取礼仪（是否遵守对方 robots.txt），逐源可关
        "respectRobots": bool(row.respect_robots),
        # P1-18：失败分级与熔断。只报"异常"不够——"连挂 7 次"和"偶发抖一下"
        # 对运营是完全不同的两件事，前者要立刻处理，后者等系统自己重试即可。
        "consecutiveFailures": row.consecutive_failures or 0,
        "failureKind": row.failure_kind,
        "nextRetryAt": row.next_retry_at,
        # P1-19：近期成功率。`rate` 为 None 表示"窗口内没抓过"，
        # 与"抓了但全失败"是两回事——前端据此才能区分「近期无数据」与「近期全挂」。
        # 单源趋势页用的也是同一份数据，这里只放摘要：列表页的核心问题是
        # "该点进哪个源看细节"，而不是复刻趋势图。
        "health": health,
    }


@router.get("/sources", response_model=None)
def list_sources(db: Session = Depends(get_db)):
    """源列表（含抓取健康状态 + **近期成功率**）。

    成功率取自 P1-19 的健康历史（一次 GROUP BY 查全部源，避免 N+1）：
    「现在挂没挂」是 P1-16 就有的信息，列表上真正缺的是
    "**哪个源在悄悄退化**"——一个源可以一直绿着，但条数在持续下滑。
    """
    ensure_seeded(db)
    rows = db.query(RssSource).order_by(RssSource.weight.desc(), RssSource.key).all()
    health = summarize_many(db, [r.key for r in rows], FEED_HEALTH_LIST_DAYS)
    return ok(
        {
            "list": [_out(r, health.get(r.key)) for r in rows],
            "total": len(rows),
            # 前端要标出"近 N 天"，否则 96% 这个数字没有时间范围就是误导
            "healthDays": FEED_HEALTH_LIST_DAYS,
        }
    )


@router.get("/sources/{key}/health", response_model=None)
def source_health(
    key: str,
    days: int | None = Query(None, ge=1, le=365, description="回溯天数，默认取保留期"),
    db: Session = Depends(get_db),
):
    """单源健康趋势（P1-19）：汇总 + 按天序列 + 最近失败明细。

    与列表页的区别：列表回答"哪个源值得点进来"，这里回答"**它到底怎么了**"。
    三段数据各有分工，缺一段就答不全：

    - `summary`——窗口总览（成功率 / 成功轮均条数），判断"是抖动还是持续恶化"；
    - `points`——按天序列，判断"是不是每天凌晨固定挂"（对方维护窗口，别乱改配置）；
    - `failures`——失败原文，判断"要不要去改 URL / 换源"。

    `days` 会被收敛到保留期：请求 90 天而只留 30 天，只会得到 60 个空格子。
    响应里回传实际生效的 `days`，前端才能正确标注时间范围。
    """
    ensure_seeded(db)
    if db.get(RssSource, key) is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    win = window_days(days)
    return ok(
        {
            "key": key,
            "days": win,
            # 保留期一并发给前端：窗口被收敛时，界面要能解释"为什么最多只能看 30 天"
            "keepDays": FEED_HEALTH_KEEP_DAYS,
            "summary": summarize(db, key, win),
            "points": daily_points(db, key, win),
            "failures": recent_failures(db, key, win),
        }
    )


@router.post("/sources", response_model=None)
def create_source(
    body: SourceIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """新增源。key 冲突返回 409。"""
    _validate_url(body.url)
    ensure_seeded(db)
    if db.get(RssSource, body.key) is not None:
        # 40902 而非 40901：40901 已表示「手机号已注册」，同一码两义会让前端文案错配
        raise HTTPException(
            status_code=409, detail={"code": 40902, "message": "该源标识已存在"}
        )
    now = now_str()
    row = RssSource(
        key=body.key,
        name=body.name,
        channel=body.channel,
        url=body.url,
        weight=body.weight,
        enabled=body.enabled,
        respect_robots=body.respectRobots,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    record_audit(
        db,
        user.phone,
        AUDIT_SOURCE_CREATE,
        body.key,
        {"snapshot": _out(row)},
    )
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已新增源")


@router.patch("/sources/{key}", response_model=None)
def update_source(
    key: str,
    body: SourceUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """局部更新源（改权重 / 启停 / 换地址）。"""
    # 先确保内置源已入库：否则在尚未触发过抓取的新环境里，改一个"明明存在"的内置源会 404
    ensure_seeded(db)
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    if body.url is not None:
        _validate_url(body.url)
    before = _out(row)
    # 入参字段名 → 模型列名。多词入参走驼峰（`respectRobots`），模型侧是蛇形——两边
    # 必须显式对齐，否则 pydantic 会静默丢掉前端传来的字段（详见 schemas.SourceIn）。
    COLUMN_OF = {"respectRobots": "respect_robots"}
    for field in ("name", "channel", "url", "weight", "enabled", "respectRobots"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, COLUMN_OF.get(field, field), value)
    row.updated_at = now_str()
    after = _out(row)
    # 只记真正变化的字段：无变化的 PATCH（前端常因表单整体提交而触发）不该灌进时间线
    changes = {
        k: [before[k], after[k]]
        for k in ("name", "channel", "url", "weight", "enabled", "respectRobots")
        if before[k] != after[k]
    }
    if changes:
        record_audit(db, user.phone, AUDIT_SOURCE_UPDATE, key, {"changes": changes})
    db.commit()
    db.refresh(row)
    return ok(_out(row), message="已更新源")


@router.delete("/sources/{key}", response_model=None)
def delete_source(
    key: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    # C10：删源影响全站抓取（源没了，该渠道的资讯从此断供），不可逆
    # （要恢复得凭审计快照手工重建），故要求二次校验。
    step: User = Depends(require_step_up),
):
    """删除源。删除后若表空了，下次读取会重新播种内置源。"""
    ensure_seeded(db)  # 同上：避免删除内置源时因尚未播种而 404
    row = db.get(RssSource, key)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40402, "message": "源不存在"})
    # 删除后行就没了，快照必须在 delete 之前取
    record_audit(db, user.phone, AUDIT_SOURCE_DELETE, key, {"snapshot": _out(row)})
    db.delete(row)
    db.commit()
    return ok({"key": key}, message="已删除源")


@router.post("/sources/reset", response_model=None)
def reset_sources(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    # C10：重置会丢弃全部自定义源（恢复出厂），同上属不可逆的全局变更。
    step: User = Depends(require_step_up),
):
    """重置为内置源：删除库中全部源并重新播种。

    用于把运营改坏的配置恢复出厂。会丢弃自定义源，故单独一个端点而非复用删除。
    """
    db.query(RssSource).delete()
    db.commit()
    seeded = ensure_seeded(db)
    record_audit(db, user.phone, AUDIT_SOURCE_RESET, "", {"seeded": seeded})
    db.commit()
    logger.info("管理员 %s 重置 RSS 源为内置 %d 个", user.phone, seeded)
    return ok({"seeded": seeded}, message=f"已重置为 {seeded} 个内置源")


