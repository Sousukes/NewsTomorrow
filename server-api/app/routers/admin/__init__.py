"""管理端路由包（D2：原先是 1695 行的单文件）。

按业务域拆成 7 个子模块（`sources / news / comments / words / nickname /
users / audit`），本文件只保留**跨模块共享**的部分：管理能力判定、鉴权依赖、
以及聚合路由。

拆分的三个硬约束（都是既有约定，不是风格偏好）：

1. **判定函数必须留在这里**：`is_admin` 在调用时解析模块全局 `ADMIN_PHONES`，
   而多个测试用 `monkeypatch.setattr(admin_mod, "ADMIN_PHONES", ...)` 打桩
   （break-glass 应急通道要能单独测）。搬到子模块就等于把这条测试废掉，
   而失效是静默的——`is_admin` 会去读另一个模块的全局。
2. **鉴权挂在聚合 router 上**：`dependencies=[Depends(require_admin)]` 对
   `include_router` 进来的所有路由生效，因此新增子模块/端点自动受保护，
   不存在"新加的模块忘了加依赖"这种漏法。
3. **子模块 `from . import require_admin`** 依赖本文件**先定义后导入**的顺序
   （见文件底部）。改成顶部导入就成了循环依赖。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...config import ADMIN_PHONES
from ...database import get_db
from ...deps import get_current_user
from ...models import AdminUser, User

logger = logging.getLogger("toutiao.admin")


def is_admin(phone: str | None, db: Session | None = None) -> bool:
    """是否具备管理能力。

    **双源并集**（P1-14）：
    - `ADMIN_PHONES` 环境变量保留为 break-glass——改配置重启即生效，不依赖库；
    - `admin_users` 表供日常运营——应用内增删，无需重启服务；
    - 两边都为空 = 整体关闭：配置疏漏时不应暴露管理接口（生产安全）。

    抽成独立函数而非在调用处各写一遍：登录响应也要复用同一判定
    （前端据此决定是否展示管理入口），两处实现一旦漂移，就会出现
    "入口看得见、一点就 403"或反之的怪象。

    **注意**：这里引用的是**本模块的全局** `ADMIN_PHONES`（而不是 import 时
    取到的快照），测试才能用 `monkeypatch.setattr(admin_mod, "ADMIN_PHONES", …)`
    单独验证应急通道。把它改小写模块/常量化会让那条测试静默失效。
    """
    if not phone:
        return False
    if ADMIN_PHONES and phone in ADMIN_PHONES:
        return True
    return db is not None and db.get(AdminUser, phone) is not None


def env_admin_phones() -> set[str]:
    """env 应急管理员名单（break-glass 通道）。

    **运行期读包全局**，不把 config 的值在 import 时取快照——因为
    `is_admin` 判定的是包全局 `ADMIN_PHONES`，而白名单端点要显示
    "env 里有几位管理员"。两边若各拿一份（一个快照、一个全局），
    就会出现「`is_admin` 说 A 是管理员、白名单接口说不认识 A」的漂移；
    单元测试对 break-glass 通道的打桩也只对其中一个生效。
    """
    return set(ADMIN_PHONES or ())


def require_admin(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    """管理端鉴权**依赖**：未登录 401，已登录但非管理员 403。

    挂在本模块 `APIRouter(dependencies=[...])` 上，**所有**管理端点自动受保护。
    此前是每个端点函数体里各写一次 `_require_admin(user, db)`（34 个端点 35 次调用）：
    新增端点忘写一次就是一个越权面，且 code review 很难逐条核对。
    改为 router 级后，「漏写」在结构上不可能发生——端点不需要为此做任何事。

    返回 `User` 供需要 actor 的端点（审计 `record_audit`、日志）直接注入：
    `user: User = Depends(require_admin)`。同一请求内 FastAPI 会缓存依赖结果，
    因此与 router 级的那次调用共用同一个实例，不会重复查库。

    C4：这里同时拦下「受限账号」（管理员刚被代重置过密码、尚未改密）。
    放在**这一个**函数里而不是 34 个端点各写一次——受保护面与判定逻辑同处，
    新增端点自动继承，不会漏。
    """
    if not is_admin(user.phone, db):
        raise HTTPException(status_code=403, detail={"code": 40301, "message": "无权限执行该操作"})
    if user.must_change_password:
        # 拿着临时密码管后台 = 临时密码长期有效，「强制改密」名存实亡
        raise HTTPException(
            status_code=403, detail={"code": 40305, "message": "请先修改初始密码"}
        )
    return user


# 鉴权在 **router 级**统一施加：`dependencies` 对路由表里的每个端点都生效，
# 包括未来新增、以及忘记声明 `user` 参数的端点。
router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


# ---- 业务域子模块 ----
# **必须放在上面所有定义之后**：子模块 `from . import require_admin`，
# 提前导入会变成循环依赖（那时 require_admin 还没存在）。
from . import audit as audit_mod  # noqa: E402
from . import comments as comments_mod  # noqa: E402
from . import news as news_mod  # noqa: E402
from . import nickname as nickname_mod  # noqa: E402
from . import sources as sources_mod  # noqa: E402
from . import users as users_mod  # noqa: E402
from . import words as words_mod  # noqa: E402
# 只再导出 record_audit：审计动作常量由各子模块直接从 `._shared` 取，
# 在这里再导出一遍等于多一个可以漂移的来源。
from ._shared import record_audit  # noqa: E402,F401


for _mod in (
    sources_mod,
    news_mod,
    comments_mod,
    words_mod,
    nickname_mod,
    users_mod,
    audit_mod,
):
    router.include_router(_mod.router)

__all__ = ["router", "is_admin", "require_admin", "ADMIN_PHONES", "record_audit"]