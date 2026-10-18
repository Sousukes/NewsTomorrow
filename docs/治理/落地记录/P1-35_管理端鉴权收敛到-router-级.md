## 批次 P1-35（2026-10-17）：管理端鉴权收敛到 router 级（C6）
backlog 规则生效后的第三批：偿还最上方的 C6（P1 安全组首项）。

**问题**：`admin.py` 的 34 个端点各自在函数体首行调用 `_require_admin(user, db)`。
当前**全部写了**，但这是「靠人记住」的约束：

- 新增端点忘写一行 → 直接成为越权面，且 34 个端点的 code review 很难逐条核对；
- 守卫藏在函数体里，OpenAPI 上看不出哪些端点需要管理员，无法用工具自动核对。

**方案：router 级依赖 + 结构性测试**

- 新增 `require_admin`（FastAPI 依赖）：未登录 **401**、非管理员 **403/40301**，
  并**返回 `User`**，供需要 actor 的端点直接注入。
- `APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])`——
  `dependencies` 对路由表里**每个**端点生效，包括未来新增、以及忘记声明 `user` 参数的端点。
  「漏写」在结构上不再可能。
- 34 处 `_require_admin(user, db)` 全部删除。按是否需要 actor 分两类：
  - **21 个**写端点（要写审计/日志）→ `user: User = Depends(require_admin)`；
    同一请求内 FastAPI 会缓存依赖结果，与 router 级那次共用实例，**不重复查库**；
  - **13 个**只读列表端点不需要 actor → 直接去掉 `user` 参数（不留无用参数）。
- `is_admin` **仍留在 `admin.py`**：`tests/test_sources.py` 的 fixture 通过
  `monkeypatch.setattr(admin_mod, "ADMIN_PHONES", ...)` 控制白名单，依赖它在运行期
  解析该模块全局名。搬到别的模块会让整类管理端用例静默 40301。

**新增结构性守卫 `tests/test_admin_guard.py`（3 项）**

用 `app.openapi()["paths"]` 枚举**全部** `/api/admin` 操作（26 路径 × 34 操作），逐个断言：

1. 未登录 → **401**（且不能被 404/422/500 抢先）；
2. 已登录但非管理员 → **403 + 40301**；
3. **结构断言**：router 上存在 `dependencies`，且源码里不再有逐端点守卫调用
   （防「重构回退」——有人图省事把守卫写回函数体，下一个人新增端点时仍会漏）。

> 取清单必须用 `app.openapi()["paths"]`：`app.routes` 里的 `include_router` 是
> `_IncludedRouter` 包装（`path=None`），直接遍历只能拿到少量直连路由（此前踩过）。

**验证**：pytest 632 → **635 全绿**（新增 3 项）；管理端相关 7 个测试文件
（sources / moderation / news_moderation / nickname_moderation / comment_admin /
audit / health_history，共 **298 项**）单独复跑通过；`python -c "import main"` 通过。
**无契约 / 迁移变更**——鉴权状态码与错误码完全不变（401 / 403+40301）。

**文档**：backlog（**C6 移行，29 → 28**）、速查表（汇总表 P1-35 行 + 详情节）、
后端 README（新增「管理端鉴权（router 级，C6）」章节）、本记录。

