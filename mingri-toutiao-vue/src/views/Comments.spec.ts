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

const mountPage = () => mount(Comments, { global: { stubs: { AppNav: true } } });
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
});
