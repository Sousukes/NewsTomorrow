/**
 * 内容安全策略。构建期生成而非写死：connect-src 取决于 VITE_API_BASE。
 * 已知边界：视频未代理，media-src 仍保留 https:。详见 docs/前端/数据与安全.md。
 */

/** 生成入参 */
export interface CspOptions {
  /** 开发服务器：放宽 ws: 与 localhost */
  dev?: boolean;
  /** VITE_API_BASE 的源，用于 connect-src */
  apiBase?: string;
  /** meta 静态托管即可生效；header 多生效 frame-ancestors */
  transport?: 'meta' | 'header';
}

/** 相对地址 / 空串 / 非法值返回空串 */
export function originOf(apiBase: string | undefined | null): string {
  const raw = (apiBase ?? '').trim();
  if (!raw) return '';
  // 相对基址与当前页同源，'self' 已覆盖
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

  // 图片代理在跨源 API 上，必须放行其源
  const imgParts = ["'self'", 'data:', 'blob:'];
  if (origin) imgParts.push(origin);
  else imgParts.push('https:'); // mock 演示态无代理，fixture 图直连外站
  const imgSrc = imgParts.join(' ');
  if (dev) {
    // HMR 走 ws；开发后端常在本地 http 端口
    connect.push('ws://127.0.0.1:*', 'ws://localhost:*');
    connect.push('http://127.0.0.1:*', 'http://localhost:*');
  }

  const directives: [string, string][] = [
    // 不写 unsafe-inline / unsafe-eval，这是本策略的价值所在
    ['default-src', "'self'"],
    ['script-src', "'self'"],
    // 显式声明，避免以后收紧 script-src 时连带挡掉 SW
    ['worker-src', "'self'"],
    // 必须保留：Vue 作用域样式与内联 style 都走它
    ['style-src', "'self' 'unsafe-inline' https://fonts.googleapis.com"],
    // 配图走本域代理后可收掉 https:（外链图片会泄露 IP 与浏览行为）；
    // 无 apiBase 的 mock 演示态才保留 https:。data/blob 留给占位图。
    ['img-src', imgSrc],
    ['font-src', "'self' data: https://fonts.gstatic.com"],
    // 视频未被代理（几十 MB + Range 请求），仍会暴露客户端 IP
    ['media-src', "'self' blob: https:"],
    ['connect-src', connect.join(' ')],
    ['object-src', "'none'"],
    ['base-uri', "'self'"],
    ['form-action', "'self'"],
  ];

  // frame-ancestors / report-uri / sandbox 在 <meta> 里被规范忽略，只在 header 下发
  if (transport === 'header') {
    directives.push(['frame-ancestors', "'none'"]);
  }

  return directives.map(([name, value]) => `${name} ${value}`).join('; ') + ';';
}
