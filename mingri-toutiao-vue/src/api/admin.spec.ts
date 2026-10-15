import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 关闭 mock：源管理没有 mock 短路（见 api/admin.ts 说明），必须走真实请求分支
vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

import {
  ADMIN_SOURCES_SUPPORTED,
  createSource,
  deleteSource,
  fetchAdminComments,
  fetchAdminNews,
  fetchSourceHealth,
  fetchSources,
  hideComment,
  hideNews,
  resetSources,
  restoreComment,
  restoreNews,
  updateNewsTop,
  updateSource,
} from '@/api/admin';

function respond(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({ code: 0, message: 'ok', data }),
  } as unknown as Response;
}

function fail(status: number, message: string): Response {
  return {
    ok: false,
    status,
    json: async () => ({ code: status, message }),
  } as unknown as Response;
}

const RAW_SOURCE = {
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
  consecutiveFailures: 0,
  failureKind: null,
  nextRetryAt: null,
  // P1-19：近期健康摘要
  health: { attempts: 24, rate: 0.9583, avgItems: 18.4 },
};

// P1-20：管理端资讯行（含 hidden 三件套）
const RAW_NEWS = {
  id: 'n_1',
  title: '某 AI 新品发布',
  summary: '一场关于端侧模型的发布会',
  source: 'AIHOT·精选',
  commentCount: 12,
  time: '2026-10-15T08:00:00',
  isTop: false,
  channel: 'tech',
  publishedAt: '2026-10-15T08:00:00',
  hotScore: 12.3456,
  hidden: false,
  hiddenAt: null,
  hiddenReason: null,
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => respond({ list: [RAW_SOURCE], total: 1, healthDays: 7 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const lastCall = () => (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.at(-1);

describe('api/admin', () => {
  it('非 mock 构建才具备源管理能力', () => {
    expect(ADMIN_SOURCES_SUPPORTED).toBe(true);
  });

  it('fetchSources 请求 /admin/sources 并归一化字段', async () => {
    const { list } = await fetchSources();
    expect(lastCall()?.[0] as string).toBe('/api/admin/sources');
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      key: 'aihot_picks',
      name: 'AIHOT·精选',
      channel: 'tech',
      url: 'https://aihot.news/feed.xml',
      weight: 9,
      enabled: true,
      lastStatus: 'ok',
      lastItemCount: 20,
      lastFetchedAt: '2026-10-13T07:38:22',
      lastError: null,
      respectRobots: true,
      // P1-18：多词字段走驼峰，落库口径与后端 `_out` 对齐（拼错会静默变 null）
      consecutiveFailures: 0,
      failureKind: null,
      nextRetryAt: null,
    });
  });

  it('respectRobots 缺失时按更保守的一方兜底为 true', async () => {
    // 后端旧版本可能不下发该字段：显式删掉它，模拟「字段缺失」
    const withoutField: Record<string, unknown> = { ...RAW_SOURCE };
    delete withoutField.respectRobots;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [withoutField] })),
    );
    const [s] = (await fetchSources()).list;
    expect(s.respectRobots).toBe(true);
  });

  it('respectRobots=false 原样透出（管理员主动破例）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, respectRobots: false }] })),
    );
    const [s] = (await fetchSources()).list;
    expect(s.respectRobots).toBe(false);
  });

  it('缺 key 的行被丢弃而非渲染成空壳', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ name: '无 key' }, RAW_SOURCE] })),
    );
    const { list } = await fetchSources();
    expect(list.map((s) => s.key)).toEqual(['aihot_picks']);
  });

  it('list 不是数组时返回空数组（不抛）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({})),
    );
    await expect(fetchSources()).resolves.toEqual({ list: [], healthDays: 7 });
  });

  it('非法 channel 归一化为默认频道，非法 status 归一化为 null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        respond({ list: [{ ...RAW_SOURCE, channel: 'tecn', lastStatus: 'weird' }] }),
      ),
    );
    const [s] = (await fetchSources()).list;
    expect(s.channel).toBe('recommend');
    expect(s.lastStatus).toBeNull();
  });

  it('lastError 空串归一化为 null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, lastError: '' }] })),
    );
    const [s] = (await fetchSources()).list;
    expect(s.lastError).toBeNull();
  });

  it('createSource 走 POST 并回传新建的源', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ ...RAW_SOURCE, key: 'demo' })),
    );
    const s = await createSource({
      key: 'demo',
      name: '演示源',
      channel: 'tech',
      url: 'https://example.com/f.xml',
      weight: 7,
    });
    expect(s.key).toBe('demo');
    const [, init] = lastCall() as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toMatchObject({ key: 'demo', channel: 'tech' });
  });

  it('updateSource 走 PATCH 且 URL 中的 key 被编码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond(RAW_SOURCE)),
    );
    await updateSource('a b/c', { weight: 3 });
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/sources/a%20b%2Fc');
    expect(init.method).toBe('PATCH');
  });

  it('deleteSource 走 DELETE', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ key: 'demo' })),
    );
    await deleteSource('demo');
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/sources/demo');
    expect(init.method).toBe('DELETE');
  });

  it('resetSources 走 POST 并取出播种条数', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ seeded: 14 })),
    );
    await expect(resetSources()).resolves.toBe(14);
    expect((lastCall()?.[1] as RequestInit).method).toBe('POST');
  });

  it('resetSources 在响应缺字段时返回 0（不抛）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({})),
    );
    await expect(resetSources()).resolves.toBe(0);
  });

  it('后端 403 时向上抛错，供页面展示无权限态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(403, '无权限执行该操作')),
    );
    await expect(fetchSources()).rejects.toThrow('无权限执行该操作');
  });

  it('后端 409（key 已存在）向上抛错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(409, '该源标识已存在')),
    );
    await expect(
      createSource({ key: 'dup', name: 'x', channel: 'tech', url: 'https://e.com/a.xml' }),
    ).rejects.toThrow('该源标识已存在');
  });
});

