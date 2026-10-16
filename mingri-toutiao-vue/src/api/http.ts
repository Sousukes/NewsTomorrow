import { API_BASE } from './config';

const TOKEN_KEY = 'mingri-toutiao:token';

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

/** 401 统一处理：清除本地 token 并广播事件（不在此处跳转，避免耦合路由） */
function handleUnauthorized() {
  // 只有「原本已登录」才算登录态失效：游客请求需登录的接口同样会拿到 401，
  // 那不是会话过期，广播事件会把游客强行跳到登录页（见 main.ts 的监听）。
  const hadToken = Boolean(getToken());
  setToken('');
  if (hadToken && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }
}

async function doFetch(path: string, options: RequestOptions): Promise<Response> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

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

  for (let attempt = 0; ; attempt++) {
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

    if (res.status === 401) handleUnauthorized();

    // 非 2xx：优先透传后端返回的 message（如登录失败提示），兜底用状态码文案
    if (!res.ok) {
      let message = `请求失败（${res.status}）`;
      try {
        const err = (await res.json()) as { message?: string } | null;
        if (err?.message) message = err.message;
      } catch {
        /* 响应体非 JSON 时沿用兜底文案 */
      }
      throw new ApiError(message, res.status);
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
