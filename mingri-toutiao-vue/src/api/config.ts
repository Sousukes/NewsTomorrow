/** 未配置 VITE_API_BASE 或显式 VITE_USE_MOCK=true 时走内置 mock。 */
const base = (import.meta.env.VITE_API_BASE ?? '').trim();

export const API_BASE = base.replace(/\/$/, '');

export const USE_MOCK =
  (import.meta.env.VITE_USE_MOCK ?? (API_BASE ? 'false' : 'true')).toString() === 'true';
