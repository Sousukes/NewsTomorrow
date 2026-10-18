#### P1-39 详情：内容安全策略 CSP（C9）
**SPA 策略**（构建期注入 `index.html`，`src/security/csp.ts`）：

| 指令 | 值 | 说明 |
| --- | --- | --- |
| `default-src` / `script-src` | `'self'` | 不放行 `unsafe-inline` / `unsafe-eval` |
| `style-src` | `'self' 'unsafe-inline' https://fonts.googleapis.com` | Vue 作用域样式与内联 style 属性必须放开 |
| `img-src` | `'self' data: blob: https:` | 资讯配图来自任意外站（B4 前无法收紧） |
| `font-src` | `'self' data: https://fonts.gstatic.com` | index.html 引 Google Fonts |
| `media-src` | `'self' blob: https:` | 视频外链 |
| `connect-src` | `'self'` + `VITE_API_BASE` 的源（开发态再加 `ws:`/localhost） | 由环境变量推导 |
| `object-src` / `base-uri` / `form-action` | `'none'` / `'self'` / `'self'` | 禁插件、禁 base 劫持、禁表单外投 |
| `frame-ancestors` | `'none'` | **仅 `transport:'header'`**——`<meta>` 里被规范忽略 |

**后端策略**（`app/security_headers.py`，仅 `/api/*`）：
`default-src 'none'; frame-ancestors 'none'; base-uri 'none'`
+ `X-Content-Type-Options: nosniff` / `Referrer-Policy: no-referrer` / `X-Frame-Options: DENY`
（后两个头对所有路径下发）。开关 `SECURITY_HEADERS_ENABLED`（默认开）。

**已验证的事实**：`dist/index.html` 与开发态 HTML 均含 CSP meta；两者内联
`<script>` 数均为 **0**（`script-src 'self'` 不会误伤自身资源）。
| **P1-40（C10）** | **高敏操作二次校验** | 会话令牌被偷后，攻击者拿到的是**与本人同等的能力**——包括把管理后台交出去、删掉 RSS 源、顶着别人的名字说话。机制：`POST /api/auth/step-up`（**已登录 + 密码**）换 5 分钟的 `typ=stepup` 令牌，受护端点以 `X-Step-Up-Token` 头校验，缺失/无效 → **403 + 40304**（不是 401：会话是好的，前端不会误清登录态）。**刻意不新造轮子**：① 复用 `login_guard` 账号维度锁定（scope=`stepup`）——否则这个端点本身就是密码 oracle；scope 隔离让「二次校验失败」不锁登录，阈值/窗口共用配置；② 复用 JWT 类型校验与 `token_version` 吊销（登出即同时失效）；③ **body 不收手机号**——那是把二次校验变成「可指定账号的登录端点」。覆盖 5 个端点：`PUT /me/nickname`、`POST/DELETE /api/admin/users`、`DELETE /api/admin/sources/{key}`、`POST /api/admin/sources/reset`（判据=不可逆或影响全局）；**内容治理类未覆盖**（可逆 + 有审计日志，纳入只需加一个 `Depends`）。管理端点同时声明 `require_admin`（在前）与 `require_step_up`（在后）：未授权者得 40301 而非 40304，不透露站内哪类操作要二次校验。前端：40304 → 广播事件挂起 → 全局密码对话框（`StepUpDialog.vue`）→ 令牌存**内存**（5 分钟，不落 localStorage）→ 原请求带头重试一次；一次请求最多弹一次（防弹窗循环）。后端 680 → **699**（新增 `tests/test_step_up.py` 17 项），前端 505 → **512**，契约 mock 端 56/56 |

