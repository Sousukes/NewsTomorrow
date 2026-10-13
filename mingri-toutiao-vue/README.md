# 明日头条 · Vue 3 组件库与多页应用

> 一套对齐 Ardot 设计稿（「组件规范 Design System」页）的可运行前端骨架：Vite 6 + Vue 3.5 + TypeScript，零 UI 框架依赖，设计令牌以 CSS 变量承载。本文件即组件级文档（Storybook 风格），实时演示见 `src/views/Showcase.vue`。

---

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

- **类型安全**：`vue-tsc --noEmit` 全程零报错；组件 Props / Emits 全类型化。
- **代码规范**：ESLint（flat config，`eslint.config.js`）+ Prettier。
- **单元测试**：Vitest 共 **227** 项，覆盖：
  - `useCarousel`（19）：克隆帧回环 / transitionend 收尾 / 兜底定时器 / 手势阈值 / 数据变化重置
  - `BannerCarousel`（8）：**克隆帧无重复 key（回归）**、配图与标题按 id 对应、危险协议拦截、点击派发、卸载清定时器
  - `utils/image`（13）：CSS 注入防护（协议白名单 + 转义）
  - `stores`（14）：消息已读幂等、无限滚动 loadMore 去重 / 并发 / 末页 / 失败
  - `stores/user`（19）：**收藏/历史云端同步 + 账号命名空间隔离**——未登录不发请求、
    登录响应缺 id 时不发同步、登录后并集合并并以服务端为准、合并结果写入 news 缓存、
    同步失败不阻断登录、匿名作用域、登录采纳匿名数据并清空匿名键、**登出切回匿名且账号数据保留**、
    **不同账号互不可见（防串号）**、同 tick「改标记→登出」不污染匿名作用域、旧格式自动迁移、
    收藏乐观更新与失败不回滚、并发推送序号守卫、历史去重/置顶/上限裁剪/重复浏览不重复请求、清空
  - `stores/user` 搜索历史（10）：去重置顶、空白词忽略、超限裁剪最旧、**独立键存储（不会被云端同步带上）**、删单条、清空、**换账号互不可见（防串号）**、登出切回匿名、损坏数据忽略、非字符串项过滤
  - `stores/user` 管理能力标志 `isAdmin`（6）：登录响应带 `isAdmin=true` 时置位、**缺省不授予特权**、
    **落盘后刷新页面运营入口不消失**、登出归位（不把上个账号的特权留给匿名态）、
    **未登录时忽略 prefs 里的 isAdmin**（token 失效不得留下管理入口）、普通账号仍为 false
  - `api/admin`（16）：`ADMIN_SOURCES_SUPPORTED` 仅在真实后端为真（mock 模式下恒隐藏入口）、
    `ensureRealBackend` 守卫、行数据归一化（缺 `key` 的脏行丢弃、`lastError` 空串转 null、
    **`respectRobots` 缺失时按更保守的一方兜底为 `true`、显式 `false` 原样透出**）、
    五个源管理接口的路径/方法/错误透传
  - `views/AdminUsers`（11）：管理员白名单渲染（含 `registered` 已注册/预授权与 `addedBy`）、
    env 计数说明、添加成功清空表单并刷新、**手机号格式前端拦截不发请求**、冲突 409 展示服务端文案、
    移除二次确认、**末位保护 409 保留行并报服务端语义**、加载失败与 403 转无权限态、
    **应急通道缺失提示**（env 计数为 0 时整张说明卡转警示态，已配置时不显示）
  - `views/AdminAudit`（7）：时间线渲染（中文动作 / 对象 / 操作者 / 时间）、
    **字段级变更渲染**（权重 9→3、enabled 翻成启用→停用而非 true→false）、
    **加载更多按游标追加且不重复**、带 `target` 进入时只查该对象并提供查看全部、
    加载失败与 403 转无权限态、**非管理员不请求**
  - `views/AdminSources`（24）：汇总计数、新增/编辑表单与前端校验、启停乐观更新+失败回滚、
    删除/重置二次确认、健康状态渲染、**非管理员不请求列表直接呈无权限态**（不泄露接口存在性）；
    **搜索与筛选**（名称/地址/标识模糊匹配 + 频道/状态 chips + 无结果清除入口）、
    **健康汇总**（总数/启用/异常/未抓取四计数，点数字即筛选；**异常源置顶**，组内保持权重降序）、
    **批量操作**（多选 + 全选只作用可见项 + 批量启停/删除，乐观更新 + 部分失败回滚汇总）、
    **robots 开关**（默认遵守时列表无标记、关闭的源留下「忽略 robots」可见标记、
    编辑表单取消勾选后 PATCH 带 `respectRobots=false`、新增表单默认勾选并透传 `true`）
  - `stores/comments`（17）：评论删除成功移除、**失败回滚到原位置**、不存在时返回 false、
    已删除评论不可再点赞；**两级评论**——`reply` 乐观插入已展开线程并递增 `replyCount`
    （服务端失败回滚两者）、未展开线程只递增计数、**回复的回复 rootId 拍平到同一主评论**、
    `loadReplies` 用完整列表替换预览（失败保留预览态）、`loadMoreReplies` 游标追加、
    `count` 含回复数、删回复递减计数、删主评论连同已加载回复与加载态一并移除
  - `api/news`（8）：相关推荐请求路径、字段归一化、**接口失败静默降级为空数组**、
    `sort` 透传查询串、缺省 `latest`、**非法 sort 归一化为 latest**
  - `constants/sort`（6）：三模式与后端取值一致（漂移会 422）、`isSortMode` 守卫、
    `toSort` 非法回退、个性化模式标出
  - `api/news` 热搜（2）：路径与字段归一化、**失败静默降级为空数组**
  - `stores/news` 排序（3）：`fetchNews` 记录当前排序、
    **`loadMore` 沿用首请求排序（换排序会使服务端游标失效）**、切换频道保持排序
  - `constants/theme`（5）与 `composables/useTheme`（6）：三态解析、`data-theme` 写入与
    `color-scheme` 同步、切换即生效、持久化、非法值归一化、停止监听后不再跟随
  - `api/http`（6）、`api/validate`（11）、`constants/channels`（9）、`NewsCard`（7）