// P1-18：失败分级三项是**旁路信息**，取值不认识时一律回落到"没有故障"，
// 绝不猜一个失败性质——误标成 permanent 会让运营白跑一趟。
describe('api/admin（失败分级字段，P1-18）', () => {
  const one = async (patch: Record<string, unknown>) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [{ ...RAW_SOURCE, ...patch }] })),
    );
    const [s] = (await fetchSources()).list;
    return s;
  };

  it('三项齐全时原样透出', async () => {
    const s = await one({
      consecutiveFailures: 7,
      failureKind: 'permanent',
      nextRetryAt: '2026-10-15T12:00:00',
    });
    expect(s.consecutiveFailures).toBe(7);
    expect(s.failureKind).toBe('permanent');
    expect(s.nextRetryAt).toBe('2026-10-15T12:00:00');
  });

  it('transient 原样透出（与 permanent 走同一通道）', async () => {
    expect((await one({ failureKind: 'transient' })).failureKind).toBe('transient');
  });

  it('整个字段缺失（后端旧版本）时回落为 0 / null / null', async () => {
    // 显式删掉三个键，真正模拟"老后端不下发"——用 RAW_SOURCE 里已有的 0/null
    // 是测不出缺失分支的
    const legacy: Record<string, unknown> = { ...RAW_SOURCE };
    delete legacy.consecutiveFailures;
    delete legacy.failureKind;
    delete legacy.nextRetryAt;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [legacy] })),
    );
    const [s] = (await fetchSources()).list;
    expect(s.consecutiveFailures).toBe(0);
    expect(s.failureKind).toBeNull();
    expect(s.nextRetryAt).toBeNull();
  });

  it('failureKind 是不认识的取值时回落为 null', async () => {
    expect((await one({ failureKind: 'fatal' })).failureKind).toBeNull();
  });

  it('consecutiveFailures 是垃圾值时回落为 0，不产生 NaN', async () => {
    expect((await one({ consecutiveFailures: '很多次' })).consecutiveFailures).toBe(0);
  });

  it('nextRetryAt 为空串时回落为 null', async () => {
    expect((await one({ nextRetryAt: '' })).nextRetryAt).toBeNull();
  });
});

