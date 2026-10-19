"""管理端 · 运营大盘（E7-a）：单端点聚合内容量 / 待办 / 互动 / 源健康 / 审核命中。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加，本模块不重复声明。
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...feeds.health import window_days
from ...metrics import build_dashboard
from ...models import User
from ...response import ok
from . import require_admin

logger = logging.getLogger("toutiao.admin")

router = APIRouter()


@router.get("/dashboard", response_model=None)
def dashboard(
    days: int | None = Query(None, ge=1, le=365, description="回溯天数，默认取保留期"),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_admin),
):
    """运营数据大盘（E7）。

    **单端点而不是 5 个**：大盘是一次性渲染的面板，拆 5 个就是 5 次往返 +
    5 个 loading 态；而且单端点才能保证**同一时间口径**——否则页面会出现
    "总量按 7 天、趋势按 30 天"的割裂，而这种割裂在运营眼里就是"数字对不上"。

    `days` 由 `window_days` 收敛到保留期内：超出保留期的窗口只会得到一片空白，
    与其让前端渲染 90 个空格子，不如给一个与数据等宽的窗口（与单源趋势端点同处方）。

    聚合逻辑在 `app/metrics.py`（纯函数，便于直接单测）；本模块只做参数收敛与包装。
    """
    win = window_days(days)
    return ok(build_dashboard(db, win))
