import { clearSession, mockDelay, request, setSession, setStepUpToken } from './http';
import { USE_MOCK } from './config';

export interface LoginParams {
  phone: string;
  password: string;
}

export interface LoginResult {
  token: string;
  /**
   * C1 双令牌：`refreshToken` 只能用来换 access（7 天），`expiresIn` 是
   * access 剩余秒数（默认 1800）。两者**刻意可选**——老环境可能还没升级后端。
   */
  refreshToken?: string;
  expiresIn?: number;
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

/**
 * POST /auth/step-up —— 高敏操作二次校验（C10）。
 *
 * 验通过后由 `http.ts` 保管 5 分钟；期间高敏请求自动带 `X-Step-Up-Token`。
 * 失败会抛错（40101 密码错 / 42902 连续失败被锁），由对话框展示。
 */
export async function stepUp(password: string): Promise<boolean> {
  const data = await request<{ stepUpToken: string; expiresIn: number }>('/auth/step-up', {
    method: 'POST',
    body: { password },
  });
  if (!data?.stepUpToken) return false;
  setStepUpToken(data.stepUpToken, data.expiresIn);
  return true;
}
