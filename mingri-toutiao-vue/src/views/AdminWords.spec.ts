import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 敏感词库没有 mock 短路：整条链路打桩在 api 层（与 AdminComments.spec.ts 同思路）
vi.mock('@/api/admin', () => ({
  fetchWords: vi.fn(async () => ({ list: [], total: 0 })),
  createWord: vi.fn(async () => ({})),
  updateWord: vi.fn(async () => ({})),
  deleteWord: vi.fn(async () => undefined),
  reloadWords: vi.fn(async () => 0),
  fetchModerationHits: vi.fn(async () => ({
    list: [],
    total: 0,
    hasMore: false,
    nextCursor: null,
  })),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import AdminWords from './AdminWords.vue';
import * as adminApi from '@/api/admin';
import type { SensitiveHitItem, SensitiveWordItem } from '@/types';

const word = (over: Partial<SensitiveWordItem> = {}): SensitiveWordItem => ({
  id: 1,
  word: '博彩',
  action: 'mask',
  category: '违规推广',
  enabled: true,
  hitCount: 12,
  createdBy: '13800138000',
  createdAt: '2026-10-15T09:00:00',
  updatedAt: '2026-10-15T09:00:00',
  ...over,
});

const hit = (over: Partial<SensitiveHitItem> = {}): SensitiveHitItem => ({
  id: 9,
  word: '博彩',
  action: 'mask',
  field: 'content',
  commentId: 'c_1',
  newsId: 'n_1',
  userId: 7,
  userName: '网友甲',
  snippet: '这评论区怎么全是博彩广告',
  createdAt: '2026-10-15T10:50:00',
  ...over,
});

const mockWords = (rows: SensitiveWordItem[], total = rows.length) =>
  vi.mocked(adminApi.fetchWords).mockResolvedValue({ list: rows, total });

const mockHits = (
  rows: SensitiveHitItem[],
  over: { total?: number; hasMore?: boolean; nextCursor?: number | null } = {},
) =>
  vi.mocked(adminApi.fetchModerationHits).mockResolvedValue({
    list: rows,
    total: over.total ?? rows.length,
    hasMore: over.hasMore ?? false,
    nextCursor: over.nextCursor ?? null,
  });

const mountPage = () => mount(AdminWords);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

/** 词库行（li）列表——命中日志也是 .admin__item，必须按 data-word 限定 */
const wordRows = (w: ReturnType<typeof mountPage>) => w.findAll('li[data-word]');
const hitRows = (w: ReturnType<typeof mountPage>) => w.findAll('li[data-hit-word]');

/** 某一行内的按钮（「删除」在两处列表里都可能出现） */
const rowBtn = (row: ReturnType<typeof wordRows>[number], text: string) =>
  row.findAll('button').find((b) => b.text() === text)!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockWords([]);
  mockHits([]);
  vi.mocked(adminApi.createWord).mockResolvedValue(word({ id: 2, word: '加微信' }));
  vi.mocked(adminApi.updateWord).mockResolvedValue(word({ enabled: false }));
});

describe('views/AdminWords', () => {
  it('非管理员不请求词库，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchWords).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理敏感词');
  });

  it('管理员加载后渲染词库：词、动作、分组、命中次数与添加人', async () => {
    mockWords([word(), word({ id: 2, word: '加微信', action: 'block', category: '广告引流' })]);
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('博彩');
    expect(w.text()).toContain('脱敏');
    expect(w.text()).toContain('违规推广');
    expect(w.text()).toContain('12 次命中');
    expect(w.text()).toContain('由 13800138000 添加');
    expect(w.text()).toContain('拦截');
    expect(w.text()).toContain('共 2 条');
  });

  it('已停用的词整行降透明度并打上「已停用」标记', async () => {
    mockWords([word({ enabled: false })]);
    const w = mountPage();
    await settle(w);

    expect(wordRows(w)[0].classes()).toContain('is-off');
    expect(w.text()).toContain('已停用');
    // 停用后按钮语义反过来：点它是「启用」
    expect(rowBtn(wordRows(w)[0], '启用').exists()).toBe(true);
  });

  it('空词库给出空态，并说明"没有词就不会拦截"', async () => {
    mockWords([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('词库还是空的');
  });

  it('后端 403 时展示无权限态', async () => {
    vi.mocked(adminApi.fetchWords).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理敏感词');
  });

  it('加载失败（非 403）时显示错误横幅，不伪装成无权限', async () => {
    vi.mocked(adminApi.fetchWords).mockRejectedValue(new Error('网络错误'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络错误');
    expect(w.text()).not.toContain('无权限管理敏感词');
  });
});

/* ------------------------------ 新增 ------------------------------ */

describe('views/AdminWords 新增与维护', () => {
  it('空词被前端拦下，不发请求', async () => {
    mockWords([]);
    const w = mountPage();
    await settle(w);

    await w.find('input[aria-label="敏感词"]').setValue('   ');
    await w.find('input[aria-label="敏感词"]').trigger('keyup.enter');
    await w.vm.$nextTick();
    expect(adminApi.createWord).not.toHaveBeenCalled();
    expect(w.text()).toContain('请填写敏感词');
  });

  it('新增成功：就地插入列表、清空输入并提示已生效', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.createWord).mockResolvedValue(
      word({ id: 3, word: '内部消息', action: 'block', category: '违规推广', hitCount: 0 }),
    );
    const w = mountPage();
    await settle(w);

    const input = w.find('input[aria-label="敏感词"]');
    await input.setValue('内部消息');
    await w.find('select').setValue('block');
    await w.find('input[aria-label="分组"]').setValue('违规推广');
    await input.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.createWord).toHaveBeenCalledWith({
      word: '内部消息',
      action: 'block',
      category: '违规推广',
      enabled: true,
    });
    expect(w.text()).toContain('已添加「内部消息」（拦截），立即生效');
    expect(wordRows(w)).toHaveLength(2);
    expect(w.text()).toContain('共 2 条');
    // 输入框清空，便于连续录入
    expect((w.find('input[aria-label="敏感词"]').element as HTMLInputElement).value).toBe('');
  });

  it('后端拒绝（太短 42201 / 重复 40905）时错误留在表单下方', async () => {
    mockWords([]);
    vi.mocked(adminApi.createWord).mockRejectedValue(new Error('敏感词至少需 2 个有效字符'));
    const w = mountPage();
    await settle(w);

    await w.find('input[aria-label="敏感词"]').setValue('傻!');
    await btn(w, '添加').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('敏感词至少需 2 个有效字符');
    expect(wordRows(w)).toHaveLength(0);
  });

  it('启停切换调用 updateWord 并就地更新行', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.updateWord).mockResolvedValue(word({ enabled: false }));
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '停用').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateWord).toHaveBeenCalledWith(1, { enabled: false });
    expect(w.text()).toContain('已停用「博彩」');
    expect(wordRows(w)[0].classes()).toContain('is-off');
  });

  it('删除要二次确认，确认后从列表移除', async () => {
    mockWords([word(), word({ id: 2, word: '加微信' })]);
    const w = mountPage();
    await settle(w);

    const row = wordRows(w)[0];
    await rowBtn(row, '删除').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.deleteWord).not.toHaveBeenCalled(); // 第一次点击只是展开确认
    expect(rowBtn(wordRows(w)[0], '确认删除').exists()).toBe(true);

    await rowBtn(wordRows(w)[0], '确认删除').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.deleteWord).toHaveBeenCalledWith(1);
    expect(w.text()).toContain('已删除「博彩」');
    expect(wordRows(w)).toHaveLength(1);
    expect(w.text()).toContain('共 1 条');
  });

  it('二次确认可以取消', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '删除').trigger('click');
    await w.vm.$nextTick();
    await rowBtn(wordRows(w)[0], '取消').trigger('click');
    await w.vm.$nextTick();

    expect(
      wordRows(w)[0]
        .findAll('button')
        .map((b) => b.text()),
    ).toContain('删除');
    expect(adminApi.deleteWord).not.toHaveBeenCalled();
  });

  it('重载索引提示生效条数', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.reloadWords).mockResolvedValue(4);
    const w = mountPage();
    await settle(w);

    await btn(w, '重载索引').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.text()).toContain('已重载词库索引（4 条生效）');
  });
});

