import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { Pinia } from 'pinia';

vi.mock('@/api/me', () => ({
  MARKS_SYNC_SUPPORTED: true,
  fetchFavorites: vi.fn(),
  addFavorite: vi.fn(),
  removeFavorite: vi.fn(),
  mergeFavorites: vi.fn(),
  fetchHistory: vi.fn(),
  recordHistory: vi.fn(),
  clearHistoryRemote: vi.fn(),
  mergeHistory: vi.fn(),
}));

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  changePassword: vi.fn(),
}));

vi.mock('@/api/news', () => ({
  fetchNews: vi.fn(),
  fetchNewsPage: vi.fn(),
  fetchNewsById: vi.fn(),
  searchNews: vi.fn(),
  normalizeNewsItem: vi.fn((raw: Record<string, unknown>) => raw),
}));

// E3-a：登录时要把匿名期曝光推送服务端。不打桩就会真的发请求
vi.mock('@/api/feedback', () => ({
  reportExposures: vi.fn(async (_ids: string[]) => ({ accepted: 0, written: 0 })),
}));

import * as authApi from '@/api/auth';
import type { LoginResult } from '@/api/auth';
import * as feedbackApi from '@/api/feedback';
import * as meApi from '@/api/me';
import { useNewsStore } from '@/stores/news';
import {
  EXPOSURE_CAP,
  EXPOSURE_RETENTION_DAYS,
  HISTORY_LIMIT,
  SEARCH_HISTORY_LIMIT as SEARCH_HISTORY_LIMIT_,
  useUserStore,
} from '@/stores/user';
import type { NewsItem } from '@/types';

const PREFS_KEY = 'mingri-toutiao:user';
const marksKey = (scope: string) => `mingri-toutiao:marks:${scope}`;
const exposuresKey = (scope: string) => `mingri-toutiao:exposures:${scope}`;
const DAY_MS = 24 * 60 * 60 * 1000;

const item = (id: string): NewsItem => ({
  id,
  title: `标题${id}`,
  source: '源',
  time: '刚刚',
  channel: 'recommend',
  variant: 'text',
});

const payload = (ids: string[]) => ({ ids, list: ids.map(item) });
const emptyPayload = { ids: [], list: [] };
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * 测试隔离：store 的 watcher 会在后续用例的 tick 中回写 localStorage，
 * 若不销毁，前序用例的实例会污染当前用例（曾导致"匿名数据被清空"的假失败）。
 * 因此统一经 `makeStore` 创建，并在 afterEach 里 `$dispose()`。
 */
let pinia: Pinia;
const liveStores: { $dispose: () => void }[] = [];

function makeStore() {
  const s = useUserStore();
  liveStores.push(s);
  return s;
}

function makeNewsStore() {
  const s = useNewsStore();
  liveStores.push(s);
  return s;
}

/** 完成登录（默认合并结果为空，便于各用例自行覆盖） */
async function loginAs(userId = 1) {
  vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: userId, name: '用户' } });
  vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
  vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
  const s = makeStore();
  await s.login('13800138000', '123456');
  return s;
}

