import { onBeforeUnmount, onMounted, ref } from 'vue';

/**
 * 在线状态（E8 / P1-78）。
 *
 * 为什么需要一个组合式函数而不是各处 `navigator.onLine`：
 * - 首屏要知道**当前**状态（`navigator.onLine`）；
 * - 过程中要知道**变化**（`online` / `offline` 事件），否则用户在地铁里穿过隧道时
 *   界面不会给任何反馈，直到下一次请求失败才后知后觉。
 *
 * `navigator.onLine` 的已知局限：它只反映"有网络接口"，不保证真的能上网
 * （连着一个不通的 Wi-Fi 也是 true）。所以它只用于**提示**，不用来阻断逻辑——
 * 请求失败本身由各 api 的降级分支处理。
 *
 * 事件处理按事件类型置值而非回读 `navigator.onLine`：事件到达时该属性是否
 * 已更新没有规范保证（实测 jsdom 始终不更新），依赖它会出现
 * "`offline` 事件到了、界面却没变"。
 */
export function useOnline() {
  const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine !== false);

  // 按**事件类型**置值，而不是再读一次 `navigator.onLine`：
  // 事件到达时该属性是否已更新并无规范保证（实测 jsdom 就一直不更新），
  // 依赖它会出现"事件到了、界面没变"。事件本身就是最直接的信号。
  function goOnline() {
    online.value = true;
  }
  function goOffline() {
    online.value = false;
  }

  onMounted(() => {
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
  });

  onBeforeUnmount(() => {
    window.removeEventListener('online', goOnline);
    window.removeEventListener('offline', goOffline);
  });

  return { online };
}
