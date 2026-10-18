import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, type VueWrapper } from '@vue/test-utils';

/**
 * 评论视图：两级结构的**展开/收起**与发表/回复的接线。
 *
 * store 侧的乐观更新与失败回滚由 `stores/comments.spec.ts` 覆盖；这里补的是
 * 视图独有的逻辑——「未展开 = 只显示最早 2 条预览」这条规则只存在于视图里，
 * 曾经因为在 store 里实现过又被挪走，才值得钉一根钉子。
 */

const byArticle: Record<string, unknown[]> = {};
const ensure = vi.fn(async () => undefined);
const loadReplies = vi.fn(async () => undefined);
const loadMoreReplies = vi.fn(async () => undefined);
const add = vi.fn();
const reply = vi.fn();
const remove = vi.fn(async () => true);
const toggleLike = vi.fn();
const count = vi.fn(() => 0);
// E6：排序真相源在 store 里（`sortOf`），切换动作也由 store 承担
const setSort = vi.fn(async () => true);
const sortOf = vi.fn(() => 'latest' as const);

vi.mock('@/stores/comments', () => ({
  useCommentsStore: () => ({
    byArticle,
    ensure,
    loadReplies,
    loadMoreReplies,
    add,
    reply,
    remove,
    toggleLike,
    count,
    setSort,
    sortOf,
  }),
}));

vi.mock('@/composables/useNewsDetail', () => ({
  useNewsDetail: () => ({
    item: { value: { id: 'n1', title: '标题' } },
    resolving: { value: false },
  }),
}));

let routeParams: Record<string, string> = { id: 'n1' };
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

import Comments from './Comments.vue';

/**
 * 举报弹窗用带名字的桩替代（E5）：这里只验**入口接线**（点了哪条评论、传了什么 id），
 * 弹窗自身的登录态/分类/提交由 `components/ReportDialog.spec.ts` 覆盖。
 * 桩必须显式命名，否则 `findComponent({ name })` 找不到（默认桩名是 `Xxx-stub`）。
 */
const ReportDialogStub = {
  name: 'ReportDialog',
  props: ['open', 'targetType', 'targetId'],
  template: '<div class="report-stub" />',
};

const mountPage = () =>
  mount(Comments, { global: { stubs: { AppNav: true, ReportDialog: ReportDialogStub } } });