describe('stores/user 收藏与历史云端同步', () => {
  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.resetAllMocks();
  });

  afterEach(() => {
    // 停掉所有 watcher，避免跨用例回写 localStorage
    for (const s of liveStores) s.$dispose();
    liveStores.length = 0;
  });

  describe('同步触发条件', () => {
    it('未登录时不发起同步（离线可用）', async () => {
      const s = makeStore();
      await s.syncMarks();
      expect(meApi.mergeFavorites).not.toHaveBeenCalled();
      expect(meApi.mergeHistory).not.toHaveBeenCalled();
    });

    it('登录响应缺少 id 时不发起同步（无法确定作用域）', async () => {
      vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { name: '用户' } });
      const s = makeStore();
      await s.login('13800138000', '123456');
      expect(s.isLoggedIn).toBe(true);
      expect(s.userId).toBeNull();
      expect(meApi.mergeFavorites).not.toHaveBeenCalled();
    });

    it('登录后把本地标记并入云端，并以服务端结果为准', async () => {
      vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 1, name: '用户' } });
      vi.mocked(meApi.mergeFavorites).mockResolvedValue(payload(['a', 'b']));
      vi.mocked(meApi.mergeHistory).mockResolvedValue(payload(['c']));

      const s = makeStore();
      s.favorites = ['a']; // 登录前的本地收藏
      await s.login('13800138000', '123456');

      expect(meApi.mergeFavorites).toHaveBeenCalledWith(['a']);
      expect(s.favorites).toEqual(['a', 'b']);
      expect(s.history).toEqual(['c']);
      expect(s.isLoggedIn).toBe(true);
      expect(s.userId).toBe(1);
    });

    it('合并返回的资讯写入 news store 缓存（新设备可直接渲染收藏页）', async () => {
      vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 1, name: '用户' } });
      vi.mocked(meApi.mergeFavorites).mockResolvedValue(payload(['remote-1']));
      vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

      const s = makeStore();
      await s.login('13800138000', '123456');

      expect(makeNewsStore().getById('remote-1')?.title).toBe('标题remote-1');
    });

    it('同步失败不阻断登录，仅记录错误', async () => {
      vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 1, name: '用户' } });
      vi.mocked(meApi.mergeFavorites).mockRejectedValue(new Error('网络错误'));
      vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

      const s = makeStore();
      await s.login('13800138000', '123456');

      expect(s.isLoggedIn).toBe(true);
      expect(s.syncError).toBe('网络错误');
      expect(s.syncing).toBe(false);
    });
  });

  describe('作用域隔离（按 userId 命名空间）', () => {
    it('未登录时的收藏落在匿名作用域', async () => {
      const s = makeStore();
      s.toggleFavorite('anon-1');
      await flush();

      expect(s.favorites).toEqual(['anon-1']);
      expect(localStorage.getItem(marksKey('anonymous'))).toContain('anon-1');
      expect(meApi.addFavorite).not.toHaveBeenCalled();
    });

    it('登录时采纳匿名期数据（匿名作用域随后清空）', async () => {
      const s = makeStore();
      s.toggleFavorite('anon-1');
      await flush();

      vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 7, name: '用户' } });
      vi.mocked(meApi.mergeFavorites).mockImplementation(async (ids: string[]) => ({
        ids,
        list: [],
      }));
      vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

      await s.login('13800138000', '123456');

      expect(s.favorites).toEqual(['anon-1']);
      expect(meApi.mergeFavorites).toHaveBeenCalledWith(['anon-1']);
      // 匿名作用域已清空（数据归属该账号，留着会重复计数）
      expect(localStorage.getItem(marksKey('anonymous'))).toBeNull();
      expect(localStorage.getItem(marksKey('u7'))).toContain('anon-1');
    });

    it('登出切回匿名作用域，账号数据保留在本地（重新登录即恢复）', async () => {
      const s = await loginAs(1);
      s.favorites = ['a'];
      s.history = ['b'];
      vi.mocked(authApi.logout).mockResolvedValue(undefined);

      await s.logout();

      expect(s.isLoggedIn).toBe(false);
      expect(s.userId).toBeNull();
      expect(s.favorites).toEqual([]); // 匿名作用域为空
      expect(localStorage.getItem(marksKey('u1'))).toContain('a'); // 账号数据未销毁

      // 重新登录同一账号：本地缓存被采纳，服务端合并回同样结果
      vi.mocked(meApi.mergeFavorites).mockImplementation(async (ids: string[]) => ({
        ids,
        list: [],
      }));
      vi.mocked(meApi.mergeHistory).mockImplementation(async (ids: string[]) => ({
        ids,
        list: [],
      }));
      await s.login('13800138000', '123456');
      expect(s.favorites).toEqual(['a']);
      expect(s.history).toEqual(['b']);
    });

    it('不同账号的本地缓存互不可见（防串号）', async () => {
      const s = await loginAs(1);
      s.favorites = ['secret-of-1'];
      vi.mocked(authApi.logout).mockResolvedValue(undefined);
      await s.logout();

      vi.mocked(authApi.login).mockResolvedValue({ token: 't2', user: { id: 2, name: '乙' } });
      vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
      vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
      await s.login('13900139000', '123456');

      expect(s.favorites).toEqual([]);
      expect(meApi.mergeFavorites).toHaveBeenLastCalledWith([]);
      expect(localStorage.getItem(marksKey('u1'))).toContain('secret-of-1'); // 甲的数据仍在
    });

    it('同一 tick 内「改标记 → 登出」不会把账号数据写进匿名作用域', async () => {
      const s = await loginAs(1);
      s.toggleFavorite('racy');
      vi.mocked(authApi.logout).mockResolvedValue(undefined);
      await s.logout(); // 不等待 flush，模拟用户快速点击

      const anon = localStorage.getItem(marksKey('anonymous'));
      expect(anon === null || !anon.includes('racy')).toBe(true);
      expect(localStorage.getItem(marksKey('u1'))).toContain('racy');
    });
  });

  describe('旧格式迁移', () => {
    it('标记内嵌在 user 键的旧数据迁移到匿名作用域，不丢数据', () => {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({
          isLoggedIn: false,
          favorites: ['old-1'],
          history: ['old-2'],
          theme: 'dark',
        }),
      );

      const s = makeStore();

      expect(s.favorites).toEqual(['old-1']);
      expect(s.history).toEqual(['old-2']);
      expect(s.theme).toBe('dark');
      expect(localStorage.getItem(marksKey('anonymous'))).toContain('old-1');
    });
  });

  describe('收藏', () => {
    it('本地立即生效并后台推送', async () => {
      const s = await loginAs();
      vi.mocked(meApi.addFavorite).mockResolvedValue(payload(['x']));

      s.toggleFavorite('x');
      expect(s.favorites).toContain('x'); // 乐观更新

      await flush();
      expect(meApi.addFavorite).toHaveBeenCalledWith('x');
      expect(s.favorites).toEqual(['x']);
    });

    it('取消收藏调用 removeFavorite', async () => {
      const s = await loginAs();
      s.favorites = ['x'];
      vi.mocked(meApi.removeFavorite).mockResolvedValue(emptyPayload);

      s.toggleFavorite('x');
      expect(s.favorites).toEqual([]);

      await flush();
      expect(meApi.removeFavorite).toHaveBeenCalledWith('x');
    });

    it('推送失败时本地不回滚（离线优先）', async () => {
      const s = await loginAs();
      vi.mocked(meApi.addFavorite).mockRejectedValue(new Error('网络错误'));

      s.toggleFavorite('y');
      await flush();

      expect(s.favorites).toContain('y');
      expect(s.syncError).toBe('网络错误');
    });

    it('并发推送只应用最新一次响应（序号守卫）', async () => {
      const s = await loginAs();
      let resolveFirst: (v: { ids: string[]; list: NewsItem[] }) => void = () => {};
      let resolveSecond: (v: { ids: string[]; list: NewsItem[] }) => void = () => {};
      vi.mocked(meApi.addFavorite)
        .mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)))
        .mockImplementationOnce(() => new Promise((r) => (resolveSecond = r)));

      s.toggleFavorite('p');
      s.toggleFavorite('q');

      // 后发的先返回，先发的后返回（乱序）
      resolveSecond(payload(['q', 'p']));
      await flush();
      resolveFirst(payload(['p']));
      await flush();

      expect(s.favorites).toEqual(['q', 'p']); // 未被过期响应覆盖
    });
  });

  describe('浏览历史', () => {
    it('去重、新记录置顶、同一篇重复浏览不重复请求', async () => {
      const s = makeStore(); // 未登录：纯本地行为
      s.addHistory('a');
      s.addHistory('b');
      expect(s.history).toEqual(['b', 'a']);

      s.addHistory('b'); // 已在最前 → 状态不变、不请求
      expect(s.history).toEqual(['b', 'a']);
      expect(meApi.recordHistory).not.toHaveBeenCalled();
    });

    it('超过上限时裁剪最旧记录', async () => {
      const s = makeStore();
      for (let i = 0; i < HISTORY_LIMIT + 5; i++) s.addHistory(`h${i}`);
      expect(s.history).toHaveLength(HISTORY_LIMIT);
      expect(s.history[0]).toBe(`h${HISTORY_LIMIT + 4}`);
      expect(s.history).not.toContain('h0');
    });

    it('已登录时记录浏览会推送服务端', async () => {
      const s = await loginAs();
      vi.mocked(meApi.recordHistory).mockResolvedValue(payload(['a']));
      s.addHistory('a');
      await flush();
      expect(meApi.recordHistory).toHaveBeenCalledWith('a');
    });

    it('清空：本地立即清空并推送', async () => {
      const s = await loginAs();
      vi.mocked(meApi.clearHistoryRemote).mockResolvedValue(emptyPayload);
      s.history = ['a', 'b'];

      s.clearHistory();
      expect(s.history).toEqual([]);

      await flush();
      expect(meApi.clearHistoryRemote).toHaveBeenCalled();
    });
  });
});

