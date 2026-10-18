## 批次 P1-43（2026-10-18）：图片代理 + CSP 收紧（B4）
偿还 backlog 性能组的 B4（M）。**必须和 CSP 收紧一起做**——backlog 里那句
"CSP 的 img-src 收紧依赖 B4"就是这个意思：只做代理不收紧，等于白做。

**为什么值得做**：配图此前由浏览器**直连源站**，代价有三——
① 源站一挂就裂图（图在源站，我们只存了 URL）；
② CSP 的 `img-src` 必须放行 `https:`，而 **CSP 恰恰挡不住
`<img src=https://evil/x>` 这类外泄型载荷**——几十个无关源站都能看到用户 IP
与浏览行为；③ 没有尺寸协商，小卡片里照原样下载 2000px 封面。

**后端**（`app/routers/img.py`，`GET /api/img?url=…&w=…`）

它是教科书式的 SSRF 攻击面（把"能发请求的服务器"交给用户指定目标），
所以**这个文件里防线比功能多**：
- 协议白名单（仅 http/https）；
- **逐条**检查 DNS 结果，非公网即拒（攻击者给域名配多条 A 记录、
  一条公网一条内网，正是为了蒙混过关；`169.254.169.254` 云元数据在
  `is_link_local` 里）；
- **跳转逐跳复检**（≤3 跳、手动跟随）——外站 302 到内网是最常见的绕过，
  只查入口 URL 等于给它留门；
- 5MB 体积上限 + 超时（内存炸弹）；只放行图片 `Content-Type`
  且**不含 SVG**（同源代理转发 HTML/JS 就是存储型 XSS 入口，SVG 可内嵌脚本）；
  `nosniff` + `Content-Disposition` 是同一道防线的第二层；
- 缓存键 = (URL, 宽度)，**宽度走白名单**（任意 `w` 就是把缓存撑爆的入口）。

**已知边界（写进代码与 README，不藏）**：DNS 解析与 httpx 建连之间有 TOCTOU
窗口（若能在这两个时刻之间改 DNS 记录，仍可能绕过 IP 检查）。彻底消除要自定义
transport 把连接钉到已校验 IP 上，当前不接受这个复杂度，改以"仅供资讯配图、
上游为已抓取过的 RSS 源"降低暴露面。

**缩略图 / WebP**：装了 Pillow 才生效（`try: import PIL`），且**不放大**。
它是 native 依赖，**刻意不进 requirements.txt**，另立 `requirements-optional.txt`
由部署方决定——未安装时**代理功能不受影响**，只是少了尺寸协商。

**前端**：`utils/image.ts::proxiedImageUrl(url, width)`，三条分支各有理由——
相对路径原样（本来同源，多余一跳）、mock 模式原样（离线演示没有后端）、
真实后端走代理。宽度只能取后端白名单值。`encodeURIComponent` 产物不含引号/
反斜杠/控制字符，拼进 CSS `url()` 也安全——比原来手写转义更强：结构上就不可能
转义逃逸。

**CSP 随之收紧**：有后端时 `img-src` 从 `'self' data: blob: https:` 收成
`'self' data: blob: <API 源>`。**两条仍存在的诚实边界**：`media-src` 仍放行
`https:`（**视频 mp4 没被代理**——几十 MB + Range 请求是另一个量级的设计，
已开 backlog 后续项）；mock 演示态没有后端代理，图片仍直连外站，
`img-src` 保留 `https:`，那是唯一保留松口的地方。

**测试**：新增 `tests/test_img_proxy.py` **29 项**，主体是 SSRF 拒绝矩阵
（非 http 协议 / 回环 / 私有网段各段 / 云元数据 / 多 A 记录混入内网 /
**302 跳内网** / 跳转次数上限 / **"被拒目标一次 HTTP 请求都不该发出"**），
外加缓存命中、宽度白名单、非图片拒绝、超大拒绝、nosniff 头、Pillow 缩放与不放大。
前端新增 `image.proxy.spec.ts` 7 项（**单独文件**——`vi.mock` 是文件级的，
默认 spec 里 `USE_MOCK=true`，代理分支根本走不到）+ CSP 3 项。

**契约**：`/api/img` 只断言**安全策略**（SSRF 拒绝与错误码），不断言"能取到图"——
真后端取图要出网，契约环境无外网；取图行为由后端单测覆盖。mock 同步实现
`/api/img`（**不回源**，返回 1×1 PNG）——它不替用户发起外网请求。

**两个坑**：
1. **不能打桩 `httpx.Client.get`**——FastAPI 的 `TestClient` 本身就是
   `httpx.Client` 的子类，一打桩连测试自己的请求都被打掉（症状是一堆用例
   返回上游的预置响应）。改在模块里留 `_build_client()` 接缝。
2. DNS 打桩返回 `(host or PUBLIC_IP, ...)` 时 `host` 恒真，于是返回的是
   **域名本身**而非 IP，`ip_address()` 解析失败 → 全部请求被判为内网。

**验证**：pytest 735 → **764 全绿**（退出码 0）；vitest 523 → **532**；
`verify` + `build` 通过；契约**双端双档全绿**——默认 68 → **72**（真）/71（mock）、
`CHECK_ADMIN=1` 277 → **285**。

**文档**：server-api README「图片代理（B4）」（含防线表与已知边界）、
前端 README「图片代理与 CSP 收紧（B4）」、`requirements-optional.txt`、
速查表 P1-43 行。backlog：**B4 移行，22 → 21**，并新增一条 P2
「视频代理（media-src 收紧）」——它是 CSP 剩余的唯一松口。

