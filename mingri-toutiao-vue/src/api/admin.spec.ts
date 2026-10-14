import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 关闭 mock：源管理没有 mock 短路（见 api/admin.ts 说明），必须走真实请求分支
vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

import {
  ADMIN_SOURCES_SUPPORTED,
  createSource,
  deleteSource,
  fetchSources,
  resetSources,
  updateSource,
} from '@/api/admin';

function respond(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({ code: 0, message: 'ok', data }),
  } as unknown as Response;
}

function fail(status: number, message: string): Response {
  return {
    ok: false,
    status,
    json: async () => ({ code: status, message }),
  } as unknown as Response;
}

const RAW_SOURCE = {
  key: 'aihot_picks',
  name: 'AIHOT·精选',
  channel: 'tech',
  url: 'https://aihot.news/feed.xml',
  weight: 9,
  enabled: true,
  lastStatus: 'ok',
  lastError: null,
  lastItemCount: 20,
  lastFetchedAt: '2026-10-13T07:38:22',
  respectRobots: true,
  consecutiveFailures: 0,
  failureKind: null,
  nextRetryAt: null,
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => respond({ list: [RAW_SOURCE], total: 1 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const lastCall = () => (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.at(-1);

describe('api/admin', () => {
  it('非 mock 构建才具备源管理能力', () => {
    expect(ADMIN_SOURCES_SUPPORTED).toBe(true);
  });

  it('fetchSources 请求 /admin/sources 并归一化字段', async () => {
    const list = await fetchSources();
    expect(lastCall()?.[0] as string).toBe('/api/admin/sources');
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      key: 'aihot_picks',
      name: 'AIHOT·精选',
      channel: 'tech',
      url: 'https://aihot.news/feed.xml',
      weight: 9,
      enabled: true,
      lastStatus: 'ok',
      lastItemCount: 20,
      lastFetchedAt: '2026-10-13T07:38:22',
      lastError: null,
      respectRobots: true,
      // P1-18：多词字段走驼峰，落库口径与后端 `_out` 对齐（拼错会静默变 null）
      consecutiveFailures: 0,
      failureKind: null,
      nextRetryAt: null,
    });
  });

  it('respectRobots 缺失时按更保守的一方兜底为 true', async () => {
    // 后端旧版本可能不下发该字段：显式删掉它，模拟「字段缺失」
    const withoutField: Record<string, unknown> = { ...RAW_SOURCE };
    delete withoutField.respectRobots;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [withoutField] })),
    );
    const [s] = await fetchSources();
    expect(s.respectRobots).toBe(true);
  });

  it('respectRobots=false 原样透出（管理员主动破例）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, respectRobots: false }] })),
    );
    const [s] = await fetchSources();
    expect(s.respectRobots).toBe(false);
  });

  it('缺 key 的行被丢弃而非渲染成空壳', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ name: '无 key' }, RAW_SOURCE] })),
    );
    const list = await fetchSources();
    expect(list.map((s) => s.key)).toEqual(['aihot_picks']);
  });

  it('list 不是数组时返回空数组（不抛）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({})),
    );
    await expect(fetchSources()).resolves.toEqual([]);
  });

  it('非法 channel 归一化为默认频道，非法 status 归一化为 null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        respond({ list: [{ ...RAW_SOURCE, channel: 'tecn', lastStatus: 'weird' }] }),
      ),
    );
    const [s] = await fetchSources();
    expect(s.channel).toBe('recommend');
    expect(s.lastStatus).toBeNull();
  });

  it('lastError 空串归一化为 null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, lastError: '' }] })),
    );
    const [s] = await fetchSources();
    expect(s.lastError).toBeNull();
  });

  it('createSource 走 POST 并回传新建的源', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ ...RAW_SOURCE, key: 'demo' })),
    );
    const s = await createSource({
      key: 'demo',
      name: '演示源',
      channel: 'tech',
      url: 'https://example.com/f.xml',
      weight: 7,
    });
    expect(s.key).toBe('demo');
    const [, init] = lastCall() as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toMatchObject({ key: 'demo', channel: 'tech' });
  });

  it('updateSource 走 PATCH 且 URL 中的 key 被编码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond(RAW_SOURCE)),
    );
    await updateSource('a b/c', { weight: 3 });
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/sources/a%20b%2Fc');
    expect(init.method).toBe('PATCH');
  });

  it('deleteSource 走 DELETE', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ key: 'demo' })),
    );
    await deleteSource('demo');
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/sources/demo');
    expect(init.method).toBe('DELETE');
  });

  it('resetSources 走 POST 并取出播种条数', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ seeded: 14 })),
    );
    await expect(resetSources()).resolves.toBe(14);
    expect((lastCall()?.[1] as RequestInit).method).toBe('POST');
  });

  it('resetSources 在响应缺字段时返回 0（不抛）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({})),
    );
    await expect(resetSources()).resolves.toBe(0);
  });

  it('后端 403 时向上抛错，供页面展示无权限态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(403, '无权限执行该操作')),
    );
    await expect(fetchSources()).rejects.toThrow('无权限执行该操作');
  });

  it('后端 409（key 已存在）向上抛错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(409, '该源标识已存在')),
    );
    await expect(
      createSource({ key: 'dup', name: 'x', channel: 'tech', url: 'https://e.com/a.xml' }),
    ).rejects.toThrow('该源标识已存在');
  });
});

