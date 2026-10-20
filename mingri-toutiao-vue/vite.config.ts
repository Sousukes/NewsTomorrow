import { existsSync, readFileSync as fsReadFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import { manifest, manifestJson } from './src/pwa/manifest';
import { CSS_VAR_MAP, palette } from './src/styles/palette';
import { buildServiceWorker } from './src/pwa/precache';
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

/**
 * 校验 `tokens.css` 与 `palette.ts` 一致（E9）。
 * 色值必须在 CSS 里，而对比度门槛要在单测里断言（vitest 会把 .css 置空），
 * 两者各存一份，靠构建期校验钉住。
 */
function tokensPlugin(): Plugin {
  return {
    name: 'tokens-palette-check',
    buildStart() {
      const css = fsReadFileSync(
        fileURLToPath(new URL('./src/styles/tokens.css', import.meta.url)),
        'utf-8',
      );
      const blocks: Record<string, string> = {
        light: css.slice(css.indexOf(':root {'), css.indexOf("[data-theme='dark']")),
        dark: css.slice(css.indexOf("[data-theme='dark']")),
      };
      for (const [mode, block] of Object.entries(blocks)) {
        for (const [cssVar, key] of Object.entries(CSS_VAR_MAP)) {
          const expected = palette[mode as 'light' | 'dark'][key];
          const found = block.match(new RegExp(`--${cssVar}:\\s*(#[0-9a-fA-F]{3,6})`))?.[1];
          if (found && found.toLowerCase() !== expected.toLowerCase()) {
            this.error(
              `tokens.css 与 palette.ts 不一致：${mode} --${cssVar} = ${found}，期望 ${expected}`,
            );
          }
        }
      }
    },
  };
}

/**
 * 取 API 基址的 origin（跨域部署时图片代理在另一个源上）。
 * 相对路径（`/api`，即同源部署）返回空串——那种部署不需要额外放行任何源。
 */
function apiOrigin(apiBase: string): string {
  const base = apiBase.trim();
  if (!base) return '';
  try {
    const u = new URL(base, 'http://placeholder.invalid');
    return u.origin === 'http://placeholder.invalid' ? '' : u.origin;
  } catch {
    return '';
  }
}

/**
 * 生成 PWA 产物：`manifest.webmanifest` + `sw.js`（E8 / P1-78）。
 *
 * 为什么不让它们以静态文件形式存在于 `public/`：
 * - **manifest** 的唯一事实源是 `src/pwa/manifest.ts`（可类型检查、可单测），
 *   静态副本必然漂移（与 D4「mock 第二实现」同一个坑）；
 * - **sw.js** 的 precache 清单必须是这一批构建的 hash 名，人工维护必然过期。
 *
 * 与 `cspPlugin` 同一模式：都在构建期改写产物。
 */
function pwaPlugin(apiBase: string): Plugin {
  return {
    name: 'pwa-manifest-and-sw',
    apply: () => true,
    generateBundle(_options, bundle) {
      // 图标存在性在**构建期**校验：缺文件时构建直接失败，
      // 而不是上线后表现为"安装按钮不出现"（那种缺失用户侧才暴露）。
      const publicDir = fileURLToPath(new URL('./public', import.meta.url));
      for (const icon of manifest.icons) {
        const file = resolve(publicDir, `.${icon.src}`);
        if (!existsSync(file)) {
          this.error(`PWA 图标缺失：${icon.src}（先跑 scripts/gen_pwa_icons.py）`);
        }
      }

      // 只 precache「带 hash 的构建产物」+ 入口与 manifest：地图、LICENSE 之类
      // 对离线启动没用，只会把清单撑大（每条都是首屏要多下一次的请求）。
      const urls = Object.values(bundle)
        .map((o) => `/${o.fileName}`)
        .concat(['/index.html', '/manifest.webmanifest', '/icons/icon-192.png'])
        .filter((v, i, a) => a.indexOf(v) === i);

      const tpl = fsReadFileSync(
        fileURLToPath(new URL('./src/pwa/sw-template.js', import.meta.url)),
        'utf-8',
      );
      const version = `b${Date.now().toString(36)}`;

      this.emitFile({
        type: 'asset',
        fileName: 'manifest.webmanifest',
        source: manifestJson(),
      });
      this.emitFile({
        type: 'asset',
        fileName: 'sw.js',
        // own origins：图片代理所在源（SW 自己的源由模板在运行时 concat 进来——
        // 部署域名构建期不可知）。同源部署时是空串；跨域 API 部署时必须带上，
        // 否则配图（`${API_BASE}/api/img?…`）一张都缓存不到。
        source: buildServiceWorker(tpl, urls, version, [apiOrigin(apiBase)]),
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    plugins: [
      vue(),
      cspPlugin(env.VITE_API_BASE ?? ''),
      pwaPlugin(env.VITE_API_BASE ?? ''),
      tokensPlugin(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      include: ['src/**/*.spec.ts'],
      /**
       * F4：覆盖率门槛（后端 P1-45 的对偶，见 `.coveragerc`）。
       *
       * **`all: true` 是关键**：不打开时只统计"被测试 import 过的文件"，
       * 于是**新增模块且不写测试 -> 数字一点不掉**，门槛形同虚设。
       * 打开后未被导入的文件按 0% 计入——这才是"加了代码要配测试"的真约束。
       *
       * 阈值取实测值下浮约 2 个点（与后端 `.coveragerc` 同一策略）：
       * 门槛只防回退，不追求数字好看；真要提覆盖率是 F5 的活。
       */
      coverage: {
        provider: 'v8',
        all: true,
        include: ['src/**/*.{ts,vue}'],
        exclude: [
          'src/**/*.spec.ts',
          'src/**/*.d.ts',
          'src/main.ts',
          'src/types/**',
          'src/**/*.stories.ts',
        ],
        reporter: ['text-summary', 'json-summary'],
        reportsDirectory: 'coverage',
        thresholds: {
          statements: 79,
          branches: 72,
          functions: 68,
          lines: 82,
        },
      },
    },
  };
});
