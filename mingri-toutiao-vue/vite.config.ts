import { fileURLToPath, URL } from 'node:url';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import vue from '@vitejs/plugin-vue';
import { buildCsp } from './src/security/csp';

/**
 * 把 CSP 注入 `index.html`（C9）。
 *
 * **为什么用 `<meta>` 而不是只靠响应头**：前端是**静态托管**（后端不托管 SPA，
 * 见 `server-api/main.py` 无 StaticFiles），响应头由谁下发取决于部署方。
 * 写进 HTML 则无论 nginx / OSS / Pages / CDN 都生效，不需要改基础设施；
 * 代价是 `frame-ancestors`、`report-uri` 在 meta 里被规范忽略（见 csp.ts）。
 * 能用响应头时建议搬过去（更强的那几条才会生效）。
 */
function cspPlugin(apiBase: string): Plugin {
  return {
    name: 'csp-meta-inject',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        // `ctx.server` 仅开发服务器下存在：据此放宽 ws / localhost
        const dev = Boolean(ctx.server);
        return [
          {
            tag: 'meta',
            attrs: {
              'http-equiv': 'Content-Security-Policy',
              content: buildCsp({ dev, apiBase }),
            },
            injectTo: 'head',
          },
        ];
      },
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    plugins: [vue(), cspPlugin(env.VITE_API_BASE ?? '')],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      include: ['src/**/*.spec.ts'],
    },
  };
});
