# E8 PWA / 离线阅读 · 设计方案（基建已落地）

> 状态：**P1-78 基建已落地，剩 P1-79 内容副本**。6 个决策点按推荐值确认（取值记录在 §三）。
> 实现期的两处坑（`replace` 只替换第一处、`navigator.onLine` 不随事件更新）记在
> `治理/落地记录/P1-78_PWA基础设施.md`。
> 来源：backlog E8「PWA / 离线阅读」。本轮只做现状核实与方案，未改任何代码。

## 一、现状核实（均已读代码确认）

**离线能力为零，但"离线友好"的地基已经铺了大半。**

| 项 | 现状 | 证据 |
| --- | --- | --- |
| PWA 基础设施 | **完全不存在**：无 `vite-plugin-pwa`、无 manifest、无 Service Worker、无 `public/` 目录 | `package.json` 无相关依赖；`index.html` 无 manifest 链接；`ls public/` 为空 |
| 构建期自定义 Vite 插件 | **已有先例**：CSP 通过自写插件注入 `index.html` | `vite.config.ts::cspPlugin` |
| CSP 是否阻挡 SW | **不挡**：`script-src 'self'` → SW 脚本同源合规；`manifest-src` 回落到 `default-src 'self'` | `src/security/csp.ts` |
| 数据本地化 | 收藏/历史（`mingri-toutiao:marks:<scope>`）、搜索历史、曝光（`mingri-toutiao:exposures:<scope>`）**都已在 localStorage**，且按账号命名空间隔离 | `stores/user.ts::MARKS_PREFIX/SEARCH_PREFIX/EXPOSURES_PREFIX` |
| 详情正文 | **只在内存**：`detailCache` 是 `ref`，刷新即失；`content` 仅详情接口下发 | `stores/news.ts` |
| 网络状态感知 | **零**：没有任何 `navigator.onLine` / online 事件使用 | 全仓 grep 无命中 |
| 内置 mock | `USE_MOCK` 走种子数据，与"网络断了但应用已安装"是**两件事**——离线阅读要的是"真实数据的可读副本" | `api/config.ts` |

**核心判断**：要做的是**离线可读**（已安装 + 断网时仍能读收藏与最近看过的内容），而不是**离线可写**。写操作（评论、点赞、举报）在断网下的正确行为是**拒绝并说明**，而不是排队重试——本项目的标记类写操作都有服务端仲裁（收藏并集、举报幂等），本地排队会造成"以为发了其实没发"。

## 二、方案范围

1. **可安装**：manifest + 图标 + SW 注册（含更新提示）；
2. **离线可读**：应用外壳（App Shell）+ 收藏内容正文 + 最近看过的详情可离线打开；
3. **图片离线**：CacheStorage 缓存配图（有界），否则"能打开但全是裂图"仍等于不可读；
4. **网络状态可见**：断网时给一条持久提示，写操作明确拒绝而不是转圈。

## 三、6 个决策点（待确认）

### D1 离线范围：只缓存外壳，还是连内容一起？

| 方案 | 断网后能做什么 |
| --- | --- |
| A. 只缓存外壳 | 能打开应用、看到结构与静态资源，所有列表请求失败 |
| B. 外壳 + 收藏详情 + 最近 N 条详情（推荐） | 能读「收藏」与最近看过的内容 |

**推荐 B**。理由：收藏是**用户主动表达"我要留着"**的行为（与 marks 同一语义），离线读它是用户预期最强的场景；而"最近 N 条"覆盖"刚看到一半断了"的续读。**不缓存整个信息流列表**——列表会过期，把一份 20 条 stale 列表当"离线内容"给用户看，比明确告知"当前离线"更糟。

### D2 内容副本存哪：localStorage 还是 CacheStorage？

**推荐分工存储**：

- **结构化正文**（`NewsItem`）→ localStorage `mingri-toutiao:reader:<scope>`，上限参考 P1-75 的做法（条数 30 + 单条 8KB 兜底）；
- **图片/静态资源**→ CacheStorage（SW 内），上限条目数 + 总字节双阈值，超出按最旧淘汰。

理由：localStorage 同步、读取路径与 marks 一致（已有 `utils/` 落盘范式可复用）；图片走 CacheStorage 才能被 SW 的 fetch 拦截命中——**把图片 URL 塞进 localStorage 没有任何用处**，它们不会被 SW 用来响应请求。

### D3 Service Worker：引入 `vite-plugin-pwa`（workbox）还是手写？

**推荐手写**（约 150 行 + 一个小 Vite 插件注入 precache 清单）。理由：

1. **零新依赖**：与项目既有偏好一致（自研组件、零 UI 框架、CSP 也是自写插件而非引库）；
2. workbox 引入 ~100KB 运行时与一整套不用的策略配置面，而本项目需要的策略只有三种（precache / network-first API / cache-first 图片）；
3. 已有的 `cspPlugin` 证明这个仓库**接受"自写 Vite 插件"这种模式**，precache 清单照抄它的写法即可（`generateBundle` 里读 `bundle` 拿 asset 名）。

