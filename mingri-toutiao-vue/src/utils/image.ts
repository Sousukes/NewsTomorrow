/** 渐变占位调色板（无真实配图时兜底，保持离线可运行） */
import { API_BASE, USE_MOCK } from '@/api/config';

const PALETTE = [
  'linear-gradient(135deg,#c9cdd4,#aeb4bd)',
  'linear-gradient(135deg,#b9c6e8,#8ea6dd)',
  'linear-gradient(135deg,#cdd6e0,#a8b8cf)',
  'linear-gradient(135deg,#c2c9d6,#9aa6bd)',
  'linear-gradient(135deg,#d7d2c8,#b3aa98)',
  'linear-gradient(135deg,#c8d0dc,#9fb0c9)',
];

function hash(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return h;
}

/** 依据种子取稳定的渐变 */
export function gradientFor(seed: string): string {
  return PALETTE[hash(seed) % PALETTE.length];
}

/** 允许的绝对协议白名单（阻断 javascript: / data: 等） */
const SAFE_SCHEME = /^https?:\/\//i;
/** 形如 scheme: 的绝对 URL */
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/**
 * 清洗图片 URL，用于安全地拼进 CSS `url("...")`：
 * 1. 非 http(s) 的绝对 URL 直接拒绝（相对路径与 // 开头者放行）
 * 2. 转义反斜杠 / 双引号 / 换行，防止逃逸 url() 上下文注入任意 CSS
 */
function sanitizeImageUrl(raw: string): string {
  const u = raw.trim();
  if (!u) return '';
  if (HAS_SCHEME.test(u) && !SAFE_SCHEME.test(u)) return '';
  return u
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]/g, '');
}

/**
 * 用于 `<img src>` 的安全 URL：仅放行 http(s) 绝对地址与相对路径。
 * 与 `sanitizeImageUrl` 的区别：不做 CSS 上下文转义（Vue 会自动转义属性）。
 */
export function safeImageUrl(url?: string | null): string {
  const u = (url ?? '').trim();
  if (!u) return '';
  if (HAS_SCHEME.test(u) && !SAFE_SCHEME.test(u)) return '';
  return u;
}

/**
 * 目标宽度（与后端 `/api/img` 的 `ALLOWED_WIDTHS` 一致）。
 *
 * **必须**取白名单里的值，不是随手写的"想要多少像素"：后端按 (URL, 宽度)
 * 缓存，任意宽度等于让缓存键爆炸。改这里就要同步改后端。
 */
export type ImageWidth = 200 | 320 | 480 | 640 | 960 | 1280;

/**
 * 把外站图片地址换成**图片代理**地址（B4）。
 *
 * 动机不只是"少裂图"：外站直连意味着每个源站都能看到用户 IP 与浏览行为，
 * 而 CSP 只能挡住外泄型脚本、挡不住 `<img src=https://evil/x>`。走代理之后
 * `img-src` 就能收紧到 `'self'`（见 security/csp.ts）。
 *
 * 三条分支：
 * - **相对路径 / 同源资源**：原样返回（本就是自己的源站，进代理是多余一跳）；
 * - **mock 模式**：原样返回。离线演示没有后端，而 fixture 图就是外站地址——
 *   此时 CSP 同样保留 `https:`（见 csp.ts 的同一条分支说明）；
 * - **真实后端**：换成 `${API_BASE}/api/img?url=…&w=…`。
 *
 * `encodeURIComponent` 的产物不含引号/反斜杠/控制字符，所以拼进 CSS `url()`
 * 也是安全的（比原来的手写转义更强：结构上就不可能出现转义逃逸）。
 */
export function proxiedImageUrl(url?: string | null, width: ImageWidth = 640): string {
  const u = safeImageUrl(url);
  if (!u) return '';
  // 相对路径：本就是同源，不进代理
  if (!HAS_SCHEME.test(u)) return u;
  if (USE_MOCK) return u;
  return `${API_BASE}/api/img?url=${encodeURIComponent(u)}&w=${width}`;
}

/**
 * 生成 backgroundImage：有真实图片 URL 时优先使用，否则回退渐变占位。
 * @param seed 稳定种子（一般用 id），用于渐变选择
 * @param url  可选的真实图片地址（cover / images[i]）
 * @param width 目标宽度（走代理时生效）
 */
export function bgFor(seed: string, url?: string | null, width: ImageWidth = 640): string {
  const proxied = proxiedImageUrl(url, width);
  if (proxied) return `url("${sanitizeImageUrl(proxied)}")`;
  return gradientFor(seed);
}
