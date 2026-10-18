import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 个人标记 API 层（`api/me.ts`）——此前 18.9% 覆盖。
 *
 * 这层有两件容易错的事：
 * 1. **方法语义**：合并是 `PUT`（幂等只增不删）、取消收藏是 `DELETE /:id`——
 *    方法或路径写错，后端会静默 404，用户只看到"收藏没反应"；
 * 2. **响应归一**：`normalizePayload` 对缺失字段兜底，否则 store 拿到 `undefined`
 *    一 map 就崩。
 */

vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

const request = vi.fn();
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  mockDelay: async (v: unknown) => v,
}));
vi.mock('@/api/news', () => ({
  normalizeNewsItem: (r: Record<string, unknown>) => ({ id: r.id, title: r.title ?? '' }),
}));

import {
  addFavorite,
  clearHistoryRemote,
  fetchFavorites,
  mergeFavorites,
  removeFavorite,
} from '@/api/me';

beforeEach(() => {
  vi.clearAllMocks();
});

const raw = { id: 'n1', title: '标题' };
const payload = { ids: ['n1'], list: [raw] };

describe('收藏', () => {
  it('取收藏走 GET /me/favorites', async () => {
    request.mockResolvedValueOnce(payload);
    const out = await fetchFavorites();
    expect(request.mock.calls[0][0]).toBe('/me/favorites');
    expect(out.ids).toEqual(['n1']);
  });

  it('收藏是 POST（幂等），取消是 DELETE /:id', async () => {
    request.mockResolvedValue(payload);
    await addFavorite('n1');
    expect(request.mock.calls[0]).toEqual([
      '/me/favorites',
      { method: 'POST', body: { newsId: 'n1' } },
    ]);

    await removeFavorite('n1');
    expect(request.mock.calls[1][0]).toBe('/me/favorites/n1');
    expect(request.mock.calls[1][1]).toMatchObject({ method: 'DELETE' });
  });

  it('合并是 PUT，且传 ids（只增不删的语义）', async () => {
    request.mockResolvedValueOnce(payload);
    await mergeFavorites(['n1', 'n2']);
    expect(request.mock.calls[0]).toEqual([
      '/me/favorites',
      { method: 'PUT', body: { ids: ['n1', 'n2'] } },
    ]);
  });

  it('响应缺字段时归一为空（store 拿到 undefined 会崩）', async () => {
    request.mockResolvedValueOnce(null);
    const out = await fetchFavorites();
    expect(out).toEqual({ ids: [], list: [] });
  });
});

describe('浏览历史', () => {
  it('清空是 DELETE /me/history（不是 POST）', async () => {
    request.mockResolvedValueOnce(payload);
    await clearHistoryRemote();
    expect(request.mock.calls[0]).toEqual(['/me/history', { method: 'DELETE' }]);
  });
});