const settle = async (w: VueWrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const vm = (w: VueWrapper) =>
  w.vm as unknown as {
    rows: {
      c: { id: string; name: string; content: string };
      isRoot: boolean;
      showMore: boolean;
    }[];
    draft: string;
    replyDraft: string;
    replyTarget: { id: string; name: string } | null;
    send: () => void;
    sendReply: () => void;
    startReply: (c: { id: string; name: string }) => void;
    cancelReply: () => void;
    toggleExpand: (root: { id: string }) => Promise<void>;
    loadMore: (root: { id: string }) => void;
    toggleLike: (c: { id: string }) => void;
    remove: (c: { id: string }) => Promise<void>;
  };

/** 构造一条符合 `stores/comments` 里 Comment 形状的评论（模板会读 seed/time/mine） */
function comment(id: string, name: string, content: string, replies: unknown[] = []) {
  return {
    id,
    name,
    seed: id,
    time: '刚刚',
    content,
    likes: 0,
    liked: false,
    mine: false,
    replies,
  };
}

function setRoot(replies: ReturnType<typeof comment>[]) {
  byArticle.n1 = [comment('c1', '甲', '主评论', replies)];
}

beforeEach(() => {
  vi.clearAllMocks();
  routeParams = { id: 'n1' };
  delete byArticle.n1;
  remove.mockResolvedValue(true);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('Comments', () => {
  it('挂载时按当前资讯确保评论已加载', async () => {
    const w = mountPage();
    await settle(w);
    expect(ensure).toHaveBeenCalledWith('n1');
  });

  it('未展开时只显示最早 2 条回复预览', async () => {
    setRoot([comment('r1', 'A', '一'), comment('r2', 'B', '二'), comment('r3', 'C', '三')]);
    const w = mountPage();
    await settle(w);
    expect(vm(w).rows).toHaveLength(3); // 1 主评论 + 2 预览回复
  });

  it('展开后显示全部回复并拉一次完整列表', async () => {
    setRoot([comment('r1', 'A', '一'), comment('r2', 'B', '二'), comment('r3', 'C', '三')]);
    const w = mountPage();
    await settle(w);
    await vm(w).toggleExpand({ id: 'c1' });
    await settle(w);
    expect(vm(w).rows).toHaveLength(4);
    expect(loadReplies).toHaveBeenCalledWith('n1', 'c1');
  });

  it('再次点击收起，不再请求', async () => {
    setRoot([comment('r1', 'A', '一')]);
    const w = mountPage();
    await settle(w);
    await vm(w).toggleExpand({ id: 'c1' });
    loadReplies.mockClear();
    await vm(w).toggleExpand({ id: 'c1' });
    expect(loadReplies).not.toHaveBeenCalled();
  });

  it('空草稿不发表（否则会发一条空评论）', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.draft = '   ';
    c.send();
    expect(add).not.toHaveBeenCalled();
  });

  it('发表成功后清空草稿', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.draft = ' 写得不错  ';
    c.send();
    expect(add).toHaveBeenCalledWith('n1', '我', '写得不错'); // 首尾空白被裁掉
    expect(c.draft).toBe('');
  });

  it('回复空内容或未选目标时不发送', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.replyDraft = '  ';
    c.sendReply();
    expect(reply).not.toHaveBeenCalled();
    c.replyTarget = { id: 'c1', name: '甲' };
    c.sendReply();
    expect(reply).not.toHaveBeenCalled();
  });

  it('回复成功后清空目标与草稿（否则下次回复还带着上次的对象）', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.startReply({ id: 'c1', name: '甲' });
    c.replyDraft = '同意';
    c.sendReply();
    expect(reply).toHaveBeenCalledWith('n1', 'c1', '我', '同意');
    expect(c.replyTarget).toBeNull();
    expect(c.replyDraft).toBe('');
  });

  it('取消回复清空目标', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.startReply({ id: 'c1', name: '甲' });
    c.cancelReply();
    expect(c.replyTarget).toBeNull();
  });

  it('删除需要二次确认；取消则不调用接口', async () => {
    setRoot([]);
    const w = mountPage();
    await settle(w);
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false);
    await vm(w).remove({ id: 'c1' });
    expect(remove).not.toHaveBeenCalled();
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true);
    await vm(w).remove({ id: 'c1' });
    expect(remove).toHaveBeenCalledWith('n1', 'c1');
  });

  it('点赞按文章与评论 id 透传', async () => {
    const w = mountPage();
    await settle(w);
    vm(w).toggleLike({ id: 'c9' });
    expect(toggleLike).toHaveBeenCalledWith('n1', 'c9');
  });

  it('举报入口只出现在他人的评论上（自己的评论给的是「删除」）', async () => {
    byArticle.n1 = [
      comment('c1', '甲', '他人评论'),
      { ...comment('c2', '我', '我的评论'), mine: true },
    ];
    const w = mountPage();
    await settle(w);

    const reportBtns = w.findAll('.cmt__report-btn');
    expect(reportBtns).toHaveLength(1);
    expect(reportBtns[0].attributes('aria-label')).toContain('他人评论');
    // 自己的评论不出现举报按钮
    expect(w.findAll('.cmt__delete')).toHaveLength(1);
  });

  it('点击举报打开弹窗并带上该条评论 id', async () => {
    byArticle.n1 = [comment('c7', '甲', '要举报的评论')];
    const w = mountPage();
    await settle(w);

    const dialog = () => w.findComponent({ name: 'ReportDialog' });
    expect(dialog().props('open')).toBe(false);

    await w.find('.cmt__report-btn').trigger('click');
    await settle(w);

    expect(dialog().props('open')).toBe(true);
    expect(dialog().props('targetType')).toBe('comment');
    expect(dialog().props('targetId')).toBe('c7');
  });
});

describe('Comments 排序切换（E6）', () => {
  it('默认「最新」为激活态', async () => {
    const w = mountPage();
    await settle(w);

    const tabs = w.findAll('.comments__sort');
    expect(tabs.map((t) => t.text())).toEqual(['最新', '最热']);
    expect(tabs[0].classes()).toContain('is-active');
    expect(tabs[1].classes()).not.toContain('is-active');
  });

  it('点「最热」把排序交给 store，激活态随之前移', async () => {
    const w = mountPage();
    await settle(w);

    await w.findAll('.comments__sort')[1].trigger('click');
    await settle(w);

    expect(setSort).toHaveBeenCalledWith('n1', 'hot');
    expect(w.findAll('.comments__sort')[1].classes()).toContain('is-active');
  });

  it('切换失败时回退激活态并提示（标签不能与列表真实顺序不一致）', async () => {
    setSort.mockResolvedValueOnce(false);
    const w = mountPage();
    await settle(w);

    await w.findAll('.comments__sort')[1].trigger('click');
    await settle(w);

    // sortOf 仍返回 latest → 激活态回到「最新」
    expect(w.findAll('.comments__sort')[0].classes()).toContain('is-active');
    expect(w.text()).toContain('切换排序失败');
  });

  it('切换排序会收起已展开的回复（列表被整体替换，展开态失去语境）', async () => {
    // 有回复但未带预览 → 渲染「查看全部」入口
    byArticle.n1 = [{ ...comment('c1', '甲', '主评论'), replyCount: 2 }];
    const w = mountPage();
    await settle(w);
    const expanded = (w.vm as unknown as { expanded: Set<string> }).expanded;

    await w.find('.cmt__more-btn').trigger('click');
    await settle(w);
    expect([...expanded]).toEqual(['c1']);

    await w.findAll('.comments__sort')[1].trigger('click');
    await settle(w);
    expect([...expanded]).toEqual([]);
  });
});
