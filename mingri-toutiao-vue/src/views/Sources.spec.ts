import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import type { SourceItem } from '@/api/follows';

/**
 * 来源目录页（E6b）视图层。
 *
 * 重点验三件事：已关注的排在最前（管理比发现常用）、关注/取关按钮态由 store 派生、
 * 未登录不给静默失败（引导去登录）。
 */

const sources: SourceItem[] = [
  { key: 'src_1', name: '科技日报', channel: 'tech', enabled: true, newsCount: 2 },
  { key: 'src_2', name: '体育晨报', channel: 'sports', enabled: true, newsCount: 9 },
  { key: 'src_3', name: '停用源', channel: 'finance', enabled: false, newsCount: 0 },
];

const follow = vi.fn(async () => true);
const unfollow = vi.fn(async () => true);
const followsState = {
  sources: [] as SourceItem[],
  keys: [] as string[],
  loaded: true,
  loading: false,
  error: '',
  loadDirectory: vi.fn(async () => undefined),
  isFollowing: (key?: string) => followsState.keys.includes(key ?? ''),
  follow,
  unfollow,
};

vi.mock('@/stores/follows', () => ({ useFollowsStore: () => followsState }));

const userState = { isLoggedIn: true };
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush, back: vi.fn() }) }));

import Sources from './Sources.vue';

const mountPage = () => mount(Sources, { global: { stubs: { AppNav: true, EmptyState: true } } });

const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const rows = (w: ReturnType<typeof mountPage>) =>
  w.findAll('.sources__row').map((r) => ({
    name: r.find('.sources__name').text(),
    label: r.find('button').text(),
  }));

beforeEach(() => {
  followsState.sources = [...sources];
  followsState.keys = [];
  userState.isLoggedIn = true;
  vi.clearAllMocks();
});

describe('views/Sources 来源目录（E6b）', () => {
  it('按「已关注优先 → 内容多优先」排序（管理关注比发现更常用）', async () => {
    followsState.keys = ['src_1'];
    const w = mountPage();
    await settle(w);
    expect(rows(w).map((r) => r.name)).toEqual(['科技日报', '体育晨报', '停用源']);
  });

  it('未关注的排后面，同状态内按内容数倒序', async () => {
    const w = mountPage();
    await settle(w);
    expect(rows(w).map((r) => r.name)).toEqual(['体育晨报', '科技日报', '停用源']);
  });

  it('按钮态由 store 派生：已关注显示「已关注」', async () => {
    followsState.keys = ['src_1'];
    const w = mountPage();
    await settle(w);
    expect(rows(w)[0].label).toBe('已关注');
    expect(rows(w)[1].label).toBe('关注');
  });

  it('点关注/取关都交给 store（页面不做本地状态）', async () => {
    const w = mountPage();
    await settle(w);
    await w.findAll('.sources__row button')[0].trigger('click');
    await settle(w);
    expect(follow).toHaveBeenCalledWith('src_2');

    followsState.keys = ['src_2'];
    const w2 = mountPage();
    await settle(w2);
    await w2.findAll('.sources__row button')[0].trigger('click');
    await settle(w2);
    expect(unfollow).toHaveBeenCalledWith('src_2');
  });

  it('未登录点关注：引导去登录（不静默失败）', async () => {
    userState.isLoggedIn = false;
    const w = mountPage();
    await settle(w);
    await w.findAll('.sources__row button')[0].trigger('click');
    await settle(w);
    expect(routerPush).toHaveBeenCalledWith('/login');
    expect(follow).not.toHaveBeenCalled();
  });

  it('关注失败时把原因显示出来', async () => {
    follow.mockResolvedValueOnce(false);
    followsState.error = '最多关注 200 个来源';
    const w = mountPage();
    await settle(w);
    await w.findAll('.sources__row button')[0].trigger('click');
    await settle(w);
    expect(w.find('.sources__error').text()).toContain('最多关注');
  });

  it('频道筛选只留该频道', async () => {
    const w = mountPage();
    await settle(w);
    const chip = w.findAll('.sources__chip').find((c) => c.text() === '体育');
    await chip!.trigger('click');
    await settle(w);
    expect(rows(w).map((r) => r.name)).toEqual(['体育晨报']);
  });
});
