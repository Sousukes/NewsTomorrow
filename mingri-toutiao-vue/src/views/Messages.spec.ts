import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { reactive } from 'vue';

/**
 * 消息列表（F3'）。
 *
 * 三条值得钉的行为：
 * 1. **进页面就拉取**（不拉的话列表永远是上次的内存快照）；
 * 2. **点消息先标已读再跳转**，且没有 `newsId` 的系统消息**只标已读、不跳**；
 * 3. 骨架屏只在「首次加载且列表为空」时出现——已有数据时闪骨架会让人以为刷掉了。
 */

const store = reactive({
  list: [] as {
    id: string;
    type: string;
    title: string;
    newsId?: string;
    time: string;
    read: boolean;
  }[],
  loading: false,
  error: '',
  unreadCount: 0,
  fetch: vi.fn(async () => undefined),
  markRead: vi.fn(async () => undefined),
  markAllRead: vi.fn(async () => undefined),
});
vi.mock('@/stores/messages', () => ({ useMessagesStore: () => store }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import Messages from '@/views/Messages.vue';

const mountPage = () => mount(Messages, { global: { stubs: { AppNav: true, Button: true } } });
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};
const vm = (w: ReturnType<typeof mountPage>) =>
  w.vm as unknown as { open: (m: { id: string; newsId?: string }) => Promise<void> };

beforeEach(() => {
  vi.clearAllMocks();
  store.list = [];
  store.loading = false;
  store.error = '';
  store.unreadCount = 0;
});

describe('Messages', () => {
  it('挂载即拉取', async () => {
    mountPage();
    expect(store.fetch).toHaveBeenCalledTimes(1);
  });

  it('有未读时显示条数与「全部已读」入口', async () => {
    store.unreadCount = 2;
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('2 条未读');
    const all = w.findAll('button').find((b) => b.text().includes('全部已读'));
    expect(all).toBeTruthy();
    await all!.trigger('click');
    expect(store.markAllRead).toHaveBeenCalled();
  });

  it('点带资讯的消息：先标已读再跳详情', async () => {
    const w = mountPage();
    await vm(w).open({ id: 'm1', newsId: 'n9' });
    expect(store.markRead).toHaveBeenCalledWith('m1');
    expect(routerPush).toHaveBeenCalledWith('/news/n9');
    expect(store.markRead.mock.invocationCallOrder[0]).toBeLessThan(
      routerPush.mock.invocationCallOrder[0],
    );
  });

  it('点系统消息（无 newsId）：只标已读，不跳转', async () => {
    const w = mountPage();
    await vm(w).open({ id: 'm2' });
    expect(store.markRead).toHaveBeenCalledWith('m2');
    expect(routerPush).not.toHaveBeenCalled();
  });

  it('首次加载且无数据才出骨架屏；已有数据时不闪骨架', async () => {
    store.loading = true;
    const empty = mountPage();
    await settle(empty);
    expect(empty.findAll('.msg--skeleton').length).toBeGreaterThan(0);

    store.list = [{ id: 'm1', type: 'system', title: '有数据', time: '刚刚', read: false }];
    const filled = mountPage();
    await settle(filled);
    expect(filled.findAll('.msg--skeleton')).toHaveLength(0);
    expect(filled.text()).toContain('有数据');
  });

  it('错误提示要显示出来（否则用户只看到空列表）', async () => {
    store.error = '加载失败';
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('加载失败');
  });
});
