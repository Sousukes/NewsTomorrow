## 批次 P1-10（2026-10-13）：RSS 源管理前端 + isAdmin 下发（P1-9 的直接延续）
- **问题**：P1-9 把源做成了可配置，但运营调整源只能用 curl / API——**无可视化界面**；
  且前端无从知道当前账号是否管理员，管理入口无从谈起。
- **`isAdmin` 下发（后端）**：
  - `admin.is_admin(phone)` 抽成**单一判定**（白名单为空 = 整体关闭），
    `/api/news/refresh`、`/api/admin/*` 与**登录 / 注册 / 续期响应里的 `user.isAdmin`** 共用——
    原先 `main.py` 自己复制了一份 `if not ADMIN_PHONES or ...`，两处逻辑迟早漂移；
  - 响应**只下发布尔值，不下发白名单本身**；前端据此决定入口显隐，省掉一次探测请求。
- **频道白名单校验**：`schemas.SourceIn/UpdateIn` 校验 `channel ∈ CHANNEL_KEYS`
  （与前端 7 个频道对齐，并补上了死常量缺失的 `video`）。
  拼错的频道（如 `tecn`）会让源匹配不到任何频道，而"频道无专属源时**回退全部**"的兜底
  会把它静默投递到**每一个**频道——校验是兜底逻辑的必要配套。
- **前端**：
  - `api/admin.ts` 五件套 + `ADMIN_SOURCES_SUPPORTED` 门；**mock 模式故意不短路假数据**——
    伪造一份可变的源列表会误导排障，入口由 `isAdmin`（mock 下恒 false）隐藏即可；
  - `AdminSources.vue`（`/admin/sources`）：汇总计数、新增 / 编辑表单与前端校验、
    启停**乐观更新 + 失败回滚**、删除 / 重置二次确认、抓取健康状态渲染；
  - `Profile.vue` 按 `user.isAdmin` 显隐入口；`stores/user.ts` 新增 `isAdmin`
    （`prefs.isAdmin && isLoggedIn`，落盘保刷新，登出 / 401 归位）。
- **顺手修掉的两个潜在缺陷**：
  - **Pydantic v2 的 422 变 500**：自定义 `field_validator` 抛 `ValueError` 时，
    异常对象会进 `errors()[i].ctx.error`，原样交给 `JSONResponse` 报 TypeError——
    一个本该返回 422 的请求就此变成 500（新增 `_jsonable_errors()`）；
  - **`CHANNEL_KEYS` 是死常量且已漂移**（无任何引用、比前端少 `video`），现启用并对齐。
- **测试顺序无关性**：新增**根级 `conftest.py`** 统一钉死测试环境（内存库 / 关限流 /
  关定时任务 / 管理员白名单）。`app.config` 在首次 import 时读取环境变量，
  此前由各测试文件自行设置 → **全套跑与单文件跑结果取决于 collection 顺序**
  （曾出现整类管理用例时绿时红）。用赋值而非 `setdefault`，开发者本机 `.env` 不会渗进测试。
- **测试**：后端 225 → **233 项**（`test_sources.py` 28 → 36：`TestAdminFlagInAuthPayload`
  5 项 + 入参校验 4 项；文档计数顺带纠偏 `test_hardening` 9 → 14、`test_api` 55 → 70）。
  前端 150 → **180 项**（`api/admin` 14 + `AdminSources` 10 + `stores/user` isAdmin 6）。
  契约检查 **37 项**（28 + 管理端 9），**真实后端（PG + uvicorn）与 mock 后端各跑一遍，
  均 37/37 通过**（真实后端这次带凭证跑完全程，补上了 P1-9 未能覆盖的授权段）。
- **踩坑（已写进测试注释 / 记忆，避免复发）**：
  - **jsdom 不实现表单提交**：点击 `type="submit"` 按钮不会触发 `<form>` 的 `submit`
    事件，测试必须直接 `trigger('submit')`；
  - 前端两套 mock 形态（`USE_MOCK` 无后端 / `dev:api` 走 3001 端口）行为不同，
    写测试前先认清当前处于哪一层；
  - 追加测试块时用 heredoc 把 `user.spec.ts` 头部写坏（块落到文件头、缺导入），
    靠 git index 里的旧版本才完整恢复——**追加内容前先 `git show :<path>` 确认基线**。
- **迁移**：无（本批无模型变更）。PG 库仍位于 `0013_rss_sources`，`alembic check` 无漂移。
- **文档**：server-api README（`isAdmin` 字段说明、`is_admin` 单一判定、频道校验、
  `conftest.py`、测试计数 233、契约检查 37 项）；前端 README（新页面 / 路由 / 测试 180 项 /
  `DEMO_PASSWORD` 用法）。

