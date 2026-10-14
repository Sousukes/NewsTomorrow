import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 源管理没有 mock 短路：整条链路都打桩在 api 层，与 `stores/comments.spec.ts` 同思路
vi.mock('@/api/admin', () => ({
  // P1-19 起列表接口返回 { list, healthDays }：成功率必须带时间范围
  fetchSources: vi.fn(async () => ({ list: [], healthDays: 7 })),
  fetchSourceHealth: vi.fn(async () => ({})),
  createSource: vi.fn(async () => ({})),
  updateSource: vi.fn(async () => ({})),
  deleteSource: vi.fn(async () => undefined),
  resetSources: vi.fn(async () => 0),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}));

import AdminSources from './AdminSources.vue';
import * as adminApi from '@/api/admin';
import type { RssSourceItem, SourceHealth } from '@/types';

const source = (over: Partial<RssSourceItem> = {}): RssSourceItem => ({
  key: 'aihot_picks',
  name: 'AIHOT·精选',
  channel: 'tech',
  url: 'https://aihot.news/feed.xml',
  weight: 9,
  enabled: true,
  lastStatus: 'ok',
  lastError: null,
  lastItemCount: 20,
  lastFetchedAt: '2026-10-13T07:38:22',
  respectRobots: true,
  // P1-18：默认"没有正在发生的故障"，需要故障态的用例自行覆盖
  consecutiveFailures: 0,
  failureKind: null,
  nextRetryAt: null,
  // P1-19：默认"近期没有抓取记录"（与"抓了全挂"是两回事，需要覆盖的用例自行给值）
  health: null,
  ...over,
});

/** 列表接口现在返回 { list, healthDays }；多数用例只关心列表本身，这里包一层 */
const mockList = (rows: RssSourceItem[]) =>
  vi.mocked(adminApi.fetchSources).mockResolvedValue({ list: rows, healthDays: 7 });

/** 单源健康趋势（P1-19）：默认"一直成功但条数在下滑"——最能体现趋势页价值的一类 */
const HEALTH = (over: Partial<SourceHealth> = {}): SourceHealth => ({
  key: 'aihot_picks',
  days: 30,
  keepDays: 30,
  summary: {
    attempts: 10,
    ok: 10,
    failed: 0,
    rate: 1,
    items: 170,
    avgItems: 17,
    lastOkAt: '2026-10-15T09:00:00',
    lastFailAt: null,
  },
  points: [
    { date: '2026-10-11', total: 1, ok: 1, failed: 0, items: 30, rate: 1 },
    { date: '2026-10-12', total: 1, ok: 1, failed: 0, items: 24, rate: 1 },
    { date: '2026-10-13', total: 1, ok: 1, failed: 0, items: 18, rate: 1 },
    { date: '2026-10-14', total: 1, ok: 1, failed: 0, items: 9, rate: 1 },
    { date: '2026-10-15', total: 1, ok: 1, failed: 0, items: 4, rate: 1 },
  ],
  failures: [],
  ...over,
});

const mountPage = () => mount(AdminSources);
/** 等 onMounted 的异步 load() 落地 + 一次渲染 tick */
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const enterBatch = async (w: ReturnType<typeof mountPage>) => {
  await w
    .findAll('button')
    .find((b) => b.text() === '批量管理')!
    .trigger('click');
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockList([]);
  vi.mocked(adminApi.updateSource).mockResolvedValue(source());
  vi.mocked(adminApi.createSource).mockResolvedValue(source({ key: 'demo' }));
  vi.mocked(adminApi.deleteSource).mockResolvedValue(undefined);
  vi.mocked(adminApi.resetSources).mockResolvedValue(14);
  vi.mocked(adminApi.fetchSourceHealth).mockResolvedValue(HEALTH());
});

