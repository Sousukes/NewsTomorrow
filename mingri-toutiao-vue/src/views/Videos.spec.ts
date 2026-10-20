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

  /**
   * 空态（P1-85）。
   *
   * 原先这一页只有骨架屏与列表，**没有空态**；而 video 频道当前恒为空
   * （P1-84 已量证）——也就是说进入这个 Tab 的人**每次**都只看到一片空白，
   * 像页面坏了。这条债此前只写在本文件的注释里（"已记为债"），
   * 却没有上账到 backlog —— 正是"新债当天上账"没做到的一例。
   */
  it('列表为空时给出空态，而不是一片空白', async () => {
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('暂无视频内容');
    expect(w.text()).toContain('视频频道还没有内容');
  });

  it('空态的「去看推荐」跳首页（给一个出口，而不是把人留在空白页）', async () => {
    const w = mountPage();
    await settle(w);
    const btn = w.findAll('button').find((b) => b.text().includes('去看推荐'))!;
    expect(btn.exists()).toBe(true);
    await btn.trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/');
  });

  it('有数据时不显示空态（别把有内容误报成空）', async () => {
    bucket.list = [{ id: 'n1' }];
    const w = mountPage();
    await settle(w);
    expect(w.text()).not.toContain('暂无视频内容');
  });

  it('加载中不显示空态（骨架屏已经在表达"等一下"）', async () => {
    bucket.loading = true;
    const w = mountPage();
    await settle(w);
    expect(w.text()).not.toContain('暂无视频内容');
    bucket.loading = false;
  });

  it('请求失败时不显示空态（错误文案更该被看见）', async () => {
    Object.assign(bucket, { error: '网络异常', loading: false });
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络异常');
    expect(w.text()).not.toContain('暂无视频内容');
    delete (bucket as Record<string, unknown>).error;
  });
});
