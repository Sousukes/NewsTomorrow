import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

// 关闭 mock：让 store 走"调用真实接口"的分支（否则 USE_MOCK=true 时 remove 直接短路）
vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

vi.mock('@/api/comments', () => ({
  listComments: vi.fn(),
  listCommentsPage: vi.fn(),
  listRepliesPage: vi.fn(),
  createComment: vi.fn(),
  setCommentLike: vi.fn(),
  deleteComment: vi.fn(),
  // mock 分支专用；本文件 USE_MOCK=false 不会走到，但缺了它导入绑定是 undefined
  sortCommentsLocal: vi.fn((list: unknown[]) => list),
}));

import * as commentsApi from '@/api/comments';
import { useCommentsStore } from '@/stores/comments';
import type { Comment } from '@/types';

const flush = () => new Promise((r) => setTimeout(r, 0));

/** 造一条主评论（两级评论字段按主评论形态兜底） */
function root(over: Partial<Comment> = {}): Comment {
  return {
    id: 'r1',
    name: '主评论作者',
    seed: 'r1',
    content: '主评论内容',
    time: '1小时前',
    likes: 0,
    liked: false,
    userId: 1,
    mine: false,
    isDeleted: false,
    parentId: null,
    rootId: null,
    replyCount: 0,
    replies: [],
    replyToName: null,
    replyToUserId: null,
    ...over,
  };
}

/** 造一条回复（默认挂在 r1 下） */
function reply(over: Partial<Comment> = {}): Comment {
  return root({
    id: 'x1',
    name: '回复者',
    content: '回复内容',
    parentId: 'r1',
    rootId: 'r1',
    replyToName: '主评论作者',
    ...over,
  });
}

describe('stores/comments 删除', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
    // ensure() 内部会调 listComments().then(...)，必须给出 Promise
    vi.mocked(commentsApi.listComments).mockResolvedValue([]);
    vi.mocked(commentsApi.createComment).mockResolvedValue({
      id: 'remote1',
      name: '我',
      seed: 'me',
      content: '',
      time: '刚刚',
      likes: 0,
      liked: false,
      userId: 1,
      mine: true,
      isDeleted: false,
    });
  });

  it('删除成功：本地移除并调用接口', async () => {
    vi.mocked(commentsApi.deleteComment).mockResolvedValue(undefined);
    const s = useCommentsStore();
    s.add('n1', '我', '要删的评论');
    const id = s.list('n1')[0].id;

    expect(await s.remove('n1', id)).toBe(true);
    expect(s.list('n1')).toHaveLength(0);
    expect(commentsApi.deleteComment).toHaveBeenCalledWith('n1', id);
  });

  it('删除失败：回滚到原位置（避免"删了又回来"的错觉）', async () => {
    vi.mocked(commentsApi.deleteComment).mockRejectedValue(new Error('403'));
    const s = useCommentsStore();
    s.add('n1', '我', '第一条');
    s.add('n1', '我', '第二条');
    const targetId = s.list('n1')[0].id;

    expect(await s.remove('n1', targetId)).toBe(false);
    expect(s.list('n1')).toHaveLength(2);
    expect(s.list('n1')[0].id).toBe(targetId);
  });

  it('删除不存在的评论返回 false 且不调接口', async () => {
    const s = useCommentsStore();
    s.add('n1', '我', 'x');
    expect(await s.remove('n1', 'nope')).toBe(false);
    expect(commentsApi.deleteComment).not.toHaveBeenCalled();
  });

  it('已删除的评论不可再点赞', () => {
    const s = useCommentsStore();
    s.add('n1', '我', 'x');
    const c = s.list('n1')[0];
    c.isDeleted = true;
    const before = c.likes;
    s.toggleLike('n1', c.id);
    expect(c.likes).toBe(before);
  });
});

