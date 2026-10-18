# 明日头条 · Vue 3 组件库与多页应用

> 一套对齐 Ardot 设计稿（「组件规范 Design System」页）的可运行前端骨架：Vite 6 + Vue 3.5 + TypeScript，零 UI 框架依赖，设计令牌以 CSS 变量承载。实时演示见 `src/views/Showcase.vue`，组件级文档见 [`docs/前端/设计系统.md`](../docs/前端/设计系统.md)。

> 本文件是**入口与索引**。深度内容按主题拆在 [`docs/前端/`](../docs/前端/)。
> 全站索引见 [`docs/README.md`](../docs/README.md)。

## 快速开始

```bash
npm install
npm run dev        # 本地开发，默认 http://localhost:5173
npm run build      # vue-tsc 类型检查 + vite 生产构建
npm run preview    # 预览构建产物
```

### 质量校验

```bash
npm run verify          # type-check + lint + format:check（提交前跑这个）
npm run test            # 单元测试（Vitest，jsdom）
npm run test:watch      # 监听模式
npm run lint / lint:fix # ESLint（Vue + TS，含 vue 规则）
npm run format          # Prettier 格式化
npm run contract-check  # 前后端契约检查（需后端已启动）
```

> `npm run verify` 的三段是**短路**的：一段失败后面不会跑，所以修完一段要整条复跑。

### 运行模式

| 模式 | 怎么起 | 数据来源 |
| --- | --- | --- |
| 离线演示（默认） | `npm run dev` | 内置 mock（`src/mock`），**不需要后端** |
| mock 后端联调 | `npm run dev:api` + `VITE_API_BASE=http://127.0.0.1:3001/api` | `mock-server/server.mjs` |
| 真后端 | `VITE_API_BASE=http://127.0.0.1:8801/api npm run dev` | FastAPI |

生产构建**必须**显式传 `VITE_USE_MOCK=false`（默认值是「没配 `VITE_API_BASE`
就当演示态」，不显式传的话镜像会静默跑内置 mock）。详见
[`docs/deploy.md`](../docs/deploy.md)。

## 文档索引

| 主题 | 文档 |
| --- | --- |
| 目录结构、开发进度、联调、Storybook | [快速开始与联调](../docs/前端/快速开始与联调.md) |
| 深色模式、设计令牌、组件总览、视图与屏幕映射、组合式函数、数据层 | [设计系统](../docs/前端/设计系统.md) |
| 状态管理（stores）、API 层、CSP、二次校验、图片代理、改密受限态 | [数据与安全](../docs/前端/数据与安全.md) |
| 组件 Props / 接口字段速查 | [速查表 · 设计系统与组件](../docs/速查表/设计系统与组件.md) |

## 几条最容易踩的约定

- **`vi.mock` 是文件级的**：想让某个 spec 走真实后端模式（`USE_MOCK: false`），
  必须**单独建文件**，混在同一文件里会污染其他用例。
- **spec 里不要开 `restoreMocks`**：它会把 `vi.mock` 工厂里的默认实现一起抹掉。
- **列表状态按 (频道 × 排序) 分桶**：`fetchNews/loadMore/resetPaging` 必传频道与排序，
  没有「隐式当前频道」——那是首页/视频页串页的根因。
- **入参多词字段一律驼峰**，与后端一致（蛇形会被 pydantic 静默丢弃）。
- **图片一律走代理**（`utils/image.ts::proxiedImageUrl`），宽度只能取后端白名单值。