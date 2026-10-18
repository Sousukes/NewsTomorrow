import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { reactive } from 'vue';

/**
 * 视频页（F3'）。
 *
 * 这一页的价值全在**桶隔离**（A4）：它固定用 `video` 频道、与首页互不覆盖。
 * 视图层要钉的是三条：
 * 1. 挂载时**先 resetPaging 再 fetch**（顺序反了会把新游标又清掉）；
 * 2. 只碰 `video` 桶，绝不碰首页的桶；
 * 3. 子分类（直播/影视/搞笑）**只切换高亮**，不触发重新拉取
 *    ——它们不是资讯频道，后端没有对应 channel。
 */

const bucket = reactive({ list: [] as { id: string }[], loading: false, hasMore: false });
const newsState = {
  resetPaging: vi.fn(),
  fetchNews: vi.fn(async (_c: string, _s: string) => undefined),
  loadMore: vi.fn(async (_c: string, _s: string) => 0),
  bucketOf: vi.fn(() => bucket),
};
vi.mock('@/stores/news', () => ({ useNewsStore: () => newsState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import Videos from '@/views/Videos.vue';

const mountPage = () => mount(Videos, { global: { stubs: { AppNav: true } } });
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  bucket.list = [];
});

describe('Videos', () => {
  it('挂载时先清游标再拉取，且只针对 video 频道', async () => {
    mountPage();
    await settle(mountPage());
    const [channel, sort] = newsState.fetchNews.mock.calls[0] as [string, string];
    expect(channel).toBe('video');
    expect(sort).toBeTruthy();
    expect(newsState.resetPaging.mock.invocationCallOrder[0]).toBeLessThan(
      newsState.fetchNews.mock.invocationCallOrder[0],
    );
  });

  it('只读 video 桶（桶隔离：首页列表状态不受影响）', async () => {
    mountPage();
    expect(newsState.bucketOf).toHaveBeenCalledWith('video', expect.anything());
  });

  it('子分类只切高亮，不重新拉取（它们不是资讯频道）', async () => {
    const w = mountPage();
    await settle(w);
    vi.clearAllMocks();
    const tabs = w.findAll('.tabs button, button');
    const live = tabs.find((b) => b.text().includes('直播'));
    if (live) await live.trigger('click');
    expect(newsState.fetchNews).not.toHaveBeenCalled();
  });

  it('列表为空时不渲染卡片、也不崩（**当前没有空态 UI，已记为债**）', async () => {
    const w = mountPage();
    await settle(w);
    // 现状：只剩标签栏。补空态属于设计决定，这里不代替产品做，只在 backlog 记账。
    expect(w.findAll('.video__card, .card').length).toBe(0);
    expect(w.text()).toContain('推荐');
  });
});