describe('stores/comments 两级评论（P1-12）', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(commentsApi.listComments).mockResolvedValue([]);
    vi.mocked(commentsApi.listRepliesPage).mockResolvedValue({
      list: [],
      hasMore: false,
      nextCursor: null,
    });
    vi.mocked(commentsApi.deleteComment).mockResolvedValue(undefined);
    vi.mocked(commentsApi.createComment).mockResolvedValue(
      reply({ id: 'srv1', content: '', mine: true }),
    );
  });

  /** 直接注入某文章的主评论列表（绕过 ensure 的种子/拉取） */
  function seed(s: ReturnType<typeof useCommentsStore>, roots: Comment[]) {
    s.byArticle['n1'] = roots;
  }

  it('count 统计主评论数 + 各主评论的回复数', () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 3 }), root({ id: 'r2', replyCount: 0 })]);
    expect(s.count('n1')).toBe(5);
  });

  it('countOf 只读：不初始化文章、不产生存储', () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 2 })]);
    expect(s.countOf('n1')).toBe(3);
    expect(s.countOf('n9')).toBe(0);
    expect(s.byArticle['n9']).toBeUndefined();
  });

  it('findComment 可定位主评论与其回复', () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 1, replies: [reply({ id: 'x1' })] })]);
    expect(s.findComment('n1', 'r1')?.id).toBe('r1');
    expect(s.findComment('n1', 'x1')?.id).toBe('x1');
    expect(s.findComment('n1', 'nope')).toBeUndefined();
  });

  it('reply：已展开线程乐观插入并递增 replyCount，成功后替换为服务端条目', async () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 1, replies: [reply({ id: 'x1' })] })]);
    s.repliesMeta['r1'] = { hasMore: true, nextCursor: 'x1', loading: false };
    vi.mocked(commentsApi.createComment).mockResolvedValue(
      reply({ id: 'srv9', content: '契约回复' }),
    );

    s.reply('n1', 'r1', '我', '契约回复');
    const rootRow = s.byArticle['n1'][0];
    // 同步乐观生效：立即可见
    expect(rootRow.replies).toHaveLength(2);
    expect(rootRow.replies?.[1].content).toBe('契约回复');
    expect(rootRow.replyCount).toBe(2);

    await flush();
    // 服务端返回后替换乐观项（拿准 id / 归属）
    expect(rootRow.replies?.[1].id).toBe('srv9');
    expect(rootRow.replyCount).toBe(2);
  });

  it('reply：服务端失败回滚插入与计数', async () => {
    vi.mocked(commentsApi.createComment).mockRejectedValue(new Error('500'));
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 1, replies: [reply({ id: 'x1' })] })]);
    s.repliesMeta['r1'] = { hasMore: false, nextCursor: null, loading: false };

    s.reply('n1', 'r1', '我', '会失败');
    expect(s.byArticle['n1'][0].replies).toHaveLength(2);

    await flush();
    expect(s.byArticle['n1'][0].replies).toHaveLength(1);
    expect(s.byArticle['n1'][0].replyCount).toBe(1);
  });

  it('reply：未展开线程只递增 replyCount，不插入列表', async () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 0, replies: [] })]);

    s.reply('n1', 'r1', '我', 'x');
    expect(s.byArticle['n1'][0].replies).toHaveLength(0);
    expect(s.byArticle['n1'][0].replyCount).toBe(1);

    await flush();
    expect(s.byArticle['n1'][0].replies).toHaveLength(0);
    expect(s.byArticle['n1'][0].replyCount).toBe(1);
  });

  it('reply：回复的回复也挂到同一 root 下（rootId 拍平）', async () => {
    const s = useCommentsStore();
    const first = reply({ id: 'x1' });
    seed(s, [root({ id: 'r1', replyCount: 1, replies: [first] })]);
    s.repliesMeta['r1'] = { hasMore: false, nextCursor: null, loading: false };

    s.reply('n1', 'x1', '我', '回复你的回复');
    const rootRow = s.byArticle['n1'][0];
    expect(rootRow.replies).toHaveLength(2);
    expect(rootRow.replies?.[1].parentId).toBe('x1');
    expect(rootRow.replies?.[1].rootId).toBe('r1');
    expect(rootRow.replies?.[1].replyToName).toBe(first.name);
    expect(commentsApi.createComment).toHaveBeenCalledWith('n1', '回复你的回复', 'x1');
  });

  it('loadReplies：完整列表替换预览并记录游标', async () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 3, replies: [reply({ id: 'p1' })] })]);
    vi.mocked(commentsApi.listRepliesPage).mockResolvedValue({
      list: [reply({ id: 'p1' }), reply({ id: 'p2' }), reply({ id: 'p3' })],
      hasMore: true,
      nextCursor: 'p3',
    });

    await s.loadReplies('n1', 'r1');
    expect(s.byArticle['n1'][0].replies?.map((r) => r.id)).toEqual(['p1', 'p2', 'p3']);
    expect(s.repliesMeta['r1']).toEqual({ hasMore: true, nextCursor: 'p3', loading: false });
    expect(commentsApi.listRepliesPage).toHaveBeenCalledWith('n1', 'r1');
  });

  it('loadReplies：失败保留预览、清除加载态', async () => {
    vi.mocked(commentsApi.listRepliesPage).mockRejectedValue(new Error('500'));
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 2, replies: [reply({ id: 'p1' })] })]);

    await s.loadReplies('n1', 'r1');
    expect(s.byArticle['n1'][0].replies?.map((r) => r.id)).toEqual(['p1']);
    expect(s.repliesMeta['r1']).toBeUndefined();
  });

  it('loadMoreReplies：按游标追加到列表尾部', async () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replyCount: 3, replies: [reply({ id: 'p1' })] })]);
    s.repliesMeta['r1'] = { hasMore: true, nextCursor: 'p1', loading: false };
    vi.mocked(commentsApi.listRepliesPage).mockResolvedValue({
      list: [reply({ id: 'p2' }), reply({ id: 'p3' })],
      hasMore: false,
      nextCursor: null,
    });

    await s.loadMoreReplies('n1', 'r1');
    expect(s.byArticle['n1'][0].replies?.map((r) => r.id)).toEqual(['p1', 'p2', 'p3']);
    expect(s.repliesMeta['r1']).toEqual({ hasMore: false, nextCursor: null, loading: false });
    expect(commentsApi.listRepliesPage).toHaveBeenCalledWith('n1', 'r1', 'p1');
  });

  it('loadMoreReplies：hasMore=false 时不发请求', async () => {
    const s = useCommentsStore();
    seed(s, [root({ id: 'r1', replies: [reply({ id: 'p1' })] })]);
    s.repliesMeta['r1'] = { hasMore: false, nextCursor: null, loading: false };

    await s.loadMoreReplies('n1', 'r1');
    expect(commentsApi.listRepliesPage).not.toHaveBeenCalled();
  });

  it('删除回复：从 root.replies 移除并递减 replyCount', async () => {
    const s = useCommentsStore();
    seed(s, [
      root({ id: 'r1', replyCount: 2, replies: [reply({ id: 'x1' }), reply({ id: 'x2' })] }),
    ]);

    expect(await s.remove('n1', 'x1')).toBe(true);
    expect(s.byArticle['n1'][0].replies?.map((r) => r.id)).toEqual(['x2']);
    expect(s.byArticle['n1'][0].replyCount).toBe(1);
    expect(commentsApi.deleteComment).toHaveBeenCalledWith('n1', 'x1');
  });

  it('删除主评论：连同已加载回复与加载态一并移除', async () => {
    const s = useCommentsStore();
    seed(s, [
      root({ id: 'r1', replyCount: 1, replies: [reply({ id: 'x1' })] }),
      root({ id: 'r2' }),
    ]);
    s.repliesMeta['r1'] = { hasMore: false, nextCursor: null, loading: false };

    expect(await s.remove('n1', 'r1')).toBe(true);
    expect(s.list('n1').map((c) => c.id)).toEqual(['r2']);
    expect(s.repliesMeta['r1']).toBeUndefined();
  });
});

