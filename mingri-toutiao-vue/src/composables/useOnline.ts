import { onBeforeUnmount, onMounted, ref } from 'vue';

/**
 * 在线状态，仅用于提示（onLine 只表示有网络接口，不保证真能上网）。
 * 按事件类型置值而非回读 navigator.onLine：该属性未必随事件更新（jsdom 就不更新）。
 */
export function useOnline() {
  const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine !== false);

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
