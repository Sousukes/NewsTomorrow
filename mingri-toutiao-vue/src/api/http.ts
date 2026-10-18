import { API_BASE, USE_MOCK } from './config';

const TOKEN_KEY = 'mingri-toutiao:token';
/**
 * refresh 令牌**单独分键**（C1）。
 * 与 access 混在一个键里会在登出/清理时漏删其一，留下"半登录态"。
 */
const REFRESH_TOKEN_KEY = 'mingri-toutiao:refresh-token';
/** access 到期时刻（epoch ms）；`0` = 未知（不主动续期，交给 401 兜底） */
const EXPIRES_KEY = 'mingri-toutiao:token-exp';
/** 提前多久续期：宁可早一点，也别在请求途中撞上过期造成一次失败往返 */
const REFRESH_MARGIN_MS = 60_000;

/** 登录响应里与令牌相关的部分（`token` 必填，其余为 C1 新增且刻意可选） */
export interface SessionPayload {
  token?: string;
  refreshToken?: string;
  /** access 剩余秒数 */
  expiresIn?: number;
}

/** 401 时广播的事件名：由 user store / 路由监听后清理登录态并跳转 */
export const UNAUTHORIZED_EVENT = 'mingri:unauthorized';

/** 默认请求超时（毫秒） */
const DEFAULT_TIMEOUT_MS = 15000;
/** GET 请求的网络层最大重试次数（幂等，可安全重试） */
const DEFAULT_RETRIES = 2;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式静默失败 */
  }
}

export function getRefreshToken(): string {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

function setRefreshToken(token: string) {
  try {
    if (token) localStorage.setItem(REFRESH_TOKEN_KEY, token);
    else localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    /* 隐私模式静默失败 */
  }
}

/** 读取到期时刻；非法值一律按"未知"处理（不主动续期） */
function getExpiresAt(): number {
  try {
    const raw = Number(localStorage.getItem(EXPIRES_KEY) ?? 0);
    return Number.isFinite(raw) && raw > 0 ? raw : 0;
  } catch {
    return 0;
  }
}

function setExpiresAt(ms: number) {
  try {
    if (ms > 0) localStorage.setItem(EXPIRES_KEY, String(ms));
    else localStorage.removeItem(EXPIRES_KEY);
  } catch {
    /* 隐私模式静默失败 */
  }
}

/**
 * 写入一次登录/续期下发的令牌对（C1）。
 *
 * 三个键**同写同清**：只更新 token 而留下旧 refreshToken，等于登出后仍留着
 * 一张可换发令牌的长令牌——那正是双轨方案要消除的东西。
 */
export function setSession(payload: SessionPayload): void {
  const data = payload ?? {};
  setToken(data.token ?? '');
  setRefreshToken(data.refreshToken ?? '');
  // 没有 expiresIn 就不记到期时间：宁可不主动续期（401 时会兜底），
  // 也别拿一个猜出来的时间反复打续期接口。
  const seconds = typeof data.expiresIn === 'number' ? data.expiresIn : 0;
  setExpiresAt(seconds > 0 ? Date.now() + seconds * 1000 : 0);
}

/** 清理登录态（登出 / 401 兜底）：三个键一并清空 */
export function clearSession(): void {
  setToken('');
  setRefreshToken('');
  setExpiresAt(0);
  // 二次校验令牌一并清：它绑定在本次会话上，留着等于给下一个用这台设备的人
  // 留了一张 5 分钟的高敏操作通行证
  clearStepUpToken();
}

/* ---------------- 高敏操作二次校验（C10） ---------------- */

/** 40304 = 缺少/无效的二次校验令牌。广播后由全局密码对话框接管 */
export const STEP_UP_REQUIRED_EVENT = 'mingri:step-up-required';
export const STEP_UP_REQUIRED_CODE = 40304;

/**
 * step-up 令牌只存**内存**（不落 localStorage）：刷新页面即失效，需再验一次。
 * 对高敏操作这是合理的安全姿态——5 分钟的凭证不值得为省一次输入而持久化。
 */
let stepUpToken = '';
let stepUpExpiresAt = 0;

export function setStepUpToken(token: string, expiresIn = 300): void {
  stepUpToken = token;
  // `<=0` 一律按「立即过期」处理：朝安全的方向猜，而不是「永不过期」。
  // 服务端下发的 seconds 理论上总为正，真拿到非正值说明有问题。
  stepUpExpiresAt = expiresIn > 0 ? Date.now() + expiresIn * 1000 : 1;
}

export function getStepUpToken(): string {
  if (!stepUpToken) return '';
  if (stepUpExpiresAt && Date.now() >= stepUpExpiresAt) {
    clearStepUpToken();
    return '';
  }
  return stepUpToken;
}

export function clearStepUpToken(): void {
  stepUpToken = '';
  stepUpExpiresAt = 0;
}

/** 等待中的二次校验请求（通常同一时刻只有一个） */
let stepUpWaiters: ((granted: boolean) => void)[] = [];

/**
 * 请求二次校验：弹出全局对话框并等待结果。
 *
 * 由 `request()` 在收到 40304 时调用；对话框验证成功后调 `resolveStepUp(true)`，
 * 取消/失败调 `resolveStepUp(false)`。
 */
export function requestStepUp(): Promise<boolean> {
  if (typeof window === 'undefined') return Promise.resolve(false);
  return new Promise((resolve) => {
    stepUpWaiters.push(resolve);
    window.dispatchEvent(new CustomEvent(STEP_UP_REQUIRED_EVENT));
  });
}

/** 对话框侧回调：granted=true 表示拿到了新令牌，等待中的请求会带上它重试 */
export function resolveStepUp(granted: boolean): void {
  const waiters = stepUpWaiters;
  stepUpWaiters = [];
  waiters.forEach((w) => w(granted));
}

type QueryValue = string | number | undefined | null;

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, QueryValue>;
  /** 覆盖默认超时（毫秒） */
  timeoutMs?: number;
  /** 覆盖默认重试次数（仅对 GET 生效） */
  retries?: number;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  let url = `${API_BASE}${path}`;
  if (query) {
    const qs = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&');
    if (qs) url += `?${qs}`;
  }
  return url;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 仅网络故障 / 超时可重试；HTTP 业务错误（ApiError）不重试 */
function isRetriable(err: unknown): boolean {
  if (err instanceof DOMException && err.name === 'AbortError') return true;
  return err instanceof TypeError; // fetch 网络层失败
}

/** 401 统一处理：清除本地令牌对并广播事件（不在此处跳转，避免耦合路由） */
function handleUnauthorized() {
  // 只有「原本已登录」才算登录态失效：游客请求需登录的接口同样会拿到 401，
  // 那不是会话过期，广播事件会把游客强行跳到登录页（见 main.ts 的监听）。
  const hadToken = Boolean(getToken());
  clearSession();
  if (hadToken && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }
}

/** access 是否已进入"该续期"的窗口 */
function isExpiringSoon(): boolean {
  const exp = getExpiresAt();
  if (!exp) return false;
  return Date.now() >= exp - REFRESH_MARGIN_MS;
}

/**
 * 真正去换一张 access（C1）。
 *
 * 刻意**不走** `request()`：`request` 里又会调 `ensureFreshToken`，形成递归。
 */
async function callRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const res = await fetch(buildUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refreshToken }),
    });
    if (!res.ok) return false;
    const body = (await res.json().catch(() => null)) as { data?: SessionPayload } | null;
    const data = body?.data;
    if (!data?.token) return false;
    setSession(data);
    return true;
  } catch {
    // 网络故障不在此处清登录态：可能是断网，交给上层按 401/网络错误分别处理
    return false;
  }
}

