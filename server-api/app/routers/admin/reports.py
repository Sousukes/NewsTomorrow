"""管理端 · 举报复核（E5）：列表 + 处置（确认隐藏 / 驳回恢复）。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加，本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ...database import get_db
from ...models import Comment, News, Report, User, now_str
from ...response import ok
from ...schemas import ReportResolveIn
from . import require_admin
from ._shared import AUDIT_REPORT_RESOLVE, record_audit

logger = logging.getLogger("toutiao.admin")

router = APIRouter()


def _report_out(r: Report) -> dict:
    return {
        "id": r.id,
        "targetType": r.target_type,
        "targetId": r.target_id,
        "reason": r.reason,
        "status": r.status,
        "resolution": r.resolution,
        "createdAt": r.created_at,
    }


@router.get("/reports", response_model=None)
def list_reports(
    status: str = Query("pending", pattern="^(all|pending|resolved|dismissed)$"),
    targetType: str | None = Query(None, pattern="^(comment|news)$"),
    limit: int = Query(20, ge=1, le=100),
    cursor: int | None = Query(None, description="上一页末条的 id"),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_admin),
):
    """举报复核队列。

    默认只返回 `pending`（待处理优先）；`status=all` 看全量（含已处置/驳回）。
    keyset 游标按 id 倒序（举报量级小，id 即时间序）。
    """
    q = db.query(Report)
    if status != "all":
        q = q.filter(Report.status == status)
    if targetType:
        q = q.filter(Report.target_type == targetType)
    if cursor:
        q = q.filter(Report.id < cursor)
    rows = q.order_by(Report.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return ok(
        {"list": [_report_out(r) for r in rows], "nextCursor": rows[-1].id if has_more else None}
    )


@router.post("/reports/{report_id}/resolve", response_model=None)
def resolve_report(
    report_id: int,
    body: ReportResolveIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """处置一条举报：`hidden` 确认隐藏 / `kept` 驳回并恢复可见。"""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"code": 40408, "message": "举报不存在"})

    if body.resolution == "hidden":
        # 确认隐藏：确保目标处于隐藏态（可能已被自动处置过）
        if report.target_type == "comment":
            obj = db.get(Comment, report.target_id)
        else:
            obj = db.get(News, report.target_id)
        if obj is not None:
            obj.hidden = True
            obj.hidden_reason = obj.hidden_reason or "用户举报（人工确认）"
        report.status = "resolved"
        report.resolution = "hidden"
    else:  # kept：驳回，恢复可见
        if report.target_type == "comment":
            obj = db.get(Comment, report.target_id)
        else:
            obj = db.get(News, report.target_id)
        if obj is not None:
            obj.hidden = False
            obj.hidden_reason = None
        report.status = "dismissed"
        report.resolution = "kept"

    report.resolved_at = now_str()
    report.resolved_by = actor.phone
    record_audit(
        db,
        actor.phone,
        AUDIT_REPORT_RESOLVE,
        target=f"{report.target_type}:{report.target_id}",
        detail={"resolution": body.resolution},
    )
    db.commit()
    return ok(_report_out(report))
