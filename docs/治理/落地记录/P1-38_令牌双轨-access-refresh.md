## 批次 P1-38（2026-10-18）：令牌双轨 access / refresh（C1）
**问题**：全站只有一张 7 天令牌（`JWT_EXPIRE_MINUTES=10080`），且存 localStorage。
真正的风险不是「7 天」这个数字，而是**只有一张令牌、没有任何缩短手段**——
一旦泄露（XSS / 日志 / 共享设备），攻击窗口就是整整 7 天，且运维端无从下手。
审查报告 C1 的建议方向里「加 CSP」「高敏操作二次校验」是另外两件事，
本批不混入（拆开才能各自闭环），已作为 **C9 / C10** 上账 backlog。

**后端**
- `security.create_token` 增加 `token_type`（`access` / `refresh`）并在载荷里写 `typ`；
  `decode_token_full(expect_type=...)` **按类型校验**。这是双轨的安全前提：
  refresh 若能被业务接口接受，攻击者拿到它就能当永久 access 用，「短 access」形同虚设
- 有效期**按类型取默认**：access 30 分钟（`JWT_ACCESS_EXPIRE_MINUTES`）、
  refresh 7 天（`JWT_REFRESH_EXPIRE_MINUTES`，兼容旧变量 `JWT_EXPIRE_MINUTES`——
  它的历史语义就是「唯一那张长令牌」，老部署升级后 refresh 时长不变、access 自动收窄）
- `_token_payload` 同时下发 `token` / `refreshToken` / `expiresIn`（秒）。
  字段名与错误码对旧客户端**零破坏**：老客户端只认 `token`，两项新增刻意可选
- `/auth/refresh` 优先用 body 的 `refreshToken` 换发；缺省回退到 `Authorization`
  里的 access 滑动续期（兼容旧客户端 / 契约脚本 / 既有测试）。
  **refresh 无效时不回退**——否则「refresh 已失效」会被掩盖成「还能续」，
  用户能靠一张过期长令牌无限滑下去
- 登出自增 `token_version` → 两类令牌**同时**失效（吊销不分类型）

**前端**
- `http.ts` 三个键**同写同清**：`token` / `refreshToken` / 到期时刻。
  只清 access 会留下一张仍能换发令牌的长令牌，那是双轨要消除的东西
- 主动续期：access 进入到期前 60 秒窗口即先换发再请求，省掉一次 401 往返；
  并发请求**单飞**（只打一次 `/auth/refresh`）
- 401 兜底：先用 refresh 换发并**重试一次**；仍 401 才清登录态并广播。
  刻意限制「一次请求最多续一次」——否则「续期成功但服务端仍 401」会变成无限循环
- mock 模式跳过续期（没有真实令牌与续期接口）

**测试**：新增 `tests/test_auth_tokens.py` **17 项**（类型隔离 / 有效期 / 续期 /
吊销）+ 前端 `http.refresh.spec.ts` **7 项** + user store 1 项。
全量跑出 **1 个真 bug**：`create_token(type=refresh)` 不传 `expires_minutes` 时
退回 access 的 30 分钟——refresh 被静默签成短命的，会话 30 分钟即死，
从代码上看不出错（类型对、签名对，只是短）。已改为**按类型取默认**，
并补上「有效期等于对应配置」的断言（这类默认值陷阱只有断言能暴露）。

**验证**：pytest 656 → **673 全绿**（`--basetemp=../.tmp/pytest-tmp`，退出码 0）；
vitest 488 → **496**；`verify`（类型 + Lint + 格式）与 `build` 通过；
**契约双端 53/53**（mock 3001 与真后端 8801 各 +5 项 C1 断言，含
「refreshToken 访问业务接口 → 401」「换发后的 access 可用」）。
端到端：登录拿到 `expiresIn=1800`，用 refresh 换发后新令牌可用。

**无迁移 / 无破坏性契约变更**：只新增响应字段与一种续期入参。

**文档**：README（新增「令牌双轨（C1）」章节）、`.env.example`（两个新变量）、
backlog（**C1 移行，26 → 27**：拆出 C9 / C10 两笔新债）、速查表、本记录。

