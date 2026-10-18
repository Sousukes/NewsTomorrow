import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

/**
 * C4 路由守卫：受限账号（管理员刚代其重置过密码）只能去改密页。
 *
 * 放在**守卫**而不是各个页面里判断的理由：受限期有二十多个页面，
 * 逐个加判断必然漏；漏掉的那个页面会让用户以为「改了就行」，
 * 然后在某个写操作上撞 40305——比一开始就被拦住更让人困惑。
 */
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  changePassword: vi.fn(),
}));

vi.mock('@/api/me', () => ({
  MARKS_SYNC_SUPPORTED: false,
  fetchFavorites: vi.fn(),
  addFavorite: vi.fn(),
  removeFavorite: vi.fn(),
  mergeFavorites: vi.fn(),
  fetchHistory: vi.fn(),
  recordHistory: vi.fn(),
  clearHistoryRemote: vi.fn(),
  mergeHistory: vi.fn(),
}));

import router from '@/router';
import { useUserStore } from '@/stores/user';

describe('router 受限态守卫（C4）', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it('受限账号访问任意页面 → 被弹到改密页', async () => {
    const user = useUserStore();
    user.mustChangePassword = true;

    await router.push('/favorites');
    expect(router.currentRoute.value.name).toBe('change-password');
  });

  it('改密页本身可访问（否则用户无处可去）', async () => {
    const user = useUserStore();
    user.mustChangePassword = true;

    await router.push('/change-password');
    expect(router.currentRoute.value.name).toBe('change-password');
  });

  it('普通账号不受影响', async () => {
    const user = useUserStore();
    user.mustChangePassword = false;

    await router.push('/favorites');
    expect(router.currentRoute.value.name).toBe('favorites');
  });
});