describe('views/AdminSources', () => {
  it('非管理员不请求列表，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchSources).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理信源');
  });

  it('管理员加载后渲染源列表与抓取健康状态', async () => {
    mockList([
      source(),
      source({
        key: 'people_politics',
        name: '人民网·时政',
        channel: 'recommend',
        enabled: false,
        lastStatus: null,
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('AIHOT·精选');
    expect(w.text()).toContain('https://aihot.news/feed.xml');
    expect(w.text()).toContain('正常');
    expect(w.text()).toContain('最近 20 条');
    expect(w.text()).toContain('未抓取');
    // 汇总四计数：总数 / 启用中 / 异常 / 未抓取
    expect(w.findAll('.admin__stat').map((b) => b.text())).toEqual([
      '2个源',
      '1启用中',
      '0异常',
      '1未抓取',
    ]);
  });

  it('开关切换走 PATCH 局部更新，只传 enabled', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);

    await w.find('[role="switch"]').trigger('click');
    expect(adminApi.updateSource).toHaveBeenCalledWith('aihot_picks', { enabled: false });
  });

  it('开关失败时回滚本地状态，避免界面与服务端不一致', async () => {
    mockList([source()]);
    vi.mocked(adminApi.updateSource).mockRejectedValue(new Error('启停失败'));
    const w = mountPage();
    await settle(w);

    const sw = w.find('[role="switch"]');
    expect(sw.attributes('aria-checked')).toBe('true');
    await sw.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(sw.attributes('aria-checked')).toBe('true'); // 已回滚
    expect(w.text()).toContain('启停失败');
  });

  it('删除需二次确认，确认后才真正调用接口', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);

    const delBtn = w.findAll('button').find((b) => b.text() === '删除');
    await delBtn!.trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.deleteSource).not.toHaveBeenCalled();
    expect(w.text()).toContain('删除「AIHOT·精选」');

    const confirm = w.findAll('button').find((b) => b.text() === '确认删除');
    await confirm!.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.deleteSource).toHaveBeenCalledWith('aihot_picks');
  });

  it('新增表单拦截非法地址，不发请求', async () => {
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));

    await w
      .findAll('button')
      .find((b) => b.text() === '新增源')!
      .trigger('click');
    await w.vm.$nextTick();

    const inputs = w.findAll('.admin__form--new input');
    await inputs[0].setValue('demo_src'); // key
    await inputs[1].setValue('演示源'); // 名称
    await inputs[2].setValue('ftp://example.com/f.xml'); // 地址
    // jsdom 不实现「点提交按钮触发表单提交」，直接派发 submit 事件
    await w.find('.admin__form--new').trigger('submit');

    expect(adminApi.createSource).not.toHaveBeenCalled();
    expect(w.text()).toContain('源地址必须是以 http/https 开头的完整地址');
  });

  it('新增表单拦截非法 key，不发请求', async () => {
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    await w
      .findAll('button')
      .find((b) => b.text() === '新增源')!
      .trigger('click');
    await w.vm.$nextTick();

    await w.findAll('.admin__form--new input')[0].setValue('Bad Key');
    await w.find('.admin__form--new').trigger('submit');

    expect(adminApi.createSource).not.toHaveBeenCalled();
    expect(w.text()).toContain('标识只能用英文小写字母、数字和下划线');
  });

  it('新增表单合法时提交并刷新列表', async () => {
    mockList([]);
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    await w
      .findAll('button')
      .find((b) => b.text() === '新增源')!
      .trigger('click');
    await w.vm.$nextTick();

    const inputs = w.findAll('.admin__form--new input');
    await inputs[0].setValue('demo_src');
    await inputs[1].setValue('演示源');
    await inputs[2].setValue('https://example.com/feed.xml');
    await w.find('.admin__form--new').trigger('submit');
    await new Promise((r) => setTimeout(r, 0));

    expect(adminApi.createSource).toHaveBeenCalledWith(
      expect.objectContaining({
        key: 'demo_src',
        name: '演示源',
        url: 'https://example.com/feed.xml',
      }),
    );
    // 保存成功后重新拉列表，保证看到的是服务端真相
    expect(adminApi.fetchSources).toHaveBeenCalledTimes(2);
  });

  it('后端 403 时展示无权限态', async () => {
    vi.mocked(adminApi.fetchSources).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理信源');
  });

  it('恢复内置源需二次确认', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);

    await w
      .findAll('button')
      .find((b) => b.text() === '恢复内置源')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.resetSources).not.toHaveBeenCalled();

    await w
      .findAll('button')
      .find((b) => b.text() === '确认重置')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.resetSources).toHaveBeenCalled();
    expect(w.text()).toContain('已重置为 14 个内置源');
  });
});

