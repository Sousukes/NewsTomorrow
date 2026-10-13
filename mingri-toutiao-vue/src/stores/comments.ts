import { defineStore } from 'pinia';
import { ref, watch } from 'vue';
import {
  createComment,
  deleteComment,
  listComments,
  listRepliesPage,
  setCommentLike,
} from '@/api/comments';
import { USE_MOCK } from '@/api/config';
import { SEED_COMMENTS } from '@/data/mockComments';
import type { Comment } from '@/types';

export type { Comment };

const LS_KEY = 'mingri-toutiao:comments';

/** 某条主评论的回复加载状态（游标分页；存在即「完整列表已加载」） */
export interface RepliesMeta {
  hasMore: boolean;
  nextCursor: string | null;
  loading: boolean;
}

function load(): Record<string, Comment[]> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Record<string, Comment[]>;
  } catch {
    /* 解析失败回落到空 */
  }
  return {};
}

/**
 * 评论 store：离线优先（本地持久化即时生效），真实模式下后台与服务端同步。
 * 接口形态：list → GET /news/:id/comments；add → POST；toggleLike → PATCH。
 *
 * 两级评论（P1-12）：
 * - `byArticle[newsId]` 只存**主评论**；每条主评论的 `replies` 字段既是
 *   列表接口返回的「最早 2 条预览」，也可被 `loadReplies` 替换为完整列表
 *   （单一数据源，避免预览/完整两份拷贝各改各的）。
 * - `repliesMeta[rootId]` 记录完整列表的游标状态；不存在 = 尚未展开。
 * - `reply()` 乐观插入完整列表（已展开时）并递增 root 的 replyCount。
 */
