"""管理端 · 运营审计日志（P1-15）：谁在什么时候改了什么。**只读**。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ...database import get_db
from ...models import AdminAuditLog
from ...response import ok
# 本模块的端点都不需要 actor（审计日志是只读的），故不注入 require_admin

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

def _audit_out(row: AdminAuditLog) -> dict:
    """detail 存的是 JSON 串；解析失败降级为空对象（宁可少显示，也不要整页报错）。"""
    try:
        detail = json.loads(row.detail) if row.detail else {}
        if not isinstance(detail, dict):
            detail = {}
    except (ValueError, TypeError):
        detail = {}
    return {
        "id": row.id,
        "actorPhone": row.actor_phone,
        "action": row.action,
        "target": row.target or "",
        "detail": detail,
        "createdAt": row.created_at,
    }


@router.get("/audit", response_model=None)
def list_audit(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="上一页末条的 id"),
    target: str | None = Query(None, description="按对象筛选（源 key / 手机号）"),
    db: Session = Depends(get_db),
):
    """审计时间线（新的在前，游标分页）。

    游标用**自增 id** 而非时间戳：created_at 是秒级精度，同秒多条会让 keyset 比较
    出现重复/遗漏（评论分页踩过同一个坑）。
    """
    q = db.query(AdminAuditLog)
    if target:
        q = q.filter(AdminAuditLog.target == target)
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=400, detail={"code": 40003, "message": "分页游标无效"})
        q = q.filter(AdminAuditLog.id < int(cursor))
    rows = q.order_by(AdminAuditLog.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        {
            "list": [_audit_out(r) for r in items],
            "hasMore": has_more,
            "nextCursor": str(items[-1].id) if has_more and items else None,
        }
    )
