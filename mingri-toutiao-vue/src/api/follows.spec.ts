import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 关注来源 API 层（E6b）。
 *
 * 重点验三件事：
 * 1. **离线分支的行为与真后端契约一致**：`follow` 幂等、`unfollow` 幂等、
 *    返回完整集合 `{ keys, list }`、站点上限校验——离线模式不是"随便存一下"，
 *    store 层不判断模式，契约漂移会直接变成 UI bug；
 * 2. **来源目录由种子数据确定性合成**：key 必须与 `api/news.ts` mock 分支派生的一致，
 *    否则「关注」后关注流筛不出内容（两边实现必须同源）；
 * 3. 真后端分支走 `POST /me/follows` / `DELETE /me/follows/{key}`，路径与错误码对齐。
 */

const request = vi.fn();
const mockDelay = async (v: unknown) => v;
// 可变开关：同文件内覆盖「离线」与「真后端」两条分支（vi.mock 工厂是文件级的）
const configState = { mock: true };

vi.mock('@/api/config', () => ({
  get USE_MOCK() {
    return configState.mock;
  },
  API_BASE: '/api',
}));
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  mockDelay: (v: unknown) => mockDelay(v),
}));

import {
  followSource,
  fetchFollows,
  fetchSourceDirectory,
  mockFollowedKeys,
  unfollowSource,
} from './follows';
import { mockNews } from '@/data/mockNews';
import { mockSourceKey } from '@/utils/source';

const LS_KEY = 'mingri-toutiao:follows';

const dirKeys = () => new Set([...new Set(mockNews.map((n) => mockSourceKey(n.source)))]);
const firstKey = () => [...dirKeys()][0];

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  configState.mock = true;
});

describe('api/follows 离线分支（内置 mock）', () => {
  it('来源目录由种子数据合成：key 与资讯侧派生一致、计数正确', async () => {
    const list = await fetchSourceDirectory();
    const unique = new Set(mockNews.map((n) => n.source));
    expect(list).toHaveLength(unique.size);
    const hit = list.find((s) => s.name === mockNews[0].source);
    expect(hit?.key).toBe(mockSourceKey(mockNews[0].source));
    expect(hit?.newsCount).toBe(mockNews.filter((n) => n.source === mockNews[0].source).length);
  });

  it('关注后写入 localStorage，并返回完整集合', async () => {
    const key = firstKey();
    const payload = await followSource(key);
    expect(payload.keys).toContain(key);
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '[]')).toContain(key);
    expect(mockFollowedKeys()).toContain(key);
  });

  it('重复关注幂等：keys 不重复、集合不回退', async () => {
    const key = firstKey();
    await followSource(key);
    await followSource(key);
    const stored = JSON.parse(localStorage.getItem(LS_KEY) ?? '[]') as string[];
    expect(stored.filter((k) => k === key)).toHaveLength(1);
    expect((await fetchFollows()).keys.filter((k) => k === key)).toHaveLength(1);
  });

  it('取关幂等：取关后不再出现，重复取关不抛错', async () => {
    const key = firstKey();
    await followSource(key);
    const payload = await unfollowSource(key);
    expect(payload.keys).not.toContain(key);
    await expect(unfollowSource(key)).resolves.toBeTruthy();
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '[]')).not.toContain(key);
  });

  it('关注不存在的来源抛错（与后端 40410 同语义）', async () => {
    await expect(followSource('src_nope')).rejects.toThrow('来源不存在或已停用');
  });

  it('空 key 在接口层就被拒（不必等到服务端）', async () => {
    await expect(followSource('  ')).rejects.toThrow('来源标识不能为空');
  });

  it('关注上限 200：超限抛错且不写入', async () => {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify(Array.from({ length: 200 }, (_, i) => `src_x${i}`)),
    );
    await expect(followSource(firstKey())).rejects.toThrow('最多关注 200 个来源');
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '[]')).toHaveLength(200);
  });

  it('目录按频道过滤', async () => {
    const all = await fetchSourceDirectory();
    const channel = all[0].channel;
    const filtered = await fetchSourceDirectory(channel);
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.every((s) => s.channel === channel)).toBe(true);
  });
});

describe('api/follows 真后端分支', () => {
  beforeEach(() => {
    configState.mock = false;
  });

  it('目录走 GET /sources（不带 channel 时不传查询参数）', async () => {
    request.mockResolvedValueOnce({
      list: [{ key: 'src_1', name: '新华社', channel: 'tech', newsCount: 3 }],
    });
    const list = await fetchSourceDirectory();
    const [path, opts] = request.mock.calls[0] as [string, { method?: string; query?: unknown }];
    expect(path).toBe('/sources');
    expect(opts.query).toEqual({ channel: undefined });
    expect(list[0]).toMatchObject({ key: 'src_1', name: '新华社', enabled: true, newsCount: 3 });
  });

  it('关注走 POST /me/follows，body 是驼峰 sourceKey', async () => {
    request.mockResolvedValueOnce({ keys: ['src_1'], list: [] });
    await followSource('src_1');
    const [path, opts] = request.mock.calls[0] as [
      string,
      { method: string; body: { sourceKey: string } },
    ];
    expect(path).toBe('/me/follows');
    expect(opts.method).toBe('POST');
    expect(opts.body).toEqual({ sourceKey: 'src_1' });
  });

  it('取关走 DELETE /me/follows/{key}，key 做 URL 编码', async () => {
    request.mockResolvedValueOnce({ keys: [], list: [] });
    await unfollowSource('src a/b');
    const [path, opts] = request.mock.calls[0] as [string, { method: string }];
    expect(path).toBe('/me/follows/src%20a%2Fb');
    expect(opts.method).toBe('DELETE');
  });

  it('响应缺字段时归一到安全默认值（不能留下 undefined 让视图崩）', async () => {
    request.mockResolvedValueOnce(null);
    const payload = await fetchFollows();
    expect(payload).toEqual({ keys: [], list: [] });
  });
});