// P1-19：健康摘要与趋势。核心是**"没抓过"不能和"抓了全挂"混为一谈**——
// 前者多半是源停用或服务没跑，后者才需要人去处理。
describe('api/admin（健康历史，P1-19）', () => {
  const RAW_HEALTH = {
    key: 'people_scitech',
    days: 30,
    keepDays: 30,
    summary: {
      attempts: 10,
      ok: 8,
      failed: 2,
      rate: 0.8,
      items: 160,
      avgItems: 20,
      lastOkAt: '2026-10-15T09:00:00',
      lastFailAt: '2026-10-15T03:00:00',
    },
    points: [
      { date: '2026-10-14', total: 2, ok: 1, failed: 1, items: 20, rate: 0.5 },
      { date: '2026-10-15', total: 2, ok: 2, failed: 0, items: 40, rate: 1 },
    ],
    failures: [{ at: '2026-10-15T03:00:00', error: 'ReadTimeout', kind: 'transient', items: 0 }],
  };

  const stubHealth = (data: unknown) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond(data)),
    );
  };

  it('fetchSources 带回窗口天数（成功率没有时间范围就无法解读）', async () => {
    const page = await fetchSources();
    expect(page.healthDays).toBe(7);
    expect(page.list).toHaveLength(1);
  });

  it('healthDays 缺失时保守回落为 7 而不是 0', async () => {
    stubHealth({ list: [RAW_SOURCE] });
    expect((await fetchSources()).healthDays).toBe(7);
  });

  it('源列表的 health 摘要原样透出', async () => {
    const [s] = (await fetchSources()).list;
    expect(s.health).toEqual({ attempts: 24, rate: 0.9583, avgItems: 18.4 });
  });

  it('health 缺失时是 null 而不是全零摘要（"没抓过"要能看出来）', async () => {
    // 显式删掉该键：用 RAW_SOURCE 里已有的值测不出"缺失"这条分支
    const legacy: Record<string, unknown> = { ...RAW_SOURCE };
    delete legacy.health;
    stubHealth({ list: [legacy] });
    const [s] = (await fetchSources()).list;
    expect(s.health).toBeNull();
  });

  it('health 是垃圾对象时回落为 null', async () => {
    stubHealth({ list: [{ ...RAW_SOURCE, health: { attempts: '很多' } }] });
    const [s] = (await fetchSources()).list;
    expect(s.health).toBeNull();
  });

  it('成功率被收敛到 0–1（后端异常值不该把图表撑爆）', async () => {
    stubHealth({ list: [{ ...RAW_SOURCE, health: { attempts: 4, rate: 3.5, avgItems: 10 } }] });
    const [s] = (await fetchSources()).list;
    expect(s.health?.rate).toBe(1);
  });

  it('fetchSourceHealth 请求单源趋势并解析三段数据', async () => {
    stubHealth(RAW_HEALTH);
    const h = await fetchSourceHealth('people_scitech');
    expect(lastCall()?.[0] as string).toBe('/api/admin/sources/people_scitech/health');
    expect(h.days).toBe(30);
    expect(h.summary).toMatchObject({ attempts: 10, ok: 8, failed: 2, rate: 0.8, avgItems: 20 });
    expect(h.points).toHaveLength(2);
    expect(h.failures[0]).toMatchObject({ error: 'ReadTimeout', kind: 'transient' });
  });

  it('days 参数拼进查询串', async () => {
    stubHealth(RAW_HEALTH);
    await fetchSourceHealth('k', 7);
    expect(lastCall()?.[0] as string).toBe('/api/admin/sources/k/health?days=7');
  });

  it('days 为 0 时不拼查询串（交给后端收敛到保留期）', async () => {
    stubHealth(RAW_HEALTH);
    await fetchSourceHealth('k', 0);
    expect(lastCall()?.[0] as string).toBe('/api/admin/sources/k/health');
  });

  it('坏点逐条丢弃，而不是让整个趋势消失', async () => {
    stubHealth({
      ...RAW_HEALTH,
      points: [{ date: 'bad-date', total: 1 }, RAW_HEALTH.points[0]],
      failures: [{ error: '没有时间' }, RAW_HEALTH.failures[0]],
    });
    const h = await fetchSourceHealth('k');
    expect(h.points).toHaveLength(1);
    expect(h.failures).toHaveLength(1);
  });

  it('整段缺失时回落为空序列而不是抛错', async () => {
    stubHealth({});
    const h = await fetchSourceHealth('k');
    expect(h.points).toEqual([]);
    expect(h.failures).toEqual([]);
    expect(h.summary.attempts).toBe(0);
    // rate 是 null 而不是 0：没抓过 ≠ 全挂
    expect(h.summary.rate).toBeNull();
  });

  it('kind 不认识时回落为 null（不猜失败性质）', async () => {
    stubHealth({
      ...RAW_HEALTH,
      failures: [{ at: '2026-10-15T03:00:00', error: 'boom', kind: 'fatal' }],
    });
    const h = await fetchSourceHealth('k');
    expect(h.failures[0].kind).toBeNull();
  });
});