- **CI**：见 `../.github/workflows/ci.yml`（前端 类型/Lint/格式/单测/构建/Storybook + 后端 导入自检/单测/契约检查）。
- **状态管理**：Pinia（`src/stores`）。收藏、浏览历史、登录态持久化到 `localStorage`，刷新不丢。
- **路由**：vue-router 4（history 模式），全部页面懒加载。
- **演示约定**：登录演示密码为 `123456`（非此值触发 S16 错误红态）；配图/头像统一用渐变占位，未引外部图床。

---

## 深色模式（三态）

| 模式 | 行为 |
| --- | --- |
| `system`（默认） | 跟随系统 `prefers-color-scheme`，并**实时响应**系统切换 |
| `light` | 强制浅色，覆盖系统偏好 |
| `dark` | 强制深色，覆盖系统偏好 |

- **单一来源**：`src/constants/theme.ts`（`THEME_MODES` / `THEME_OPTIONS` / `resolveTheme`）。
- **生效方式**：由 JS 把**解析后的实际主题**写成 `<html data-theme="light|dark">`，并同步
  `color-scheme`（让原生滚动条与表单控件也跟随）。CSS 只认 `:root[data-theme='dark']`——
  因此"跟随系统"与"手动指定"共用同一套深色令牌，**不必**在 CSS 里把媒体查询与属性
  选择器各写一遍（避免两处不一致）。
- **避免首屏闪白**：`main.ts` 在 `mount()` **之前**调用 `useTheme()`。
- **持久化**：主题偏好存于 `user` store（localStorage `mingri-toutiao:user` 的 `theme` 字段），
  非法值经 `toThemeMode()` 归一化为 `system`。
- **入口**：`Profile.vue` 的「外观」分段控件（radiogroup，含 `aria-checked`）。
- **新增组件注意**：颜色一律用 `var(--color-*)`，勿硬编码——`global.css` 的 body 底色、
  `App.vue` 外壳与正文色均已改为令牌。

## 设计令牌 `src/styles/tokens.css`

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--color-primary` | `#2D6CFF` | 主蓝，主按钮 / 激活态 / 链接 |
| `--color-page-bg` | `#F4F5F6` | 页面底色 |
| `--color-card-bg` | `#FFFFFF` | 卡片 / 导航底 |
| `--color-text-primary` | `#222222` | 主文字 |
| `--color-text-secondary` | `#9A9FA8` | 次文字 / 元信息 |
| `--color-divider` | `#F0F1F2` | 分割线 |
| `--color-border` | `#E5E6E8` | 描边 |
| `--color-error` | `#E5484D` | 语义错误红（仅表单报错 / 错误提示） |
| `--color-error-bg` | `#FCEDED` | 错误浅底 |
| `--radius-card / --radius-button / --radius-tabbar / --radius-avatar` | `6 / 24 / 36 / 50%` | 圆角 |
| `--font-cn / --font-num` | `Noto Sans SC / Inter` | 中文字体 / 数字字体 |

