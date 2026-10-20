"""用户举报（E5）的业务逻辑：幂等落库 + 达阈值自动软隐藏/下架。

设计见 `docs/后端/E5用户举报设计.md`。本模块是**纯函数式**（接收 session），
路由层只做参数绑定与错误翻译，方便单测。
"""
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import REPORT_AUTO_HIDE_THRESHOLD, REPORT_REASONS
from .models import Comment, News, Report, now_str

logger = logging.getLogger("toutiao.reports")

TARGET_COMMENT = "comment"
TARGET_NEWS = "news"
TARGET_TYPES = (TARGET_COMMENT, TARGET_NEWS)

# 自动处置时写入的隐藏理由（与 P1-20/P1-22 的 soft-hide 同口径）
AUTO_HIDE_REASON = "用户举报"


class ReportError(Exception):
    """举报流程的业务错误：`code` 是 HTTP 错误码，`status` 是 HTTP 状态码。"""

    def __init__(self, status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _target_exists(db: Session, target_type: str, target_id: str) -> bool:
    """目标是否存在（不区分是否已隐藏——已隐藏的目标仍应能举报）。"""
    if target_type == TARGET_COMMENT:
        return db.query(Comment.id).filter(Comment.id == target_id).first() is not None
    if target_type == TARGET_NEWS:
        return db.query(News.id).filter(News.id == target_id).first() is not None
    return False


def _pending_count(db: Session, target_type: str, target_id: str) -> int:
    """去重后（不同 reporter）的待处理举报数。"""
    return (
        db.query(func.count(Report.id))
        .filter(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == "pending",
        )
        .scalar()
        or 0
    )


def _auto_hide(db: Session, target_type: str, target_id: str) -> None:
    """软隐藏/下架目标（公开查询已统一过滤 hidden，前端无感知地消失）。"""
    if target_type == TARGET_COMMENT:
        obj = db.query(Comment).filter(Comment.id == target_id).first()
        if obj is not None:
            obj.hidden = True
            obj.hidden_reason = AUTO_HIDE_REASON
    elif target_type == TARGET_NEWS:
        obj = db.query(News).filter(News.id == target_id).first()
        if obj is not None:
            obj.hidden = True
            obj.hidden_reason = AUTO_HIDE_REASON


def create_report(
    db: Session,
    reporter_id: int,
    target_type: str,
    target_id: str,
    reason: str,
) -> dict:
    """落一条举报（幂等），达阈值时自动软隐藏。返回 `{ status, autoHidden }`。

    幂等语义：同一用户对同一目标重复举报 → **更新 reason、不新增行**。
    这是自动处置防刷的地基（单用户最多贡献 1 次计数）。
    """
    if target_type not in TARGET_TYPES:
        raise ReportError(422, 42206, "举报对象类型不合法")
    if reason not in REPORT_REASONS:
        raise ReportError(422, 42206, "举报理由不合法")
    if not _target_exists(db, target_type, target_id):
        raise ReportError(404, 40409, "举报对象不存在或已删除")

    existing = (
        db.query(Report)
        .filter(
            Report.reporter_id == reporter_id,
            Report.target_type == target_type,
            Report.target_id == target_id,
        )
        .first()
    )
    if existing is not None:
        existing.reason = reason
        # 已被人工处置过的举报，用户再次举报视为重新发起
        existing.status = "pending"
        existing.resolution = None
        db.commit()
        return {"status": "pending", "autoHidden": False}

    db.add(
        Report(
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            status="pending",
        )
    )
    # 阈值判定与处置**并入同一次提交**：分成两次 commit 会留下
    # 「举报写进去了、处置没生效」的窗口，而阈值检查只在提交新举报时触发，
    # 该窗口没有自愈路径（要等第 N+1 个人举报才会再判）。
    db.flush()
    auto_hidden = False
    if _pending_count(db, target_type, target_id) >= REPORT_AUTO_HIDE_THRESHOLD:
        _auto_hide(db, target_type, target_id)
        auto_hidden = True
    db.commit()

    if auto_hidden:
        logger.info(
            "举报达阈值自动处置：%s:%s（%d 人举报）",
            target_type,
            target_id,
            REPORT_AUTO_HIDE_THRESHOLD,
        )

    return {"status": "pending", "autoHidden": auto_hidden}
