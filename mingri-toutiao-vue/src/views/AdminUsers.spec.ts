import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 白名单管理没有 mock 短路：整条链路都打桩在 api 层，与 AdminSources.spec.ts 同思路
vi.mock('@/api/admin', () => ({
  fetchAdminUsers: vi.fn(async () => ({ list: [], total: 0, envAdminCount: 1 })),
  addAdminUser: vi.fn(async () => ({})),
  removeAdminUser: vi.fn(async () => undefined),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}));

import AdminUsers from './AdminUsers.vue';
import * as adminApi from '@/api/admin';
import type { AdminUserItem } from '@/types';

const adminUser = (over: Partial<AdminUserItem> = {}): AdminUserItem => ({
  phone: '13900000002',
  note: '运营-小红',
  addedBy: '13800138000',
  createdAt: '2026-10-15T07:38:22',
  registered: false,
  name: '',
  ...over,
});

const mountPage = () => mount(AdminUsers);
/** 等 onMounted 的异步 load() 落地 + 一次渲染 tick */
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const phoneInput = (w: ReturnType<typeof mountPage>) => w.find('input[aria-label="管理员手机号"]');

beforeEach(() => {
  vi.clearAllMocks();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  vi.mocked(adminApi.fetchAdminUsers).mockResolvedValue({
    list: [],
    total: 0,
    envAdminCount: 1,
  });
  vi.mocked(adminApi.addAdminUser).mockResolvedValue(adminUser());
  vi.mocked(adminApi.removeAdminUser).mockResolvedValue(undefined);
});

describe('views/AdminUsers', () => {
  it('非管理员不请求列表，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminUsers).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理管理员');
  });

  it('管理员加载后渲染白名单行与双源说明', async () => {
    vi.mocked(adminApi.fetchAdminUsers).mockResolvedValue({
      list: [
        adminUser(),
        adminUser({ phone: '13900000003', registered: true, name: '老王', note: '' }),
      ],
      total: 2,
      envAdminCount: 2,
    });
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('13900000002');
    expect(w.text()).toContain('运营-小红');
    expect(w.text()).toContain('由 13800138000 添加');
    expect(w.text()).toContain('预授权');
    expect(w.text()).toContain('已注册·老王');
    // 双源说明卡披露 env 计数（不下发号码）
    expect(w.text()).toContain('共 2 位');
  });

  it('添加成功：带备注提交、清空表单并刷新列表', async () => {
    const w = mountPage();
    await settle(w);

    await phoneInput(w).setValue('13900000004');
    await w.find('input[placeholder="如：运营-张三"]').setValue('运营-小赵');
    await w.find('form').trigger('submit');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.addAdminUser).toHaveBeenCalledWith({
      phone: '13900000004',
      note: '运营-小赵',
    });
    expect(w.text()).toContain('已添加管理员 13900000004');
    expect((phoneInput(w).element as HTMLInputElement).value).toBe(''); // 表单已清空
    expect(adminApi.fetchAdminUsers).toHaveBeenCalledTimes(2); // 挂载 + 添加后刷新
  });

  it('手机号格式不对时前端拦截，不发请求', async () => {
    const w = mountPage();
    await settle(w);

    await phoneInput(w).setValue('12345');
    await w.find('form').trigger('submit');
    await w.vm.$nextTick();

    expect(adminApi.addAdminUser).not.toHaveBeenCalled();
    expect(w.text()).toContain('请输入正确的 11 位手机号');
  });

  it('添加冲突（409）时展示服务端文案', async () => {
    vi.mocked(adminApi.addAdminUser).mockRejectedValue(new Error('该手机号已是管理员'));
    const w = mountPage();
    await settle(w);

    await phoneInput(w).setValue('13900000005');
    await w.find('form').trigger('submit');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('该手机号已是管理员');
  });

  it('移除需二次确认，确认后才真正调用接口', async () => {
    vi.mocked(adminApi.fetchAdminUsers).mockResolvedValue({
      list: [adminUser()],
      total: 1,
      envAdminCount: 1,
    });
    const w = mountPage();
    await settle(w);

    await w
      .findAll('button')
      .find((b) => b.text() === '移除')!
      .trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.removeAdminUser).not.toHaveBeenCalled();

    await w
      .findAll('button')
      .find((b) => b.text() === '确认移除')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.removeAdminUser).toHaveBeenCalledWith('13900000002');
    expect(w.text()).toContain('已移除 13900000002');
    // 删除的本地真相 = 服务端真相：行已移出列表（退化为空态提示）
    expect(w.findAll('.admin__item')).toHaveLength(0);
    expect(w.text()).toContain('应用内白名单还是空的');
  });

  it('末位保护（409）时错误横幅透出服务端文案，行保留', async () => {
    vi.mocked(adminApi.fetchAdminUsers).mockResolvedValue({
      list: [adminUser()],
      total: 1,
      envAdminCount: 0,
    });
    vi.mocked(adminApi.removeAdminUser).mockRejectedValue(
      new Error('已是最后一位管理员，不能移除'),
    );
    const w = mountPage();
    await settle(w);

    await w
      .findAll('button')
      .find((b) => b.text() === '移除')!
      .trigger('click');
    await w.vm.$nextTick();
    await w
      .findAll('button')
      .find((b) => b.text() === '确认移除')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('已是最后一位管理员，不能移除');
    expect(w.text()).toContain('13900000002'); // 行仍在
  });

  it('加载失败时展示错误横幅', async () => {
    vi.mocked(adminApi.fetchAdminUsers).mockRejectedValue(new Error('网络异常'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络异常');
  });

  it('加载返回 403 时转入无权限态', async () => {
    vi.mocked(adminApi.fetchAdminUsers).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理管理员');
  });
});