> 语义色仅用于报错场景，与品牌主蓝无耦合；错误红已正式纳入设计系统色彩令牌区。

---

## 组件总览 `src/components/`

| 组件 | 一句话说明 | 对应设计 |
| --- | --- | --- |
| `Button` | 主 / 次 / 文字三态按钮，支持禁用、块级、两档高度 | Button（设计系统页） |
| `AppNav` | 顶部导航，四变体：品牌 / 标题 / 搜索 / 详情-作者 | AppNav |
| `ChannelTabs` | 频道横向滚动标签栏，激活蓝 + 下划线 | ChannelTabs |
| `TabBar` | 底部 4 Tab 胶囊导航，内联 SVG 图标，支持 `badges` 未读徽标 | 各主屏底部 |
| `NewsCard` | 资讯卡片，四变体：三图 / 左文右图 / 纯文字 / 视频 | NewsCard / S3 / S10 |
| `EmptyState` | 复用空态 / 错误态插画（S11/S12/S13/S14 语义） | S11–S14 |
| `BannerCarousel` | 首页焦点图轮播（基于 `useCarousel`，自动播放 + 指示点） | 首页扩展 |
| `ErrorBoundary` | 全局错误边界，捕获渲染异常避免白屏 | 工程基建 |
| `PhoneFrame` | 390×844 手机外壳，承载屏幕 | 画布外壳 |

---

### Button

三态按钮，全类型化 Props 与 `click` 事件。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `variant` | `'primary' \| 'secondary' \| 'text'` | `'primary'` | 主按钮(蓝胶囊) / 次按钮(白底蓝描边) / 文字按钮(蓝链接) |
| `disabled` | `boolean` | `false` | 禁用态（灰底） |
| `block` | `boolean` | `false` | 占满父容器宽度 |
| `size` | `'md' \| 'lg'` | `'md'` | `md`=44px / `lg`=48px |
| `label` | `string` | `''` | 文案；也可用默认插槽覆盖 |

**Events**

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `click` | `MouseEvent` | 点击（禁用时不触发） |

**用法**

```vue
<Button variant="primary" block label="登录 / 注册" />
<Button variant="secondary" :disabled="!valid" label="收藏" />
<Button variant="text" label="不想注册？游客浏览" />
```

---

### AppNav

顶部导航，根据 `variant` 渲染四种形态。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `variant` | `'title' \| 'brand' \| 'search' \| 'detail'` | `'title'` | 标题 / 品牌(Logo+名) / 搜索框 / 详情-作者关注 |
| `title` | `string` | `''` | `title` 变体的标题文案 |
| `showBack` | `boolean` | `false` | 显示返回按钮 |
| `showSearch` | `boolean` | `false` | 显示搜索图标 |
| `showMessage` | `boolean` | `false` | 显示消息图标 |
| `showMore` | `boolean` | `false` | 显示更多图标 |
| `author` | `Author` | — | `detail` 变体必填：名称 / 头像 / 时间 / 是否已关注 |

**Events**：`back` / `search` / `message` / `more` / `follow`

**用法**

```vue
<AppNav variant="brand" show-search show-message
        @search="router.push('/search')" @message="router.push('/messages')" />
<AppNav variant="detail" :author="{ name: item.source, time: item.time }"
        show-back show-more @back="router.back()" />
```

---

### ChannelTabs

横向频道标签栏，激活态蓝字 + 蓝色下划线；内容超出可滚动。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `tabs` | `ChannelTab[]` | — | `{ key, label, active? }` 列表 |
| `active` | `string` | `''` | 当前激活 key（v-model:active） |
| `scrollable` | `boolean` | `true` | 是否允许横向滚动 |

**Events**：`update:active` `(key: string)`

**用法**

```vue
<ChannelTabs :tabs="tabs" :active="active" @update:active="onTab" />
```

---

### TabBar

底部 4 Tab 胶囊导航，图标为内联 SVG，激活态主蓝。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `items` | `TabItem[]` | — | `{ key, label, icon }`，`icon ∈ home/video/message/mine` |
| `active` | `string` | `'home'` | 当前激活 key（v-model:active） |
| `fixed` | `boolean` | `true` | 是否吸底 |

**Events**：`update:active` `(key: string)`

**用法**

```vue
<TabBar :items="tabs" :active="activeTab" @update:active="onTab" />
```

---

### NewsCard

资讯卡片，按 `item.variant` 渲染四变体；元信息统一为「来源 · 评论/播放 · 时间」。

