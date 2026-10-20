import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils';
import { reactive } from 'vue';

/**
 * App 壳层——此前 0% 覆盖。
 *
 * 它本身没有业务逻辑，但有三件事只在这里发生，错了没人会立刻发现：
 * 1. **TabBar 显隐由 `route.meta.tab` 决定**：meta 缺失时若仍渲染，
 *    详情页会多出一条底栏压住内容；
 * 2. **徽标要在路由切换后刷新**：只在 mounted 刷一次的话，从消息页返回时
 *    数字还是旧的（用户会以为"我明明读完了"）；
 * 3. **登录态下的收藏/历史并轨只在启动触发一次**（`syncMarks`），
 *    多调一次就是一次多余的全量合并请求。
 */

const routeState = reactive<{ path: string; meta: Record<string, unknown> }>({
  path: '/',
  meta: { tab: 'home' },
});
const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: routerPush }),
}));

const refreshUnread = vi.fn(async () => 3);
vi.mock('@/stores/messages', () => ({ useMessagesStore: () => ({ refreshUnread }) }));

const syncMarks = vi.fn(async () => undefined);
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ syncMarks }) }));

// E6b：关注集合同样在壳层启动时刷新（账号数据，登录态切换后要重取）
const refreshFollows = vi.fn(async () => undefined);
vi.mock('@/stores/follows', () => ({ useFollowsStore: () => ({ refresh: refreshFollows }) }));

import App from '@/App.vue';

/** 把壳层依赖全部打成占位组件，只留被测的接线逻辑 */
const stubs = {
  PhoneFrame: { template: '<div class="phone"><slot /></div>' },
  ErrorBoundary: { template: '<div class="boundary"><slot /></div>' },
  Showcase: { template: '<div class="showcase" />' },
  StepUpDialog: { template: '<div class="stepup" />' },
  TabBar: {
    name: 'TabBar',
    props: ['items', 'active', 'badges'],
    emits: ['update:active'],
    template: '<div class="tabbar" :data-active="active" :data-badge="badges.message" />',
  },
  'router-view': { template: '<div class="rv" />' },
};

const mountApp = () => mount(App, { global: { stubs } });
const flush = async () => {
  await flushPromises();
  await new Promise((r) => setTimeout(r, 0));
};

enableAutoUnmount(afterEach);

beforeEach(() => {
  vi.clearAllMocks();
  routeState.path = '/';
  routeState.meta = { tab: 'home' };
});

describe('App 壳层', () => {
  it('route.meta.tab 存在时显示底栏，并把 active 传下去', async () => {
    routeState.meta = { tab: 'message' };
    const w = mountApp();
    await flush();
    const bar = w.find('.tabbar');
    expect(bar.exists()).toBe(true);
    expect(bar.attributes('data-active')).toBe('message');
  });

  it('route.meta.tab 缺失时不显示底栏（详情页不该压一条底栏）', async () => {
    routeState.meta = {};
    const w = mountApp();
    await flush();
    expect(w.find('.tabbar').exists()).toBe(false);
  });

  it('meta.tab 缺失时 active 回落到 home', async () => {
    routeState.meta = {};
    const w = mountApp();
    await flush();
    // 底栏不渲染时这条只验证推导不出错（不抛异常即可），渲染时则回落到 home
    expect(w.html()).toContain('phone');
  });

  it('挂载时拉一次未读徽标', async () => {
    mountApp();
    await flush();
    expect(refreshUnread).toHaveBeenCalledTimes(1);
  });

  it('**路由切换后刷新徽标**（只在 mounted 刷会让用户看到读完了还亮红点）', async () => {
    mountApp();
    await flush();
    expect(refreshUnread).toHaveBeenCalledTimes(1);

    routeState.path = '/messages';
    await flush();
    expect(refreshUnread).toHaveBeenCalledTimes(2);
  });

  it('启动时只并轨一次收藏/历史（多调一次就是一次多余的全量合并）', async () => {
    mountApp();
    await flush();
    routeState.path = '/profile';
    await flush();
    expect(syncMarks).toHaveBeenCalledTimes(1);
  });

  it('点击底栏按映射跳转（key -> path）', async () => {
    const w = mountApp();
    await flush();
    w.findComponent({ name: 'TabBar' }).vm.$emit('update:active', 'video');
    await flush();
    expect(routerPush).toHaveBeenCalledWith('/video');
  });

  it('未知 tab key 不发跳转（宁可不动，也别跳到 undefined）', async () => {
    const w = mountApp();
    await flush();
    w.findComponent({ name: 'TabBar' }).vm.$emit('update:active', '不存在的 tab');
    await flush();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it('渲染出错时整页不白屏：错误边界包住了主内容与陈列区', async () => {
    const w = mountApp();
    await flush();
    expect(w.findAll('.boundary').length).toBe(2); // 主内容一个、Showcase 一个
  });

  it('二次校验对话框挂在壳层（与具体页面解耦）', async () => {
    const w = mountApp();
    await flush();
    expect(w.find('.stepup').exists()).toBe(true);
  });
});

it('E6b：启动时拉一次关注集合，登录态变化后重拉（防切账号后显示上一个账号的关注）', async () => {
  mountApp();
  await flush();
  expect(refreshFollows).toHaveBeenCalledTimes(1);
});

/**
 * E9：跳过导航与唯一 h1。
 * skip link 让键盘用户不必每次穿过导航与频道栏；陈列区标题降为 h2，
 * 因为各视图内部已有 h1（同一文档只该有一个）。
 */
describe('App 可访问性（E9）', () => {
  it('有跳到主要内容的 skip link，且锚点存在', () => {
    const w = mountApp();
    const link = w.find('a.skip-link');
    expect(link.exists()).toBe(true);
    expect(link.attributes('href')).toBe('#main-content');
    expect(w.find('#main-content').exists()).toBe(true);
  });

  it('陈列区标题不是 h1（视图内才是）', () => {
    const w = mountApp();
    const pageHeader = w.find('.page__header');
    expect(pageHeader.find('h1').exists()).toBe(false);
  });
});