describe('stores/user 搜索历史', () => {
  const searchKey = (scope: string) => `mingri-toutiao:search-history:${scope}`;

  // 必须重置：store 与 localStorage 是跨用例共享的，不清会读到上一个用例的数据
  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.resetAllMocks();
  });

  afterEach(() => {
    for (const s of liveStores) s.$dispose();
    liveStores.length = 0;
  });

  it('记录搜索词：去重置顶', async () => {
    const s = makeStore();
    s.addSearchTerm('甲');
    s.addSearchTerm('乙');
    s.addSearchTerm('甲');
    await flush();

    expect(s.searchTerms).toEqual(['甲', '乙']);
  });

  it('空白词不记录', () => {
    const s = makeStore();
    s.addSearchTerm('   ');
    s.addSearchTerm('');
    expect(s.searchTerms).toEqual([]);
  });

  it('超过上限时裁剪最旧的（保留最新的 N 条）', async () => {
    const s = makeStore();
    for (let i = 1; i <= SEARCH_HISTORY_LIMIT_ + 3; i++) s.addSearchTerm(`词${i}`);
    await flush();

    expect(s.searchTerms).toHaveLength(SEARCH_HISTORY_LIMIT_);
    // 最新的一条在最前
    expect(s.searchTerms[0]).toBe(`词${SEARCH_HISTORY_LIMIT_ + 3}`);
  });

  it('持久化到独立键（不进 marks，避免被云端同步带上）', async () => {
    const s = makeStore();
    s.addSearchTerm('新能源');
    await flush();

    expect(localStorage.getItem(searchKey('anonymous'))).toContain('新能源');
    // marks 键里不应出现搜索词
    expect(localStorage.getItem(marksKey('anonymous')) ?? '').not.toContain('新能源');
  });

  it('可删除单条', async () => {
    const s = makeStore();
    s.addSearchTerm('甲');
    s.addSearchTerm('乙');
    s.removeSearchTerm('甲');
    await flush();

    expect(s.searchTerms).toEqual(['乙']);
    expect(localStorage.getItem(searchKey('anonymous'))).not.toContain('"甲"');
  });

  it('可清空', async () => {
    const s = makeStore();
    s.addSearchTerm('甲');
    s.clearSearchHistory();
    await flush();

    expect(s.searchTerms).toEqual([]);
  });

  it('不同账号的搜索历史互不可见（防串号）', async () => {
    // 同一时刻只有一个 user store 实例，换账号走的是「登出 → 登录」的作用域切换，
    // 因此按真实路径验证，而不是假设能同时存在两个 store。
    const s = await loginAs(11);
    s.addSearchTerm('甲的词');
    await flush();
    expect(localStorage.getItem(searchKey('u11'))).toContain('甲的词');

    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    await s.logout();

    vi.mocked(authApi.login).mockResolvedValue({ token: 't2', user: { id: 22, name: '乙' } });
    await s.login('13800138001', '123456');
    // 乙看不到甲的搜索历史
    expect(s.searchTerms).toEqual([]);

    s.addSearchTerm('乙的词');
    await flush();
    const u22 = localStorage.getItem(searchKey('u22')) ?? '';
    expect(u22).toContain('乙的词');
    expect(u22).not.toContain('甲的词');
    // 甲的仍完好
    expect(localStorage.getItem(searchKey('u11'))).toContain('甲的词');
  });

  it('登出切回匿名作用域后看不到账号的搜索历史', async () => {
    const s = await loginAs(33);
    s.addSearchTerm('账号词');
    await flush();

    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    await s.logout();

    expect(s.searchTerms).toEqual([]);
    // 账号数据仍留在本地，重新登录即恢复
    expect(localStorage.getItem(searchKey('u33'))).toContain('账号词');
  });

  it('损坏的 localStorage 数据被安全忽略', () => {
    localStorage.setItem(searchKey('anonymous'), '{ 不是 JSON');
    const s = makeStore();
    expect(s.searchTerms).toEqual([]);
  });

  it('过滤旧数据里的非字符串项', () => {
    localStorage.setItem(
      searchKey('anonymous'),
      JSON.stringify({ terms: ['甲', 123, null, '乙'] }),
    );
    const s = makeStore();
    expect(s.searchTerms).toEqual(['甲', '乙']);
  });
});