**Props**

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `item` | `NewsItem` | 资讯数据对象 |

**NewsItem 关键字段**

| 字段 | 说明 |
| --- | --- |
| `variant` | `'triple' \| 'left-right' \| 'text' \| 'video'` |
| `title` / `summary` | 标题 / 摘要（左文右图、纯文字、视频展示） |
| `source` / `commentCount` / `time` | 来源 / 评论数 / 时间 |
| `images` | 三图变体取前 3 张 |
| `cover` | 单图 / 视频封面 |
| `playCount` | 视频变体替代评论位 |
| `isTop` | 纯文字变体左上角蓝标「置顶」 |

**Events**：`click` `(id: string)` / `image` `(id: string, index: number)`

**用法**

```vue
<NewsCard v-for="item in list" :key="item.id" :item="item" @click="open" />
```

---

### EmptyState

复用空态 / 错误态插画组件（承载 S11 消息 / S12 收藏 / S13 历史 / S14 网络 / 搜索 语义）。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `title` | `string` | — | 空态标题 |
| `description` | `string` | `''` | 辅助说明 |
| `icon` | `'favorite' \| 'history' \| 'message' \| 'search' \| 'network' \| 'collect'` | `'collect'` | 内置线性插图 |

**插槽**：`action`（操作按钮，如「去逛逛」「清空」）

**用法**

```vue
<EmptyState icon="favorite" title="还没有收藏的内容"
            description="在资讯详情页点击收藏，喜欢的内容会保存在这里">
  <Button variant="primary" label="去发现" @click="router.push('/')" />
</EmptyState>
```

---

### BannerCarousel

首页焦点图轮播，**完全基于 `useCarousel` 组合式函数**构建（自动播放 + 指示点 + 手势滑动 + 克隆帧回环），是组合式函数复用的示例。

**Props**

| 名称 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `items` | `NewsItem[]` | — | 轮播帧数据（取 `id` 作为帧标识） |
| `interval` | `number` | `4000` | 自动播放间隔(ms)，`0` 关闭自动播放 |

**Events**：`select` `(id: string)`（点击某帧；拖拽后紧跟的点击不触发）

**用法**

```vue
<BannerCarousel :items="news.list.slice(0, 3)" @select="open" />
```

> 自动播放会在悬停 / 拖拽时暂停、离开后恢复；`useCarousel` 被 rollup 抽为共享 chunk 同时供 Gallery 与 BannerCarousel 使用。

---

## 视图与屏幕映射 `src/views/`

| 路由 | 视图 | 对应设计稿 |
| --- | --- | --- |
| `/` | `HomeFeed` | S3 首页信息流（**无限滚动**） |
| `/video` | `Videos` | 视频频道（首页内频道落地为整页） |
| `/messages` | `Messages` | S11 消息（接后端 + TabBar 未读徽标） |
| `/profile` | `Profile` | S9 个人中心 |
| `/news/:id` | `NewsDetail` | S5 新闻详情（含 S15 图集入口） |
| `/news/:id/comments` | `Comments` | **S6 评论** |
| `/news/:id/gallery` | `Gallery` | **S15 新闻详情图集** |
| `/login` | `Login` | S1 登录（含 S16 失败态） |
| `/search` | `Search` | S4 搜索 |
| `/search/results` | `SearchResults` | S10 搜索结果（**服务端** `/api/news/search`） |
| `/favorites` | `Favorites` | S7 收藏（S12 空态） |
| `/history` | `History` | S8 浏览历史（S13 空态） |
| `/admin/sources` | `AdminSources` | **RSS 源管理**（仅 `isAdmin` 账号可见入口；增删改/启停/重置 + 抓取健康状态 + 搜索筛选 + 异常置顶 + 批量操作 + **「遵守 robots.txt」逐源开关**） |
| `/admin/users` | `AdminUsers` | **管理员管理**（P1-14）：应用内增删「指定账号」——双源说明（env break-glass + 库白名单）、添加并支持预授权、注册状态标记、移除确认与末位保护提示、**env 管理员为 0 时提示应急通道缺失** |
| `/admin/audit` | `AdminAudit` | **运营审计**（P1-15）：时间线展示谁改了什么——中文动作标签、对象、操作者、字段级变更（旧→新）、游标分页「加载更多」；带 `?target=` 进入时只看该对象 |

> 底部 `TabBar` 仅对带 `meta.tab` 的路由（首页/视频/消息/我的）显示；详情、评论、图集、登录、搜索类整页不显示。

---

## 状态管理 `src/stores/`