/* ------------------------------ P1-13：搜索与筛选 ------------------------------ */

describe('views/AdminSources 搜索与筛选', () => {
  it('搜索按名称 / 地址 / 标识过滤，无结果时给出清除入口', async () => {
    mockList([
      source(),
      source({
        key: 'people_politics',
        name: '人民网·时政',
        url: 'http://www.people.com.cn/rss/politics.xml',
      }),
    ]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('aihot'); // 标识命中
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('AIHOT·精选');

    await search.setValue('people.com.cn'); // 地址命中
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('人民网·时政');

    await search.setValue('人民网'); // 名称命中
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(1);

    await search.setValue('不存在的源');
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(0);
    expect(w.text()).toContain('没有匹配的源');

    // 一键清除回到全量
    await w
      .findAll('button')
      .find((b) => b.text() === '清除筛选')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(2);
  });

  it('按频道筛选：只显示该频道的源', async () => {
    mockList([
      source(),
      source({ key: 'people_politics', name: '人民网·时政', channel: 'recommend' }),
    ]);
    const w = mountPage();
    await settle(w);

    // 第二组 filters 是频道（第一组是状态）
    const channelGroup = w.findAll('.admin__filters')[1];
    const chip = channelGroup.findAll('button').find((b) => b.text() === '推荐')!;
    await chip.trigger('click');
    await w.vm.$nextTick();

    expect(chip.attributes('aria-pressed')).toBe('true');
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('人民网·时政');
    expect(w.text()).not.toContain('AIHOT·精选');
  });

  it('按状态筛选：只看异常源', async () => {
    mockList([
      source(),
      source({ key: 'bad_feed', name: '坏源', lastStatus: 'error', lastError: 'timeout' }),
    ]);
    const w = mountPage();
    await settle(w);

    const statusGroup = w.findAll('.admin__filters')[0];
    await statusGroup
      .findAll('button')
      .find((b) => b.text() === '异常')!
      .trigger('click');
    await w.vm.$nextTick();

    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('坏源');
    expect(w.text()).not.toContain('AIHOT·精选');
  });
});

/* ------------------------------ P1-13：健康汇总与异常置顶 ------------------------------ */

describe('views/AdminSources 健康汇总', () => {
  it('汇总四计数正确，点数字即筛选、再点取消', async () => {
    mockList([
      source({ key: 'a' }),
      source({ key: 'bad_feed', name: '坏源', lastStatus: 'error' }),
      source({ key: 'fresh_one', name: '新源', lastStatus: null }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.findAll('.admin__stat').map((b) => b.text())).toEqual([
      '3个源',
      '3启用中',
      '1异常',
      '1未抓取',
    ]);

    // 点「异常」→ 只看异常源
    await w.findAll('.admin__stat')[2].trigger('click');
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('坏源');

    // 再点一次取消筛选
    await w.findAll('.admin__stat')[2].trigger('click');
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(3);
  });

  it('异常源置顶显示，组内保持权重降序', async () => {
    mockList([
      source({ key: 'k_high', name: '高权重正常源', weight: 10, lastStatus: 'ok' }),
      source({ key: 'k_low', name: '低权重正常源', weight: 2, lastStatus: 'ok' }),
      source({ key: 'k_bad', name: '异常源', weight: 1, lastStatus: 'error' }),
    ]);
    const w = mountPage();
    await settle(w);

    const names = w.findAll('.admin__item .admin__name').map((n) => n.text());
    expect(names).toEqual(['异常源', '高权重正常源', '低权重正常源']);
  });
});

/* ------------------------------ P1-13：批量操作 ------------------------------ */

describe('views/AdminSources 批量操作', () => {
  const three = () => [
    source({ key: 'a', name: '源A', enabled: false }),
    source({ key: 'b', name: '源B', enabled: false }),
    source({ key: 'c', name: '源C' }),
  ];

  it('进入批量模式出现多选与操作条，全选只作用于当前可见项', async () => {
    mockList(three());
    const w = mountPage();
    await settle(w);

    await enterBatch(w);
    expect(w.findAll('.admin__pick input')).toHaveLength(3);
    expect(w.text()).toContain('已选 0 项');

    // 未选中时批量条三个按钮均禁用
    const batchBtns = w.findAll('.admin__batchbar button');
    expect(batchBtns).toHaveLength(3);
    expect(batchBtns.every((b) => b.attributes('disabled') !== undefined)).toBe(true);

    // 先筛选到「已停用」（2 条可见），再全选——只应选中这 2 条
    await w
      .findAll('.admin__filters')[0]
      .findAll('button')
      .find((b) => b.text() === '已停用')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(2);

    await w
      .findAll('button')
      .find((b) => b.text() === '全选')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(w.text()).toContain('已选 2 项');

    // 清除筛选后，筛选范围外的源C仍未选中（证明全选没有越过可见范围）
    await w
      .findAll('button')
      .find((b) => b.text() === '清除筛选')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(w.text()).toContain('已选 2 项');
    expect(w.findAll('.admin__pick input:checked')).toHaveLength(2);
  });

  it('批量启用：逐个调用接口、本地状态更新、成功后清空选择', async () => {
    mockList(three());
    const w = mountPage();
    await settle(w);

    await enterBatch(w);
    await w
      .findAll('button')
      .find((b) => b.text() === '全选')!
      .trigger('click');
    await w.vm.$nextTick();

    await w.findAll('.admin__batchbar button')[0].trigger('click'); // 批量启用
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateSource).toHaveBeenCalledTimes(3);
    expect(adminApi.updateSource).toHaveBeenCalledWith('a', { enabled: true });
    expect(adminApi.updateSource).toHaveBeenCalledWith('b', { enabled: true });
    expect(w.text()).toContain('已启用 3 个源');
    // 成功清空选择，但仍在批量模式（可继续操作）
    expect(w.text()).toContain('已选 0 项');
    expect(w.text()).toContain('退出批量');
  });

  it('批量启停部分失败：失败项回滚并汇总提示', async () => {
    mockList(three());
    vi.mocked(adminApi.updateSource).mockImplementation(async (key: string) => {
      if (key === 'b') throw new Error('network');
      return source();
    });
    const w = mountPage();
    await settle(w);

    await enterBatch(w);
    await w
      .findAll('button')
      .find((b) => b.text() === '全选')!
      .trigger('click');
    await w.vm.$nextTick();

    await w.findAll('.admin__batchbar button')[0].trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateSource).toHaveBeenCalledTimes(3);
    expect(w.text()).toContain('1 个源操作失败，已还原，请重试');

    // 退出批量模式后核对：成功的两个为开，失败的 b 保持原状（关）
    await w
      .findAll('button')
      .find((b) => b.text() === '退出批量')!
      .trigger('click');
    await w.vm.$nextTick();
    const states = w.findAll('[role="switch"]').map((s) => s.attributes('aria-checked'));
    expect(states).toEqual(['true', 'false', 'true']);
  });

  it('批量删除：二次确认后逐个删除并移出列表', async () => {
    mockList(three());
    const w = mountPage();
    await settle(w);

    await enterBatch(w);
    await w.findAll('.admin__pick input')[0].setValue(true);
    await w.findAll('.admin__pick input')[1].setValue(true);
    await w.vm.$nextTick();
    expect(w.text()).toContain('已选 2 项');

    await w.findAll('.admin__batchbar button')[2].trigger('click'); // 批量删除
    await w.vm.$nextTick();
    expect(w.text()).toContain('删除选中的 2 个源');
    expect(adminApi.deleteSource).not.toHaveBeenCalled();

    await w
      .findAll('button')
      .find((b) => b.text() === '确认删除')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.deleteSource).toHaveBeenCalledTimes(2);
    expect(adminApi.deleteSource).toHaveBeenCalledWith('a');
    expect(adminApi.deleteSource).toHaveBeenCalledWith('b');
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.text()).toContain('源C');
    expect(w.text()).toContain('已删除 2 个源');
  });

  it('批量模式下不渲染单项编辑/删除与开关（交互不叠加）', async () => {
    mockList(three());
    const w = mountPage();
    await settle(w);

    await enterBatch(w);
    expect(w.findAll('[role="switch"]')).toHaveLength(0);
    expect(w.findAll('button').some((b) => b.text() === '编辑')).toBe(false);

    // 退出后恢复
    await w
      .findAll('button')
      .find((b) => b.text() === '退出批量')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(w.findAll('[role="switch"]')).toHaveLength(3);
  });
});