// P1-20：资讯审核与下架。这一层的关键是**归一化不猜状态**——
// hidden 缺失按可见兜底（老后端），isTop 非 true 一律 false（不猜置顶态），
// 坏行整行丢弃（没有 id 的行无法定位，下架/恢复/置顶全都按 id 操作）。
describe('api/admin（资讯审核与下架，P1-20）', () => {
  const stubNews = (data: unknown) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond(data)),
    );
  };

  /** 列表信封：多数用例只关心 list 本身，兜底值单独覆盖 */
  const envelope = (over: Record<string, unknown> = {}) => ({
    list: [RAW_NEWS],
    total: 1,
    hasMore: false,
    nextCursor: null,
    ...over,
  });

  it('fetchAdminNews 不带参数时请求裸路径，并解析信封', async () => {
    stubNews(envelope());
    const page = await fetchAdminNews();
    expect(lastCall()?.[0] as string).toBe('/api/admin/news');
    expect(page.list).toHaveLength(1);
    expect(page.total).toBe(1);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('查询串带上筛选与分页参数，keyword 做 trim', async () => {
    stubNews(envelope());
    await fetchAdminNews({
      status: 'hidden',
      keyword: '  发布会  ',
      channel: 'tech',
      limit: 20,
      cursor: 'c1',
    });
    // 顺序即插入顺序：status → keyword → channel → limit → cursor
    expect(lastCall()?.[0] as string).toBe(
      '/api/admin/news?status=hidden&keyword=%E5%8F%91%E5%B8%83%E4%BC%9A&channel=tech&limit=20&cursor=c1',
    );
  });

  it('纯空白 keyword 不拼进查询串（否则等于搜全部，还多一次无效请求）', async () => {
    stubNews(envelope());
    await fetchAdminNews({ keyword: '   ' });
    expect(lastCall()?.[0] as string).toBe('/api/admin/news');
  });

  it('信封整段缺失时回落为空列表与零值，不抛', async () => {
    stubNews({});
    const page = await fetchAdminNews();
    expect(page.list).toEqual([]);
    expect(page.total).toBe(0);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('整行归一化：hidden 三件套与公开字段一起透出', async () => {
    stubNews(
      envelope({
        list: [
          {
            ...RAW_NEWS,
            hidden: true,
            hiddenAt: '2026-10-15T09:30:00',
            hiddenReason: '来源不可靠',
          },
        ],
      }),
    );
    const [n] = (await fetchAdminNews()).list;
    expect(n).toMatchObject({
      id: 'n_1',
      title: '某 AI 新品发布',
      hidden: true,
      hiddenAt: '2026-10-15T09:30:00',
      hiddenReason: '来源不可靠',
      isTop: false,
      channel: 'tech',
      hotScore: 12.3456,
      publishedAt: '2026-10-15T08:00:00',
      commentCount: 12,
    });
  });

  it('缺 id 的行被丢弃（下架/恢复/置顶都按 id 定位）', async () => {
    stubNews(envelope({ list: [{ title: '没有 id' }, RAW_NEWS] }));
    const page = await fetchAdminNews();
    expect(page.list.map((n) => n.id)).toEqual(['n_1']);
  });

  it('hidden 三件套缺失（老后端）时按可见兜底，而不是 undefined', async () => {
    // 显式删键：用 RAW_NEWS 里已有的 false/null 测不出"缺失"这条分支
    const legacy: Record<string, unknown> = { ...RAW_NEWS };
    delete legacy.hidden;
    delete legacy.hiddenAt;
    delete legacy.hiddenReason;
    stubNews(envelope({ list: [legacy] }));
    const [n] = (await fetchAdminNews()).list;
    expect(n.hidden).toBe(false);
    expect(n.hiddenAt).toBeNull();
    expect(n.hiddenReason).toBeNull();
  });

  it('hiddenReason / hiddenAt 空串归一化为 null', async () => {
    stubNews(
      envelope({
        list: [{ ...RAW_NEWS, hidden: true, hiddenAt: '', hiddenReason: '' }],
      }),
    );
    const [n] = (await fetchAdminNews()).list;
    expect(n.hiddenAt).toBeNull();
    expect(n.hiddenReason).toBeNull();
  });

  it('isTop 非 true 一律 false（不猜置顶态）', async () => {
    stubNews(envelope({ list: [{ ...RAW_NEWS, isTop: 'yes' }] }));
    expect((await fetchAdminNews()).list[0].isTop).toBe(false);
  });

  it('hotScore / commentCount 是垃圾值时回落为 0，不产生 NaN', async () => {
    stubNews(envelope({ list: [{ ...RAW_NEWS, hotScore: '很高', commentCount: null }] }));
    const [n] = (await fetchAdminNews()).list;
    expect(n.hotScore).toBe(0);
    expect(n.commentCount).toBe(0);
  });

  it('title 缺失时回落为 id（界面至少有可定位的一行）', async () => {
    stubNews(envelope({ list: [{ ...RAW_NEWS, title: '' }] }));
    expect((await fetchAdminNews()).list[0].title).toBe('n_1');
  });

  it('非法 channel 归一化为默认频道', async () => {
    stubNews(envelope({ list: [{ ...RAW_NEWS, channel: 'tecn' }] }));
    expect((await fetchAdminNews()).list[0].channel).toBe('recommend');
  });

  it('hideNews 走 POST 并带上理由，id 被编码', async () => {
    stubNews({
      ...RAW_NEWS,
      hidden: true,
      hiddenAt: '2026-10-15T10:00:00',
      hiddenReason: '内容错误',
    });
    const n = await hideNews('a/b', { reason: '内容错误' });
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/news/a%2Fb/hide');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ reason: '内容错误' });
    expect(n.hidden).toBe(true);
    expect(n.hiddenReason).toBe('内容错误');
  });

  it('hideNews 响应无法解析时抛错（而不是把空壳当成功）', async () => {
    stubNews({ title: '没有 id' });
    await expect(hideNews('n_1', { reason: 'x' })).rejects.toThrow('下架成功但响应无法解析');
  });

  it('restoreNews 走 POST 且不带 body', async () => {
    stubNews({ ...RAW_NEWS, hidden: false });
    const n = await restoreNews('n_1');
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/news/n_1/restore');
    expect(init.method).toBe('POST');
    expect(init.body).toBeUndefined();
    expect(n.hidden).toBe(false);
  });

  it('updateNewsTop 走 PATCH，body 焊死驼峰 isTop（蛇形会被后端静默丢弃）', async () => {
    stubNews({ ...RAW_NEWS, isTop: true });
    await updateNewsTop('n_1', { isTop: true });
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/news/n_1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ isTop: true });
  });

  it('后端 422（空理由）向上抛错，供页面原地展示', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(422, '下架理由不能为空')),
    );
    await expect(hideNews('n_1', { reason: '' })).rejects.toThrow('下架理由不能为空');
  });
});