- **`news.ts`（useNewsStore）**：`list` / `loading` / `error` / `detailCache` / `searching` / `searchError` / `page` / `total` / `hasMore` / `loadingMore` / `currentChannel` + `fetchNews(channel)` / `loadMore()`（**无限滚动追加，按 id 去重、并发幂等**）/ `resetPaging()` / `getById(id)` / `loadDetail(id)`（深链兜底）/ `search(keyword,page,pageSize)`（**服务端搜索**）/ `searchLocal(keyword)`（离线兜底）。数据来自 `api/news`，未配后端时自动 mock。
- **`messages.ts`（useMessagesStore）**：`list` / `loading` / `error` / `unreadCount` + `fetch()` / `markRead(id)` / `markAllRead()` / `refreshUnread()`。已读状态**本地持久化 + 回写服务端**，未登录时仅本地生效。
- **`user.ts`（useUserStore）**：`token` / `isLoggedIn` / `userId` / `isAdmin` / `favorites` / `history` / `theme` / `syncing` / `syncError` + `login(phone,password)` / `logout()` / `syncMarks()` / `toggleFavorite` / `isFavorite` / `addHistory` / `clearHistory` / `setTheme`。
  - **`isAdmin` 由登录响应下发并落盘**（`prefs.isAdmin && isLoggedIn`，刷新不丢入口）；登出/401 归位。
    后端按 `ADMIN_PHONES` 白名单判定，**只下发布尔值**；白名单变动要等下次登录/续期才更新，
    届时入口可能短暂失准，但接口自身仍会 403，不会越权。mock 模式恒为 `false`（入口隐藏）。
  - **收藏与历史为「离线优先 + 云端同步」，且本地按账号命名空间隔离**：localStorage 是本地缓存（未登录也能用），存储键为 `mingri-toutiao:marks:<anonymous|u<id>>`。
  - 未登录时的数据落在**匿名作用域**，登出不会丢失；各账号本地缓存互不可见（**不串号**）。
  - 已登录时在**登录后**与**应用启动**各做一次 `syncMarks()`（`PUT /me/*` **并集合并**，只增不删）；登录时把「匿名期数据 + 该账号缓存」采纳进账号，匿名作用域随即清空。
  - 登出仅切回匿名作用域，**账号数据保留在本地**，重新登录立即恢复；写操作本地乐观生效 + 后台推送，失败不回滚（下次合并收敛）。
  - token 由 `api/http` 单独管理；401 事件会同步清理登录态并切回匿名作用域。
- **`comments.ts`（useCommentsStore）**：按文章 id 管理评论，`byArticle`（**只存主评论**）+ `ensure` / `list` / `count` / `countOf` / `findComment` / `add` / `toggleLike` / `reply` / `loadReplies` / `loadMoreReplies` / `remove`，**持久化到 `localStorage`**。结构对齐真实接口：`list` → `GET /news/:id/comments`、`add` → `POST`、`toggleLike` → `PATCH`、`remove` → `DELETE`（仅自己的评论，服务端二次校验归属）。
  - **两级评论（P1-12）**：每条主评论的 `replies` 字段**既是列表接口的「最早 2 条预览」，也可被 `loadReplies` 替换为完整列表**（单一数据源，不维护两份拷贝）；`repliesMeta[rootId]` 记录完整列表的游标状态，不存在即「尚未展开」。
  - `reply()` 乐观插入已展开的回复列表并递增 root 的 `replyCount`，服务端返回后用真实条目替换乐观项；失败则回滚两者。未展开线程时只递增计数，展开后自然从服务端取到。
  - `remove` 为**乐观更新 + 失败回滚**：先从本地移除，接口失败则插回原位置，避免出现"删了又回来"的错觉。删主评论连同其已加载回复一并移除；删回复从所属主评论的 `replies` 中移除并递减计数。首访用内置 SEED 示例评论（n4）初始化。

---

## API 层 `src/api/`

数据访问统一走 `api/`，**未配置后端时自动走内置 mock**（离线可运行）；配置 `VITE_API_BASE` 即切换为真实后端。

