import { computed, ref } from 'vue';
import { useUserStore } from '@/stores/user';

/**
 * 管理端页面的三段样板：鉴权守卫、错误文案、列表状态机。
 *
 * 抽这三个是稳的，抽「表格/筛选栏」不稳（见文件末尾的说明），原因写在下面。
 */

/** 错误 → 文案。原先 8 个页面各写一遍 2 行。 */
export function messageOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** 判定"这次失败是不是权限问题"。403 与「无权限」文案都算。 */
function isDeniedMessage(msg: string): boolean {
  return /403|无权限/.test(msg);
}

/**
 * 管理端鉴权守卫。
 *
 * 两个触发源缺一不可：
 * - 前端态（`isLoggedIn` / `isAdmin`）：入口已按 `isAdmin` 隐藏，这里兜住
 *   **手敲网址**与**登录态过期**；
 * - 服务端态（`denied`）：令牌有效但服务端判定无权限。前端标志可能过期
 *   （管理员刚被移除），只看前端态会让页面继续显示旧数据。
 */
export function useAdminGuard() {
  const user = useUserStore();
  const denied = ref(false);

  const noAccess = computed(() => !user.isLoggedIn || !user.isAdmin || denied.value);

  /**
   * 翻译一次失败：命中权限问题就置 `denied` 并返回空串（不写 error，
   * 否则页面上会同时出现「无权限」和一段错误文案），否则返回文案给调用方。
   */
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
 * 列表状态机：首屏重载 + **游标续页** + 去重 + 代次守卫。
 *
 * **契约只覆盖游标分页**（响应带 `nextCursor`）。`AdminNicknameReviews` 走的是
 * offset + `total.length` 推断，它没有游标可续——硬塞进来就得加
 * `mode: 'cursor' | 'offset'` 开关，两套算法挤在一个 `if/else` 里，
 * 以后再加一种就成三路分支。那个页面保留自己的 `loadMore`，
 * 只用 `useAdminGuard` —— **不适用就不该用**，硬抽象的代价是所有人一起将就。
 *
 * 代次守卫的必要性：运营改筛选条件后，旧请求可能更晚返回。
 * 门店页（A4）踩过同样的坑——`loadMore` 在途时重新 `fetch`，
 * 旧响应回来会覆盖新列表、或把 `loadingMore` 卡成 true 导致翻页永久卡死。
 * 同一个错误值得同一个解法。
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