describe('stores/user 管理能力标志（isAdmin）', () => {
  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.resetAllMocks();
  });

  afterEach(() => {
    for (const s of liveStores) s.$dispose();
    liveStores.length = 0;
  });

  /** 以指定管理能力完成登录；flush 一次确保 prefs 已落盘 */
  async function loginWith(isAdmin?: boolean) {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      user: { id: 1, name: '用户', isAdmin },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    const s = makeStore();
    await s.login('13800138000', '123456');
    await flush();
    return s;
  }

  it('登录响应带 isAdmin=true 时置位', async () => {
    const s = await loginWith(true);
    expect(s.isAdmin).toBe(true);
  });

  it('登录响应缺 isAdmin 时保持 false（不默认授予特权）', async () => {
    const s = await loginWith(undefined);
    expect(s.isAdmin).toBe(false);
  });

  it('isAdmin 落盘，刷新页面后运营入口不消失', async () => {
    await loginWith(true);
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!).isAdmin).toBe(true);

    // 重建 pinia = 模拟刷新页面后重新创建 store
    pinia = createPinia();
    setActivePinia(pinia);
    localStorage.setItem('mingri-toutiao:token', 't');
    const s2 = makeStore();
    expect(s2.isLoggedIn).toBe(true);
    expect(s2.isAdmin).toBe(true);
  });

  it('登出后 isAdmin 归位，不把上个账号的特权留给匿名态', async () => {
    const s = await loginWith(true);
    await s.logout();
    await flush();
    expect(s.isAdmin).toBe(false);
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!).isAdmin).toBe(false);
  });

  it('未登录时忽略 prefs 里的 isAdmin（token 失效不得留下管理入口）', async () => {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ isLoggedIn: true, userId: 1, isAdmin: true, theme: 'system' }),
    );
    // 故意不写 token：登录态不成立
    const s = makeStore();
    expect(s.isLoggedIn).toBe(false);
    expect(s.isAdmin).toBe(false);
  });

  it('普通账号登录后 isAdmin 仍为 false', async () => {
    const s = await loginWith(false);
    expect(s.isLoggedIn).toBe(true);
    expect(s.isAdmin).toBe(false);
  });
});