describe('stores/comments 排序切换（E6）', () => {
  // 本文件前面的 beforeEach 只作用于第一个 describe：这里要自己重置，
  // 否则上一个用例的 mock 调用记录会让「不该发请求」的断言假失败
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('默认 latest；切到 hot 会重拉并替换列表', async () => {
    const s = useCommentsStore();
    s.byArticle['n1'] = [root({ id: 'r1', likes: 1 })];
    vi.mocked(commentsApi.listComments).mockResolvedValueOnce([root({ id: 'r2', likes: 9 })]);

    expect(s.sortOf('n1')).toBe('latest');
    expect(await s.setSort('n1', 'hot')).toBe(true);
    expect(commentsApi.listComments).toHaveBeenCalledWith('n1', 'hot');
    expect(s.byArticle['n1'].map((c) => c.id)).toEqual(['r2']);
    expect(s.sortOf('n1')).toBe('hot');
  });

  it('切到当前排序不重复请求（点两次「最热」不该打两次接口）', async () => {
    const s = useCommentsStore();
    await s.setSort('n1', 'latest');
    expect(commentsApi.listComments).not.toHaveBeenCalled();
  });

  it('失败时回退排序并保留原列表（标签不能与列表顺序不一致）', async () => {
    const s = useCommentsStore();
    s.byArticle['n1'] = [root({ id: 'r1' })];
    vi.mocked(commentsApi.listComments).mockRejectedValueOnce(new Error('network'));

    expect(await s.setSort('n1', 'hot')).toBe(false);
    expect(s.sortOf('n1')).toBe('latest');
    expect(s.byArticle['n1'].map((c) => c.id)).toEqual(['r1']);
  });

  it('排序按文章独立记录（A 文切 hot 不影响 B 文）', async () => {
    const s = useCommentsStore();
    vi.mocked(commentsApi.listComments).mockResolvedValueOnce([root({ id: 'r2' })]);
    await s.setSort('n1', 'hot');
    expect(s.sortOf('n1')).toBe('hot');
    expect(s.sortOf('n2')).toBe('latest');
  });
});
