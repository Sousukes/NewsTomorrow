"""管理端 · 举报复核（E5）：列表 + 处置（确认隐藏 / 驳回恢复）。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加，本模块不重复声明。

**列表带「被举报对象」的预览**（P1-77 补）：复核页要回答"这条该不该藏"，
只给 `targetId` 等于让运营自己去别处对照——尤其是评论，光看 id 完全没法判断。
预览用**批量查询**取（两类对象各一条 IN），不是逐行查（那是 N+1）。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from ...database import get_db
from ...models import Comment, News, Report, User, now_str
from ...response import ok
from ...schemas import ReportResolveIn
from . import require_admin
from ._shared import AUDIT_REPORT_RESOLVE, record_audit

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

# 预览片段长度：复核只需要"看得懂在说什么"，不需要全文（评论上限 500 字，
# 整段塞进列表响应会让一页 20 条变成几十 KB 的无效带宽）
PREVIEW_LEN = 60


def _target_previews(db: Session, rows: list[Report]) -> dict[tuple[str, str], dict]:
    """批量取被举报对象的预览：`(target_type, target_id) → {title, snippet, newsId}`。

    两类对象各一条 `IN` 查询（共 2 条），不做逐行查询。
    找不到的（已被删除）不出现在结果里——调用方据此置 `targetMissing`。
    """
    comment_ids = [r.target_id for r in rows if r.target_type == "comment"]
    news_ids = [r.target_id for r in rows if r.target_type == "news"]
    out: dict[tuple[str, str], dict] = {}

    if news_ids:
        for nid, title in db.execute(
            select(News.id, News.title).where(News.id.in_(news_ids))
        ).all():
            # 资讯对象自己就是跳转目标：`targetNewsId` 填自身 id
            out[("news", nid)] = {"title": title, "snippet": None, "newsId": nid}

    if comment_ids:
        for cid, news_id, content in db.execute(
            select(Comment.id, Comment.news_id, Comment.content).where(
                Comment.id.in_(comment_ids)
            )
        ).all():
            body = (content or "").strip()
            out[("comment", cid)] = {
                "title": None,
                "snippet": body[:PREVIEW_LEN] + ("…" if len(body) > PREVIEW_LEN else ""),
                # 评论要能跳回它所属的资讯——否则运营拿着 comment id 无处可去
                "newsId": news_id,
            }
    return out


def _report_out(r: Report, preview: dict | None) -> dict:
    return {
        "id": r.id,
        "targetType": r.target_type,
        "targetId": r.target_id,
        "reason": r.reason,
        "status": r.status,
        "resolution": r.resolution,
        "createdAt": r.created_at,
        # ---- P1-77：被举报对象的预览（缺失 = 对象已被删除）----
        "targetTitle": (preview or {}).get("title"),
        "targetSnippet": (preview or {}).get("snippet"),
        "targetNewsId": (preview or {}).get("newsId"),
        "targetMissing": not preview,
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
    previews = _target_previews(db, rows)
    return ok(
        {
            "list": [_report_out(r, previews.get((r.target_type, r.target_id))) for r in rows],
            "nextCursor": rows[-1].id if has_more else None,
        }
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
    # 预览在 commit 之后取：处置只改 hidden 状态，内容与所属资讯都不变，
    # 这会儿取到的与列表页看到的一致（且不影响审计写入的时序）
    preview = _target_previews(db, [report]).get((report.target_type, report.target_id))
    return ok(_report_out(report, preview))
