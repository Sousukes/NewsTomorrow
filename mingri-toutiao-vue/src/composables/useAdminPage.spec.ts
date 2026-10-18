import { describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';

import { messageOf, useAdminList } from '@/composables/useAdminPage';

type Item = { id: string };

function page(items: Item[], extra: Record<string, unknown> = {}) {
  return { list: items, ...extra } as never;
}

describe('useAdminList', () => {
  it('首屏写入列表、总数与游标', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }, { id: 'b' }], { total: 2, nextCursor: 'c1' }));
    expect(s.list.value.map((x) => x.id)).toEqual(['a', 'b']);
    expect(s.total.value).toBe(2);
    expect(s.nextCursor.value).toBe('c1');
    expect(s.hasMore.value).toBe(false);
    expect(s.loading.value).toBe(false);
  });

  it('续页追加且按 id 去重', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { total: 3, hasMore: true, nextCursor: 'c1' }));
    await s.loadMore(async () =>
      page([{ id: 'a' }, { id: 'b' }], { hasMore: true, nextCursor: 'c2' }),
    );
    expect(s.list.value.map((x) => x.id)).toEqual(['a', 'b']);
    expect(s.nextCursor.value).toBe('c2');
  });

  it('reload 接管 loadingMore（否则过期续页会把翻页卡死）', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { hasMore: true, nextCursor: 'c1' }));
    const slow = s.loadMore(
      async () =>
        new Promise((r) => setTimeout(() => r(page([{ id: 'b' }], { nextCursor: 'c2' })), 20)),
    );
    // 重载在途：必须先把 loadingMore 接管掉
    await s.reload(async () => page([{ id: 'z' }]));
    expect(s.loadingMore.value).toBe(false);
    await slow;
    expect(s.loadingMore.value).toBe(false);
  });

  it('reload 会作废在途的 loadMore（代次守卫）', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { hasMore: true, nextCursor: 'c1' }));

    const slow = ref(false);
    const p = s.loadMore(async () => {
      slow.value = true;
      await new Promise((r) => setTimeout(r, 20));
      return page([{ id: 'b' }], { hasMore: true, nextCursor: 'c2' });
    });
    // 中途换筛选条件：新的一页先到
    await s.reload(async () => page([{ id: 'z' }], { hasMore: false }));
    expect(s.list.value.map((x) => x.id)).toEqual(['z']);
    await p;
    // 旧续页回来后不得追加、不得覆盖游标
    expect(s.list.value.map((x) => x.id)).toEqual(['z']);
    expect(s.nextCursor.value).toBe(null);
    expect(s.loadingMore.value).toBe(false);
  });

  it('过期请求不得清掉新请求的 loading 标志', async () => {
    const s = useAdminList<Item>();
    const first = s.reload(
      async () =>
        new Promise((resolve) => setTimeout(() => resolve(page([{ id: 'slow' }])), 30)) as never,
    );
    const second = s.reload(async () => page([{ id: 'fast' }]));
    await Promise.all([first, second]);
    expect(s.loading.value).toBe(false);
    expect(s.list.value.map((x) => x.id)).toEqual(['fast']);
  });

  it('失败写入 error 并把异常继续抛给调用方', async () => {
    const s = useAdminList<Item>();
    await expect(
      s.reload(async () => {
        throw new Error('后端炸了');
      }),
    ).rejects.toThrow('后端炸了');
    expect(s.error.value).toBe('后端炸了');
    expect(s.loading.value).toBe(false);
  });

  it('hasMore 为 false 或无游标时不发续页请求', async () => {
    const s = useAdminList<Item>();
    const fetchMore = vi.fn(async () => page([{ id: 'b' }]));
    await s.reload(async () => page([{ id: 'a' }], { hasMore: false }));
    await s.loadMore(fetchMore);
    expect(fetchMore).not.toHaveBeenCalled();
  });

  it('游标原地不动即停止（否则无限翻页）', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { hasMore: true, nextCursor: 'c1' }));
    // 服务端把同一个游标又发了回来：没有任何推进手段
    await s.loadMore(async () => page([{ id: 'b' }], { hasMore: true, nextCursor: 'c1' }));
    expect(s.hasMore.value).toBe(false);
    expect(s.list.value.map((x) => x.id)).toEqual(['a', 'b']);
  });

  it('整页都是重复项且无新游标时停止翻页', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { hasMore: true, nextCursor: 'c1' }));
    await s.loadMore(async () => page([{ id: 'a' }], { hasMore: true }));
    expect(s.hasMore.value).toBe(false);
  });

  it('resetPaging 清游标与总数', async () => {
    const s = useAdminList<Item>();
    await s.reload(async () => page([{ id: 'a' }], { total: 9, hasMore: true, nextCursor: 'c1' }));
    s.resetPaging();
    expect(s.nextCursor.value).toBe(null);
    // total 是 number（初始 0），与迁移前各页 `ref(0)` 行为一致
    expect(s.total.value).toBe(0);
    expect(s.hasMore.value).toBe(false);
  });
});

describe('messageOf', () => {
  it('取 Error 的 message，否则回落文案', () => {
    expect(messageOf(new Error('具体原因'), '兜底')).toBe('具体原因');
    expect(messageOf('字符串', '兜底')).toBe('兜底');
    expect(messageOf(null, '兜底')).toBe('兜底');
  });
});
