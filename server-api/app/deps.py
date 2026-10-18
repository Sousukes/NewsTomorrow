"""鉴权依赖：解析 Authorization: Bearer <token>。"""
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .models import User
from .security import decode_token_full


def _bearer_token(authorization: Optional[str]) -> str:
    """从 `Authorization` 头取出 Bearer 令牌；格式不符返回空串。"""
    if not authorization:
        return ""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1]


def _user_from_token(authorization: Optional[str], db: Session) -> Optional[User]:
    token = _bearer_token(authorization)
    if not token:
        return None
    decoded = decode_token_full(token)
    if decoded is None:
        return None
    user_id, token_version = decoded
    user = db.get(User, user_id)
    # 吊销校验：token 携带的 ver 必须与库中一致（/logout 自增版本即失效全部旧 token）
    if user is None or (user.token_version or 0) != token_version:
        return None
    return user


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """未登录返回 None（用于公开接口按当前用户计算 liked）。"""
    return _user_from_token(authorization, db)


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    user = _user_from_token(authorization, db)
    if user is None:
        raise HTTPException(
            status_code=401, detail={"code": 40100, "message": "未登录或登录已过期"}
        )
    return user


def require_password_current(user: User = Depends(get_current_user)) -> User:
    """C4：受限账号（管理员刚代其重置过密码）不能做「以本人身份对外」的动作。

    拦截范围刻意**只覆盖写操作**：受限账号仍可浏览、搜索、看消息、改自己的
    资料——把它整个锁死除了让人焦急，没有任何安全收益；真正要防的是
    「拿着临时密码发评论/收藏/进管理端」，因为那等于让临时密码长期有效，
    「强制改密」就名存实亡。

    刻意用 403 / 40305：会话是有效的（401 会让前端清登录态，把用户踢回登录页，
    而他此刻需要的恰恰是留在原地改密）。
    """
    if user.must_change_password:
        raise HTTPException(
            status_code=403, detail={"code": 40305, "message": "请先修改初始密码"}
        )
    return user


def require_ops_access(
    authorization: Optional[str] = Header(None),
    x_ops_token: Optional[str] = Header(None),
) -> None:
    """运维接口（`/api/health`、`/api/metrics`）的访问控制（C5）。

    这两个接口会暴露**连接池状态**与 **Top20 路径分布**，不应公开可读。
    规则：

    - 配置了 `OPS_TOKEN`：必须携带 `Authorization: Bearer <token>` 或
      `X-Ops-Token: <token>`（Prometheus 用后者更方便，不必伪造 Bearer 格式）；
    - 未配置：**开发环境放行**（本地 / CI / 契约检查零改动），
      **生产环境拒绝**——fail-closed，避免忘记配置就把内部状态挂在公网。

    刻意用 **403 / 40303** 而非 401：本项目里 401 代表「会话失效」，前端据此清登录态
    并跳登录页；运维接口与用户会话无关，返回 401 会误导客户端。

    注意这里读 `config.OPS_TOKEN` / `config.IS_PRODUCTION` 的**模块属性**而非
    `from .config import ...`：测试需要 monkeypatch 这两个值来覆盖分支，
    值一旦被 import 到本模块就固定了（与 `admin.is_admin` 读 `ADMIN_PHONES` 同理）。
    """
    token = config.OPS_TOKEN
    if not token:
        if config.IS_PRODUCTION:
            raise HTTPException(
                status_code=403,
                detail={"code": 40303, "message": "运维接口未开放（生产环境未配置 OPS_TOKEN）"},
            )
        return

    provided = (x_ops_token or "").strip() or _bearer_token(authorization)
    # 定时安全比较：避免按字符逐位比较泄露令牌前缀
    if not provided or not secrets.compare_digest(provided, token):
        raise HTTPException(
            status_code=403, detail={"code": 40303, "message": "无权限访问运维接口"}
        )