代价：要自己写更新逻辑与缓存淘汰——但那正是本方案的其他决策点，引库也不会更少。

### D4 缓存策略：API 走哪种？

**推荐 network-first + 短 TTL 兜底**（列表类 GET：在线时先打网络，失败且命中缓存才回落，且回落时**在 UI 上标注"离线数据"**）。

明确**不**用 stale-while-revalidate：它会让用户先看到旧数据再静默更新——信息流场景下"先看旧的、忽然跳成新的"比"转个圈"体验更差，且掩盖了网络问题。

**POST/PUT/DELETE 一律绕过 SW**（不缓存、不重试）：写操作必须看到确定结果。

### D5 更新策略：自动激活还是提示刷新？

**推荐提示刷新**（`reg.updatefound` → 顶部出现"发现新版本 · 点击刷新"；点击后 `skipWaiting()`）。

明确**不**自动 `skipWaiting`：自动激活会在用户翻页/看详情的中途换掉整个执行环境，表现为"页面忽然刷新、位置丢了"。而一个新版本的 PWA 不值得打断一次阅读。

### D6 断网时的范围：哪些操作被明确拒绝？

**推荐**：读（列表/详情/收藏/历史/搜索历史）走缓存降级；写（评论、点赞、收藏同步、举报、关注、负反馈）**直接拒绝**并提示"当前离线，已保留在本地/稍后重试"中的哪一种要说清楚——**只有 marks（收藏）值得本地暂存**（它已有服务端并集合并语义，重发是无损的），其余一律拒绝。

理由：marks 的 `/me/favorites` 是并集合并入账，重发不会覆盖服务端；而评论/举报没有"重发"语义（幂等键在服务端），队列重试会重复计数。

## 四、接口与前端改动

| 类别 | 内容 |
| --- | --- |
| 资源 | `public/manifest.webmanifest`、`public/icons/{192,512,maskable}.png`（附 SVG 源） |
| SW | `public/sw.js`（precache + 三策略 + 有界淘汰），`src/pwa/register.ts`（注册 + 更新提示，**jsdom 下整体跳过**） |
| 构建 | `vite.config.ts` 增 `precachePlugin`：`generateBundle` 收集 asset 名写入 `sw.js` 里的占位符（照抄 `cspPlugin` 的 `transformIndexHtml` 模式） |
| 存储 | `src/utils/reader-cache.ts`（`mingri-toutiao:reader:<scope>`，条数 + 单字节双上限 + 账号隔离） |
| 状态 | `src/composables/useOnline.ts`（`navigator.onLine` + online/offline 事件） |
| 视图 | `App.vue` 顶部离线条（持久、可关闭）；收藏/历史页读本地副本兜底；写操作前检查在线态 |
| CSP | `src/security/csp.ts` 显式 `worker-src 'self'`（现在靠回落，写明更稳） |

## 五、刻意不做

- **不做离线写队列**（评论/点赞/举报排队重发）：幂等键在服务端，重发会重复计数；
- **不做整站预缓存**（只 precache 外壳与关键 asset）：其余走 runtime，避免首次访问就下几十 MB；
- **不做后台同步**（Background Sync API）：Safari 全家不支持，做了也是半边功能；
- **不把 mock 模式与离线混为一谈**：mock 是"没配后端"，离线是"有后端但没网"，两者降级路径不同。

## 六、拆批建议

1. **P1-78 基础设施**：manifest + 图标 + 手写 SW + precache 插件 + 注册与更新提示 + CSP 显式 `worker-src` + 离线提示条；
2. **P1-79 离线阅读**：reader-cache（收藏/最近详情本地副本）+ 收藏/历史页离线兜底 + 图片 CacheStorage 有界缓存 + 写操作在线校验。

## 七、验收标准

- Lighthouse PWA 四项（可安装、离线、manifest、SW）通过；构建产物含 `manifest` 与 `sw.js`，且 precache 清单里的文件名带 hash 而非 `/index.html` 写死；
- 断网后：仍能打开应用、仍能读**收藏**与最近看过的详情、配图可显示；列表明确显示"当前离线，展示的是缓存内容"；
- 断网时点评论/收藏/举报：给出明确失败提示，**不发请求**、不排队；
- 在线时一切行为与改动前一致（SW 不得把任何请求结果变旧——network-first）；
- `npm run verify` / `npm run build` / vitest 全绿；新增单测覆盖 reader-cache 上限与账号隔离、`useOnline`、注册守卫（jsdom 无 SW 时不炸）。

## 八、待你确认

1. **D1 范围**：外壳 + 收藏 + 最近 30 条详情（推荐）还是只做外壳？
2. **D2 存储**：localStorage 存正文 + CacheStorage 存图片（推荐）？
3. **D3 SW 实现**：手写零依赖（推荐）还是引 `vite-plugin-pwa`？
4. **D4 API 策略**：network-first + 失败回落并标注（推荐）还是 stale-while-revalidate？
5. **D5 更新**：提示刷新（推荐）还是自动激活？
6. **D6 写操作**：仅 marks 本地暂存、其余拒绝（推荐）还是一律拒绝？
