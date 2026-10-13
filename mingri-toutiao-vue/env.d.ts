/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 真实后端地址，如 https://api.example.com；留空则走内置 mock */
  readonly VITE_API_BASE?: string;
  /** 强制 mock：'true' 时即使配置了 base 也走 mock */
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>;
  export default component;
}
