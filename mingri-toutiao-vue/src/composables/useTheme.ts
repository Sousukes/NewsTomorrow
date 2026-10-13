import { watch, type WatchStopHandle } from 'vue';
import { resolveTheme, type ResolvedTheme, type ThemeMode } from '@/constants/theme';
import { useUserStore } from '@/stores/user';

/** 写在 <html> 上的属性名，CSS 侧据此切换深色令牌 */
export const THEME_ATTR = 'data-theme';

const MQL = '(prefers-color-scheme: dark)';

/**
 * 把解析后的主题写到根元素。
 *
 * 之所以由 JS 解析而不是交给 CSS 媒体查询：
 * 「跟随系统」与「手动指定」共用同一套深色令牌（`:root[data-theme='dark']`），
 * 不必在 CSS 里把媒体查询与属性选择器各写一遍，避免两处不一致。
 */
export function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode);
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute(THEME_ATTR, resolved);
    // 同步 color-scheme，让原生滚动条/表单控件也跟随
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}

/**
 * 在应用根部调用一次：
 * - 立即应用当前主题（在挂载前调用可避免首屏闪白/闪黑）
 * - 主题偏好变化时跟随
 * - 处于「跟随系统」时，系统偏好切换也实时响应
 *
 * @returns 停止函数（测试用；应用生命周期内一般不需要调用）
 */
export function useTheme(): () => void {
  const user = useUserStore();
  applyTheme(user.theme);

  // 主题偏好变化 → 重新应用
  const stopMode: WatchStopHandle = watch(
    () => user.theme,
    (m) => applyTheme(m),
  );

  // 系统偏好变化 → 仅在「跟随系统」时生效
  let mql: MediaQueryList | null = null;
  const onChange = () => {
    if (user.theme === 'system') applyTheme('system');
  };
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mql = window.matchMedia(MQL);
    // Safari <14 只有 addListener
    if (typeof mql.addEventListener === 'function') mql.addEventListener('change', onChange);
    else mql.addListener(onChange);
  }

  return () => {
    stopMode();
    if (mql) {
      if (typeof mql.removeEventListener === 'function')
        mql.removeEventListener('change', onChange);
      else mql.removeListener(onChange);
    }
  };
}
