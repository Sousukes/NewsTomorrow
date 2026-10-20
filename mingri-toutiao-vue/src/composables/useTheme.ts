import { watch, type WatchStopHandle } from 'vue';
import { resolveTheme, type ResolvedTheme, type ThemeMode } from '@/constants/theme';
import { useUserStore } from '@/stores/user';

/** CSS 据此切换深色令牌 */
export const THEME_ATTR = 'data-theme';

const MQL = '(prefers-color-scheme: dark)';

/** 由 JS 解析后写 data-theme：跟随系统与手动指定共用同一套深色令牌。 */
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
 * 应用根部调用一次：立即应用（挂载前调用可避免闪白）、跟随偏好与系统变化。
 * @returns 停止函数（测试用）
 */
export function useTheme(): () => void {
  const user = useUserStore();
  applyTheme(user.theme);

  const stopMode: WatchStopHandle = watch(
    () => user.theme,
    (m) => applyTheme(m),
  );

  // 仅「跟随系统」时响应
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
