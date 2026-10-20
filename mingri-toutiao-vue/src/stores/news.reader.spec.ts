import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { readReader, readerKey, writeReader } from '@/utils/reader';

/**
 * news store 的阅读副本接线（E8 / P1-79）。
 *
 * 三条契约：
 * 1. **读成功即写穿**（离线时有东西可看）；
 * 2. **读失败回退到副本**并打 `fromCache`（UI 据此标注"离线内容"——
 *    静默展示旧内容等于骗人）；
 * 3. **mock 模式不落盘**：种子数据不是用户真实读到的东西，
 *    落成本地档案只会污染副本。
 */

const fetchNewsById = vi.fn();
vi.mock('@/api/news', () => ({
  fetchNewsById: (id: string) => fetchNewsById(id),
  fetchNewsPage: vi.fn(async () => ({ list: [], total: 0, page: 1, pageSize: 20, hasMore: false })),
  searchNews: vi.fn(async () => ({ list: [], total: 0, page: 1, keyword: '' })),
}));
// 非 mock 模式：否则写穿分支会被跳过
vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

import { useNewsStore } from './news';

const item = (id: string, content: string) => ({
  id,
  title: `标题 ${id}`,
  source: '源',
  source_key: 'src_1',
  channel: 'recommend',
  time: '1小时前',
  commentCount: 0,
  content,
});

const SCOPE = readerKey(null); // 未登录（prefs 无 userId）

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  vi.clearAllMocks();
  fetchNewsById.mockResolvedValue(item('r_n1', '正文内容'));
});

describe('stores/news 阅读副本', () => {
  it('详情读成功后写穿到本地副本', async () => {
    const store = useNewsStore();
    await store.loadDetail('r_n1');
    const saved = readReader(SCOPE, 'r_n1');
    expect(saved).not.toBeNull();
    expect(saved).toMatchObject({ id: 'r_n1', content: '正文内容' });
  });

  it('读失败回退到副本并标记 fromCache（UI 要能区分离线内容）', async () => {
    await useNewsStore().loadDetail('r_n1'); // 先写穿一份副本

    // **重建 pinia 模拟冷启动**：同一次会话内内存缓存会先命中，
    // 走不到回落分支；而断网可读的真实场景恰恰是"重启页面后内存已空"。
    setActivePinia(createPinia());
    fetchNewsById.mockRejectedValue(new Error('断网'));

    const got = await useNewsStore().loadDetail('r_n1');
    expect(got).not.toBeNull();
    expect(got?.fromCache).toBe(true);
    expect(got?.content).toBe('正文内容');
  });

  it('副本也没有时返回 null（调用方走自己的失败态）', async () => {
    const store = useNewsStore();
    fetchNewsById.mockRejectedValueOnce(new Error('断网'));
    await expect(store.loadDetail('ghost')).resolves.toBeNull();
  });

  it('offlineDetail 不依赖内存桶（重启页面后内存已空）', async () => {
    // 直接写一份副本，不用先加载
    writeReader(SCOPE, 'r_n9', item('r_n9', '冷启动内容'));
    const store = useNewsStore();
    expect(store.getById('r_n9')).toBeNull(); // 内存里没有
    const got = store.offlineDetail('r_n9');
    expect(got).toMatchObject({ id: 'r_n9', fromCache: true });
  });

  it('readerIds 列出当前作用域的副本（供收藏/历史页离线排序）', async () => {
    writeReader(SCOPE, 'a', item('a', 'x'));
    writeReader(readerKey(7), 'b', item('b', 'x')); // 别的账号，不该出现
    const store = useNewsStore();
    expect(store.readerIds()).toEqual(['a']);
  });
});
