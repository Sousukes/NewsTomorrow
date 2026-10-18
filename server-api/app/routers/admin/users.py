"""管理端 · 管理员白名单（P1-14）与代重置密码（C4）：含末位管理员保护。

鉴权由 `admin/__init__.py` 的 router 级依赖统一施加（见那里的说明），
本模块不重复声明。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ...database import get_db
from ...models import AdminUser, User, now_str
from ...response import ok
from ...schemas import AdminIn
from ...security import generate_temp_password, hash_password
from ...step_up import require_step_up
from . import env_admin_phones, require_admin
from ._shared import record_audit, AUDIT_ADMIN_ADD, AUDIT_ADMIN_REMOVE, AUDIT_PASSWORD_RESET

logger = logging.getLogger("toutiao.admin")

router = APIRouter()

router = APIRouter()

router = APIRouter()

def _admin_out(row: AdminUser, db: Session) -> dict:
    """白名单行 → 输出。`registered` 区分「已注册账号 / 预授权」供界面展示。"""
    # 按 phone 查而非 db.get(User, phone)：User 主键是自增 id，
    # 拿手机号当主键查会永远查不到（registered 恒为 False）。
    account = db.query(User).filter(User.phone == row.phone).first()
    return {
        "phone": row.phone,
        "note": row.note or "",
        "addedBy": row.added_by or "",
        "createdAt": row.created_at,
        "registered": account is not None,
        "name": account.name if account else "",
    }


@router.get("/users", response_model=None)
def list_admin_users(db: Session = Depends(get_db)):
    """管理员白名单列表（DB 源）。

    附 `envAdminCount`（环境变量来源的管理员**数量**，不下发号码）：
    界面据此解释「为什么移除最后一位库管理员仍被允许」——env 是 break-glass。
    """
    rows = db.query(AdminUser).order_by(AdminUser.created_at, AdminUser.phone).all()
    return ok(
        {
            "list": [_admin_out(r, db) for r in rows],
            "total": len(rows),
            "envAdminCount": len(env_admin_phones()),
        }
    )


@router.post("/users", response_model=None)
def add_admin_user(
    body: AdminIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    # C10：授权管理员是最敏感的操作——一次误操作就是把管理后台交出去，
    # 且回收要靠另一个人（末位保护只兜「一个都不剩」）。必须二次校验。
    step: User = Depends(require_step_up),
):
    """添加管理员。

    已在**任一来源**（env 或库）中的手机号返回 40903——重复入库只会让
    界面出现一行"管不了其实也无需管"的冗余记录。
    """
    if db.get(AdminUser, body.phone) is not None or (
        body.phone in env_admin_phones()
    ):
        raise HTTPException(status_code=409, detail={"code": 40903, "message": "该手机号已是管理员"})
    row = AdminUser(
        phone=body.phone,
        note=body.note.strip(),
        added_by=user.phone,
        created_at=now_str(),
    )
    db.add(row)
    record_audit(db, user.phone, AUDIT_ADMIN_ADD, body.phone, {"note": row.note or ""})
    db.commit()
    db.refresh(row)
    logger.info("管理员 %s 添加管理员 %s（备注：%s）", user.phone, body.phone, row.note or "无")
    return ok(_admin_out(row, db), message="已添加管理员")


@router.delete("/users/{phone}", response_model=None)
def remove_admin_user(
    phone: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    # C10：回收管理员同样敏感（把人踢出后台），且与授权一样影响面在「人」而不在数据。
    step: User = Depends(require_step_up),
):
    """移除管理员。

    **末位保护**：移除后若任何来源都不再有任何管理员，拒绝（40904）。
    注意 env 管理员不受库操作影响——移除一位"同时也在 env 里"的管理员时，
    env 仍在授权，故只有 env 为空且这是库中最后一条时才拦截。
    """
    row = db.get(AdminUser, phone)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40403, "message": "管理员不存在"})
    if db.query(AdminUser).count() - 1 <= 0 and not env_admin_phones():
        raise HTTPException(
            status_code=409, detail={"code": 40904, "message": "已是最后一位管理员，不能移除"}
        )
    record_audit(db, user.phone, AUDIT_ADMIN_REMOVE, phone, {"note": row.note or ""})
    db.delete(row)
    db.commit()
    logger.info("管理员 %s 移除管理员 %s", user.phone, phone)
    return ok({"phone": phone}, message="已移除管理员")


@router.post("/users/{phone}/reset-password", response_model=None)
def reset_user_password(
    phone: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    # C10 + C4：代重置等价于「替别人换锁」，是本项目里权限最高的一类动作——
    # 攻击者拿到管理员会话就能锁死任意账号。
    step: User = Depends(require_step_up),
):
    """管理员代重置密码（C4）：解决「用户忘记密码只能无解」。

    为什么不给用户自助找回：本项目**没有可送达的凭证通道**（无短信、无邮箱，
    C3 说的正是这件事）。在通道补上之前，「管理员线下核实身份 + 代发一次性
    临时密码」是唯一诚实的解法——它把「验证身份」放在人身上，而不是假装
    系统能确认你是谁。

    三处刻意：
    - **临时密码由服务端生成**：管理员不该替用户挑密码（容易挑出弱口令，
      且会成为跨站点复用的源头）。字符集剔除易混字符，因为它靠口头/手写传达；
    - **置 `must_change_password` 并自增 `token_version`**：临时密码若能长期
      使用，「强制改密」就名存实亡；顺带吊销该用户所有已签发令牌
      （密码泄露场景的关键一步，否则旧的 access 还能用 30 分钟）；
    - **不能重置自己**：那是一条把自己踢下线的路，自助改密才是正解。

    临时密码**只在此响应里出现一次**，不写日志、不入审计 detail。
    """
    if phone == user.phone:
        raise HTTPException(
            status_code=400,
            detail={"code": 40007, "message": "不能重置自己的密码，请使用「修改密码」"},
        )
    target = db.query(User).filter(User.phone == phone).first()
    if target is None:
        # 与公开接口同码：不能靠错误码区分「手机号是否已注册」（账号枚举）
        raise HTTPException(status_code=404, detail={"code": 40400, "message": "用户不存在"})
    temp_password = generate_temp_password()
    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    target.token_version = (target.token_version or 0) + 1
    # detail 只留「是否已改过」这类事实，绝不含明文
    record_audit(db, user.phone, AUDIT_PASSWORD_RESET, phone, {"forced": True})
    db.commit()
    logger.info("管理员 %s 重置了 %s 的密码（受限态已置位）", user.phone, phone)
    return ok(
        {"phone": phone, "tempPassword": temp_password, "mustChangePassword": True},
        message="临时密码仅显示一次，请立即转交本人并提醒其登录后修改",
    )


