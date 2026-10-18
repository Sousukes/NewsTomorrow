"""用户举报（E5）：用户侧举报接口。

设计见 `docs/后端/E5用户举报设计.md`。核心逻辑在 `app/reports.py`，
本层只做参数绑定、鉴权与错误翻译。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..reports import ReportError, create_report
from ..response import ok

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportIn(BaseModel):
    targetType: str
    targetId: str
    reason: str


@router.post("", response_model=None)
def submit_report(
    body: ReportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交举报（幂等：同一用户对同一目标重复举报只更新理由）。

    达阈值时自动软隐藏/下架，`autoHidden` 告知前端本次是否触发了自动处置。
    """
    try:
        result = create_report(
            db,
            reporter_id=user.id,
            target_type=body.targetType,
            target_id=body.targetId,
            reason=body.reason,
        )
    except ReportError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        )
    return ok(result)