export const useCommentsStore = defineStore('comments', () => {
  const byArticle = ref<Record<string, Comment[]>>(load());
  const repliesMeta = ref<Record<string, RepliesMeta>>({});

  function persist() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(byArticle.value));
    } catch {
      /* 隐私模式 / 容量已满时静默失败 */
    }
  }

  watch(byArticle, persist, { deep: true });

  /** 取某文章评论；首次访问用离线种子初始化（真实模式再后台拉取服务端覆盖）。 */
  function ensure(id: string): Comment[] {
    if (!byArticle.value[id]) {
      byArticle.value[id] = (SEED_COMMENTS[id] ?? []).map((c) => ({ ...c }));
      if (!USE_MOCK) {
        void listComments(id)
          .then((remote) => {
            if (remote.length) byArticle.value[id] = remote;
          })
          .catch(() => {});
      }
    }
    return byArticle.value[id];
  }

  function list(id: string): Comment[] {
    return ensure(id);
  }

  /** 评论总数 = 主评论数 + 各主评论的回复数（与后端 comment_count 口径一致）。 */
  function count(id: string): number {
    return ensure(id).reduce((n, c) => n + 1 + (c.replyCount ?? 0), 0);
  }

  /** 只读评论数，不触发初始化 / 不写回（用于详情页角标）。 */
  function countOf(id: string): number {
    const arr = byArticle.value[id];
    if (arr) return arr.reduce((n, c) => n + 1 + (c.replyCount ?? 0), 0);
    return (SEED_COMMENTS[id] ?? []).reduce((n, c) => n + 1 + (c.replyCount ?? 0), 0);
  }

  /** 在主评论或其回复（预览/完整列表）中按 id 查找；找不到返回 undefined。 */
  function findComment(newsId: string, commentId: string): Comment | undefined {
    for (const c of byArticle.value[newsId] ?? []) {
      if (c.id === commentId) return c;
      const hit = c.replies?.find((x) => x.id === commentId);
      if (hit) return hit;
    }
    return undefined;
  }

  function add(id: string, name: string, content: string) {
    ensure(id);
    byArticle.value[id].unshift({
      id: `c${Date.now()}`,
      name,
      seed: 'me',
      userId: null,
      mine: false,
      content,
      time: '刚刚',
      likes: 0,
      liked: false,
      parentId: null,
      rootId: null,
      replyCount: 0,
      replies: [],
      replyToName: null,
      replyToUserId: null,
    });
    if (!USE_MOCK) void createComment(id, content).catch(() => {});
  }

  function toggleLike(id: string, commentId: string) {
    const c = findComment(id, commentId);
    if (!c || c.isDeleted) return;
    c.liked = !c.liked;
    c.likes += c.liked ? 1 : -1;
    if (!USE_MOCK) void setCommentLike(id, commentId, c.liked).catch(() => {});
  }

  /**
   * 展开某条主评论的完整回复（首页，替换预览）。
   * 失败时保留预览不报错——预览已足够展示讨论概况。
   */
  async function loadReplies(newsId: string, rootId: string): Promise<void> {
    const root = byArticle.value[newsId]?.find((c) => c.id === rootId);
    if (!root) return;
    const meta: RepliesMeta = { hasMore: false, nextCursor: null, loading: true };
    repliesMeta.value[rootId] = meta;
    if (USE_MOCK) {
      // 纯前端 mock：完整回复不存在，用预览充数（保持可交互）
      root.replies = (root.replies ?? []).map((c) => ({ ...c }));
      meta.loading = false;
      return;
    }
    try {
      const page = await listRepliesPage(newsId, rootId);
      root.replies = page.list;
      meta.hasMore = page.hasMore;
      meta.nextCursor = page.nextCursor;
    } catch {
      delete repliesMeta.value[rootId]; // 失败：回到预览态
    } finally {
      meta.loading = false;
    }
  }

  /** 加载更多回复（游标追加到完整列表尾部）。 */
  async function loadMoreReplies(newsId: string, rootId: string): Promise<void> {
    const meta = repliesMeta.value[rootId];
    const root = byArticle.value[newsId]?.find((c) => c.id === rootId);
    if (!meta || !root || meta.loading || !meta.hasMore || !meta.nextCursor) return;
    meta.loading = true;
    try {
      const page = await listRepliesPage(newsId, rootId, meta.nextCursor ?? undefined);
      root.replies = [...(root.replies ?? []), ...page.list];
      meta.hasMore = page.hasMore;
      meta.nextCursor = page.nextCursor;
    } catch {
      /* 保留现有列表，下次可重试 */
    } finally {
      meta.loading = false;
    }
  }

  /**
   * 发表回复（二级评论）。乐观更新：立即挂进已展开的回复列表并递增
   * root 的 replyCount；服务端失败则回滚两者。
   * 未展开线程时只递增 replyCount，展开后自然从服务端取到。
   */
  function reply(newsId: string, parentId: string, name: string, content: string) {
    ensure(newsId);
    const parent = findComment(newsId, parentId);
    if (!parent) return;
    const rootId = parent.rootId ?? parent.id;
    const root = byArticle.value[newsId]?.find((c) => c.id === rootId);
    if (!root) return;

    const optimistic: Comment = {
      id: `c${Date.now()}`,
      name,
      seed: 'me',
      userId: null,
      mine: false,
      content,
      time: '刚刚',
      likes: 0,
      liked: false,
      parentId,
      rootId,
      replyCount: 0,
      replies: [],
      replyToName: parent.name,
      replyToUserId: parent.userId,
    };
    const expanded = Boolean(repliesMeta.value[rootId]);
    if (expanded) root.replies = [...(root.replies ?? []), optimistic];
    root.replyCount = (root.replyCount ?? 0) + 1;

    if (USE_MOCK) return;
    void createComment(newsId, content, parentId)
      .then((created) => {
        // 用服务端返回的真实条目替换乐观项（拿准 id / rootId / 归属）
        if (expanded && root.replies) {
          const idx = root.replies.findIndex((x) => x.id === optimistic.id);
          if (idx >= 0) root.replies.splice(idx, 1, created);
        }
      })
      .catch(() => {
        if (expanded && root.replies) {
          const idx = root.replies.findIndex((x) => x.id === optimistic.id);
          if (idx >= 0) root.replies.splice(idx, 1);
        }
        root.replyCount = Math.max((root.replyCount ?? 1) - 1, 0);
      });
  }

  /**
   * 删除评论。仅对自己的评论可用（服务端二次校验，非作者返回 403）。
   *
   * 乐观更新：先从本地移除，服务端失败则回滚并恢复，避免出现"删了又回来"的错觉。
   * 删主评论 = 连同其已加载回复一并移除（服务端同样不再展示其回复）；
   * 删回复 = 从所属主评论的 replies 中移除并递减 replyCount。
   */
  async function remove(id: string, commentId: string): Promise<boolean> {
    const arr = byArticle.value[id];
    if (!arr) return false;

    // 回复：定位所属主评论后从 replies 中移除
    for (const root of arr) {
      const idx = root.replies?.findIndex((x) => x.id === commentId) ?? -1;
      if (idx < 0) continue;
      const backup = root.replies![idx];
      root.replies!.splice(idx, 1);
      const countBackup = root.replyCount ?? 0;
      root.replyCount = Math.max(countBackup - 1, 0);
      if (USE_MOCK) return true;
      try {
        await deleteComment(id, commentId);
        return true;
      } catch {
        root.replies!.splice(idx, 0, backup);
        root.replyCount = countBackup;
        return false;
      }
    }

    // 主评论
    const idx = arr.findIndex((x) => x.id === commentId);
    if (idx < 0) return false;
    const backup = arr[idx];
    arr.splice(idx, 1);
    delete repliesMeta.value[commentId];

    if (USE_MOCK) return true;
    try {
      await deleteComment(id, commentId);
      return true;
    } catch {
      // 回滚：按原位置插回
      arr.splice(idx, 0, backup);
      return false;
    }
  }

  return {
    byArticle,
    repliesMeta,
    ensure,
    list,
    count,
    countOf,
    findComment,
    add,
    toggleLike,
    loadReplies,
    loadMoreReplies,
    reply,
    remove,
  };
});
