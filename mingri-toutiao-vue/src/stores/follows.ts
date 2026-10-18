import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  fetchFollows,
  fetchSourceDirectory,
  followSource,
  unfollowSource,
  type FollowsPayload,
  type SourceItem,
} from '@/api/follows';

/**
 * 关注来源 store（E6b）。
 *
 * 两份状态：`sources`（公开目录，可关注项）与 `keys`（我已关注的 key 列表）。
 * 分开是因为目录里的大多数源我并未关注；合成一个数组会让「是否已关注」要靠查找。
 *
 * 乐观更新规则（与收藏/点赞同处方）：
 * - 关注/取关**立即**改 `keys`，后台调接口；
 * - 成功 → 用服务端返回的**完整集合**覆盖（服务端即事实源）；
 * - 失败 → 回滚到调用前的 keys（只动本地会让界面永久显示一个假状态）。
 *
 * **不做「未登录也能关注」的本地分支**：关注属于账号数据，未登录时任何写入
 * 都没有归宿，还会在登录后把匿名期的关注悄悄带上去（与 marks 的账号隔离冲突）。
 * 未登录由调用方（视图）引导登录。
 */
export const useFollowsStore = defineStore('follows', () => {
  const sources = ref<SourceItem[]>([]);
  const keys = ref<string[]>([]);
  /** 目录是否已加载（避免每次渲染都去打接口） */
  const loaded = ref(false);
  const loading = ref(false);
  const error = ref('');

  const sourceMap = computed<Record<string, SourceItem>>(() =>
    Object.fromEntries(sources.value.map((s) => [s.key, s])),
  );

  function isFollowing(key?: string | null): boolean {
    return !!key && keys.value.includes(key);
  }

  function sourceOf(key?: string | null): SourceItem | undefined {
    return key ? sourceMap.value[key] : undefined;
  }

  /** 该源是否可关注：目录里存在、已启用（停用源只能取关不能关注） */
  function canFollow(key?: string | null): boolean {
    const s = sourceOf(key);
    return !!s && s.enabled;
  }

  function apply(payload: FollowsPayload) {
    keys.value = payload.keys;
    sources.value = mergeSources(sources.value, payload.list);
  }

  function mergeSources(current: SourceItem[], incoming: SourceItem[]): SourceItem[] {
    // 关注返回的列表可能缺 newsCount 的实时值，但一定有 name/channel；
    // 用目录里已有的那份补齐，避免关注后目录变"空壳"
    const byKey = new Map(current.map((s) => [s.key, s]));
    for (const s of incoming) {
      const hit = byKey.get(s.key);
      byKey.set(s.key, hit ? { ...hit, ...s, newsCount: s.newsCount || hit.newsCount } : s);
    }
    return [...byKey.values()];
  }

  async function loadDirectory(channel?: string): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      sources.value = await fetchSourceDirectory(channel);
      loaded.value = true;
    } catch (e) {
      error.value = e instanceof Error ? e.message : '来源目录加载失败';
    } finally {
      loading.value = false;
    }
  }

  /** 刷新关注集合（登录态变化 / 启动时调用）。失败时保留旧状态，不回滚成空 */
  async function refresh(): Promise<void> {
    try {
      apply(await fetchFollows());
    } catch {
      /* 未登录或网络失败：保持现状（清空会让界面闪成未关注） */
    }
  }

  /**
   * @returns 是否成功（失败时视图提示，别默默吞掉——用户会以为点了没反应）
   */
  async function follow(key: string): Promise<boolean> {
    if (!key || keys.value.includes(key)) return true;
    const backup = keys.value;
    keys.value = [key, ...keys.value];
    try {
      apply(await followSource(key));
      return true;
    } catch (e) {
      keys.value = backup;
      error.value = e instanceof Error ? e.message : '关注失败';
      return false;
    }
  }

  async function unfollow(key: string): Promise<boolean> {
    if (!key || !keys.value.includes(key)) return true;
    const backup = keys.value;
    keys.value = keys.value.filter((k) => k !== key);
    try {
      apply(await unfollowSource(key));
      return true;
    } catch (e) {
      keys.value = backup;
      error.value = e instanceof Error ? e.message : '取消关注失败';
      return false;
    }
  }

  return {
    sources,
    keys,
    loaded,
    loading,
    error,
    sourceMap,
    isFollowing,
    sourceOf,
    canFollow,
    loadDirectory,
    refresh,
    follow,
    unfollow,
  };
});