describe('stores/user 昵称（P1-24）', () => {
  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.resetAllMocks();
  });

  afterEach(() => {
    for (const s of liveStores) s.$dispose();
    liveStores.length = 0;
  });

  /** 以指定昵称完成登录；flush 一次确保 prefs 已落盘 */
  async function loginWithName(name: string) {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      user: { id: 1, name },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    const s = makeStore();
    await s.login('13800138000', '123456');
    await flush();
    return s;
  }

  it('登录响应下发昵称，store 持有', async () => {
    const s = await loginWithName('坏蛋哥');
    expect(s.name).toBe('坏蛋哥');
  });

  it('登录响应缺昵称时为空串（界面回落默认名）', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      // 类型上 name 必填，运行时可能缺：按缺字段处理，回落空串
      user: { id: 1 },
    } as unknown as LoginResult);
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    const s = makeStore();
    await s.login('13800138000', '123456');
    expect(s.name).toBe('');
  });

  it('改名成功后 setName 即更新并落盘，刷新页面不退回默认名', async () => {
    const s = await loginWithName('旧名字');
    s.setName('新名字');
    await flush();
    expect(s.name).toBe('新名字');
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!).name).toBe('新名字');

    // 重建 pinia = 模拟刷新页面后重新创建 store
    pinia = createPinia();
    setActivePinia(pinia);
    localStorage.setItem('mingri-toutiao:token', 't');
    const s2 = makeStore();
    expect(s2.isLoggedIn).toBe(true);
    expect(s2.name).toBe('新名字');
  });

  it('登出后清空昵称，不把上个账号的名字留给匿名态', async () => {
    const s = await loginWithName('坏蛋哥');
    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    await s.logout();
    await flush();
    expect(s.name).toBe('');
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)!).name).toBe('');
  });

  it('登录写入整对令牌，登出一并清空（C1）', async () => {
    // 只清 access、留下 refresh，等于登出后仍握着一张能换发令牌的长令牌
    vi.mocked(authApi.login).mockResolvedValue({
      token: 'a',
      refreshToken: 'r',
      expiresIn: 1800,
      user: { id: 1, name: '用户' },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

    const s = makeStore();
    await s.login('13800138000', '123456');

    expect(localStorage.getItem('mingri-toutiao:token')).toBe('a');
    expect(localStorage.getItem('mingri-toutiao:refresh-token')).toBe('r');
    expect(localStorage.getItem('mingri-toutiao:token-exp')).toBeTruthy();

    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    await s.logout();

    expect(localStorage.getItem('mingri-toutiao:token')).toBeNull();
    expect(localStorage.getItem('mingri-toutiao:refresh-token')).toBeNull();
    expect(localStorage.getItem('mingri-toutiao:token-exp')).toBeNull();
  });

  it('登录带 mustChangePassword → 置受限标志并落盘（C4）', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      user: { id: 1, name: '用户', mustChangePassword: true },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

    const s = makeStore();
    await s.login('13800138000', 'old-pass');

    expect(s.mustChangePassword).toBe(true);
    // **必须落盘**：刷新页面后守卫失效，用户能到处逛，直到某个写操作撞 40305
    const prefs = JSON.parse(localStorage.getItem(PREFS_KEY)!);
    expect(prefs.mustChangePassword).toBe(true);
  });

  it('普通登录 mustChangePassword 为 false（缺省不授予受限）', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      user: { id: 1, name: '用户' },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

    const s = makeStore();
    await s.login('13800138000', 'old-pass');
    expect(s.mustChangePassword).toBe(false);
  });

  it('修改密码成功后清受限标志，并写入后端下发的新令牌（C4）', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 'temp-access',
      refreshToken: 'temp-refresh',
      user: { id: 1, name: '用户', mustChangePassword: true },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    vi.mocked(authApi.changePassword).mockResolvedValue({
      token: 'new-access',
      refreshToken: 'new-refresh',
      expiresIn: 1800,
      user: { id: 1, name: '用户', mustChangePassword: false },
    });

    const s = makeStore();
    await s.login('13800138000', 'temp-pass');
    expect(s.mustChangePassword).toBe(true);

    await s.changePassword('temp-pass', 'brand-new-pass');

    expect(authApi.changePassword).toHaveBeenCalledWith('temp-pass', 'brand-new-pass');
    expect(s.mustChangePassword).toBe(false);
    expect(s.token).toBe('new-access');
    // 后端已作废旧令牌，本地必须换成新的一对，否则用户被自己踢下线
    expect(localStorage.getItem('mingri-toutiao:token')).toBe('new-access');
    expect(localStorage.getItem('mingri-toutiao:refresh-token')).toBe('new-refresh');
  });

  it('登出清掉受限标志（下一位用这台设备的人不该被锁在改密页）', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 't',
      user: { id: 1, name: '用户', mustChangePassword: true },
    });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    vi.mocked(authApi.logout).mockResolvedValue(undefined);

    const s = makeStore();
    await s.login('13800138000', 'old-pass');
    await s.logout();

    expect(s.mustChangePassword).toBe(false);
  });
});

