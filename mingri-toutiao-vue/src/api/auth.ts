import { mockDelay, request, setToken } from './http';
import { USE_MOCK } from './config';

export interface LoginParams {
  phone: string;
  password: string;
}

export interface LoginResult {
  token: string;
  /**
   * `id` 用于本地标记的作用域隔离（mock 模式下可能缺失）。
   * `isAdmin` 由后端按 `ADMIN_PHONES` 白名单判定后下发——前端据此决定
   * 是否展示管理入口，省掉一次探测请求；只下发布尔值，不下发白名单本身。
   */
  user: { id?: number; name: string; phone?: string; isAdmin?: boolean };
}

/** POST /auth/login —— 登录（成功即写入 token） */
export async function login({ phone, password }: LoginParams): Promise<LoginResult> {
  if (USE_MOCK) {
    await mockDelay(null, 300);
    // mock 演示：密码 123456 视为成功，其余触发 S16 失败态
    if (password !== '123456') throw new Error('手机号或密码错误，请重新输入');
    const token = `mock-token-${Date.now()}`;
    setToken(token);
    // mock 模式没有白名单概念，恒不授予管理能力（入口随之隐藏）
    return { token, user: { name: '明日头条用户', isAdmin: false } };
  }

  const data = await request<LoginResult>('/auth/login', {
    method: 'POST',
    body: { phone, password },
  });
  if (data?.token) setToken(data.token);
  return data;
}

/** POST /auth/logout —— 退出（清 token） */
export async function logout(): Promise<void> {
  if (USE_MOCK) {
    setToken('');
    return mockDelay(undefined, 100);
  }
  try {
    await request<void>('/auth/logout', { method: 'POST' });
  } finally {
    setToken('');
  }
}
