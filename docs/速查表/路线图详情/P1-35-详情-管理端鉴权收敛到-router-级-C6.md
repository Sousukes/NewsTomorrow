#### P1-35 详情：管理端鉴权收敛到 router 级（C6）
**问题**：34 个管理端点各自在函数体首行调用 `_require_admin(user, db)`。当前**全部写了**，
但这是「靠人记住」的约束——新增端点忘写一行即成为越权面，且 34 个端点的 review 很难逐条核对。

**方案**

| 项 | 变化 |
| --- | --- |
| 依赖 | 新增 `require_admin(user=Depends(get_current_user), db=Depends(get_db)) -> User`：未登录 401、非管理员 403/40301，**返回管理员 `User`** |
| 挂载 | `APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])`——对路由表里**每个**端点生效 |
| 21 个写端点 | `user: User = Depends(get_current_user)` → `Depends(require_admin)`（取 actor 写审计/日志；同请求内依赖结果被缓存，不重复查库） |
| 13 个只读列表 | 直接去掉 `user` 参数（不留无用参数） |
| 守卫调用 | 34 处 `_require_admin(user, db)` 全部删除 |
| `is_admin` | **仍留在 `admin.py`**：`test_sources.py` 的 fixture 用 `monkeypatch.setattr(admin_mod, "ADMIN_PHONES", ...)`，依赖它在运行期解析该模块全局名 |

**结构性守卫 `tests/test_admin_guard.py`（3 项）**

用 `app.openapi()["paths"]` 枚举全部 `/api/admin` 操作（26 路径 × 34 操作），逐个断言：

1. 未登录 → **401**（不能被 404/422/500 抢先）；
2. 已登录但非管理员 → **403 + 40301**；
3. **结构断言**：router 上有 `dependencies`，且源码中不再存在逐端点守卫调用
   （防「重构回退」——有人图省事写回函数体，下一个人新增端点时仍会漏）。

> 取清单必须用 `app.openapi()["paths"]`：`app.routes` 里的 `include_router` 是
> `_IncludedRouter` 包装（`path=None`），直接遍历只能拿到少量直连路由。

**测试**：后端 632 → **635 项**；管理端相关 7 个测试文件（298 项）单独复跑通过。
**无契约 / 迁移变更**（状态码与错误码完全不变）。

