/** 渐变占位调色板（无真实配图时兜底，保持离线可运行） */
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
 * 生成 backgroundImage：有真实图片 URL 时优先使用，否则回退渐变占位。
 * @param seed 稳定种子（一般用 id），用于渐变选择
 * @param url  可选的真实图片地址（cover / images[i]）
 */
export function bgFor(seed: string, url?: string | null): string {
  const safe = sanitizeImageUrl(url ?? '');
  if (safe) return `url("${safe}")`;
  return gradientFor(seed);
}
