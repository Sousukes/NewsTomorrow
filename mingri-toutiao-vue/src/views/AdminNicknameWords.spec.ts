import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 昵称词库没有 mock 短路：整条链路打桩在 api 层（与 AdminWords.spec.ts 同思路）
vi.mock('@/api/admin', () => ({
  fetchNicknameWords: vi.fn(async () => ({ list: [], total: 0 })),
  createNicknameWord: vi.fn(async () => ({})),
  updateNicknameWord: vi.fn(async () => ({})),
  deleteNicknameWord: vi.fn(async () => undefined),
  reloadNicknameWords: vi.fn(async () => 0),
  fetchNicknameHits: vi.fn(async () => ({
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

import AdminNicknameWords from './AdminNicknameWords.vue';
import * as adminApi from '@/api/admin';
import type { NicknameHitItem, NicknameWordItem } from '@/types';

const word = (over: Partial<NicknameWordItem> = {}): NicknameWordItem => ({
  id: 1,
  word: '坏蛋',
  action: 'block',
  category: '辱骂',
  aliases: [],
  enabled: true,
  hitCount: 8,
  createdBy: '13800138000',
  createdAt: '2026-10-15T09:00:00',
  updatedAt: '2026-10-15T09:00:00',
  ...over,
});

const hit = (over: Partial<NicknameHitItem> = {}): NicknameHitItem => ({
  id: 9,
  word: '坏蛋',
  matched: '坏蛋',
  action: 'block',
  source: 'update',
  userId: 7,
  userName: '网友甲',
  snippet: '坏蛋哥',
  createdAt: '2026-10-15T10:50:00',
  ...over,
});

const mockWords = (rows: NicknameWordItem[], total = rows.length) =>
  vi.mocked(adminApi.fetchNicknameWords).mockResolvedValue({ list: rows, total });

const mockHits = (
  rows: NicknameHitItem[],
  over: { total?: number; hasMore?: boolean; nextCursor?: number | null } = {},
) =>
  vi.mocked(adminApi.fetchNicknameHits).mockResolvedValue({
    list: rows,
    total: over.total ?? rows.length,
    hasMore: over.hasMore ?? false,
    nextCursor: over.nextCursor ?? null,
  });

const mountPage = () => mount(AdminNicknameWords);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

/** 词库行（li）列表——命中日志也是 .admin__item，必须按 data-word 限定 */
const wordRows = (w: ReturnType<typeof mountPage>) => w.findAll('li[data-word]');
const hitRows = (w: ReturnType<typeof mountPage>) => w.findAll('li[data-hit-word]');

/** 某一行内的按钮 */
const rowBtn = (row: ReturnType<typeof wordRows>[number], text: string) =>
  row.findAll('button').find((b) => b.text() === text)!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockWords([]);
  mockHits([]);
  vi.mocked(adminApi.createNicknameWord).mockResolvedValue(word({ id: 2, word: '加微信' }));
  vi.mocked(adminApi.updateNicknameWord).mockResolvedValue(word({ enabled: false }));
});

describe('views/AdminNicknameWords', () => {
  it('非管理员不请求词库，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameWords).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理昵称敏感词');
  });

  it('管理员加载后渲染词库：词、动作、分组、别名、命中次数与添加人', async () => {
    mockWords([
      word({ aliases: ['shabi', 'sb'] }),
      word({ id: 2, word: '广告哥', action: 'review', category: '广告引流', aliases: ['adgg'] }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('坏蛋');
    expect(w.text()).toContain('拦截');
    expect(w.text()).toContain('辱骂');
    expect(w.text()).toContain('shabi');
    expect(w.text()).toContain('sb');
    expect(w.text()).toContain('8 次命中');
    expect(w.text()).toContain('由 13800138000 添加');
    expect(w.text()).toContain('复核'); // 第二行的 review 动作
    expect(w.text()).toContain('共 2 条');
  });

  it('未配别名的词显示「未配别名」而不是空白', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('未配别名');
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
    vi.mocked(adminApi.fetchNicknameWords).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理昵称敏感词');
  });

  it('加载失败（非 403）时显示错误横幅，不伪装成无权限', async () => {
    vi.mocked(adminApi.fetchNicknameWords).mockRejectedValue(new Error('网络错误'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络错误');
    expect(w.text()).not.toContain('无权限管理昵称敏感词');
  });
});

/* ------------------------------ 新增 ------------------------------ */

describe('views/AdminNicknameWords 新增与维护', () => {
  it('空词被前端拦下，不发请求', async () => {
    mockWords([]);
    const w = mountPage();
    await settle(w);

    await w.find('input[aria-label="敏感词"]').setValue('   ');
    await w.find('input[aria-label="敏感词"]').trigger('keyup.enter');
    await w.vm.$nextTick();
    expect(adminApi.createNicknameWord).not.toHaveBeenCalled();
    expect(w.text()).toContain('请填写敏感词');
  });

  it('新增成功：别名按逗号拆分、去重，就地插入并提示已生效', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.createNicknameWord).mockResolvedValue(
      word({ id: 3, word: '广告哥', action: 'review', category: '广告引流', aliases: ['adgg'] }),
    );
    const w = mountPage();
    await settle(w);

    const input = w.find('input[aria-label="敏感词"]');
    await input.setValue('广告哥');
    await w.find('select[aria-label="命中后的处理方式"]').setValue('review');
    await w.find('input[aria-label="分组"]').setValue('广告引流');
    await w.find('input[aria-label="别名"]').setValue('adgg, adgg，广告哥哥');
    await input.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.createNicknameWord).toHaveBeenCalledWith({
      word: '广告哥',
      action: 'review',
      category: '广告引流',
      aliases: ['adgg', '广告哥哥'], // 重复项去重；中英文逗号都认
      enabled: true,
    });
    expect(w.text()).toContain('已添加「广告哥」（复核），立即生效');
    expect(wordRows(w)).toHaveLength(2);
    expect(w.text()).toContain('共 2 条');
    // 输入框清空，便于连续录入
    expect((w.find('input[aria-label="敏感词"]').element as HTMLInputElement).value).toBe('');
  });

  it('后端拒绝（太短 42204 / 重复 40906）时错误留在表单下方', async () => {
    mockWords([]);
    vi.mocked(adminApi.createNicknameWord).mockRejectedValue(
      new Error('昵称敏感词至少需 2 个有效字符（空格与标点不计）'),
    );
    const w = mountPage();
    await settle(w);

    await w.find('input[aria-label="敏感词"]').setValue('射');
    await btn(w, '添加').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('昵称敏感词至少需 2 个有效字符');
    expect(wordRows(w)).toHaveLength(0);
  });

  it('启停切换调用 updateNicknameWord 并就地更新行', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.updateNicknameWord).mockResolvedValue(word({ enabled: false }));
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '停用').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateNicknameWord).toHaveBeenCalledWith(1, { enabled: false });
    expect(w.text()).toContain('已停用「坏蛋」');
    expect(wordRows(w)[0].classes()).toContain('is-off');
  });

  it('行内改动作：block ↔ review 走 PATCH 并就地替换行', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.updateNicknameWord).mockResolvedValue(word({ action: 'review' }));
    const w = mountPage();
    await settle(w);

    const select = wordRows(w)[0].find('select[aria-label="修改命中后的处理方式"]');
    await select.setValue('review');
    await select.trigger('change');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateNicknameWord).toHaveBeenCalledWith(1, { action: 'review' });
    expect(w.text()).toContain('「坏蛋」已改为复核');
    expect(wordRows(w)[0].text()).toContain('复核');
  });

  it('改别名：展开编辑器预填当前别名，保存走 PATCH aliases', async () => {
    mockWords([word({ aliases: ['shabi'] })]);
    vi.mocked(adminApi.updateNicknameWord).mockResolvedValue(
      word({ aliases: ['shabi', 'sb', '煞笔'] }),
    );
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '改别名').trigger('click');
    await w.vm.$nextTick();
    const editor = w.find('input[aria-label="编辑别名"]');
    expect((editor.element as HTMLInputElement).value).toBe('shabi');

    await editor.setValue('shabi, sb，煞笔');
    await w.find('input[aria-label="编辑别名"]').trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateNicknameWord).toHaveBeenCalledWith(1, {
      aliases: ['shabi', 'sb', '煞笔'],
    });
    expect(w.text()).toContain('「坏蛋」别名已更新');
    // 保存后编辑器收起
    expect(w.find('input[aria-label="编辑别名"]').exists()).toBe(false);
  });

  it('别名无变化时不发请求，只收起编辑器', async () => {
    mockWords([word({ aliases: ['shabi', 'sb'] })]);
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '改别名').trigger('click');
    await w.vm.$nextTick();
    await w.find('input[aria-label="编辑别名"]').trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateNicknameWord).not.toHaveBeenCalled();
    expect(w.find('input[aria-label="编辑别名"]').exists()).toBe(false);
  });

  it('清空别名输入 = 清空全部别名', async () => {
    mockWords([word({ aliases: ['shabi'] })]);
    vi.mocked(adminApi.updateNicknameWord).mockResolvedValue(word({ aliases: [] }));
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '改别名').trigger('click');
    await w.vm.$nextTick();
    await w.find('input[aria-label="编辑别名"]').setValue('');
    await rowBtn(wordRows(w)[0], '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.updateNicknameWord).toHaveBeenCalledWith(1, { aliases: [] });
    expect(w.text()).toContain('未配别名');
  });

  it('删除要二次确认，确认后从列表移除', async () => {
    mockWords([word(), word({ id: 2, word: '加微信' })]);
    const w = mountPage();
    await settle(w);

    await rowBtn(wordRows(w)[0], '删除').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.deleteNicknameWord).not.toHaveBeenCalled(); // 第一次点击只是展开确认
    expect(rowBtn(wordRows(w)[0], '确认删除').exists()).toBe(true);

    await rowBtn(wordRows(w)[0], '确认删除').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.deleteNicknameWord).toHaveBeenCalledWith(1);
    expect(w.text()).toContain('已删除「坏蛋」');
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
    expect(adminApi.deleteNicknameWord).not.toHaveBeenCalled();
  });

  it('重载索引提示生效条数', async () => {
    mockWords([word()]);
    vi.mocked(adminApi.reloadNicknameWords).mockResolvedValue(5);
    const w = mountPage();
    await settle(w);

    await btn(w, '重载索引').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.text()).toContain('已重载昵称词库索引（5 条生效）');
  });

  it('提供跳转复核队列的入口', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '复核队列').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/nickname-reviews');
  });
});

