import { clearSession, mockDelay, request, setSession, setStepUpToken } from './http';
import { USE_MOCK } from './config';

export interface LoginParams {
  phone: string;
  password: string;
}

export interface LoginResult {
  token: string;
  /** 双令牌：refreshToken 换 access（7 天），expiresIn 为 access 剩余秒数。 */
  refreshToken?: string;
  expiresIn?: number;
  /** id 用于本地标记作用域（mock 下可能缺失）；isAdmin 只下发布尔值。 */
  user: {
    id?: number;
    name: string;
    phone?: string;
    isAdmin?: boolean;
    /** C4：管理员刚代其重置过密码 → 前端强制跳改密页 */
    mustChangePassword?: boolean;
  };
}

/** POST /auth/login —— 登录（成功即写入 token） */
export async function login({ phone, password }: LoginParams): Promise<LoginResult> {
  if (USE_MOCK) {
    await mockDelay(null, 300);
    // mock 演示：密码 123456 视为成功，其余触发 S16 失败态
    if (password !== '123456') throw new Error('手机号或密码错误，请重新输入');
    const token = `mock-token-${Date.now()}`;
    // mock 模式不发真实过期时间：`http.ts` 在 mock 下跳过续期，这里给出形状即可
    setSession({ token, refreshToken: `mock-refresh-${Date.now()}` });
    // mock 模式没有白名单概念，恒不授予管理能力（入口随之隐藏）
    return { token, user: { name: '明日头条用户', isAdmin: false } };
  }

  const data = await request<LoginResult>('/auth/login', {
    method: 'POST',
    body: { phone, password },
  });
  // 令牌对 + 到期时间一次写入（C1）：只写 token 会留下旧的 refreshToken
  if (data?.token) setSession(data);
  return data;
}

/** POST /auth/logout —— 退出（清 token） */
export async function logout(): Promise<void> {
  if (USE_MOCK) {
    clearSession();
    return mockDelay(undefined, 100);
  }
  try {
    await request<void>('/auth/logout', { method: 'POST' });
  } finally {
    // 即使请求失败也要清本地：服务端吊销失败不能留下一张可用的长令牌
    clearSession();
  }
}

/** 二次校验：通过后由 http.ts 保管 5 分钟（40101 密码错 / 42902 被锁）。 */
export async function stepUp(password: string): Promise<boolean> {
  const data = await request<{ stepUpToken: string; expiresIn: number }>('/auth/step-up', {
    method: 'POST',
    body: { password },
  });
  if (!data?.stepUpToken) return false;
  setStepUpToken(data.stepUpToken, data.expiresIn);
  return true;
}

/** 自助改密：后端作废旧令牌并下发新的一对，必须立刻写入。 */
export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<LoginResult> {
  const data = await request<LoginResult>('/auth/password', {
    method: 'PUT',
    body: { oldPassword, newPassword },
  });
  if (data?.token) setSession(data);
  return data;
}