// P1-22：评论治理。这一层的关键是**驼峰查询参数**——`newsId` 写成蛇形
// `news_id` 会被 FastAPI 静默忽略（不报错、不过滤），表现为「按文章筛选
// 永不生效」而前后端各自单测都不失败（与 P1-18 respectRobots 同款缝，
// 见仓库记忆 #8）。hidden 缺省按可见兜底；userId 缺失回落 null（匿名评论）。
describe('api/admin（评论治理，P1-22）', () => {
  const RAW_COMMENT = {
    id: 'c_1',
    newsId: 'n_1',
    newsTitle: '某 AI 新品发布',
    userId: 7,
    userName: '网友甲',
    content: '这是一条评论内容',
    createdAt: '2026-10-15T09:00:00',
    likes: 3,
    parentId: null,
    rootId: null,
    replyCount: 2,
    isDeleted: false,
    hidden: false,
    hiddenAt: null,
    hiddenReason: null,
  };

  const stubComments = (data: unknown) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond(data)),
    );
  };

  /** 列表信封：多数用例只关心 list 本身，兜底值单独覆盖 */
  const envelope = (over: Record<string, unknown> = {}) => ({
    list: [RAW_COMMENT],
    total: 1,
    hasMore: false,
    nextCursor: null,
    ...over,
  });

  it('fetchAdminComments 不带参数时请求裸路径，并解析信封', async () => {
    stubComments(envelope());
    const page = await fetchAdminComments();
    expect(lastCall()?.[0] as string).toBe('/api/admin/comments');
    expect(page.list).toHaveLength(1);
    expect(page.total).toBe(1);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('查询串带上筛选与分页参数，newsId 焊死驼峰，keyword 做 trim', async () => {
    stubComments(envelope());
    await fetchAdminComments({
      status: 'hidden',
      keyword: '  广告  ',
      newsId: 'n_1',
      limit: 20,
      cursor: 'c1',
    });
    // 顺序即插入顺序：status → keyword → newsId → limit → cursor
    expect(lastCall()?.[0] as string).toBe(
      '/api/admin/comments?status=hidden&keyword=%E5%B9%BF%E5%91%8A&newsId=n_1&limit=20&cursor=c1',
    );
  });

  it('纯空白 keyword 不拼进查询串（否则等于搜全部，还多一次无效请求）', async () => {
    stubComments(envelope());
    await fetchAdminComments({ keyword: '   ' });
    expect(lastCall()?.[0] as string).toBe('/api/admin/comments');
  });

  it('信封整段缺失时回落为空列表与零值，不抛', async () => {
    stubComments({});
    const page = await fetchAdminComments();
    expect(page.list).toEqual([]);
    expect(page.total).toBe(0);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('整行归一化：hidden 三件套与回复结构（rootId/parentId/replyCount）一起透出', async () => {
    stubComments(
      envelope({
        list: [
          {
            ...RAW_COMMENT,
            hidden: true,
            hiddenAt: '2026-10-15T10:00:00',
            hiddenReason: '含广告信息',
          },
        ],
      }),
    );
    const [c] = (await fetchAdminComments()).list;
    expect(c).toMatchObject({
      id: 'c_1',
      newsId: 'n_1',
      newsTitle: '某 AI 新品发布',
      userName: '网友甲',
      userId: 7,
      likes: 3,
      rootId: null,
      parentId: null,
      replyCount: 2,
      hidden: true,
      hiddenAt: '2026-10-15T10:00:00',
      hiddenReason: '含广告信息',
    });
  });

  it('缺 id 的行被丢弃（隐藏/恢复都按 id 定位）', async () => {
    stubComments(envelope({ list: [{ content: '没有 id' }, RAW_COMMENT] }));
    const page = await fetchAdminComments();
    expect(page.list.map((c) => c.id)).toEqual(['c_1']);
  });

  it('hidden 三件套缺失（老后端）时按可见兜底，而不是 undefined', async () => {
    // 显式删键：用 RAW_COMMENT 里已有的 false/null 测不出"缺失"这条分支
    const legacy: Record<string, unknown> = { ...RAW_COMMENT };
    delete legacy.hidden;
    delete legacy.hiddenAt;
    delete legacy.hiddenReason;
    stubComments(envelope({ list: [legacy] }));
    const [c] = (await fetchAdminComments()).list;
    expect(c.hidden).toBe(false);
    expect(c.hiddenAt).toBeNull();
    expect(c.hiddenReason).toBeNull();
  });

  it('userName 缺失回落「匿名」，userId 缺失回落 null', async () => {
    stubComments(envelope({ list: [{ ...RAW_COMMENT, userName: '', userId: undefined }] }));
    const [c] = (await fetchAdminComments()).list;
    expect(c.userName).toBe('匿名');
    expect(c.userId).toBeNull();
  });

  it('likes / replyCount 是垃圾值时回落为 0，不产生 NaN', async () => {
    stubComments(envelope({ list: [{ ...RAW_COMMENT, likes: '很多', replyCount: null }] }));
    const [c] = (await fetchAdminComments()).list;
    expect(c.likes).toBe(0);
    expect(c.replyCount).toBe(0);
  });

  it('hideComment 走 POST 并带上理由，id 被编码', async () => {
    stubComments({
      ...RAW_COMMENT,
      hidden: true,
      hiddenAt: '2026-10-15T10:00:00',
      hiddenReason: '含广告信息',
    });
    const c = await hideComment('a/b', { reason: '含广告信息' });
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/comments/a%2Fb/hide');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ reason: '含广告信息' });
    expect(c.hidden).toBe(true);
    expect(c.hiddenReason).toBe('含广告信息');
  });

  it('hideComment 响应无法解析时抛错（而不是把空壳当成功）', async () => {
    stubComments({ content: '没有 id' });
    await expect(hideComment('c_1', { reason: 'x' })).rejects.toThrow('隐藏成功但响应无法解析');
  });

  it('restoreComment 走 POST 且不带 body', async () => {
    stubComments({ ...RAW_COMMENT, hidden: false });
    const c = await restoreComment('c_1');
    const [url, init] = lastCall() as unknown as [string, RequestInit];
    expect(url).toBe('/api/admin/comments/c_1/restore');
    expect(init.method).toBe('POST');
    expect(init.body).toBeUndefined();
    expect(c.hidden).toBe(false);
  });

  it('后端 422（空理由）向上抛错，供页面原地展示', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fail(422, '隐藏理由不能为空')),
    );
    await expect(hideComment('c_1', { reason: '' })).rejects.toThrow('隐藏理由不能为空');
  });
});

