## 批次 P1-39（2026-10-18）：内容安全策略 CSP（C9）
**问题**：C9 由 P1-38 从 C1 拆出（P1-38 只做了令牌双轨，CSP 与高敏二次校验另案）。
登录令牌存 `localStorage`，一次 XSS 就等于令牌泄露——access 已收窄到 30 分钟，
但「窗口短」不等于「不发生」。CSP 是这一层兜底：把能执行的脚本限制到自源，
绝大多数注入载荷（内联脚本、外部脚本、eval、插件）就此失效。

**为什么分两半落**：后端**不托管** SPA（`main.py` 无 `StaticFiles`），响应头由
谁下发完全取决于部署方（nginx / OSS / Pages / CDN，仓库里也没有任何部署编排，
D8 仍开放）。所以：

- **SPA 侧**：策略由构建期注入 `index.html` 的 `<meta http-equiv>`——
  静态托管必定生效，不依赖基础设施改造；
- **后端侧**：给自己的 JSON 响应上 `default-src 'none'`（见下）。

**前端**（`src/security/csp.ts` + `vite.config.ts` 插件）
- `buildCsp({ dev, apiBase, transport })`：**构建期**生成——`connect-src` 取决于
  `VITE_API_BASE`（生产 https 域名 / 开发 `http://127.0.0.1:8000` / 演示态留空走
  mock）。写死要么生产连不上后端，要么开发把 API 挡掉；开发态额外放行 `ws:` 与
  localhost（Vite HMR 与本地后端）
- `script-src: 'self'`，**不放行** `unsafe-inline` / `unsafe-eval`——这是策略的核心价值
- `style-src` 必须带 `'unsafe-inline'`：Vue 作用域样式与组件内联 `style` 属性都走它
  （配图占位是渐变，也是内联样式）
- `frame-ancestors` **只在 `transport: 'header'` 下发**：它在 `<meta>` 里被规范明确
  忽略，留在 meta 版本里会让读代码的人以为「已经防了点击劫持」其实没生效
- **诚实边界**：`img-src` / `media-src` 必须放行 `https:`（资讯配图与视频来自任意
  外站），因此 CSP **挡不住** `<img src=https://evil/x>` 这类外泄型载荷；真正收紧
  要等图片代理（B4）把外链收敛到自源

**后端**（`app/security_headers.py`）
- `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`
- `/api/*` 追加 CSP：`default-src 'none'; frame-ancestors 'none'; base-uri 'none'`。
  JSON 响应没有文档上下文，就没有任何资源需要加载——**任何加载都是异常**
- **只给 `/api/*`**：`/docs`（Swagger UI）依赖 CDN 脚本与内联脚本，套上
  `default-src 'none'` 会直接打不开；docs 只在 development 存在（生产 `DOCS_URL=None`），
  不为它放宽策略即可，非 `/api` 路径仍拿基础头
- **注册顺序是有意的**：Starlette 的 `add_middleware` 往栈头插，后注册者在最外层，
  故本中间件写在限流之后——限流直接返回的 429 也带得上这些头。
  用源码顺序断言守住（写在中间件里但看不出来，改顺序就会静默丢头）
- 开关 `SECURITY_HEADERS_ENABLED`（默认开），仅在前置网关已统一发这些头时关闭

**验证**：后端 pytest 673 → **680 全绿**（新增 `tests/test_security_headers.py` 7 项：
API 头 / 404 也带 / docs 只有基础头 / 开关两分支 / **中间件顺序的源码守卫**）；
前端 vitest 488 → 496 → **505**（新增 `src/security/csp.spec.ts` 9 项：脚本禁内联、
connect-src 按环境、frame-ancestors 只在 header 模式等）；`verify`（类型 + Lint + 格式）
与 `build` 通过；构建产物与开发态 HTML **均含 CSP meta 且内联 `<script>` 数为 0**
（`script-src 'self'` 不会误伤自身资源——这是策略成立的前提，不是想当然）；
契约双端 **53/53**（本批不改接口行为，只加响应头与 HTML，故未新增断言）；
真后端响应头实测：`nosniff` / `no-referrer` / `DENY` / `default-src 'none'; frame-ancestors 'none'; base-uri 'none'`。

**文档**：server-api README「安全响应头（C9）」、前端 README「内容安全策略 CSP（C9）」、
`.env.example`（`SECURITY_HEADERS_ENABLED`）、backlog（**C9 移行，27 → 26**）、
速查表、本记录。