// P1-18：失败分级三项是**旁路信息**，取值不认识时一律回落到"没有故障"，
// 绝不猜一个失败性质——误标成 permanent 会让运营白跑一趟。
describe('api/admin（失败分级字段，P1-18）', () => {
  const one = async (patch: Record<string, unknown>) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, ...patch }] })),
    );
    const [s] = await fetchSources();
    return s;
  };

  it('三项齐全时原样透出', async () => {
    const s = await one({
      consecutiveFailures: 7,
      failureKind: 'permanent',
      nextRetryAt: '2026-10-15T12:00:00',
    });
    expect(s.consecutiveFailures).toBe(7);
    expect(s.failureKind).toBe('permanent');
    expect(s.nextRetryAt).toBe('2026-10-15T12:00:00');
  });

  it('transient 原样透出（与 permanent 走同一通道）', async () => {
    expect((await one({ failureKind: 'transient' })).failureKind).toBe('transient');
  });

  it('整个字段缺失（后端旧版本）时回落为 0 / null / null', async () => {
    // 显式删掉三个键，真正模拟"老后端不下发"——用 RAW_SOURCE 里已有的 0/null
    // 是测不出缺失分支的
    const legacy: Record<string, unknown> = { ...RAW_SOURCE };
    delete legacy.consecutiveFailures;
    delete legacy.failureKind;
    delete legacy.nextRetryAt;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [legacy] })),
    );
    const [s] = await fetchSources();
    expect(s.consecutiveFailures).toBe(0);
    expect(s.failureKind).toBeNull();
    expect(s.nextRetryAt).toBeNull();
  });

  it('failureKind 是不认识的取值时回落为 null', async () => {
    expect((await one({ failureKind: 'fatal' })).failureKind).toBeNull();
  });

  it('consecutiveFailures 是垃圾值时回落为 0，不产生 NaN', async () => {
    expect((await one({ consecutiveFailures: '很多次' })).consecutiveFailures).toBe(0);
  });

  it('nextRetryAt 为空串时回落为 null', async () => {
    expect((await one({ nextRetryAt: '' })).nextRetryAt).toBeNull();
  });
});

describe('api/admin（mock 模式）', () => {
  it('USE_MOCK=true 时明确拒绝，而不是伪造一份可增删的假列表', async () => {
    vi.resetModules();
    vi.doMock('@/api/config', () => ({ USE_MOCK: true, API_BASE: '' }));
    const mod = await import('@/api/admin');
    expect(mod.ADMIN_SOURCES_SUPPORTED).toBe(false);
    await expect(mod.fetchSources()).rejects.toThrow('mock 模式');
    vi.doUnmock('@/api/config');
    vi.resetModules();
  });
});
