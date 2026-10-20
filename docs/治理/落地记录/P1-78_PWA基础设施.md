## 批次 P1-78（E8-a）：PWA 基础设施

按已确认方案实现（D1=外壳+收藏+最近详情、D2=双存储分工、D3=手写 SW 零依赖、
D4=network-first、D5=提示刷新、D6=写操作仅 marks）。本节对应方案的 P1-78
（manifest / SW / 注册 / 离线提示）；内容副本与图片有界缓存在 P1-79。

## 交付

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 图标 | `scripts/gen_pwa_icons.py`（新） | 确定性脚本生成 192 / 512 / maskable 三张 PNG（零依赖，`zlib` 手写编码） |
| manifest | `src/pwa/manifest.ts`（新） | manifest 的**唯一事实源**，可类型检查、可单测 |
| SW | `src/pwa/sw-template.js`（新） | 三条策略：precache cache-first / API network-first / 图片 cache-first 有界淘汰 |
| 注入 | `src/pwa/precache.ts`（新） | `buildServiceWorker(template, urls, version)` 纯函数；模板缺占位符**抛错** |
| 构建 | `vite.config.ts::pwaPlugin` | 生成 `manifest.webmanifest` + `sw.js`，并**在构建期校验图标存在** |
| 注册 | `src/pwa/register.ts`（新） | 三条守卫（prod / 有 SW / 安全上下文）+ 发现新版本提示 |
| 状态 | `src/composables/useOnline.ts`（新） | 在线状态（首屏同步 + 事件驱动） |
| 视图 | `App.vue` | 壳层内两条横幅：发现新版本（点击刷新）/ 当前离线（持久） |
| 入口 | `index.html` | manifest / theme-color / apple-touch-icon |
| CSP | `src/security/csp.ts` | 显式 `worker-src 'self'` |

## 六个实现要点

1. **manifest 的唯一事实源在 `src/` 而不是 `public/`**：放进 src 才能被 vitest
   import、被 tsc 校验（`purpose` 写成 `maskabble` 编译期就发现）；产物由插件
   生成，public 目录不残留副本——两份 manifest 必漂移，那是 D4 的同一个坑。
2. **precache 注入抽成纯函数并单测**：这条链路错了**零运行时报错**，只表现为
   "离线打不开"。注入函数对"模板缺占位符"直接抛错——静默产出一份空清单的 SW
   比构建失败难查得多。
3. **`replace` 只替换第一处的坑踩了两次**（这是本批最反复的一处）：
   - 第一次：占位符字样写在模板注释里 → 注入后注释变成产物清单、赋值行仍是 `undefined`；
   - 第二次：为过 ESLint 把占位符写进 `/* global */` 声明 → 同一个后果。
   最终解法是注释里**不出现字面量**（用 `eslint-disable-next-line` 覆盖那一行），
   并在注释里写明"不要在此写出字面量"的原因。
4. **SW 手写零依赖**，`/* global */` 声明 SW 全局对象：
   ESLint 默认按 Node 环境判 `caches`/`fetch`/`Headers` 未定义。
5. **离线回落打 `X-Offline-Fallback: 1` 响应头**而不是改写 body：
   调用方需要区分"这是旧副本"，SW 悄悄返回旧数据会让 UI 把过期内容当实时结果展示。
6. **更新走提示刷新**：`reg.updatefound` → 横幅 → 点击才 `postMessage('SKIP_WAITING')`；
   自动 `skipWaiting` 会在阅读中途换掉整个执行环境（页面忽然刷新、位置丢失）。

## 修掉一个自己引入的设计缺陷（测试抓到）

`useOnline` 初版在事件处理里回读 `navigator.onLine`。单测立刻抓到：
jsdom 派发 `offline` 事件后该属性**不会**跟着变（规范也未保证事件到达时它已更新），
表现为"事件到了、界面没变"。改为**按事件类型直接置值**——事件本身就是最直接的信号。

## 测试（29 项，`src/pwa/*.spec.ts` + `useOnline.spec.ts`）

- `manifest`（6）：可安装必需字段、主题/背景色 hex 且与设计令牌一致、
  **同时覆盖 any 与 maskable**、快捷方式路由、JSON 可序列化
- `precache`（6）：占位符全替换、**版本号每一处都替换**（缓存键与引用一致）、
  清单顺序原样、空清单合法、**缺占位符抛错**、真实模板可注入
- `sw-template`（8）：非 GET 不碰、回落打标记、图片上限、跨域不拦截、
  导航回落外壳、旧缓存清理、消息触发更新、API 先网络后缓存
- `register`（7）：无 SW 不抛、**dev 不注册**、prod 注册 `/sw.js`、
  非安全上下文不注册、注册失败被捕获、有新版本时提示、点刷新发 `SKIP_WAITING`
- `useOnline`（3）：首屏同步、事件驱动、卸载后不响应
- `csp`（2）：`worker-src 'self'`、`default-src 'self'`

## 回归

- vitest 892 通过（71 文件，比 P1-77 的 867 多 25 项）；覆盖率
  **82.3 / 75.12 / 73.67 / 85.45**，均在阈值（79 / 72 / 68 / 82）之上
- `npm run verify`：0 error（2 个 warning 是 ErrorBoundary.spec 既有的多组件提示）
- `npm run build`：产出 `manifest.webmanifest` + `sw.js`；
  precache 清单里是**带 hash 的产物名**（`index-Dzp-57Xd.js` 等）+ `/index.html` +
  `/icons/icon-192.png`，无残留占位符
- 图标由脚本生成并校验为合法 PNG（192² / 512² / maskable 512²）

## 已知边界（属 P1-79）

- 断网时**列表仍是网络优先失败后回落**：目前 SW 只缓存被请求过的 API 响应，
  首次进入的页面在断网时仍会拿到 `{ code: 0, data: null }` 降级——内容副本
  （`reader:<scope>`）与图片 CacheStorage 的有界淘汰策略在 P1-79 落地。
- Lighthouse 的"离线可用"一项要等内容副本完成后才真正达标；本批达成
  "可安装 + manifest + SW 已注册 + 离线提示"。
