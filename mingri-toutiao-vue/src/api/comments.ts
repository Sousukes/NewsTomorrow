import { SEED_COMMENTS } from '@/data/mockComments';
import type { Comment } from '@/types';
import { DEFAULT_COMMENT_SORT, type CommentSort } from '@/constants/comments';
import { mockDelay, request } from './http';
import { USE_MOCK } from './config';
import { VALIDATE_RESPONSES, validateComment, warnIssues } from './validate';

/** 两级评论（P1-12）的本地默认值：主评论形态 */
const ROOT_DEFAULTS = {
  parentId: null,
  rootId: null,
  replyCount: 0,
  replies: [] as Comment[],
  replyToName: null,
  replyToUserId: null,
} as const;

function normalize(raw: Record<string, unknown>): Comment {
  const r = raw as Record<string, unknown>;
  const item: Comment = {
    id: String(r.id ?? r.commentId ?? ''),
    name: String(r.name ?? r.userName ?? '匿名'),
    seed: String(r.seed ?? r.id ?? ''),
    content: String(r.content ?? r.text ?? ''),
    time: String(r.time ?? r.createTime ?? ''),
    likes: Number(r.likes ?? r.likeCount ?? 0),
    liked: Boolean(r.liked ?? false),
    // 归属：后端无此字段（历史评论）时为 null；mine 由后端按当前登录用户计算
    userId: r.userId === null || r.userId === undefined ? null : Number(r.userId),
    mine: Boolean(r.mine ?? false),
    isDeleted: Boolean(r.isDeleted ?? false),
    // 两级评论：缺省按主评论形态兜底（mock/旧数据没有这些字段）
    parentId: r.parentId === null || r.parentId === undefined ? null : String(r.parentId),
    rootId: r.rootId === null || r.rootId === undefined ? null : String(r.rootId),
    replyCount: Number(r.replyCount ?? 0),
    // 预览回复递归 normalize（后端已排序：最早 2 条）
    replies: Array.isArray(r.replies)
      ? (r.replies as Record<string, unknown>[]).map(normalize)
      : [],
    replyToName:
      r.replyToName === null || r.replyToName === undefined ? null : String(r.replyToName),
    replyToUserId:
      r.replyToUserId === null || r.replyToUserId === undefined ? null : Number(r.replyToUserId),
  };
  if (VALIDATE_RESPONSES) {
    const issues = validateComment(item);
    if (issues.length) warnIssues('comment', issues);
  }
  return item;
}

/** 评论一页（后端已改为游标分页，M2） */
export interface CommentPage {
  list: Comment[];
  hasMore: boolean;
  nextCursor: string | null;
}

/**
 * 本地排序（mock 专用）：镜像后端 `_order_for` 的两条规则。
 *
 * 后端 `latest` = `created_at` 倒序 + `id` 倒序；`hot` = `likes` 倒序 + 时间 + id。
 * mock 数据没有 `created_at`，只有 `time` 展示文案，故只能按 `likes` 与 id 近似——
 * **这是 mock 的诚实边界**：排序结果与真后端在"同赞不同时"的细分上可能不同，
 * 但"赞多的在前"这条用户能感知的规则一致。
 */
export function sortCommentsLocal(list: Comment[], sort: CommentSort): Comment[] {
  if (sort !== 'hot') return list;
  return [...list].sort((a, b) => b.likes - a.likes);
}

/** GET /news/:id/comments —— 主评论列表（游标分页；P1-12 起只返回主评论） */
export async function listCommentsPage(
  newsId: string,
  cursor?: string,
  limit = 20,
  sort: CommentSort = DEFAULT_COMMENT_SORT,
): Promise<CommentPage> {
  if (USE_MOCK) {
    return mockDelay({
      list: sortCommentsLocal(
        (SEED_COMMENTS[newsId] ?? []).map((c) => ({ ...c })),
        sort,
      ),
      hasMore: false,
      nextCursor: null,
    });
  }
  const data = await request<{
    list?: unknown[];
    hasMore?: boolean;
    nextCursor?: string | null;
  }>(`/news/${newsId}/comments`, { query: { cursor, limit, sort } });
  const arr = Array.isArray(data) ? data : (data?.list ?? []);
  return {
    list: (arr as Record<string, unknown>[]).map(normalize),
    hasMore: Boolean(data?.hasMore),
    nextCursor: data?.nextCursor ?? null,
  };
}

/** 便捷封装：只取列表 */
export async function listComments(
  newsId: string,
  sort: CommentSort = DEFAULT_COMMENT_SORT,
): Promise<Comment[]> {
  return (await listCommentsPage(newsId, undefined, 20, sort)).list;
}

/**
 * GET /news/:id/comments/:cid/replies —— 某条主评论下的回复（时间正序，游标分页）。
 * root 已软删除时后端返回空列表。
 */
export async function listRepliesPage(
  newsId: string,
  commentId: string,
  cursor?: string,
  limit = 20,
): Promise<CommentPage> {
  if (USE_MOCK) {
    // mock 数据是扁平的：回复预览已内嵌在主评论上，完整回复列表不存在
    return mockDelay({ list: [], hasMore: false, nextCursor: null });
  }
  const data = await request<{
    list?: unknown[];
    hasMore?: boolean;
    nextCursor?: string | null;
  }>(`/news/${newsId}/comments/${commentId}/replies`, { query: { cursor, limit } });
  const arr = Array.isArray(data) ? data : (data?.list ?? []);
  return {
    list: (arr as Record<string, unknown>[]).map(normalize),
    hasMore: Boolean(data?.hasMore),
    nextCursor: data?.nextCursor ?? null,
  };
}

/** POST /news/:id/comments —— 发表评论；parentId 非空时为回复（二级评论） */
export async function createComment(
  newsId: string,
  content: string,
  parentId?: string,
): Promise<Comment> {
  if (USE_MOCK) {
    return mockDelay(
      {
        id: `c${Date.now()}`,
        name: '我',
        seed: 'me',
        content,
        time: '刚刚',
        likes: 0,
        liked: false,
        // mock 模式没有真实登录用户：视为无归属
        userId: null,
        mine: false,
        isDeleted: false,
        ...ROOT_DEFAULTS,
        // 回复形态：root 由调用方（store）补全，这里只标记父子关系
        ...(parentId ? { parentId, replies: [] } : {}),
      },
      200,
    );
  }
  const data = await request<unknown>(`/news/${newsId}/comments`, {
    method: 'POST',
    body: parentId ? { content, parentId } : { content },
  });
  return normalize((data ?? {}) as Record<string, unknown>);
}

/**
 * DELETE /news/:id/comments/:cid —— 删除自己的评论（软删除）。
 * 服务端会校验作者归属；非作者返回 403。
 */
export async function deleteComment(newsId: string, commentId: string): Promise<void> {
  if (USE_MOCK) return mockDelay(undefined, 150);
  await request<void>(`/news/${newsId}/comments/${commentId}`, { method: 'DELETE' });
}

/** PATCH /news/:id/comments/:cid —— 点赞 / 取消点赞 */
export async function setCommentLike(
  newsId: string,
  commentId: string,
  liked: boolean,
): Promise<void> {
  if (USE_MOCK) return mockDelay(undefined, 100);
  await request<void>(`/news/${newsId}/comments/${commentId}`, {
    method: 'PATCH',
    body: { liked },
  });
}