- **开关**（`.env`）：`VITE_API_BASE=https://api.example.com` 指向真实后端；`VITE_USE_MOCK=true` 可强制 mock。
- **`http.ts`**：统一 `fetch` 客户端——自动注入 `Authorization: Bearer <token>`、约定后端返回 `{ code, message?, data }`（`code` 0/200 为成功）、token 持久化（`getToken`/`setToken`）、`ApiError`。
- **`news.ts`**：`fetchNewsPage` / `fetchNews` / `fetchNewsById` / `searchNews` / `fetchRelated`（`GET /news/search`、`GET /news/:id/related`）→ `NewsItem`，含后端字段归一化（`normalizeNewsItem` 对外导出，供 `api/me` 复用）。
- **`comments.ts`**：`listCommentsPage` / `listComments` / `listRepliesPage` / `createComment`（`parentId` 非空时为回复）/ `setCommentLike` / `deleteComment` → `GET/POST/PATCH/DELETE /news/:id/comments` 与 `GET /news/:id/comments/:cid/replies`（回复列表，时间正序游标分页；root 已删返回空）。
- **`messages.ts`**：`fetchMessagesPage` / `fetchMessages` / `fetchUnreadCount` / `markMessageRead` / `markAllMessagesRead` → `GET /messages`、`GET /messages/unread-count`、`PATCH /messages/{id}`、`POST /messages/read-all`。
- **`me.ts`**：`fetchFavorites` / `addFavorite` / `removeFavorite` / `mergeFavorites` / `fetchHistory` / `recordHistory` / `clearHistoryRemote` / `mergeHistory` → `/me/favorites`、`/me/history`。所有写操作返回**完整集合** `{ ids, list }`（服务端即事实源）；导出 `MARKS_SYNC_SUPPORTED`，mock 模式下为 `false`，store 据此跳过同步请求。
- **`auth.ts`**：`login({phone,password})` → `POST /auth/login`（成功写 token）；`logout()` → `POST /auth/logout`（清 token）。登录/注册/续期响应携带 `user.isAdmin`（布尔），store 据此决定管理入口显隐。
- **`admin.ts`**：`fetchSources` / `createSource` / `updateSource` / `deleteSource` / `resetSources` → `/admin/sources` 五件套（**仅真实后端**，`ADMIN_SOURCES_SUPPORTED` 为门；mock 模式下不短路假数据——伪造一份可变的源列表会误导排障，入口由 `isAdmin` 隐藏即可）。行数据经 `toSource()` 归一化：丢弃缺 `key` 的脏行、`channel` 归一化、空 `lastError` 转 `null`、`respectRobots` 缺失时按更保守的一方兜底为 `true`。
  另含管理员白名单三接口：`fetchAdminUsers` / `addAdminUser` / `removeAdminUser` → `/admin/users`（P1-14，同样只有真实后端），
  列表额外返回 `envAdminCount`（环境变量来源管理员**数量**，不下发号码）用于在界面解释双源机制；
  行数据经 `toAdminUser()` 归一化，**形态非法的手机号直接丢弃**（删除按 phone 定位，脏行无法操作）。
  另含审计时间线 `fetchAuditPage({ limit, cursor, target })` → `/admin/audit`（P1-15，同样只有真实后端），
  游标由服务端下发（自增 id）、客户端不解析；行数据经 `toAudit()` 归一化。

> store 采用**离线优先**：本地先更新并持久化，真实模式下再后台与服务端同步（失败静默，不影响交互）。

---

## 后端联调

项目配套后端在 `../server-api/`（**FastAPI + SQLite + JWT**）。两种后端形态均可用：

**A. FastAPI 正式后端（推荐）**

```bash
# 终端 1：启动后端（自动建表 + 种子数据）
cd ../server-api
pip install -r requirements.txt
uvicorn main:app --reload --port 8000      # 文档 /docs
#数据源：PostgreSQL（见 ../server-api/.env 的 DATABASE_URL）

# 终端 2：前端指向它（.env.api 已配好 http://127.0.0.1:8000/api）
npm run dev:api
```

**B. 零依赖 Mock 后端**（无需 Python 环境）

```bash
npm run mock:api    # 终端 1，Node 原生 http，端口 3001
npm run dev:api     # 终端 2（把 .env.api 指向 3001 即切换）
```

**契约自检**（复刻本项目 `api/http.ts` + `api/news.ts` 逻辑，对后端跑一遍字段/错误码/鉴权）：

```bash
node scripts/contract-check.mjs    # 需后端已启动 + DEMO_PASSWORD（47 项，含 18 项二级评论契约）
# 追加校验管理端接口（当前账号须具备管理能力，共 72 项：源管理 9 + 白名单 9 + 审计 7）
DEMO_PASSWORD=123456 CHECK_ADMIN=1 node scripts/contract-check.mjs
```

> mock 后端同样支持全套校验（演示账号：任意 11 位手机号 + `123456`，`13800138000` 为管理员；
> 二级评论的回复创建 / 拍平 / 正序分页 / 预览 / 删除语境在 mock 上与真实后端行为一致；
> 管理员白名单在 mock 上以进程内白名单并存，`isAdminPhone` 与后端同为「env ∪ 库」双源判定）。

