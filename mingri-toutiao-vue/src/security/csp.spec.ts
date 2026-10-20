import { describe, expect, it } from 'vitest';
import { buildCsp, originOf } from './csp';

/** 把策略串解析成 { 指令: 值列表 }，避免断言依赖书写顺序 */
function parse(policy: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const part of policy.split(';')) {
    const seg = part.trim();
    if (!seg) continue;
    const [name, ...rest] = seg.split(/\s+/);
    out[name] = rest;
  }
  return out;
}

describe('security/csp（C9）', () => {
  it('不放行内联脚本与 eval（这是策略的核心价值）', () => {
    const d = parse(buildCsp());
    expect(d['script-src']).toEqual(["'self'"]);
    expect(d['script-src']).not.toContain("'unsafe-inline'");
    expect(d['script-src']).not.toContain("'unsafe-eval'");
    // default-src 也不能带 unsafe-*，否则 script-src 之外的地方照样能执行
    expect(d['default-src']).toEqual(["'self'"]);
  });

  it('禁插件、锁 base 与表单目标', () => {
    const d = parse(buildCsp());
    expect(d['object-src']).toEqual(["'none'"]);
    expect(d['base-uri']).toEqual(["'self'"]);
    expect(d['form-action']).toEqual(["'self'"]);
  });

  it('样式必须放开 unsafe-inline（Vue 作用域样式与内联 style 属性）', () => {
    const d = parse(buildCsp());
    expect(d['style-src']).toContain("'unsafe-inline'");
  });

  it('无后端（mock 演示态）：图片保留 https 外链，媒体不放行', () => {
    const d = parse(buildCsp());
    expect(d['img-src']).toContain('https:');
    expect(d['img-src']).toContain('data:');
    // 媒体与图片不是一回事：图片外链是 mock 演示态的必要让步，
    // 媒体没有这个理由（见下一条的零数据证据）
    expect(d['media-src']).not.toContain('https:');
  });

  it('B4：有后端时 img-src 收掉 https（外链图一律走本域代理）', () => {
    // 这一条是 B4 落地后 CSP 真正多出来的收益：
    // 挡掉「<img src=https://evil/x> 泄露用户 IP 与浏览行为」——
    // 纯 CSP 在图片上无论如何都挡不住，只有把图收进自己源站才行。
    const d = parse(buildCsp({ apiBase: 'https://api.example.com/api' }));
    expect(d['img-src']).not.toContain('https:');
    expect(d['img-src']).toContain("'self'");
    // 代理跨源时它自己的源要放行（图片请求直接打到 API 源）
    expect(d['img-src']).toContain('https://api.example.com');
    expect(d['img-src']).toContain('data:');
  });

  /**
   * P1-84：media-src 收掉 https（零视频数据，不为零流量能力留敞口）。
   *
   * 证据：真实库 700 条资讯 `video_url` 非空 **0 条**；种子唯一的 video 变体条目
   * 不带地址；mock 同样不带。也就是说 `<video>` 的渲染条件
   * （`variant === 'video' && videoUrl`）**从未成立过**。
   *
   * 这条断言的价值在将来：谁要是放开 media-src（或接入视频），会先在这里红，
   * 被迫显式记录理由——而不是让视频在浏览器里被 CSP 静默拦掉、没人知道为什么。
   */
  it('P1-84：media-src 不放行 https（零视频数据；要接视频先做 B7 代理）', () => {
    for (const opts of [{}, { apiBase: 'https://api.example.com/api' }, { dev: true }]) {
      const d = parse(buildCsp(opts));
      expect(d['media-src']).not.toContain('https:');
      expect(d['media-src']).toContain("'self'");
    }
  });

  it('生产态 connect-src 只放行 self 与后端源，不含 localhost', () => {
    const d = parse(buildCsp({ apiBase: 'https://api.example.com/api' }));
    expect(d['connect-src']).toEqual(["'self'", 'https://api.example.com']);
    expect(d['connect-src']?.join(' ')).not.toContain('localhost');
    expect(d['connect-src']?.join(' ')).not.toContain('ws:');
  });

  it('开发态额外放行 ws 与 localhost（HMR 与本地后端）', () => {
    const d = parse(buildCsp({ dev: true, apiBase: 'http://127.0.0.1:8000/api' }));
    expect(d['connect-src']).toContain('http://127.0.0.1:8000');
    expect(d['connect-src']).toContain('ws://localhost:*');
    expect(d['connect-src']).toContain('http://localhost:*');
  });

  it('演示态（未配后端）connect-src 只有 self', () => {
    const d = parse(buildCsp({ dev: true }));
    expect(d['connect-src']).toContain("'self'");
    // 开发态仍有 ws / localhost（HMR 本身需要）
    expect(d['connect-src']).toContain('ws://127.0.0.1:*');
  });

  it('frame-ancestors 只在 header 模式出现（meta 里会被规范忽略）', () => {
    expect(parse(buildCsp({ transport: 'meta' }))['frame-ancestors']).toBeUndefined();
    expect(parse(buildCsp({ transport: 'header' }))['frame-ancestors']).toEqual(["'none'"]);
  });

  it('originOf 处理相对地址 / 空值 / 非法值', () => {
    expect(originOf('https://api.example.com/api')).toBe('https://api.example.com');
    expect(originOf('/api')).toBe('');
    expect(originOf('')).toBe('');
    expect(originOf(undefined)).toBe('');
    expect(originOf('not a url')).toBe('');
  });
});
