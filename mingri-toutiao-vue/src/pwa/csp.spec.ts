import { describe, expect, it } from 'vitest';
import { buildCsp } from '../security/csp';

/**
 * CSP 与 PWA 的相容性（E8 / P1-78）。
 *
 * Service Worker 是同源脚本，`worker-src` 未声明时回落到 `script-src`——行为相同。
 * 但**显式声明**的意义在于：以后若有人收紧 `script-src`（比如加 nonce），
 * 回落规则会连带把 SW 一起挡掉，而症状是"SW 注册失败、离线不可用"，
 * 没人会联想到 CSP。先钉住这一行。
 */
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
