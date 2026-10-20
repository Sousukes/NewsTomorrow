import { describe, expect, it } from 'vitest';
import { buildCsp } from '../security/csp';

/** 显式 worker-src：否则以后收紧 script-src 会连带挡掉 SW，症状是"离线不可用"。 */
describe('CSP 与 Service Worker', () => {
  it('worker-src 放行同源（缺了它 SW 在收紧 script-src 时会被连带挡掉）', () => {
    const csp = buildCsp();
    expect(csp).toContain("worker-src 'self'");
  });

  it('manifest-src 同源可用（回落到 default-src，与它一致）', () => {
    const csp = buildCsp();
    expect(csp).toContain("default-src 'self'");
  });
});
