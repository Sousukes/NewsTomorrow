/**
 * API 运行时配置。
 * - 未配置 VITE_API_BASE（或 VITE_USE_MOCK=true）→ 走内置 mock，保证离线可运行。
 * - 配置 VITE_API_BASE 后即切换为真实后端请求。
 */
const base = (import.meta.env.VITE_API_BASE ?? '').trim();

export const API_BASE = base.replace(/\/$/, '');

export const USE_MOCK =
  (import.meta.env.VITE_USE_MOCK ?? (API_BASE ? 'false' : 'true')).toString() === 'true';
