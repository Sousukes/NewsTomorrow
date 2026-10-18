import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 评论 API 层（`src/api/comments.ts`）——此前 0% 覆盖。
 *
 * 这层不是纯转发，它做三件**后端契约变化时救命**的事：
 * 1. 响应可能是**裸数组**也可能是 `{list}`——两种都要能吃；
 * 2. `hasMore` / `nextCursor` 缺失时归一到安全默认值（`false` / `null`），
 *    否则 `undefined` 会被当成"还有更多"，翻页直接死循环；
 * 3. 每条都要过 `normalize`（后端字段 → 前端形状），漏一条列表就崩一半。
 *
 * `USE_MOCK: false` 必须打桩，否则 mock 分支短路一切。
 */

vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

const request = vi.fn();
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  mockDelay: async (v: unknown) => v,
}));

import {
  createComment,
  deleteComment,
  listComments,
  listCommentsPage,
  listRepliesPage,
  setCommentLike,
  sortCommentsLocal,
} from '@/api/comments';

/** 后端返回的一条评论（字段名与前端不同，靠 normalize 映射） */
const raw = {
  id: 'c1',
  name: '甲',
  seed: 's1',
  content: '内容',
  time: '刚刚',
  createdAt: '2026-10-18T06:00:00+00:00',
  likes: 3,
  liked: true,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listCommentsPage', () => {
  it('响应是裸数组时也能吃（老接口/过渡期形态）', async () => {
    request.mockResolvedValueOnce([raw]);
    const page = await listCommentsPage('n1');
    expect(page.list).toHaveLength(1);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('响应是 {list} 时取 list', async () => {
    request.mockResolvedValueOnce({ list: [raw], hasMore: true, nextCursor: 'c2' });
    const page = await listCommentsPage('n1');
    expect(page.list).toHaveLength(1);
    expect(page.hasMore).toBe(true);
    expect(page.nextCursor).toBe('c2');
  });

  it('缺 hasMore/nextCursor 时归一到"没有更多"，不能留 undefined（会无限翻页）', async () => {
    request.mockResolvedValueOnce({ list: [] });
    const page = await listCommentsPage('n1');
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('把游标、条数与排序传给后端（默认 20 / latest）', async () => {
    request.mockResolvedValueOnce({ list: [] });
    await listCommentsPage('n1', 'cur');
    const [path, opts] = request.mock.calls[0] as [string, { query: Record<string, unknown> }];
    expect(path).toBe('/news/n1/comments');
    expect(opts.query).toEqual({ cursor: 'cur', limit: 20, sort: 'latest' });
  });

  it('sort 透传（hot 才让服务端按点赞数排）', async () => {
    request.mockResolvedValueOnce({ list: [] });
    await listCommentsPage('n1', undefined, 20, 'hot');
    const [, opts] = request.mock.calls[0] as [string, { query: Record<string, unknown> }];
    expect(opts.query.sort).toBe('hot');
  });

  it('逐条过 normalize（字段映射漏一条，列表就崩一半）', async () => {
    request.mockResolvedValueOnce({ list: [raw] });
    const [c] = (await listCommentsPage('n1')).list;
    expect(c.id).toBe('c1');
    expect(c.name).toBe('甲');
    expect(c.likes).toBe(3);
    expect(c.liked).toBe(true);
    // 缺失字段必须兜底而不是留下 undefined（列表渲染不会崩）
    expect(c.replies).toEqual([]);
    expect(c.userId).toBeNull();
    expect(c.replyToName).toBeNull();
  });
});

describe('listComments', () => {
  it('只取列表（便捷封装）', async () => {
    request.mockResolvedValueOnce({ list: [raw] });
    const list = await listComments('n1');
    expect(list).toHaveLength(1);
  });

  it('sort 一路透传到 query（否则切换排序只会重拉同一份数据）', async () => {
    request.mockResolvedValueOnce({ list: [raw] });
    await listComments('n1', 'hot');
    const [, opts] = request.mock.calls[0] as [string, { query: Record<string, unknown> }];
    expect(opts.query.sort).toBe('hot');
  });
});

describe('sortCommentsLocal（mock 专用排序）', () => {
  const mk = (id: string, likes: number) =>
    ({ ...raw, id, likes }) as unknown as Parameters<typeof sortCommentsLocal>[0][number];

  it('hot 按点赞数倒序，且不改原数组（本地列表是 store 的状态，原地排序会让视图错乱）', () => {
    const list = [mk('a', 1), mk('b', 9), mk('c', 5)];
    const sorted = sortCommentsLocal(list, 'hot');
    expect(sorted.map((c) => c.id)).toEqual(['b', 'c', 'a']);
    expect(list.map((c) => c.id)).toEqual(['a', 'b', 'c']);
  });

  it('latest 原样返回（mock 无绝对时间，保持既有顺序）', () => {
    const list = [mk('a', 1), mk('b', 9)];
    expect(sortCommentsLocal(list, 'latest')).toBe(list);
  });
});

describe('listRepliesPage', () => {
  it('路径带上主评论 id，且同样兼容裸数组', async () => {
    request.mockResolvedValueOnce([raw]);
    const page = await listRepliesPage('n1', 'c1');
    const [path] = request.mock.calls[0] as [string];
    expect(path).toBe('/news/n1/comments/c1/replies');
    expect(page.list).toHaveLength(1);
  });
});

describe('createComment / deleteComment / setCommentLike', () => {
  it('createComment 把 content 与 parentId 一起提交（回复靠 parentId 区分层级）', async () => {
    request.mockResolvedValueOnce(raw);
    await createComment('n1', '说点话', 'c0');
    const [path, opts] = request.mock.calls[0] as [string, { method: string; body: unknown }];
    expect(path).toBe('/news/n1/comments');
    expect(opts.method).toBe('POST');
    expect(opts.body).toMatchObject({ content: '说点话', parentId: 'c0' });
  });

  it('deleteComment 走 DELETE（软删除由后端决定）', async () => {
    request.mockResolvedValueOnce(undefined);
    await deleteComment('n1', 'c1');
    const [path, opts] = request.mock.calls[0] as [string, { method: string }];
    expect(path).toBe('/news/n1/comments/c1');
    expect(opts.method).toBe('DELETE');
  });

  it('setCommentLike 用 PATCH 提交目标状态，而不是"切换"（避免重放导致来回抖）', async () => {
    request.mockResolvedValueOnce(undefined);
    await setCommentLike('n1', 'c1', true);
    const [, opts] = request.mock.calls[0] as [string, { method: string; body: unknown }];
    expect(opts.method).toBe('PATCH');
    expect(opts.body).toMatchObject({ liked: true });
  });
});