接口清单与错误码见 `../server-api/README.md`。响应统一 `{ code, message, data }`，`code: 0` 成功。

> 切换真实后端：改 `.env.api` 的 `VITE_API_BASE`（或用 `.env.local` 覆盖），前端业务代码零改动。`npm run dev`（无 api 模式）仍走内置 mock，保证离线可跑。

---

## 组合式函数 `src/composables/`

- **`useCarousel.ts`** — 通用轮播：**首尾克隆帧无限回环 + Pointer 手势滑动 + transitionend跟手收尾**（并带兜底定时器，覆盖 reduced-motion）。图集页（`Gallery.vue`）与首页 Banner 均基于它实现。
  - **入参**：`{ frames: Ref<readonly string[]> | (() => readonly string[]), threshold?, transitionMs?, infinite? }`
  - **返回**：`slides`（渲染轨道，含克隆帧）/ `N` / `index` / `realPos`（真实帧位）/ `offset` / `dragging` / `noTransition` / `moved` / `trackStyle`（绑到轨道的位移样式）/ `goTo(i)` / `prev()` / `next()` / `onPointerDown·Move·Up·Cancel` / `onTrackClick` / `onTransitionEnd`

- **`useNewsDetail.ts`** — 深链安全的详情加载：直接访问或刷新 `/news/:id` 时内存无此条，自动调 `GET /news/:id` 兜底，并区分「加载中」与「确实不存在」（避免误显空态）。`NewsDetail` / `Comments` / `Gallery` 三处共用。
  - **返回**：`item` / `resolving`（加载中）/ `failed` / `reload()`

- **`useTheme.ts`** — 主题生效：把解析后的主题写到 `<html data-theme>` 并同步 `color-scheme`；
  监听 store 变化与（处于 system 时）系统偏好变化。**返回停止函数**（测试/卸载用）。
  - 由 `main.ts` 在挂载前调用，避免首屏闪白。

- **`useInfiniteList.ts`** — 无限滚动：观察哨兵元素进入视口即触发加载，含并发冷却、卸载自动断开观察（防内存泄漏）。
  - **入参**：`{ onLoadMore, hasMore, loading?, root?, rootMargin? }`；**返回**：`sentinel`（哨兵 ref）/ `tryLoad` / `disconnect` / `observe`

- **`constants/sort.ts`** — 排序方式**单一来源**（与后端 `app/ranking.py` 的 `SORT_MODES`、`mock-server` 三方一致）：`SORT_MODES` 派生 `SortMode` 类型、`DEFAULT_SORT`、`PERSONALIZED_SORT`，提供 `isSortMode()` / `toSort()` 守卫。取值漂移会被契约检查（422 校验）拦下。
- **`constants/channels.ts`** — 资讯频道**单一来源**：`CHANNELS`（key + 中文标签）派生出 `Channel` 类型、`CHANNEL_KEYS`、`DEFAULT_CHANNEL`，并提供 `channelLabel()` / `isChannel()` / `toChannel()` 守卫。
  新增频道**只需改这一个文件**；不可信输入（路由 query、后端返回）一律经 `toChannel()` 归一化。

---

## 数据层

- `src/data/mockNews.ts`：6 条真实中文资讯样例，四变体齐全；`images` / `cover` 为空串，由组件用渐变占位呈现（真实接口填充远程 URL 即生效）。
- `src/types.ts`：`NewsItem` / `Channel` / `ChannelTab` / `TabItem` / `Author` 等类型定义。
- 接口约定见项目根 `明日头条-组件与接口速查表.md`。

---

## 演示文档（Storybook）

组件级交互文档已工程化为 **Storybook 10**（`@storybook/vue3-vite`）。

```bash
npm run storybook          # 本地交互预览 http://localhost:6006
npm run build-storybook# 产出静态站 storybook-static/
```

- 配置：`.storybook/main.ts`（stories glob + 框架）、`.storybook/preview.ts`（引入 `tokens.css` / `global.css`，保证与真实应用一致）。
- Stories：`src/components/*.stories.ts`（CSF3），覆盖 **Button / AppNav / ChannelTabs / TabBar / NewsCard / EmptyState / BannerCarousel**，含四变体与禁用/大号等状态。
- 另有 `src/views/Showcase.vue`：随主应用运行的实时陈列（桌面端并排展示令牌色板与组件），开发态可与主应用一同查看。

---

## 开发进度

> 截至 2026-10-11。`npm run build`（`vue-tsc` 类型检查 + 构建）零报错通过。