/* ------------------------------ P1-16：robots 开关 ------------------------------ */

describe('views/AdminSources robots 开关', () => {
  it('默认遵守时列表不出现「忽略 robots」标记', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__robots-off').exists()).toBe(false);
  });

  it('关闭遵守的源在列表上留下可见标记', async () => {
    mockList([source({ key: 'strict_feed', name: '严格源', respectRobots: false })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__robots-off').text()).toContain('忽略 robots');
  });

  it('编辑表单取消勾选后 PATCH 带上 respectRobots=false', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);

    await w
      .findAll('button')
      .find((b) => b.text() === '编辑')!
      .trigger('click');
    await w.vm.$nextTick();

    const checks = w.findAll('.admin__form .admin__check input');
    expect(checks).toHaveLength(2);
    // 第二个复选是「遵守 robots.txt」，默认勾选（后端默认值）
    expect((checks[1].element as HTMLInputElement).checked).toBe(true);
    await checks[1].setValue(false);
    await w.find('.admin__form').trigger('submit');
    await new Promise((r) => setTimeout(r, 0));

    expect(adminApi.updateSource).toHaveBeenCalledWith(
      'aihot_picks',
      expect.objectContaining({ respectRobots: false }),
    );
  });

  it('新增表单默认勾选遵守，提交时透传 true', async () => {
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    await w
      .findAll('button')
      .find((b) => b.text() === '新增源')!
      .trigger('click');
    await w.vm.$nextTick();

    const inputs = w.findAll('.admin__form--new input');
    await inputs[0].setValue('demo_src');
    await inputs[1].setValue('演示源');
    await inputs[2].setValue('https://example.com/feed.xml');
    await w.find('.admin__form--new').trigger('submit');
    await new Promise((r) => setTimeout(r, 0));

    expect(adminApi.createSource).toHaveBeenCalledWith(
      expect.objectContaining({ respectRobots: true }),
    );
  });
});

