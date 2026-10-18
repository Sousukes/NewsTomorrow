import { onMounted, ref, watch } from 'vue';
import { useNewsStore } from '@/stores/news';
import type { NewsItem } from '@/types';

/**
 * 深链安全的资讯详情加载：
 * 直接访问 /news/:id（或刷新）时内存无此条，自动按 id 拉取，
 * 避免把「尚未加载」误判为「内容不存在」。
 *
 * @param id 资讯 id 的 getter
 * @returns item / resolving / failed
 */
export function useNewsDetail(id: () => string) {
  const news = useNewsStore();
  const item = ref<NewsItem | null>(news.getById(id()));
  const resolving = ref(false);
  const failed = ref(false);

  async function resolve() {
    const key = id();
    if (!key) return;
    const local = news.getById(key);
    if (local) {
      item.value = local;
      failed.value = false;
      return;
    }
    resolving.value = true;
    failed.value = false;
    try {
      const found = await news.loadDetail(key);
      item.value = found;
      failed.value = !found;
    } catch {
      // 网络/服务端错误：必须置 `failed`。
      // 不改的话 `loadDetail` 抛出的错误会变成 **unhandled rejection**（`onMounted`
      // 里没人 await 它），而且 `resolving` 已复位、`item` 仍是 null——视图落到
      // `v-else` 的「内容不存在或已下架」，**把临时故障说成了下架**。
      item.value = null;
      failed.value = true;
    } finally {
      resolving.value = false;
    }
  }

  onMounted(resolve);
  watch(id, resolve);

  return { item, resolving, failed, reload: resolve };
}