/* ------------------------------ 筛选 ------------------------------ */

describe('views/AdminWords 筛选', () => {
  it('动作筛选带上 action 重新请求，再点一次取消', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: 'block' }),
    );

    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchWords).toHaveBeenLastCalledWith(expect.objectContaining({ action: '' }));
  });

  it('「仅看已启用」带上 enabled=true', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '仅看已启用').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: true }),
    );
  });

  it('搜索回车带上 keyword 重新请求', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('博彩');
    await search.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '博彩' }),
    );
  });

  it('清除筛选复位状态**并重新拉取**（状态复位 ≠ 数据复位）', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    await btn(w, '清除筛选').trigger('click');
    await new Promise((r) => setTimeout(r, 0));

    expect(adminApi.fetchWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: '', keyword: '' }),
    );
    // 服务端筛选：清除后必须真的发过一次请求，不能只改本地状态
    expect(adminApi.fetchWords).toHaveBeenCalledTimes(3);
  });

  it('筛选无结果时提示清除筛选', async () => {
    mockWords([]);
    const w = mountPage();
    await settle(w);
    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.text()).toContain('没有匹配的词');
  });
});

/* ------------------------------ 命中日志 ------------------------------ */

describe('views/AdminWords 命中日志', () => {
  it('渲染命中行：词、动作、字段、片段、作者与时间', async () => {
    mockWords([word()]);
    mockHits([hit()]);
    const w = mountPage();
    await settle(w);

    const row = hitRows(w)[0];
    expect(row.text()).toContain('命中「博彩」');
    expect(row.text()).toContain('已脱敏');
    expect(row.text()).toContain('正文');
    expect(row.text()).toContain('这评论区怎么全是博彩广告');
    expect(row.text()).toContain('网友甲');
    expect(row.text()).toContain('2026-10-15 10:50');
    expect(row.text()).toContain('评论 c_1');
  });

  it('block 命中显示「已拦截」，未落库的显示「未落库」而不是评论 id', async () => {
    mockWords([word()]);
    mockHits([hit({ action: 'block', commentId: null })]);
    const w = mountPage();
    await settle(w);

    expect(hitRows(w)[0].text()).toContain('已拦截');
    expect(hitRows(w)[0].text()).toContain('未落库');
  });

  it('昵称命中的行标注「昵称」', async () => {
    mockWords([word()]);
    mockHits([hit({ field: 'name', snippet: '博彩小妹' })]);
    const w = mountPage();
    await settle(w);
    expect(hitRows(w)[0].text()).toContain('昵称');
  });

  it('字段筛选带 field 重新请求，再点一次取消', async () => {
    mockWords([word()]);
    mockHits([hit()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '昵称').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchModerationHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ field: 'name' }),
    );

    await btn(w, '昵称').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchModerationHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ field: '' }),
    );
  });

  it('空命中列表给出空态文案', async () => {
    mockWords([word()]);
    mockHits([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('还没有命中记录');
  });

  it('加载更多追加第二批并带上 cursor', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.fetchModerationHits)
      .mockResolvedValueOnce({ list: [hit()], total: 3, hasMore: true, nextCursor: 5 })
      .mockResolvedValueOnce({
        list: [hit({ id: 4, word: '加微信', action: 'block' })],
        total: 3,
        hasMore: false,
        nextCursor: null,
      });
    const w = mountPage();
    await settle(w);
    expect(hitRows(w)).toHaveLength(1);

    await btn(w, '加载更多').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.fetchModerationHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 5 }),
    );
    expect(hitRows(w)).toHaveLength(2);
  });

  it('没有更多时不出加载更多按钮', async () => {
    mockWords([word()]);
    mockHits([hit()]);
    const w = mountPage();
    await settle(w);
    expect(w.findAll('button').some((b) => b.text() === '加载更多')).toBe(false);
  });

  it('页面顶部说明归一化策略（大小写/全半角/空格标点不影响判定）', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('归一化');
    expect(w.text()).toContain('全半角');
  });
});
