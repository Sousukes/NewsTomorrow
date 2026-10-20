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

/** 用于 CSS url()：拒绝非 http(s) 绝对地址，并转义反斜杠/引号/换行防注入。 */
function sanitizeImageUrl(raw: string): string {
  const u = raw.trim();
  if (!u) return '';
  if (HAS_SCHEME.test(u) && !SAFE_SCHEME.test(u)) return '';
  return u
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]/g, '');
}

/** 用于 <img src>：仅放行 http(s) 与相对路径（Vue 会转义属性，无需 CSS 转义）。 */
export function safeImageUrl(url?: string | null): string {
  const u = (url ?? '').trim();
  if (!u) return '';
  if (HAS_SCHEME.test(u) && !SAFE_SCHEME.test(u)) return '';
  return u;
}

/** 必须取白名单值（后端按 URL+宽度 缓存，任意宽度会让缓存键爆炸）。 */
export type ImageWidth = 200 | 320 | 480 | 640 | 960 | 1280;

/**
 * 外站图片改走本域代理（否则每个源站都能看到用户 IP，且 img-src 无法收紧）。
 * 相对路径与 mock 模式原样返回，其余换成 ${API_BASE}/api/img。
 */
export function proxiedImageUrl(url?: string | null, width: ImageWidth = 640): string {
  const u = safeImageUrl(url);
  if (!u) return '';
  // 相对路径：本就是同源，不进代理
  if (!HAS_SCHEME.test(u)) return u;
  if (USE_MOCK) return u;
  return `${API_BASE}/api/img?url=${encodeURIComponent(u)}&w=${width}`;
}

/** 有图用图，否则按 seed 取渐变占位。 */
export function bgFor(seed: string, url?: string | null, width: ImageWidth = 640): string {
  const proxied = proxiedImageUrl(url, width);
  if (proxied) return `url("${sanitizeImageUrl(proxied)}")`;
  return gradientFor(seed);
}