describe('api/admin（mock 模式）', () => {
  it('USE_MOCK=true 时明确拒绝，而不是伪造一份可增删的假列表', async () => {
    vi.resetModules();
    vi.doMock('@/api/config', () => ({ USE_MOCK: true, API_BASE: '' }));
    const mod = await import('@/api/admin');
    expect(mod.ADMIN_SOURCES_SUPPORTED).toBe(false);
    await expect(mod.fetchSources()).rejects.toThrow('mock 模式');
    vi.doUnmock('@/api/config');
    vi.resetModules();
  });

  it('USE_MOCK=true 时资讯审核三个接口同样明确拒绝', async () => {
    vi.resetModules();
    vi.doMock('@/api/config', () => ({ USE_MOCK: true, API_BASE: '' }));
    const mod = await import('@/api/admin');
    await expect(mod.fetchAdminNews()).rejects.toThrow('mock 模式');
    await expect(mod.hideNews('n_1', { reason: 'x' })).rejects.toThrow('mock 模式');
    await expect(mod.restoreNews('n_1')).rejects.toThrow('mock 模式');
    await expect(mod.updateNewsTop('n_1', { isTop: true })).rejects.toThrow('mock 模式');
    vi.doUnmock('@/api/config');
    vi.resetModules();
  });

  it('USE_MOCK=true 时评论治理三个接口同样明确拒绝', async () => {
    vi.resetModules();
    vi.doMock('@/api/config', () => ({ USE_MOCK: true, API_BASE: '' }));
    const mod = await import('@/api/admin');
    await expect(mod.fetchAdminComments()).rejects.toThrow('mock 模式');
    await expect(mod.hideComment('c_1', { reason: 'x' })).rejects.toThrow('mock 模式');
    await expect(mod.restoreComment('c_1')).rejects.toThrow('mock 模式');
    vi.doUnmock('@/api/config');
    vi.resetModules();
  });
});
