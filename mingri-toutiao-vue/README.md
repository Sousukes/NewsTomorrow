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
- **单元测试**：Vitest 共 **488** 项，覆盖：
  - `useCarousel`（19）：克隆帧回环 / transitionend 收尾 / 兜底定时器 / 手势阈值 / 数据变化重置
  - `BannerCarousel`（8）：**克隆帧无重复 key（回归）**、配图与标题按 id 对应、危险协议拦截、点击派发、卸载清定时器
  - `utils/image`（13）：CSS 注入防护（协议白名单 + 转义）
  - `stores`（22）：消息已读幂等、无限滚动 loadMore 去重 / 并发 / 末页 / 失败；
    **列表分桶隔离（A4）**——不同频道互不覆盖（首页与视频页不再串页）、同频道不同排序各自成桶、
    `getById` 可命中任意桶；**竞态守卫（A4）**——`loadMore` 在途时重新 fetch 旧响应被丢弃
    （不追加、不覆盖游标、不卡住 `loadingMore`）、先发的 fetch 后返回不覆盖后发结果、
    `resetPaging` 使在途 `loadMore` 失效、`loadMore` 续接沿用桶的排序
  - `stores/user`（19）：**收藏/历史云端同步 + 账号命名空间隔离**——未登录不发请求、
    登录响应缺 id 时不发同步、登录后并集合并并以服务端为准、合并结果写入 news 缓存、
    同步失败不阻断登录、匿名作用域、登录采纳匿名数据并清空匿名键、**登出切回匿名且账号数据保留**、
    **不同账号互不可见（防串号）**、同 tick「改标记→登出」不污染匿名作用域、旧格式自动迁移、
    收藏乐观更新与失败不回滚、并发推送序号守卫、历史去重/置顶/上限裁剪/重复浏览不重复请求、清空
  - `stores/user` 搜索历史（10）：去重置顶、空白词忽略、超限裁剪最旧、**独立键存储（不会被云端同步带上）**、删单条、清空、**换账号互不可见（防串号）**、登出切回匿名、损坏数据忽略、非字符串项过滤
  - `stores/user` 管理能力标志 `isAdmin`（6）：登录响应带 `isAdmin=true` 时置位、**缺省不授予特权**、
    **落盘后刷新页面运营入口不消失**、登出归位（不把上个账号的特权留给匿名态）、
    **未登录时忽略 prefs 里的 isAdmin**（token 失效不得留下管理入口）、普通账号仍为 false
  - `stores/user` 昵称下发与清理（P1-24，4）：登录响应下发昵称且 store 持有、
    缺昵称回落空串（界面显示默认名）、改名成功 `setName` 即更新并落盘（刷新不退回默认名）、
    **登出清空昵称**（不把上个账号的名字留给匿名态）
  - `api/admin`（81）：`ADMIN_SOURCES_SUPPORTED` 仅在真实后端为真（mock 模式下恒隐藏入口）、
    `ensureRealBackend` 守卫、行数据归一化（缺 `key` 的脏行丢弃、`lastError` 空串转 null、
    **`respectRobots` 缺失时按更保守的一方兜底为 `true`、显式 `false` 原样透出**）、
    五个源管理接口的路径/方法/错误透传；
    **失败分级字段**（P1-18：三项齐备时原样透出、整个字段缺失或取值不认识时一律回落
    `0` / `null`，**绝不猜一个失败性质**——误标 permanent 会让运营白跑一趟）；
    **健康历史**（P1-19：`fetchSources()` 返回 `{list, healthDays}`、`healthDays` 缺失回落 7、
    每行 `health` 归一化（`rate` 收敛 0–1、非对象/坏字段回落 `null`）、
    `fetchSourceHealth` 的 `days` 传参与三段数据归一化——**坏日期与无时间点的日桶丢弃**、
    `kind` 白名单外的取值回落 `null`、failures 上限 20）；
    **资讯审核与下架**（P1-20：`fetchAdminNews` 查询串按空白 trim 后拼接（纯空白 keyword
    不拼串）、信封缺失兜底、**缺 `id` 的脏行丢弃**、`hidden` 三件套缺失一律按可见兜底、
    理由空串转 `null`、`isTop` 非 `true` 一律 `false`、`hotScore`/`commentCount` 垃圾值归零、
    标题缺失回落 id、非法 `channel` 回落 `recommend`；`hideNews` 的 POST/请求体/**id 编码**
    与「响应无法解析」报错、`restoreNews` POST 无请求体、`updateNewsTop` 焊死
    `{isTop: true}` 驼峰体且 422 向上抛）；    **mock 模式下四个新闻函数一律拒绝**
    （与源管理同——不伪造可变的审核数据）
    **评论治理**（P1-22：`fetchAdminComments` 查询串按空白 trim 后拼接（纯空白 keyword
    不拼串）、查询参数顺序 `status → keyword → newsId → limit → cursor` 且 newsId 驼峰、
    信封缺失兜底、整行归一化（rootId/parentId/replyCount + hidden 三件套）、缺 `id` 丢弃、
    hidden 三件套缺失按可见兜底、**userName 缺失回落「匿名」+ userId 缺失回落 null**、
    likes/replyCount 垃圾值归零；`hideComment` 的 POST/id 编码/理由请求体/响应透出与
    「响应无法解析」报错、`restoreComment` POST 无请求体、422 向上抛）；
    **mock 模式下三个评论函数一律拒绝**（与源管理同——不伪造可变的治理数据）
    **敏感词与复核**（P1-23：`fetchWords` 查询串拼接（keyword/action/**布尔 enabled**/
    limit/offset）、**未知 action 一律降级 `mask`**（宁可先脱敏也不吞掉发言）、
    字段缺失兜底（hitCount→0、enabled→true）、list 非数组当空列表不抛错；
    `createWord`/`updateWord`/`deleteWord` 的方法与请求体（`updateWord` **只传要改的字段**）、
    `reloadWords` 返回生效条数且缺失回落 0、`fetchModerationHits` 的 field/word/cursor
    查询串与信封解析、**`commentId` 缺失落成 null**（"被拦截、没落库"的唯一信号）、
    `reviewComment` 的 POST 与 `pendingReview`/`reviewedAt` 兜底、40405 向上抛；
    **mock 模式下七个敏感词函数一律拒绝**）
  - `views/AdminUsers`（11）：管理员白名单渲染（含 `registered` 已注册/预授权与 `addedBy`）、
    env 计数说明、添加成功清空表单并刷新、**手机号格式前端拦截不发请求**、冲突 409 展示服务端文案、
    移除二次确认、**末位保护 409 保留行并报服务端语义**、加载失败与 403 转无权限态、
    **应急通道缺失提示**（env 计数为 0 时整张说明卡转警示态，已配置时不显示）
  - `views/AdminAudit`（11）：时间线渲染（中文动作 / 对象 / 操作者 / 时间）、
    **字段级变更渲染**（权重 9→3、enabled 翻成启用→停用而非 true→false）、
    **加载更多按游标追加且不重复**、带 `target` 进入时只查该对象并提供查看全部、
    加载失败与 403 转无权限态、**非管理员不请求**；
    **自动禁用条目**（P1-18：`system` 显示为「系统自动执行」而非一个假账号、
    快照自证原因——连续失败次数与最后错误都渲染、人工操作仍显示手机号、
    未映射的动作退化成原样显示而非空白）
  - `views/AdminSources`（38）：汇总计数、新增/编辑表单与前端校验、启停乐观更新+失败回滚、
    删除/重置二次确认、健康状态渲染、**非管理员不请求列表直接呈无权限态**（不泄露接口存在性）；
    **搜索与筛选**（名称/地址/标识模糊匹配 + 频道/状态 chips + 无结果清除入口）、
    **健康汇总**（总数/启用/异常/未抓取四计数，点数字即筛选；**异常源置顶**，组内保持权重降序）、
    **批量操作**（多选 + 全选只作用可见项 + 批量启停/删除，乐观更新 + 部分失败回滚汇总）、
    **robots 开关**（默认遵守时列表无标记、关闭的源留下「忽略 robots」可见标记、
    编辑表单取消勾选后 PATCH 带 `respectRobots=false`、新增表单默认勾选并透传 `true`）、
    **失败分级与熔断**（P1-18：连续 N 次 + 永久性标红、可重试用中性样式、
    **只失败一次且无性质时不出标签**（偶发抖动多显示一格就是噪音）、
    冷却期内显示恢复时间且过期即消失、**时间字段是垃圾串时静默跳过不渲染 NaN**）、
    **健康趋势**（P1-19：行内 chip 按 `<0.5` 红 / `<0.9` 琥珀分色、无尝试显示"—"、
    「趋势」就地展开且**一次只开一个**、失败只在行内报错**不掀页**、
    `healthDays` 从列表响应带出供趋势面板解释时间范围）
  - `views/AdminNews`（23）：基础渲染（无权限态 / 频道标签 / 评论数 / 发布时间 / 共 N 条 /
    置顶 chip / **已下架红 chip + 理由 + 下架时间** / 403 态 / 错误横幅 / 空态）；
    **筛选与搜索**（status chips 带参重请求 + 选中态标红、再点取消、Enter 与搜索按钮、
    清除筛选后重新拉取、无结果提示）；
    **下架与恢复**（**空理由前端拦截**「请填写下架理由（留痕是准入条件）」、超 300 字拦截、
    成功路径行更新 + toast + 弹层关闭、失败留层不关、**可见筛选下下架后行移除且 total 递减**、
    恢复回可见态、恢复失败留层）；
    **置顶与更多**（**乐观更新立即换文案** + PATCH 焊死 `{isTop: true}`、失败回滚、
    加载更多带 cursor 追加、无更多不出按钮、**「历史」按钮跳 `/admin/audit?target=<id>`**）
  - `views/AdminWords`（26）：基础渲染（词/动作 chip/分组/命中次数/添加人、无权限态、
    403 态、错误横幅、**空词库文案点明"没有词就不会拦截"**、**已停用词整行降透明度 +
    「已停用」chip 且按钮反过来是「启用」**）；**新增**（空词前端拦截不发请求、
    成功就地插入 + 清空输入 + toast 说明"立即生效"、后端 42201/40905 错误留在表单下方、
    动作与分组随表单提交）；**启停与删除**（`updateWord` 只传 `{enabled}`、就地更新行、
    **删除二次确认**（第一次点击只展开确认、可取消）、删除后行移除且 total 递减、
    重载索引提示生效条数）；**筛选**（动作 chips 带 action 重请求且再点取消、
    「仅看已启用」带 enabled=true、搜索回车带 keyword、
    **清除筛选必须重新拉取**——状态复位 ≠ 数据复位）；**命中日志**（行含命中词/
    已拦截或已脱敏/正文或昵称/片段/作者/时间、**未落库的显示「未落库」而不是评论 id**、
    字段筛选带 field 重请求、cursor 追加、无更多不出按钮、顶部说明归一化策略）
  - `views/AdminComments`（28）：基础渲染（无权限态「无权限管理评论」/ 《资讯》标签 /
    主评论与回复 chip / 正文 / 时间 / 赞与回复数 / 共 N 条 / **已隐藏行整行降透明度 +
    红 chip + 理由 + 隐藏于** + 恢复按钮 / 403 态 / 错误横幅 / 空态）；
    **筛选与搜索**（全部/可见/已隐藏三态 chips 带参重请求 + **已隐藏态标红**（它是处置态，
    不是普通分类）、Enter 与搜索按钮、清除筛选重新拉取、无结果提示、
    **点《资讯》标签带 newsId 重新请求 + 提示条 + 再点取消**）；
    **隐藏与恢复**（**空理由前端拦截**「请填写隐藏理由（留痕是准入条件）」、超 300 字拦截、
    成功行更新 + toast、失败留层不关、**可见筛选下隐藏后行移除且 total 递减**、
    恢复回可见态、恢复失败横幅）；
    **加载更多与历史**（cursor 追加、无更多不出按钮、
    **「历史」按钮跳 `/admin/audit?target=<id>`**）；
    **待复核**（P1-23：**黄 chip「待复核」+「通过复核」按钮**，且不套用已隐藏的降透明度
    （脱敏评论仍可见）、非待复核行没有复核入口、**「待复核」筛选带 status=pending 重请求
    且再点取消**、复核成功清标记 + toast、**待复核筛选下复核成功后行移除**（只复位状态
    不清数据会停在旧结果）、复核失败错误进横幅且行保持待复核)
  - `views/AdminNicknameWords`（32）：基础渲染（词/动作 chip/分组/**别名标签**/命中次数/
    添加人、无权限态、403 态、错误横幅、空词库文案、**已停用词整行降透明度 + chip
    且按钮反转为「启用」**）；**新增**（空词前端拦截、成功就地插入 + toast 说明"立即生效"、
    后端 **42204/40906** 错误留在表单下方、**别名随主词一起提交**）；
    **启停与删除**（删除二次确认、删除后行移除且 total 递减、重载索引提示生效条数）；
    **筛选**（动作 chips 带参重请求且再点取消、「仅看已启用」、搜索回车、**清除筛选重新拉取**）；
    **命中日志**（行含命中词/**实际命中模式**（主词或别名）/拦截或待复核/注册或改名来源/
    昵称原文/用户/时间、字段筛选带参重请求、cursor 追加、无更多不出按钮）
  - `views/AdminNicknameReviews`（15）：队列渲染（用户/手机/**当初提交的昵称**/
    **哪条别名拦的**/标记时间、共 N 条、无权限态、403 态、错误横幅、**空态文案点明
    "没有待复核昵称"**）；**搜索**（关键词按当前昵称/手机号带参重请求、清除重新拉取）；
    **通过复核**（成功行移除且 total 递减 + toast、**重复复核幂等**不报错、
    40408 错误进横幅、**只清标志不改昵称**——行里名字始终是用户当前的名字）；
    **加载更多**（offset 追加、无更多不出按钮）
  - `views/Profile`（12）：昵称展示（**待复核徽标**——名字是放行的但没人看过；
    缺省回落默认名「头条用户」）；**改名弹窗**（形状前端预检——空/纯空白/超 24/非法字符
    不给发请求、成功即刷新展示 + toast、**block 40006 提示不回显命中词**且弹层不关、
    **review 结果提示"将通过人工复核后展示"并给昵称加待复核徽标**、
    失败错误进弹层、未登录不进入口）
  - `views/SearchResults`（5，P1-26）：首屏渲染第一页且按钮带进度；**「加载更多」是追加
    而非整段替换**（点一次只能看到最后一页的旧行为已回归锁定）；追加按 id 去重；
    **`hasMore=false` 不出按钮**（终止条件改用服务端 `hasMore`，不再用
    `results.length >= total`——两者不是同一个量）；**换关键词回到第一页且不叠加旧结果**
  - `views/NewsDetail`（4，E1）：**按段落渲染真实正文**（后端以 `\n` 分隔下发）；
    无正文显示「暂无正文」且**不渲染任何占位文案**（修复前三段「据相关方面介绍……」
    是前端写死的假正文——界面假装有内容）；空串正文同样走空态（trim 后无段落）；
    标题/来源/评论数等既有信息不受正文改动影响
  - `components/AppNav`（3，P1-27）：**图标不出现任何硬编码色值**（`#222` / `#9a9fa8`
    在深色模式下与 `--color-card-bg` 几乎同色 = 图标消失）、描边与填充一律
    `currentColor`（颜色由容器 CSS 变量给）、品牌变体不渲染图标
  - `components/TabBar`（3，P1-27）：**图标描边 `currentColor` 且不随激活态内联变色**
    （原 `:stroke="active ? '#2d6cff' : '#9a9fa8'"` 在深色模式下与亮蓝文字不一致）、
    激活态只加 `is-active` 类（配色交给 CSS 变量）、未读徽标按 key 显示且 0 不渲染
  - `components/SourceHealthTrend`（17）：四指标渲染（成功率/成功轮均条数/抓取轮次/失败次数）、
    **空窗口不给图**、`rate` 为 null 显示"—"而非 0%、**格子按 `days` 铺满再按日期落点**、
    无数据日留浅底空格（**不伪装失败**）、全失败日柱高强制 6% 不塌成零像素、
    **条数下滑结论**（前半 vs 后半均条数、最近不足前期六成且样本 ≥4 天才提示；
    样本不足或小幅波动都不报警）、**规律抖动提示**（≥3 次失败且六成以上同小时）、
    失败分散时不下结论、失败列表带**永久性/可重试**标签、页脚说明保留期
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
- **CI**：见 `../.github/workflows/ci.yml`（前端 依赖审计/类型/Lint/格式/单测/构建/Storybook + 后端 导入自检/单测/契约检查）。
  **依赖漏洞审计（C8）**：前端 job 在 `npm ci` 后跑 `npm audit --audit-level=high`——
  本机用 npmmirror 的同学跑不了（镜像不实现审计端点，报 `NOT_IMPLEMENTED`），
  本地自查临时加 `--registry=https://registry.npmjs.org`。
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
| `/admin/sources` | `AdminSources` | **RSS 源管理**（仅 `isAdmin` 账号可见入口；增删改/启停/重置 + 抓取健康状态 + 搜索筛选 + 异常置顶 + 批量操作 + **「遵守 robots.txt」逐源开关**；P1-18 **失败分级标签**（连续 N 次 · 永久性/可重试）与**熔断冷却至 xx:xx**；P1-19 **健康 chip + 就地展开趋势面板**——按天序列/失败原文，及条数下滑与规律抖动两条结论） |
| `/admin/users` | `AdminUsers` | **管理员管理**（P1-14）：应用内增删「指定账号」——双源说明（env break-glass + 库白名单）、添加并支持预授权、注册状态标记、移除确认与末位保护提示、**env 管理员为 0 时提示应急通道缺失** |
| `/admin/audit` | `AdminAudit` | **运营审计**（P1-15）：时间线展示谁改了什么——中文动作标签、对象、操作者、字段级变更（旧→新）、游标分页「加载更多」；带 `?target=` 进入时只看该对象；P1-18 新增**自动禁用源**条目（`system` 显示为「系统自动执行」，并带连续失败次数与最后错误自证原因） |
| `/admin/news` | `AdminNews` | **资讯审核与下架**（P1-20）：全部/可见/已下架三态 chips + 关键词搜索（服务端筛选）、频道标签、置顶 chip、**已下架行红 chip + 理由 + 下架时间**；**下架弹窗理由必填**（空理由/超 300 字前端拦截，「留痕是准入条件」）、一键恢复、置顶开关（乐观更新 + 失败回滚）、游标「加载更多」、每行「历史」直达审计时间线 |
| `/admin/comments` | `AdminComments` | **评论治理**（P1-22）：全部/可见/已隐藏/**待复核**四态 chips + 关键词搜索（服务端筛选）、**已隐藏行降透明度 + 红 chip + 理由 + 隐藏于**、隐藏理由必填（前端拦截）、一键恢复、游标「加载更多」、每行「历史」直达审计；P1-23 新增**待复核黄 chip + 「通过复核」**（只清标记，不改可见性） |
| `/admin/words` | `AdminWords` | **敏感词库**（P1-23）：拦截/脱敏/白名单三类词的新增（动作 + 分组）与启停/删除（二次确认）、**「重载索引」**手动同步其他实例、按动作与启用筛选；下方**命中日志**（正文/昵称筛选、原文片段、未落库标注、游标「加载更多」）——脱敏不可逆，它判断误杀的唯一证据 |
| `/admin/nickname-words` | `AdminNicknameWords` | **昵称敏感词库**（P1-24）：拦截/待复核/白名单三类词的新增（动作 + 分组 + **拼音/谐音别名**）与启停/删除（二次确认）、「重载索引」；下方**昵称命中日志**（注册/改名来源筛选、**实际命中模式**（主词或哪条别名）、提交的昵称原文、游标「加载更多」） |
| `/admin/nickname-reviews` | `AdminNicknameReviews` | **昵称待复核队列**（P1-24）：命中 `review` 级词被放行但置待复核标志的用户列表——展示**当初提交的昵称**与**哪条别名拦的**（队列回答「哪个提交需要人看」，不是「他现在叫什么」）；「通过复核」只清标志、**不改昵称本身**；关键词按当前昵称/手机号搜索 |

> 底部 `TabBar` 仅对带 `meta.tab` 的路由（首页/视频/消息/我的）显示；详情、评论、图集、登录、搜索类整页不显示。

---

## 状态管理 `src/stores/`

- **`news.ts`（useNewsStore）**：列表状态按 **(频道 × 排序)** **分桶**（`NewsBucket`，7 频道 × 3 排序 = 21 个桶，初始化预建）。
  - 读取：`bucketOf(channel, sort)`（含 `list/loading/error/page/total/hasMore/loadingMore/cursor/generation`）/ `listOf(channel, sort)`。
  - 写入：`fetchNews(channel, sort)` / `loadMore(channel, sort)`（**无限滚动追加，按 id 去重、并发幂等、代次守卫**）/ `resetPaging(channel, sort)`——**频道与排序必传**，没有「隐式当前频道」。
  - 其他：`getById(id)`（遍历全部桶 + 详情缓存 + mock）/ `cacheMany(items)` / `loadDetail(id)`（深链兜底）/ `search(keyword,page,pageSize)`（**服务端搜索**）/ `searchLocal(keyword)`（离线兜底）/ `detailCache` / `searching` / `searchError`。
  - **为什么分桶（A4）**：此前单一 `list` + `currentChannel` 被首页与视频页共用，视频页请求会覆盖首页列表（KeepAlive 下切回首页看到视频内容）；且无竞态守卫，`loadMore` 在途时切频道会把旧频道条目追加进新列表。现在每个视图读自己的桶，异步响应带**代次校验**，过期响应整体丢弃。
- **`messages.ts`（useMessagesStore）**：`list` / `loading` / `error` / `unreadCount` + `fetch()` / `markRead(id)` / `markAllRead()` / `refreshUnread()`。已读状态**本地持久化 + 回写服务端**，未登录时仅本地生效——**无登录态时不发起这两个写请求**（后端标记已读要求登录，401 会触发全局未授权跳转，不该因游客点开一条消息就把他踢到登录页）。
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
- **`http.ts`**：统一 `fetch` 客户端——自动注入 `Authorization: Bearer <token>`、约定后端返回 `{ code, message?, data }`（`code` 0/200 为成功）、token 持久化、`ApiError`。
  - **令牌双轨（C1）**：`token`（access，30 分钟）/ `refresh-token`（refresh，7 天）/ `token-exp`（到期时刻）三键由 `setSession()` 同写、`clearSession()` 同清——只清 access 会留下一张仍能换发令牌的长令牌。
  - **自动续期**：access 进入到期前 60 秒窗口即先换发再发请求（并发**单飞**，只打一次 `/auth/refresh`）；撞上 401 时先用 refresh 续期并**重试一次**，仍失败才清登录态并广播 `UNAUTHORIZED_EVENT`。限制「一次请求最多续一次」，否则「续期成功但服务端仍 401」会死循环。mock 模式跳过续期。
- **`news.ts`**：`fetchNewsPage` / `fetchNews` / `fetchNewsById` / `searchNews` / `fetchRelated`（`GET /news/search`、`GET /news/:id/related`）→ `NewsItem`，含后端字段归一化（`normalizeNewsItem` 对外导出，供 `api/me` 复用）。**`content`（正文）只由 `GET /news/:id` 下发**（E1）：列表一页 20 条，每条几 KB 是纯浪费；归一化对该字段是可选字符串，详情页按 `\n` 切段渲染，缺省显示「暂无正文」。
- **`comments.ts`**：`listCommentsPage` / `listComments` / `listRepliesPage` / `createComment`（`parentId` 非空时为回复）/ `setCommentLike` / `deleteComment` → `GET/POST/PATCH/DELETE /news/:id/comments` 与 `GET /news/:id/comments/:cid/replies`（回复列表，时间正序游标分页；root 已删返回空）。
- **`messages.ts`**：`fetchMessagesPage` / `fetchMessages` / `fetchUnreadCount` / `markMessageRead` / `markAllMessagesRead` → `GET /messages`、`GET /messages/unread-count`、`PATCH /messages/{id}`（**需登录**，不可见/不存在同码 40400）、`POST /messages/read-all`。
- **`me.ts`**：`fetchFavorites` / `addFavorite` / `removeFavorite` / `mergeFavorites` / `fetchHistory` / `recordHistory` / `clearHistoryRemote` / `mergeHistory` → `/me/favorites`、`/me/history`。所有写操作返回**完整集合** `{ ids, list }`（服务端即事实源）；导出 `MARKS_SYNC_SUPPORTED`，mock 模式下为 `false`，store 据此跳过同步请求。
- **`auth.ts`**：`login({phone,password})` → `POST /auth/login`（成功写 token）；`logout()` → `POST /auth/logout`（清 token）。登录/注册/续期响应携带 `user.isAdmin`（布尔），store 据此决定管理入口显隐。
- **`admin.ts`**：`fetchSources` / `createSource` / `updateSource` / `deleteSource` / `resetSources` → `/admin/sources` 五件套（**仅真实后端**，`ADMIN_SOURCES_SUPPORTED` 为门；mock 模式下不短路假数据——伪造一份可变的源列表会误导排障，入口由 `isAdmin` 隐藏即可）。行数据经 `toSource()` 归一化：丢弃缺 `key` 的脏行、`channel` 归一化、空 `lastError` 转 `null`、`respectRobots` 缺失时按更保守的一方兜底为 `true`。
  另含管理员白名单三接口：`fetchAdminUsers` / `addAdminUser` / `removeAdminUser` → `/admin/users`（P1-14，同样只有真实后端），
  列表额外返回 `envAdminCount`（环境变量来源管理员**数量**，不下发号码）用于在界面解释双源机制；
  行数据经 `toAdminUser()` 归一化，**形态非法的手机号直接丢弃**（删除按 phone 定位，脏行无法操作）。
  另含审计时间线 `fetchAuditPage({ limit, cursor, target })` → `/admin/audit`（P1-15，同样只有真实后端），
  游标由服务端下发（自增 id）、客户端不解析；行数据经 `toAudit()` 归一化。
  另含资讯审核四接口（P1-20，同样只有真实后端）：`fetchAdminNews({ status, keyword, channel, limit, cursor })`
  → `GET /admin/news`（**游标由服务端下发**，`{list, total}` 中 total 始终带回）、`hideNews(id, reason)`
  → `POST /admin/news/:id/hide`（**理由必填**，空理由走前端拦截，服务端 422 向上抛）、
  `restoreNews(id)` → `POST /admin/news/:id/restore`、`updateNewsTop(id, isTop)` → `PATCH /admin/news/:id`
  （**焊死驼峰 `{isTop}`**——蛇形会被后端 `extra='ignore'` 静默丢弃，表现为「开关永不生效」）。
  行数据经 `toNews()` 归一化：缺 `id` 的脏行丢弃、`hidden` 三件套缺失按**可见**兜底、
  理由空串转 `null`、`isTop` 非 `true` 一律 `false`、非法 `channel` 回落 `recommend`。
  另含评论治理三接口（P1-22，同样只有真实后端）：`fetchAdminComments({ status, keyword,
  newsId, limit, cursor })` → `GET /admin/comments`（**游标由服务端下发**，`{list, total}`
  中 total 始终带回；默认 `status=all` 含已隐藏，**已软删除一概不返回**）、
  `hideComment(id, reason)` → `POST /admin/comments/:id/hide`（**理由必填**，空理由走前端拦截，
  服务端 42200 向上抛）、`restoreComment(id)` → `POST /admin/comments/:id/restore`。
  行数据经 `toAdminComment()` 归一化：缺 `id` 的脏行丢弃、`hidden` 三件套缺失按**可见**兜底、
  `userName` 缺失回落「匿名」、`userId` 缺失回落 `null`、likes/replyCount 垃圾值归零。
  另含敏感词库与复核七接口（P1-23，同样只有真实后端）：`fetchWords({ keyword, action,
  enabled, limit, offset })` → `GET /admin/words`（limit+offset，按命中次数降序）、
  `createWord(input)` → `POST /admin/words`、`updateWord(id, input)` → `PATCH /admin/words/:id`
  （**只传要改的字段**）、`deleteWord(id)` → `DELETE /admin/words/:id`、
  `reloadWords()` → `POST /admin/words/reload`（返回生效条数）、
  `fetchModerationHits({ word, action, field, limit, cursor })` → `GET /admin/moderation/hits`、
  `reviewComment(id)` → `POST /admin/comments/:id/review`（幂等，只清 `pendingReview`）。
  行数据经 `toWord()` / `toHit()` 归一化：**未知 `action` 一律降级 `mask`**（数据异常时
  宁可"先脱敏留给人看"，也不把用户发言直接吞掉——与后端 `MODERATION_DEFAULT_ACTION` 同口径）、
  命中行的 `commentId` 缺失落成 `null`（"被拦截、没落库"的唯一信号，不编造 id）。
  另含昵称校验与昵称词库八接口（P1-24）：`updateNickname(name)` → `PUT /me/nickname`
  （`api/me.ts`；三级结果——形状 42203 / block 40006 提示**不回显命中词** / review
  **放行** + `pendingReview`；mock 模式有同规则形状校验副本，只服务离线演示）、
  `fetchNicknameWords({ keyword, action, enabled, limit, offset })` → `GET /admin/nickname-words`、
  `createNicknameWord(input)` → `POST /admin/nickname-words`（**别名逐个随主词同管道校验**）、
  `updateNicknameWord(id, input)` → `PATCH /admin/nickname-words/:id`、
  `deleteNicknameWord(id)` → `DELETE /admin/nickname-words/:id`、
  `reloadNicknameWords()` → `POST /admin/nickname-words/reload`、
  `fetchNicknameHits({ word, action, source, userId, limit, cursor })` → `GET /admin/nickname-hits`、
  `fetchNicknameReviews({ keyword, limit, offset })` → `GET /admin/nickname-reviews`、
  `reviewNickname(userId)` → `POST /admin/nickname-reviews/:userId`
  （后七个仅真实后端，`ensureRealBackend` 守卫）。`toNicknameWord()` **未知 `action`
  降级 `review`**（与后端 `FALLBACK_ACTION` 同口径——数据异常时"放行 + 让人看一眼"，
  不吞用户昵称）；`aliases` 脏数据按空数组归一。

> store 采用**离线优先**：本地先更新并持久化，真实模式下再后台与服务端同步（失败静默，不影响交互）。

---

## 内容安全策略 CSP（C9）

登录令牌存 `localStorage`，一次 XSS 就等于令牌泄露（access 已收窄到 30 分钟，
但"窗口短"不等于"不发生"）。CSP 是这一层兜底。

**策略来源**：`src/security/csp.ts` 的 `buildCsp()`，**构建期**由 `vite.config.ts`
的插件注入 `index.html` 的 `<meta http-equiv="Content-Security-Policy">`。

- **为什么写进 HTML 而不是只靠响应头**：前端是静态托管（后端不托管 SPA），
  响应头由谁下发取决于部署方；写进 HTML 则 nginx / OSS / Pages / CDN 都生效，
  不需要改基础设施。能控制响应头时建议搬过去——
  `frame-ancestors`、`report-uri`、`sandbox` 在 `<meta>` 里**被规范忽略**，
  `buildCsp({ transport: 'header' })` 就是为那条路径准备的。
- **为什么是构建期生成**：`connect-src` 取决于 `VITE_API_BASE`
  （生产 https 域名 / 开发 `http://127.0.0.1:8000` / 演示态留空走 mock）。
  写死要么生产连不上后端，要么开发把 API 挡掉。开发态额外放行 `ws:` 与 localhost（HMR）。

| 指令 | 值 | 说明 |
| --- | --- | --- |
| `script-src` | `'self'` | **不放行** `unsafe-inline` / `unsafe-eval`——这是策略的核心价值 |
| `style-src` | `'self' 'unsafe-inline' …` | Vue 作用域样式与组件内联 `style` 属性必须放开 |
| `img-src` / `media-src` | `… https:` | 资讯配图与视频来自任意外站 |
| `connect-src` | `'self'` + 后端源（+ 开发态 localhost/ws） | 由 `VITE_API_BASE` 推导 |
| `object-src` / `base-uri` / `form-action` | `'none'` / `'self'` / `'self'` | 禁插件、禁 base 劫持、禁表单外投 |

**诚实边界**：配图与视频必须放行 `https:`，因此 CSP **挡不住**
`<img src=https://evil/x>` 这类外泄型载荷；真正收紧它们要等图片代理（B4）把
外链收敛到自源。本策略的实际收益是：禁内联脚本、禁 eval、禁非自源脚本、
禁插件、禁嵌套、禁 base 劫持——绝大多数注入载荷就此失效。

后端那半（`/api/*` 的 `default-src 'none'`）见 `server-api/README.md`。

## 高敏操作二次校验（C10）

改昵称、删/重置 RSS 源、管理员增删这类**不可逆或影响全局**的操作，要求
「再证明一次你是本人」——会话令牌被偷时，攻击者拿到的是与本人同等的能力。

**流程**（`src/api/http.ts` + `src/stores/stepUp.ts` + `src/components/StepUpDialog.vue`）：

1. 请求撞上 **40304** → `http.ts` 广播 `STEP_UP_REQUIRED_EVENT` 并挂起；
2. 全局对话框（挂在 App 根部）弹出，用户输密码 → `POST /auth/step-up`；
3. 成功 → 令牌存**内存**（5 分钟，刷新页面即失效）→ 原请求带 `X-Step-Up-Token`
   重试一次；失败/取消 → 按原错误抛出。

三处刻意的设计：
- **一次请求最多弹一次**：与 401 续期同款守卫，否则「验证成功但仍被拒」
  会变成弹窗循环；
- **令牌只存内存、不落 localStorage**：5 分钟的高敏凭证不值得为省一次输入
  而持久化；`clearSession()`（登出/401 兜底）会一并清掉它；
- **请求层不碰 UI**：`http.ts` 只广播事件并等待，弹窗与验证在 store/组件里——
  否则无视图的请求层会被迫依赖组件。

后端那半（端点清单、账号维度锁定、40304 而非 401 的理由）见 `server-api/README.md`。

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
# 追加校验管理端接口（当前账号须具备管理能力，共 206 项：源管理 9 + 白名单 9 + 审计 7 + 健康趋势 11 + 资讯审核 31 + 评论治理 38 + 敏感词 42 + 昵称校验 59）
DEMO_PASSWORD=123456 CHECK_ADMIN=1 node scripts/contract-check.mjs
```

> mock 后端同样支持全套校验（演示账号：任意 11 位手机号 + `123456`，`13800138000` 为管理员；
> 二级评论的回复创建 / 拍平 / 正序分页 / 预览 / 删除语境在 mock 上与真实后端行为一致；
> 管理员白名单在 mock 上以进程内白名单并存，`isAdminPhone` 与后端同为「env ∪ 库」双源判定；
> P1-19 的 `days` 非法值在 mock 上同样 422，与真实后端 `ge=1, le=365` 对齐；
> P1-20 的资讯审核五端点在 mock 上同样可用——含**已下架种子资讯**供演示「已下架」分支，
> 公开四路过滤（列表/详情/搜索/评论）对已下架条目返回与「不存在」**同码 40400**）。
> P1-22 的评论治理三端点在 mock 上同样可用——含**演示资讯的种子评论**（其中一条已隐藏）
> 供演示「已隐藏」分支，公开五路过滤（主列表/回复/计数/点赞/回复）与真实后端一致。
> P1-24 的昵称校验八端点在 mock 上同样可用——含**种子昵称词**（5 条，其中 1 条带拼音别名）
> 与**待复核账号**（2 个）供演示队列分支；`PUT /me/nickname` 的三级结果（42203/40006/review）
> 与真实后端逐字一致。

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

> 截至 2026-10-15。`npm run build`（`vue-tsc` 类型检查 + 构建）零报错通过。

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
- [x] **单元测试**：Vitest 484 项（前端）+ pytest 626 项（后端），已接入 CI
- [x] API 层：`api/`（news / comments / auth + http 客户端，token 注入、`{code,data}` 解包），未配后端自动走 mock
- [x] store 全面接 API（news/comments 离线优先 + 后台同步；user 增 token 与 login/logout）
- [x] 真实图片渲染（`utils/image.ts` 真实 URL 优先 / 渐变兜底）+ 视频变体 `<video>` 播放
- [x] Storybook 工程化（`@storybook/vue3-vite`，7 个组件 stories + 静态构建）
- [x] **FastAPI + SQLite 后端**（`../server-api/`）：JWT 鉴权、PBKDF2 密码、资讯/评论/认证接口、SQLite 持久化
- [x] 前后端契约自动化校验（`scripts/contract-check.mjs`，**47 项全通过**，含 `/me` 收藏与历史、排序三模式与非法 `sort` 422、热搜词与搜索即统计、**二级评论 18 项**；`CHECK_ADMIN=1` 另加管理端 **206 项**——源管理 9 + 白名单 9 + 审计 7 + 健康趋势 11 + **P1-20 资讯审核 31** + **P1-22 评论治理 38** + **P1-23 敏感词 42** + **P1-24 昵称校验 59**）
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
- [x] **失败分级与熔断的前端表达**（P1-18）：源列表按性质分色——「连续 N 次失败 · 永久性」
  标红（等不会好，得去改配置）、「· 可重试」用中性样式（系统会自动退避重试）；
  冷却期内显示「冷却至 xx:xx」，避免运营误判成定时任务挂了。审计页新增
  **自动禁用源**动作（`system` 显示为「系统自动执行」，并带失败次数与最后错误自证原因）。
  `api/admin.ts` 三项新字段一律按「没有故障」兜底（0 / null），**不猜失败性质**
- [x] **源健康历史与趋势**（P1-19）：源列表每行健康 chip（近 N 天成功率分色）+「趋势」
  就地展开面板——按天序列（缺天留空**不补零**，区分"没抓"与"全失败"）、失败原文带性质标签、
  两条只有历史看得见的结论（**条数下滑**：成功轮均条数最近不足前期六成；
  **规律抖动**：失败集中同一钟点 → 疑似对方维护窗口）。`fetchSources()` 随之改返
  `{list, healthDays}`；mock 后端补两个样本源（条数下滑 / 凌晨规律失败）供演示
- [x] **资讯审核与下架**（P1-20）：新增 `/admin/news` 管理页——全部/可见/已下架三态筛选 +
  关键词搜索、**下架必须填理由**（空理由/超 300 字前端拦截，「留痕是准入条件」）、
  一键恢复、置顶开关（乐观更新 + 失败回滚）、游标「加载更多」、每行「历史」直达审计。
  后端**软下架非硬删**（`hidden`/`hidden_at`/`hidden_reason`），公开侧列表/详情/搜索/
  相关/评论五路过滤且与「不存在」**同码 40400**（管理端 40404 区分）；**后审核模式**——
  不改内容管线，恢复即实时可见；下架/恢复**幂等**（重复下架不覆盖原理由、恢复清空理由防张冠李戴）；
  审计新增 `news.hide`/`news.restore`/`news.update` 三动作，置顶走 PATCH 字段级 diff；
  mock 后端五端点 + 已下架种子同步支持
- [x] **评论治理**（P1-22）：新增 `/admin/comments` 管理页——全部/可见/已隐藏三态筛选 +
  关键词搜索、**隐藏必须填理由**（空理由/超 300 字前端拦截，「留痕是准入条件」）、
  一键恢复、游标「加载更多」、每行「历史」直达审计。后端**软隐藏非硬删**
  （`hidden`/`hidden_at`/`hidden_reason`，不给 hidden 建索引），公开侧主列表/回复/计数/
  点赞/回复五路过滤，**主评论隐藏后其回复亦不可见**（不裸露失去语境的回复）；
  管理端 40405 与公开端 40401 刻意分开；计数热度联动（`was_visible` 守卫防重复隐藏
  重复扣减）；幂等（重复隐藏不覆盖原理由、恢复清空理由防张冠李戴）；
  审计新增 `comment.hide`/`comment.restore` 两动作；mock 后端三端点 + 演示评论种子同步支持
- [x] **评论敏感词自动审核**（P1-23）：落库**之前**的服务端校验（`app/moderation.py`），
  归一化吃掉大小写 / 全半角 / 空格标点零宽干扰，Aho-Corasick 一次扫描；三种动作——
  `block` 拒绝提交（提示**不回显命中词**）、`mask` 脱敏后发布并置**待复核**、
  `allow` 白名单豁免（降误杀）。新增 `/admin/words` 管理页（新增/启停/删除 + 重载索引 +
  下方命中日志——**原文片段是误杀复盘的唯一证据**，被拦截的标「未落库」）；
  评论治理页新增「待复核」筛选与「通过复核」（只清标记，不改可见性；仍不合适应走隐藏）。
  词库改完**立即生效**（本进程缓存失效，其他实例靠 TTL 或手动重载）；
  mock 后端七端点 + 词库/命中/待复核种子同步支持
- [x] **用户昵称敏感词校验**（P1-24）：此前昵称**没有任何修改入口与校验**——脏昵称跟着
  每条评论与通知展示，等于让绕过者换个地方贴。新增 `PUT /me/nickname`（注册与改名同一条链，
  规则不搞两套），**三动作刻意无 mask**（昵称是账号属性，「脱敏后发布」等于改掉用户自己起的
  名字）：`block` 拒绝设置（提示**不回显命中词**）、`review` **默认**——**放行** + 置待复核
  进人工队列、`allow` 白名单完全包裹才豁免、`pass` 清待复核标志。归一化抗**七类绕过**：
  大小写/全半角/空格标点零宽、**重复压缩**（`aabbcc→abc`，昵称短无语故评论不做的这里做）、
  **leetspeak**（`4/@→a、0→o…`，模式与文本同管道）、**数字插字**（「博0彩→博彩」，
  与 leetspeak 互补）、**繁简 curated 表**（零依赖）；**拼音谐音走词条别名**（同音字替换
  无字符级算法可解，运营发现后补 `aliases`，与主词同管道——零新依赖路线）。四形态
  `base/nodigits/collapsed/collapsed-nodigits` 各带下标映射；引擎**复用**评论的
  Aho-Corasick 与指纹缓存，**新表不复用** `sensitive_words`（拦评论还是拦昵称要说清）。
  新增 `/admin/nickname-words`（词库 + **别名编辑** + 命中日志——**实际命中模式**回答
  「哪条别名拦的」）与 `/admin/nickname-reviews`（待复核队列——展示**当初提交的昵称**，
  「通过复核」只清标志、**不改昵称本身**）；`Profile.vue` 增昵称展示（**待复核徽标**）
  与改名弹窗（形状预检 + block/review/pass 三级反馈）。mock 后端六 admin 路由 +
  `PUT /me/nickname` + 种子（5 词含拼音别名、2 个待复核账号）同步支持

### 待完成 / 依赖后端

- [ ] 生产化：PG 强密码与备份策略、多实例限流换 Redis、内容审核与合规
- [ ] 视频真实资源 URL（渲染链路已通，待填真实地址）

> 完整进度与取舍见项目根目录 `明日头条-组件与接口速查表.md` 第 5 节。
