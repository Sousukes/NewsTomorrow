"""高敏操作二次校验（C10）。

**问题**：会话令牌被偷（XSS / 共享设备 / 日志泄露）后，攻击者拿到的不是
「只读权限」，而是**与本人同等的能力**——包括把管理后台交出去
（`POST /api/admin/users`）、把 RSS 源删掉、把别人的昵称改掉。
这些操作的共同点是**不可逆或影响全局**，理应要求「再证明一次你是本人」。

**方案**：`POST /api/auth/step-up`（需已登录 + 密码）换取一张**短命**的
`step-up` 令牌（默认 5 分钟），高敏端点以 `X-Step-Up-Token` 头校验它。

与既有机制的关系（刻意不新造）：
- **复用 `login_guard` 的账号维度锁定**（scope=`stepup`）：否则这个端点本身
  就是一个没有防爆破的密码 oracle——猜对一次就能执行删源。独立 scope 让
  「二次校验失败」不会把用户的登录也锁掉，但阈值/窗口与登录共用一套配置。
- **复用 JWT 的类型校验**（`typ=stepup`）：与 C1 的 access/refresh 同源，
  一张 access 令牌不能被当成 step-up 用，反之亦然。
- **复用 `token_version` 吊销**：登出即自增版本号，两类令牌同时失效。

**为什么用 403 + 40304 而不是 401**：401 在本项目代表「会话失效」，
前端会清登录态并跳登录页；而这里的用户**登录态是好的**，只是缺一张
二次校验令牌。用 401 会把用户莫名登出（与 C5 运维接口同理）。
"""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .models import User
from .security import TOKEN_TYPE_STEP_UP, create_token, decode_token_full

#: 缺失 / 无效 / 类型不符 / 已被吊销时统一的口径（不区分细节，避免变成探针）
STEP_UP_REQUIRED = {"code": 40304, "message": "此操作需要二次校验，请先验证身份"}


def issue_step_up_token(user: User) -> dict:
    """签发 step-up 令牌（由 `/auth/step-up` 调用）。"""
    token = create_token(
        user.id,
        user.token_version or 0,
        token_type=TOKEN_TYPE_STEP_UP,
        expires_minutes=config.STEP_UP_TOKEN_MINUTES,
    )
    return {"stepUpToken": token, "expiresIn": config.STEP_UP_TOKEN_MINUTES * 60}


def user_from_step_up_token(token: str, db: Session) -> User | None:
    """用 step-up 令牌定位用户；任何不符一律 None（由调用方统一报 40304）。"""
    decoded = decode_token_full(token, expect_type=TOKEN_TYPE_STEP_UP)
    if decoded is None:
        return None
    user_id, token_version = decoded
    user = db.get(User, user_id)
    # 吊销校验与 access 完全一致：/logout 自增 token_version → 同时失效
    if user is None or (user.token_version or 0) != token_version:
        return None
    return user


def require_step_up(
    x_step_up_token: str | None = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """高敏端点依赖：校验 `X-Step-Up-Token`，返回当前用户。

    与 `require_admin` 同时声明时，**admin 声明在前**——未授权者拿到的是
    40301（权限不足）而不是 40304（要二次校验），不向外透露站内有哪类操作。
    """
    token = (x_step_up_token or "").strip()
    user = user_from_step_up_token(token, db) if token else None
    if user is None:
        raise HTTPException(status_code=403, detail=dict(STEP_UP_REQUIRED))
    return user
