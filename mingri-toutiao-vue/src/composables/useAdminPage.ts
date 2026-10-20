import { computed, ref } from 'vue';
import { useUserStore } from '@/stores/user';

/** 管理端三段样板：鉴权守卫、错误文案、列表状态机。 */

/** 错误 → 文案。原先 8 个页面各写一遍 2 行。 */
export function messageOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** 判定"这次失败是不是权限问题"。403 与「无权限」文案都算。 */
function isDeniedMessage(msg: string): boolean {
  return /403|无权限/.test(msg);
}

/** 前端态兜手敲网址与登录过期，服务端态兜权限变更（前端标志可能过期）。 */
export function useAdminGuard() {
  const user = useUserStore();
  const denied = ref(false);

  const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

  /** 权限问题置 denied 并返回空串，避免同时出现两条提示。 */
  function handleError(e: unknown, fallback: string): string {
    const msg = messageOf(e, fallback);
    if (isDeniedMessage(msg)) {
      denied.value = true;
      return '';
    }
    return msg;
  }

  /** 重新加载前清掉上一次的权限判定。 */
  function resetDenied() {
    denied.value = false;
  }

  return { denied, noAccess, handleError, resetDenied };
}

/** 后端列表接口的公共响应形状（total / hasMore 允许缺省）。 */
export interface AdminPage<T> {
  list: T[];
  total?: number;
  hasMore?: boolean;
  nextCursor?: string | null;
}

export interface UseAdminListOptions<T> {
  /** 列表项主键，默认 `id`。用于 loadMore 去重。 */
  idOf?: (item: T) => string | number;
}

/**
 * 列表状态机：首屏重载 + 游标续页 + 去重 + 代次守卫。
 * 只支持游标分页（offset 页面用自己的 loadMore）；代次守卫防旧响应覆盖新列表。
 */
export function useAdminList<T>(options: UseAdminListOptions<T> = {}) {
  const idOf = options.idOf ?? ((item: T) => (item as { id?: string | number }).id);

  const list = ref([]) as import('vue').Ref<T[]>;
  /** 总条数；接口不给时保持 0（与迁移前各页 `ref(0)` 行为一致，模板不用额外判空） */
  const total = ref(0);
  const hasMore = ref(false);
  const nextCursor = ref<string | null>(null);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error = ref('');

  let generation = 0;

  async function reload(fetch: () => Promise<AdminPage<T>>): Promise<void> {
    const gen = ++generation;
    loading.value = true;
    // 关键：重载要**接管** loadingMore，否则在途的旧续页回来时它的 finally
    // 因代次不符而不清标志，loadingMore 就永久为 true（翻页卡死）。
    // A4 门店页已经为这件事付过一次学费，这里直接沿用结论。
    loadingMore.value = false;
    error.value = '';
    try {
      const page = await fetch();
      // 过期响应：这次重载已被后续 reload 取代，整体丢弃
      if (gen !== generation) return;
      list.value = page.list;
      total.value = page.total ?? 0;
      nextCursor.value = page.nextCursor ?? null;
      hasMore.value = Boolean(page.hasMore);
    } catch (e) {
      if (gen !== generation) return;
      error.value = messageOf(e, '加载失败');
      throw e;
    } finally {
      // 代次判断也要在 finally 里：过期请求若照常清 loading，
      // 会把新请求的 loading 标志抹掉
      if (gen === generation) loading.value = false;
    }
  }

  async function loadMore(fetch: (cursor: string) => Promise<AdminPage<T>>): Promise<void> {
    if (loading.value || loadingMore.value) return;
    if (!hasMore.value || !nextCursor.value) return;
    const gen = generation;
    const usedCursor = nextCursor.value;
    loadingMore.value = true;
    try {
      const page = await fetch(usedCursor);
      if (gen !== generation) return; // 已重新加载：本次续页作废
      const seen = new Set(list.value.map(idOf));
      const fresh = page.list.filter((item) => !seen.has(idOf(item)));
      if (fresh.length) list.value = [...list.value, ...fresh];
      nextCursor.value = page.nextCursor ?? null;
      total.value = page.total ?? total.value;
      hasMore.value = Boolean(page.hasMore);
      // 游标不前进 = 没有任何推进手段：再点一次「加载更多」会拿到同一页，
      // 无限循环。与其等到用户发现，不如这里直接停。
      if (!nextCursor.value || nextCursor.value === usedCursor) hasMore.value = false;
    } catch (e) {
      if (gen === generation) error.value = messageOf(e, '加载更多失败');
    } finally {
      if (gen === generation) loadingMore.value = false;
    }
  }

  /** 筛选条件变化后重置分页游标（不重新请求）。 */
  function resetPaging(): void {
    nextCursor.value = null;
    hasMore.value = false;
    total.value = 0;
  }

  return {
    list,
    total,
    hasMore,
    nextCursor,
    loading,
    loadingMore,
    error,
    reload,
    loadMore,
    resetPaging,
  };
}

/*
 * 为什么不抽 `<AdminTable>` / `<FilterBar>`：
 * 表格列与交互差异远超想象——`AdminSources` 1473 行里塞着健康趋势图、
 * 批量操作、统计卡与行内开关，强行统一的结果是一个塞满 slot 与 prop 的空壳，
 * 比现在更难读。本批只抽**行为**（守卫/错误/列表状态机），
 * 视图层的相似留给真正要改样式时顺手做。
 */