/* ------------------------------ 筛选 ------------------------------ */

describe('views/AdminNicknameWords 筛选', () => {
  it('动作筛选带上 action 重新请求，再点一次取消', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: 'block' }),
    );

    await btn(w, '拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: '' }),
    );
  });

  it('「仅看已启用」带上 enabled=true', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '仅看已启用').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: true }),
    );
  });

  it('搜索回车带上 keyword 重新请求', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('坏蛋');
    await search.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '坏蛋' }),
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

    expect(adminApi.fetchNicknameWords).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: '', keyword: '' }),
    );
    // 服务端筛选：清除后必须真的发过一次请求，不能只改本地状态
    expect(adminApi.fetchNicknameWords).toHaveBeenCalledTimes(3);
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

describe('views/AdminNicknameWords 命中日志', () => {
  it('渲染命中行：词、动作、入口、昵称原文与时间', async () => {
    mockWords([word()]);
    mockHits([hit()]);
    const w = mountPage();
    await settle(w);

    const row = hitRows(w)[0];
    expect(row.text()).toContain('命中「坏蛋」');
    expect(row.text()).toContain('已拦截');
    expect(row.text()).toContain('改名');
    expect(row.text()).toContain('坏蛋哥');
    expect(row.text()).toContain('网友甲');
    expect(row.text()).toContain('2026-10-15 10:50');
  });

  it('review 命中显示「待复核」，注册入口标注「注册」', async () => {
    mockWords([word()]);
    mockHits([hit({ action: 'review', source: 'register', userId: null, snippet: '广告哥' })]);
    const w = mountPage();
    await settle(w);

    expect(hitRows(w)[0].text()).toContain('待复核');
    expect(hitRows(w)[0].text()).toContain('注册');
  });

  it('实际命中的模式 ≠ 主词时点出来（这是"该补哪条别名"的依据）', async () => {
    mockWords([word()]);
    mockHits([hit({ matched: 'shabi' })]);
    const w = mountPage();
    await settle(w);
    expect(hitRows(w)[0].text()).toContain('实际命中「shabi」');
  });

  it('动作 / 入口筛选带上参数重新请求，再点一次取消', async () => {
    mockWords([word()]);
    mockHits([hit()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '已拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: 'block' }),
    );

    await btn(w, '已拦截').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: '' }),
    );

    await btn(w, '注册').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameHits).toHaveBeenLastCalledWith(
      expect.objectContaining({ source: 'register' }),
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
    vi.mocked(adminApi.fetchNicknameHits)
      .mockResolvedValueOnce({ list: [hit()], total: 3, hasMore: true, nextCursor: 5 })
      .mockResolvedValueOnce({
        list: [hit({ id: 4, word: '广告哥', action: 'review' })],
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

    expect(adminApi.fetchNicknameHits).toHaveBeenLastCalledWith(
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

  it('页面顶部说明三级动作与不回显命中词', async () => {
    mockWords([word()]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('不回显命中词');
    expect(w.text()).toContain('别名');
  });
});
