/**
 * 内容安全策略（C9）。
 *
 * **为什么要有它**：登录令牌存在 `localStorage`，一次 XSS 就等于令牌泄露
 * （即便 access 已收窄到 30 分钟——泄露窗口短不等于不发生）。CSP 是这一层
 * 兜底：把「能执行的脚本」限制到自源，**内联脚本与外部脚本一律不执行**，
 * 绝大多数注入载荷就此失效。
 *
 * **为什么是构建期生成而不是写死在 index.html**：`connect-src` 取决于
 * `VITE_API_BASE`（生产是 https 域名、开发是 `http://127.0.0.1:8000`、
 * 演示态留空走内置 mock）。写死要么在生产连不上后端，要么在开发把 API 挡掉。
 *
 * **为什么 img-src 现在能收紧（B4）**：资讯配图一律改走本域图片代理
 * （`utils/image.ts::proxiedImageUrl`），外站 URL 不再直接进 `<img>`，
 * 于是 `https:` 可以在**有后端时**收掉，挡住「外链图片泄露用户 IP 与
 * 浏览行为」这类载荷——这是纯 CSP 无论如何挡不住的。
 *
 * **仍然存在的诚实边界**：`media-src` 保留 `https:`（**视频没有被代理**，
 * 几十 MB + Range 请求是另一个量级的设计，见 backlog B4 后续项）；
 * 没有配 `VITE_API_BASE` 的 mock 演示态没有后端代理，图片仍直连外站。
 * CSP 在这里的其余收益不变：禁内联脚本、禁 eval、禁非自源脚本、禁插件、
 * 禁嵌套、禁 base 劫持。
 */

/** 生成策略的入参 */
export interface CspOptions {
  /** 开发服务器：放宽 `ws:`（HMR）与 localhost（本地后端） */
  dev?: boolean;
  /** 后端 API 基址（`VITE_API_BASE`）的**源**，用于 `connect-src` */
  apiBase?: string;
  /**
   * `meta`：下发到 `<meta http-equiv>`（静态托管也能生效，无需改基础设施）；
   * `header`：下发到 HTTP 响应头（多生效 `frame-ancestors`，详见下方注释）。
   */
  transport?: 'meta' | 'header';
}

/** 取 URL 的源（origin）；相对地址 / 空串 / 非法值一律返回空串 */
export function originOf(apiBase: string | undefined | null): string {
  const raw = (apiBase ?? '').trim();
  if (!raw) return '';
  // 相对基址（如 `/api`）与当前页同源，'self' 已覆盖，无需额外放行
  if (raw.startsWith('/')) return '';
  try {
    return new URL(raw).origin;
  } catch {
    return '';
  }
}

export function buildCsp(options: CspOptions = {}): string {
  const { dev = false, apiBase = '', transport = 'meta' } = options;

  const connect = ["'self'"];
  const origin = originOf(apiBase);
  if (origin) connect.push(origin);

  // 图片代理地址是 `${apiBase}/api/img`，跨源时它自己的源必须放行
  const imgParts = ["'self'", 'data:', 'blob:'];
  if (origin) imgParts.push(origin);
  else imgParts.push('https:'); // mock 演示态：没有代理，fixture 图仍直连外站
  const imgSrc = imgParts.join(' ');
  if (dev) {
    // Vite HMR 走 ws；开发后端常在 127.0.0.1 / localhost 的 http 端口上
    connect.push('ws://127.0.0.1:*', 'ws://localhost:*');
    connect.push('http://127.0.0.1:*', 'http://localhost:*');
  }

  const directives: [string, string][] = [
    // 不写 'unsafe-inline' / 'unsafe-eval'：这正是本策略的价值所在
    ['default-src', "'self'"],
    ['script-src', "'self'"],
    // 'unsafe-inline' 是必须的：Vue 作用域样式与组件的内联 style 属性都走它
    ['style-src', "'self' 'unsafe-inline' https://fonts.googleapis.com"],
    // B4 之后资讯配图一律走**本域的图片代理**（`${apiBase}/api/img`），
    // 于是可以收掉 `https:`——这挡住的正是「<img src=https://evil/x> 把
    // 用户 IP 与浏览行为泄露给任意外站」这类外泄型载荷。
    // data/blob 保留给占位图与本地 Object URL。
    // **没有 apiBase 时（mock 演示态）才保留 `https:`**：此时没有后端代理，
    // fixture 图就是外站地址，离线演示要能看到图——这是唯一保留松口的地方。
    ['img-src', imgSrc],
    ['font-src', "'self' data: https://fonts.gstatic.com"],
    // media-src 仍放行 https:：**视频（mp4）没有被代理**。图片是几百 KB、
    // 可以缓存；视频是几十 MB 且需要 Range 请求，走代理是另一个量级的设计
    // （见 backlog B4 的后续项）。诚实边界：视频仍会暴露客户端 IP。
    ['media-src', "'self' blob: https:"],
    ['connect-src', connect.join(' ')],
    ['object-src', "'none'"],
    ['base-uri', "'self'"],
    ['form-action', "'self'"],
  ];

  // `frame-ancestors`、`report-uri`、`sandbox` 在 <meta> 里**被规范明确忽略**。
  // 只在 header 模式下下发，避免读代码的人以为「已经防了点击劫持」其实没生效。
  if (transport === 'header') {
    directives.push(['frame-ancestors', "'none'"]);
  }

  return directives.map(([name, value]) => `${name} ${value}`).join('; ') + ';';
}