### 已完成

- [x] 设计令牌与组件规范对齐（含语义错误红 `#E5484D` / `#FCEDED`）
- [x] Vue 3 骨架：6 个基础组件 + EmptyState / PhoneFrame
- [x] 多页应用：vue-router + Pinia，S1–S16 全屏路由化（12 条懒加载路由）
- [x] 收藏 / 浏览历史 / 登录态 localStorage 持久化
- [x] S6 评论页 + 评论 store 持久化 + 详情页实时评论数
- [x] S15 图集页：手势滑动 + peek + 克隆帧无限回环 + transitionend 收尾
- [x] `useCarousel` 通用轮播组合式函数（被 Gallery / BannerCarousel 复用）
- [x] 首页 BannerCarousel 焦点图（自动播放 + 指示点）
- [x] 组件级 README 文档
- [x] **工程基建**：ESLint + Prettier + `npm run verify` + GitHub Actions CI
- [x] 全局错误边界（`ErrorBoundary` + `app.config.errorHandler`），消除白屏
- [x] **无限滚动**：`useInfiniteList` + store `loadMore`（去重/并发幂等/末页判定）+ 骨架屏与底部状态
- [x] **单元测试**：Vitest 42 项（前端）+ pytest 54 项（后端），已接入 CI
- [x] API 层：`api/`（news / comments / auth + http 客户端，token 注入、`{code,data}` 解包），未配后端自动走 mock
- [x] store 全面接 API（news/comments 离线优先 + 后台同步；user 增 token 与 login/logout）
- [x] 真实图片渲染（`utils/image.ts` 真实 URL 优先 / 渐变兜底）+ 视频变体 `<video>` 播放
- [x] Storybook 工程化（`@storybook/vue3-vite`，7 个组件 stories + 静态构建）
- [x] **FastAPI + SQLite 后端**（`../server-api/`）：JWT 鉴权、PBKDF2 密码、资讯/评论/认证接口、SQLite 持久化
- [x] 前后端契约自动化校验（`scripts/contract-check.mjs`，**47 项全通过**，含 `/me` 收藏与历史、排序三模式与非法 `sort` 422、热搜词与搜索即统计、**二级评论 18 项**）
- [x] **数据源迁移到 PostgreSQL 18.6**（JSONB 生效，SQLite 全量数据已迁移并保留回退）
- [x] **收藏 / 浏览历史云端同步**：`api/me.ts` + `stores/user.ts`（离线优先、登录后**并集合并**、
  写操作乐观更新 + 后台推送、登出清空本地标记防串号）；服务端 `user_favorites` / `user_history`
  （迁移 `0010_user_marks`），新设备登录即可看到全部收藏与历史
- [x] **RSS 源管理前端**（`/admin/sources`）：登录响应下发 `isAdmin` → 个人中心按权限显隐入口；
  `api/admin.ts` 五件套 + 汇总/表单/启停回滚/删除确认/健康状态；mock 后端同步支持
- [x] **源管理增强**（P1-13）：搜索（名称/地址/标识）+ 频道/状态筛选、健康四计数与异常置顶、
  批量启停/删除（乐观更新 + 部分失败回滚）
- [x] **二级评论**（P1-12）：`Comments.vue` 展平行（主评论 + 可见回复，末条带「更多」）、
  回复框跟随目标、「回复 @谁」前缀；store 乐观回复 + 游标分页懒加载；mock 后端同步支持
- [x] **管理员白名单应用内管理**（P1-14）：新增 `/admin/users` 页面（添加/移除指定账号、
  支持**预授权**（手机号无需先注册）、注册状态标记、移除二次确认、**末位保护**由服务端 409
  兜住）；后端 `admin_users` 表（迁移 0015）与 `ADMIN_PHONES` 环境变量**双源并集**判定，
  env 保留为 break-glass；入口在个人中心与源管理页两处可达；mock 后端同步支持
- [x] **运营审计日志**（P1-15）：新增 `/admin/audit` 时间线（谁改了什么，含字段级变更
  旧→新、游标分页「加载更多」）；源管理页每行「历史」直达该源、管理员页审计入口；
  后端 `admin_audit_logs` 表（迁移 0016）与业务**同事务写入**；mock 后端同步支持

### 待完成 / 依赖后端

- [ ] 生产化：PG 强密码与备份策略、多实例限流换 Redis、内容审核与合规
- [ ] 视频真实资源 URL（渲染链路已通，待填真实地址）

> 完整进度与取舍见项目根目录 `明日头条-组件与接口速查表.md` 第 5 节。