/** 并发合并：多个请求同时发现令牌将过期时，只打一次续期接口 */
let refreshInFlight: Promise<boolean> | null = null;

/**
 * 保证 access 可用。
 *
 * @param force 已撞上 401 时强制换发（此时不再看到期时间）
 * @returns 是否可以继续发请求
 */
function ensureFreshToken(force = false): Promise<boolean> {
  // mock 模式没有真实令牌与续期接口，任何续期尝试都是噪声
  if (USE_MOCK) return Promise.resolve(false);
  if (!getToken()) return Promise.resolve(false);
  if (!force && !isExpiringSoon()) return Promise.resolve(true);
  if (!refreshInFlight) {
    refreshInFlight = callRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function doFetch(path: string, options: RequestOptions): Promise<Response> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  // 二次校验令牌（C10）：存在且未过期时才带，避免把过期凭证发给服务端
  const stepUp = getStepUpToken();
  if (stepUp) headers['X-Step-Up-Token'] = stepUp;

  try {
    return await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 统一请求：自动带 token、超时中断、GET 网络重试、401 清理登录态。
 * 约定后端返回 `{ code, message?, data }`（code 0/200 为成功）。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const maxRetries = options.retries ?? (method === 'GET' ? DEFAULT_RETRIES : 0);
  /** 本次请求是否已因 401 续过期：**一次请求最多续一次**，否则会被服务端循环 401 */
  let renewed = false;
  /** 是否已因 40304 弹过一次二次校验：同款守卫，防弹窗循环 */
  let stepped = false;

  for (let attempt = 0; ; attempt++) {
    // C1：access 短（默认 30 分钟），快到期就先换一张再发，避免无谓的 401 往返
    await ensureFreshToken();

    let res: Response;
    try {
      res = await doFetch(path, options);
    } catch (e) {
      if (attempt < maxRetries && isRetriable(e)) {
        await sleep(200 * 2 ** attempt); // 指数退避
        continue;
      }
      throw e;
    }

    if (res.status === 401) {
      // 过期是最常见的 401 原因：先换令牌重试一次，别一上来就清登录态把用户踢走。
      // 续期失败（refresh 也过期 / 已登出）才按会话失效处理。
      if (!renewed && (await ensureFreshToken(true))) {
        renewed = true;
        continue;
      }
      handleUnauthorized();
    }

    // 非 2xx：优先透传后端返回的 message（如登录失败提示），兜底用状态码文案
    if (!res.ok) {
      // 错误体只解析一次：40304 的判定与 message 透传都要用它，
      // 用 `res.clone()` 解析两次既浪费，也会让测试替身必须实现 clone
      const errBody = (await res.json().catch(() => null)) as {
        code?: number;
        message?: string;
      } | null;

      // C10：高敏操作缺二次校验 → 弹全局密码对话框，拿到令牌后重试一次。
      // 与 401 续期同款守卫：一次请求最多弹一次，否则「验证成功但仍被拒」
      // 会变成弹窗循环。
      if (res.status === 403 && errBody?.code === STEP_UP_REQUIRED_CODE && !stepped) {
        if (await requestStepUp()) {
          stepped = true;
          continue;
        }
      }

      throw new ApiError(errBody?.message ?? `请求失败（${res.status}）`, res.status);
    }

    const body = (await res.json().catch(() => null)) as unknown;

    // 约定形如 { code, message, data }
    if (body && typeof body === 'object' && 'code' in (body as Record<string, unknown>)) {
      const { code, message, data } = body as { code: number; message?: string; data?: unknown };
      if (code !== 0 && code !== 200) throw new ApiError(message ?? '接口返回错误', res.status);
      return data as T;
    }

    return body as T;
  }
}

/** mock 模式模拟网络延迟 */
export function mockDelay<T>(data: T, ms = 150): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}