// P1-18：失败分级 + 熔断冷却。列表页的核心问题不是"挂没挂"，
// 而是"我是该等它自己恢复，还是得去改配置"——这两类必须看起来不一样。
describe('views/AdminSources（失败分级与熔断，P1-18）', () => {
  it('连续失败 + 永久性 → 红色标签写明次数与性质', async () => {
    mockList([
      source({
        key: 'dead_src',
        name: '死源',
        lastStatus: 'error',
        lastError: 'HTTP 404',
        consecutiveFailures: 7,
        failureKind: 'permanent',
      }),
    ]);
    const w = mountPage();
    await settle(w);

    const tag = w.find('.admin__failure');
    expect(tag.text()).toBe('连续 7 次失败 · 永久性');
    expect(tag.classes()).toContain('is-permanent');
  });

  it('可重试失败用中性样式，且不带 is-permanent', async () => {
    mockList([
      source({
        lastStatus: 'error',
        lastError: 'ReadTimeout',
        consecutiveFailures: 3,
        failureKind: 'transient',
      }),
    ]);
    const w = mountPage();
    await settle(w);

    const tag = w.find('.admin__failure');
    expect(tag.text()).toBe('连续 3 次失败 · 可重试');
    expect(tag.classes()).not.toContain('is-permanent');
  });

  it('只失败一次且无性质时不出标签：偶发抖动多显示一格就是噪音', async () => {
    mockList([source({ lastStatus: 'error', lastError: 'HTTP 500', consecutiveFailures: 1 })]);
    const w = mountPage();
    await settle(w);
    // 状态仍然是"异常"，只是不再追加失败性质标签
    expect(w.text()).toContain('异常');
    expect(w.find('.admin__failure').exists()).toBe(false);
  });

  it('正常的源不出现任何失败标签', async () => {
    mockList([source({ lastStatus: 'ok', consecutiveFailures: 5, failureKind: 'transient' })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__failure').exists()).toBe(false);
  });

  it('熔断冷却期内显示恢复时间，过期后不再显示', async () => {
    const future = new Date(Date.now() + 25 * 60 * 1000).toISOString();
    mockList([source({ key: 'cb', lastStatus: 'error', nextRetryAt: future })]);
    const w = mountPage();
    await settle(w);

    const tag = w.find('.admin__cooldown');
    expect(tag.exists()).toBe(true);
    expect(tag.text()).toMatch(/^冷却至 \d{2}-\d{2} \d{2}:\d{2}$/);

    // 同一个源过了冷却点：再显示"冷却至…"就是在骗人
    mockList([
      source({
        key: 'cb',
        lastStatus: 'error',
        nextRetryAt: new Date(Date.now() - 60 * 1000).toISOString(),
      }),
    ]);
    const w2 = mountPage();
    await settle(w2);
    expect(w2.find('.admin__cooldown').exists()).toBe(false);
  });

  it('冷却时间是垃圾字符串时静默跳过，不渲染 NaN', async () => {
    mockList([source({ key: 'cb', lastStatus: 'error', nextRetryAt: '不是时间' })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__cooldown').exists()).toBe(false);
    expect(w.text()).not.toContain('NaN');
  });
});

// P1-19：近期成功率 + 就地展开的趋势面板。
describe('views/AdminSources（健康历史与趋势，P1-19）', () => {
  /** 找到某一行的「趋势」按钮（未展开时文案为"趋势"，展开后为"收起趋势"） */
  const trendBtn = (w: ReturnType<typeof mountPage>) =>
    w.findAll('button').find((b) => b.text() === '趋势');

  it('有近期摘要的源显示成功率，并标注窗口天数', async () => {
    mockList([source({ key: 's1', health: { attempts: 12, rate: 0.4, avgItems: 8 } })]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('近 7 天 40%');
    expect(w.find('.admin__health').classes()).toContain('is-bad');
  });

  it('成功率在 0.5–0.9 之间用警示色，满分为中性色', async () => {
    mockList([
      source({ key: 'a', health: { attempts: 12, rate: 0.8, avgItems: 8 } }),
      source({ key: 'b', health: { attempts: 12, rate: 1, avgItems: 8 } }),
    ]);
    const w = mountPage();
    await settle(w);
    const rows = w.findAll('.admin__health');
    expect(rows[0].classes()).toContain('is-warn');
    expect(rows[1].classes()).not.toContain('is-warn');
    expect(rows[1].classes()).not.toContain('is-bad');
  });

  it('health 为 null（没抓过）时整段不显示，而不是显示 0%', async () => {
    mockList([source({ key: 's1', health: null })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__health').exists()).toBe(false);
    expect(w.text()).not.toContain('近 7 天');
  });

  it('attempts 为 0 时同样不显示（与 rate===0 的"全挂"区分）', async () => {
    mockList([source({ key: 's1', health: { attempts: 0, rate: null, avgItems: null } })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__health').exists()).toBe(false);
  });

  it('点击「趋势」就地展开并请求单源健康数据', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);
    await trendBtn(w)!.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchSourceHealth).toHaveBeenCalledWith('aihot_picks');
    expect(w.find('.mt-health').exists()).toBe(true);
    expect(w.text()).toContain('近 30 天成功率');
  });

  it('再次点击收起趋势面板', async () => {
    mockList([source()]);
    const w = mountPage();
    await settle(w);
    await trendBtn(w)!.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.find('.mt-health').exists()).toBe(true);

    const collapse = w.findAll('button').find((b) => b.text() === '收起趋势')!;
    await collapse.trigger('click');
    await w.vm.$nextTick();
    expect(w.find('.mt-health').exists()).toBe(false);
  });

  it('趋势请求失败只在这一行内报错，不掀翻整个页面', async () => {
    vi.mocked(adminApi.fetchSourceHealth).mockRejectedValue(new Error('网络错误'));
    mockList([source()]);
    const w = mountPage();
    await settle(w);
    await trendBtn(w)!.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.find('.admin__trend .admin__banner').exists()).toBe(true);
    // 列表本身还在
    expect(w.find('.admin__list').exists()).toBe(true);
  });

  it('一次只展开一个源的趋势', async () => {
    mockList([source({ key: 'a' }), source({ key: 'b', name: '源乙' })]);
    const w = mountPage();
    await settle(w);
    await trendBtn(w)!.trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.findAll('.mt-health')).toHaveLength(1);
  });
});