/**
 * E3-a：曝光缓冲（未登录落本地作用域）与登录采纳。
 *
 * 三条被钉住的不变量：
 * 1. **匿名期曝光不进服务端**（去了也是 401），只落本地；
 * 2. **登录时单向推送**，成功才清本地副本——失败保留，下次登录重试（幂等）；
 * 3. **同一 id 取较新时间戳**：匿名期与账号期都看过它时，不能因为合并丢掉较新那次。
 */
describe('stores/user 曝光缓冲与登录采纳（E3-a）', () => {
  /** 直接往某作用域塞一份曝光存档（绕过 store，模拟"上一段时间攒下的"） */
  const seed = (scope: string, map: Record<string, number>) =>
    localStorage.setItem(exposuresKey(scope), JSON.stringify({ exposures: map }));
  const readSeed = (scope: string): Record<string, number> => {
    const raw = JSON.parse(localStorage.getItem(exposuresKey(scope)) ?? '{}') as {
      exposures?: Record<string, number>;
    };
    return raw.exposures ?? {};
  };

  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.resetAllMocks();
  });

  afterEach(() => {
    for (const s of liveStores) s.$dispose();
    liveStores.length = 0;
  });

  it('未登录记录的曝光落匿名作用域，且不进服务端', async () => {
    const s = makeStore();
    s.recordExposures(['a', 'b']);
    await flush();

    const stored = readSeed('anonymous');
    expect(Object.keys(stored).sort()).toEqual(['a', 'b']);
    expect(s.exposures).toEqual(stored);
    // 匿名上报只会吃 401：本地这份的唯一消费者是"登录时采纳"
    expect(feedbackApi.reportExposures).not.toHaveBeenCalled();
  });

  it('空批不写盘（"划过什么都没看到"不该留下一个空文件）', async () => {
    const s = makeStore();
    s.recordExposures([]);
    await flush();
    expect(localStorage.getItem(exposuresKey('anonymous'))).toBeNull();
    expect(s.exposures).toEqual({});
  });

  it('同一 id 重复记录只刷新时间戳（幂等，不膨胀）', async () => {
    const s = makeStore();
    s.recordExposures(['a']);
    await flush();
    const first = readSeed('anonymous').a;
    s.recordExposures(['a', 'b']);
    await flush();

    const stored = readSeed('anonymous');
    expect(Object.keys(stored).sort()).toEqual(['a', 'b']);
    expect(stored.a).toBeGreaterThanOrEqual(first);
  });

  it('超过保留期的曝光被裁掉（与服务端 EXPOSURE_RETENTION_DAYS 同口径）', async () => {
    seed('anonymous', { stale: Date.now() - (EXPOSURE_RETENTION_DAYS + 1) * DAY_MS });
    const s = makeStore();
    s.recordExposures(['fresh']);
    await flush();

    const stored = readSeed('anonymous');
    expect(Object.keys(stored)).toEqual(['fresh']);
  });

  it('超过条数上限时保留最近的 N 条', async () => {
    // 第 i 条的时间戳比第 i+1 条新 1 秒：被裁掉的必须是尾部最旧的
    const old: Record<string, number> = {};
    const base = Date.now();
    for (let i = 0; i < EXPOSURE_CAP + 20; i += 1) old[`id-${i}`] = base - i * 1000;
    seed('anonymous', old);

    const s = makeStore();
    s.recordExposures(['brand-new']);
    await flush();

    const stored = readSeed('anonymous');
    expect(Object.keys(stored)).toHaveLength(EXPOSURE_CAP);
    expect(stored['brand-new']).toBeGreaterThan(0);
    // 最旧的 21 条（id-500 ~ id-519）应被裁掉
    expect(stored[`id-${EXPOSURE_CAP}`]).toBeUndefined();
    expect(stored['id-0']).toBeDefined();
  });

  it('登录时采纳匿名曝光并推送服务端，两份本地副本随即清空', async () => {
    seed('anonymous', { a: Date.now(), b: Date.now() });
    vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 7, name: '用户' } });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

    const s = makeStore();
    await s.login('13800138000', '123456');

    expect(feedbackApi.reportExposures).toHaveBeenCalledTimes(1);
    const pushed = vi.mocked(feedbackApi.reportExposures).mock.calls[0][0];
    expect([...pushed].sort()).toEqual(['a', 'b']);
    // 数据已归属该账号：两份本地副本都该退役，否则下次登录会重复推送
    expect(localStorage.getItem(exposuresKey('anonymous'))).toBeNull();
    expect(localStorage.getItem(exposuresKey('u7'))).toBeNull();
    expect(s.exposures).toEqual({});
  });

  it('采纳时合并账号作用域里已有的副本（并集，不覆盖）', async () => {
    seed('anonymous', { a: Date.now() });
    seed('u7', { b: Date.now() });
    vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 7, name: '用户' } });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);

    const s = makeStore();
    await s.login('13800138000', '123456');

    const pushed = vi.mocked(feedbackApi.reportExposures).mock.calls[0][0];
    expect([...pushed].sort()).toEqual(['a', 'b']);
  });

  it('同一 id 在两份作用域里取较新的时间戳', async () => {
    const older = Date.now() - 5 * DAY_MS;
    const newer = Date.now() - 1000;
    // 账号作用域反而更新：若合并写成"后者覆盖前者"（匿名盖账号），
    // 这里会退化成 older——两个方向都要钉住，否则断言恒真
    seed('anonymous', { x: older });
    seed('u7', { x: newer });
    // 推送失败 → 本地副本保留，正好能看出合并后留的是哪个时间戳
    vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 7, name: '用户' } });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    vi.mocked(feedbackApi.reportExposures).mockRejectedValue(new Error('网络异常'));

    const s = makeStore();
    await s.login('13800138000', '123456');

    expect(s.exposures.x).toBe(newer);
    expect(readSeed('u7').x).toBe(newer);
  });

  it('推送失败保留本地副本，下次登录重试（幂等）', async () => {
    seed('anonymous', { a: Date.now() });
    vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 7, name: '用户' } });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    vi.mocked(feedbackApi.reportExposures).mockRejectedValueOnce(new Error('网络异常'));

    const s = makeStore();
    await s.login('13800138000', '123456');

    // 失败：账号作用域已落盘（可重试），匿名作用域不清（数据还没被承认）
    expect(readSeed('u7').a).toBeGreaterThan(0);
    expect(readSeed('anonymous').a).toBeGreaterThan(0);
    expect(s.exposures).toEqual({ a: readSeed('u7').a });

    await s.logout();
    // 登出后匿名作用域仍在 → 重新登录会再试一次
    vi.mocked(feedbackApi.reportExposures).mockResolvedValue({ accepted: 1, written: 1 });
    await s.login('13800138000', '123456');

    expect(feedbackApi.reportExposures).toHaveBeenCalledTimes(2);
    const retried = vi.mocked(feedbackApi.reportExposures).mock.calls[1][0];
    expect(retried).toEqual(['a']);
    // 这次成功了：两份副本都清掉
    expect(localStorage.getItem(exposuresKey('anonymous'))).toBeNull();
    expect(localStorage.getItem(exposuresKey('u7'))).toBeNull();
  });

  it('登出切回匿名作用域并载入该作用域的曝光（账号副本不被登出销毁）', async () => {
    seed('anonymous', { a: Date.now() });
    vi.mocked(authApi.login).mockResolvedValue({ token: 't', user: { id: 1, name: '用户' } });
    vi.mocked(meApi.mergeFavorites).mockResolvedValue(emptyPayload);
    vi.mocked(meApi.mergeHistory).mockResolvedValue(emptyPayload);
    vi.mocked(authApi.logout).mockResolvedValue(undefined);
    // 推送失败 → 账号作用域留下合并结果，匿名作用域也未清（数据还没被承认）
    vi.mocked(feedbackApi.reportExposures).mockRejectedValue(new Error('网络异常'));

    const s = makeStore();
    await s.login('13800138000', '123456');
    await s.logout();

    expect(s.userId).toBeNull();
    expect(Object.keys(s.exposures)).toEqual(['a']); // 载入的是匿名作用域
    expect(readSeed('u1').a).toBeGreaterThan(0); // 账号副本仍在，重新登录可重试
  });

  it('脏数据（非正整数时间戳）被忽略，不让分桶算出怪结果', async () => {
    localStorage.setItem(
      exposuresKey('anonymous'),
      JSON.stringify({ exposures: { good: Date.now(), zero: 0, neg: -5, str: 'x' } }),
    );
    const s = makeStore();
    expect(Object.keys(s.exposures)).toEqual(['good']);
  });
});
