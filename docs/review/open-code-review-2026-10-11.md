# 代码审查报告 · 明日头条（AI 掘金头条）

| 项 | 值 |
| --- | --- |
| 审查日期 | 2026-10-11 |
| 工具 | `ocr`（alibaba/open-code-review）**委派模式**（delegation，无需 LLM 密钥） |
| 模式 | `workspace`（工作区无历史提交，全部文件视为新增） |
| 规则来源 | `ocr delegate rule` 解析的系统规则（5 组 glob） |
| 执行方式 | 6 个并行审查子代理，按架构分批，逐文件**完整**读取 |
| 可审查文件 | 109 个（约 11330 行） |
| 覆盖率 | **100%**（109/109 已审；ocr 自动排除测试/README/31 个文件） |
| 发现总数 | **166** 条（去重后） |

**严重程度分布**：critical 0 ｜ high 11 ｜ medium 56 ｜ low 99

> 说明：`low` 多为命名/注释/微风格建议，仅按类别汇总；`medium` 全部列出；`high` 逐条展开。

---

## 0. 结论与优先修复顺序

代码整体结构清晰、分层合理（前端 `api/` → `stores/` → `views/`；后端 `routers/` → `models/` → `feeds/`），
未发现 critical 级问题，也没有硬编码密钥、SQL 注入、`eval/exec` 或弱哈希。主要风险集中在**三类**：

1. **时间基准不统一**（`now_str()` 用本地时间、`seed`/`feeds`/`me` 用 UTC，且 `_parse_time` 会丢弃时区偏移）
   —— 直接冲击项目最核心的「按绝对时间排序」契约，并会污染时效清理的判定。**建议最优先修**（改动小、影响面大）。
2. **越权与契约缺口**：`mark_read` 可读取他人定向消息（IDOR）；`/api/me/*` 合并未去重、游标解析漏捕 `TypeError`、
   注册存在 check-then-insert 竞态 —— 这些未捕获异常因为**缺少兜底 `Exception` 处理器**而返回裸文本，
   同时破坏了 `{ code, message, data }` 统一契约。建议一并补齐全局处理器。
3. **前端状态共享与竞态**：`news` store 单一 `list`/`currentChannel` 被首页与视频页共用（串页），
   切频道与 `loadMore` 无序号守卫（跨频道污染），搜索「加载更多」实为整体替换。

---

## 1. Critical

**无。** 未发现会导致数据丢失、大范围安全突破或常规路径崩溃的缺陷。

---

## 2. High（11 条）

### H1. `mingri-toutiao-vue/mock-server/server.mjs:238` — 测试/契约

违反项目不变量 5（mock 与真实后端契约必须一致）：mock 后端缺失多个真实后端已实现、前端会调用的接口，且文件头注释（第 1-11 行）声称可验证「token 注入/鉴权/错误码/评论 CRUD 均可验证」，与实际不符。缺失清单：1) DELETE /api/news/:id/comments/:cid（真实后端 comments.py:185 支持软删除，前端 api/comments.ts:98 deleteComment 非 mock 路径直接调用，对 mock 会 404）；2) GET /api/news/search（真实 news.py:178，前端 searchNews 未做 catch，走 mock 后端时搜索页会直接报错）；3) GET /api/news/:id/related（真实 news.py:205，前端虽有 catch 但会静默返回空）；4) /api/messages*（真实 messages 路由，前端 messages.ts 全部依赖）；5) /api/auth/register、/api/auth/refresh（真实 auth.py:24/63）。建议补齐这些路由（至少覆盖前端实际调用的路径），让 mock 真正等价于真实后端。

### H2. `mingri-toutiao-vue/src/components/AppNav.vue:44` — 缺陷

违反项目不变量 2：SVG 图标颜色被硬编码。第 47 行 stroke="#222"（返回图标），另外第 90/91 行 stroke="#9a9fa8"（搜索变体）、第 107/108 行 stroke="#222"（搜索按钮）、第 120 行 stroke="#222"（消息按钮）、第 133-135 行 fill="#222"（更多按钮）同样是硬编码。深色模式下导航背景会变成 --color-card-bg=#1c1e22，而图标仍是 #222，几乎与背景同色导致图标不可见；#9a9fa8 也不会跟随 --color-text-secondary 的深色取值。建议把 stroke/fill 改为 currentColor（并由 CSS 用 var(--color-text-primary)/var(--color-text-secondary) 控制），或直接引用令牌色，禁止在组件内写死色值。

### H3. `mingri-toutiao-vue/src/components/TabBar.vue:58` — 缺陷

违反项目不变量 2：第 58 行 :stroke="item.key === active ? '#2d6cff' : '#9a9fa8'" 硬编码了图标颜色。深色模式下 --color-primary 是 #4d86ff、--color-text-secondary 是 #9aa0a6，而图标仍用浅色主题的 #2d6cff/#9a9fa8，会出现「图标蓝得发暗、文字却是亮蓝」的不一致；同时在深色底上对比度也不达标。建议改用 currentColor 并在 CSS 里由 .mt-tabbar__item / .is-active 通过 var(--color-text-secondary)/var(--color-primary) 控制描边色。

### H4. `mingri-toutiao-vue/src/stores/news.ts:40` — 缺陷

`fetchNews`（切频道/下拉刷新）与 `loadMore`（无限滚动）共享 `list/page/cursor/currentChannel`，但没有任何请求序号守卫或取消机制。若 `loadMore` 已在飞行中，此时用户切换频道触发 `fetchNews`：`fetchNews` 会用新频道数据整体替换 `list` 并写入新 `cursor`；随后旧的 `loadMore` 响应到达，会执行 `list.value = [...list.value, ...fresh]` 把**旧频道**条目追加进新频道列表，并把 `cursor` 覆盖为旧频道游标，导致列表混入错误频道内容、后续翻页错乱。建议为列表请求引入递增 `seq`（或 `AbortController`），仅当响应仍属当前请求时才应用；`loadMore` 返回后还需校验 `currentChannel` 是否与发起时一致。

### H5. `mingri-toutiao-vue/src/views/SearchResults.vue:34` — 缺陷

「加载更多」会把结果整段替换而非追加。runSearch(nextPage) 内部执行 `results.value = res.list`，而 store.search 直接返回 apiSearchNews 的当前页 list（非累计）。因此 loadMore() 调用 runSearch(page+1) 后，列表被第 2 页数据整体覆盖，用户永远只能看到最后一页，且按钮文案「加载更多（x/total）」与实际行为不符。建议：为追加语义单独处理，例如 `if (nextPage === 1) results.value = res.list; else results.value = [...results.value, ...res.list]`，并按 id 去重；同时改用 res.hasMore 判断是否还有下一页，而非依赖 results.length >= total（total 可能不准）。

### H6. `mingri-toutiao-vue/src/views/Videos.vue:27` — 缺陷

视频页与首页共用同一个 news store 的 `list`，导致跨 Tab 内容串页。Videos 的 onMounted 执行 `news.fetchNews('video')` 会把 store.list 覆盖为视频频道数据；HomeFeed 被 KeepAlive 缓存且模板直接渲染 `news.list`，切回首页时会显示视频内容；更严重的是 store 的 `currentChannel` 被改成 'video'，回到首页触发 useInfiniteList 的 `news.loadMore()` 会继续按 video 频道向首页列表追加数据。建议：让 store 按频道/视图分桶维护列表（如 `listByChannel`），或在 Videos 使用独立的视频 store/局部状态，避免多视图共享单一 list 与 currentChannel。

### H7. `mingri-toutiao-vue/tmp/image.mjs:1` — 可维护性

违反项目不变量 7：仓库中残留了临时/调试文件。该文件已被 git 跟踪（git ls-files 可见 mingri-toutiao-vue/tmp/image.mjs），内容是把 src/utils/image.ts 用 esbuild/tsc 编译出来的产物（含 var、双引号字符串），属于调试残留。更危险的是它与源码已漂移：只导出 bgFor/gradientFor/sanitizeImageUrl，缺少安全关键函数 safeImageUrl（组件 <img src> 依赖它做协议白名单）。若有人误从此文件导入，会绕过图片协议过滤（javascript:/data: 注入）。建议：直接删除该文件与 tmp/ 目录，并在 .gitignore（根与子项目）中忽略 tmp/，同时加一条 lint/CI 检查防止再次提交。

### H8. `server-api/app/feeds/fetcher.py:121` — 缺陷 · 已由复核上调

_parse_time 对带时区偏移的 ISO 串（格式 %Y-%m-%dT%H:%M:%S%z，如 2026-10-08T12:00:00+08:00）先 strptime 得到带 tzinfo 的时间，随后又无条件 .replace(tzinfo=timezone.utc)，会把解析出的真实偏移直接覆盖为 UTC，导致 published_at 整体偏移 8 小时，进而影响 feed 排序与 prune_old_news 的过期判定。建议：仅当 dt.tzinfo is None 时才补 UTC，否则保留原 tzinfo（必要时用 .astimezone(timezone.utc) 归一化）。

### H9. `server-api/app/models.py:14` — 缺陷

时间基准不一致，会破坏「按绝对时间排序」契约。now_str() 用 datetime.now().isoformat() 生成**本地无时区**字符串（如 2026-10-12T15:30:00），并被 Comment.created_at、News.time、UserFavorite.created_at、UserHistory.viewed_at、MessageRead.created_at、User.created_at 等所有默认值使用；而 seed.py::_ago() 与 feeds/ingest.py 写入的是 **UTC** ISO 字符串（如 2026-10-12T07:30:00+00:00）。两者混在同一列后按字符串排序，同一时刻的本地值恒大于 UTC 值，导致种子评论/资讯与运行时新增数据排序错乱（违反不变量 #4：排序必须基于绝对时间字段）。建议统一为 UTC aware：now_str() 改为 datetime.now(timezone.utc).isoformat(timespec='seconds')，并让种子/抓取路径复用同一函数。

### H10. `server-api/app/routers/messages.py:61` — 缺陷

列表按 `Message.id.desc()` 排序并以此做 keyset 游标，但 id 是随机串（notify.py 用 `msg_{uuid4().hex[:16]}`，种子为 m1..m4），排序结果与真实时间无关：以种子为例会得到 m4(1天前)→m3(2小时前)→m2(1小时前)→m1(刚刚)，恰好把最旧排在最前。Message 表也没有绝对时间列（time 只是展示文案，notify 恒定写 "刚刚"），违反「排序必须用绝对时间字段」的不变量 4。建议新增 created_at(ISO) 列，按 (created_at DESC, id DESC) 排序与分页。

### H11. `server-api/app/routers/messages.py:108` — 安全

mark_read 未做可见性校验：`db.get(Message, message_id)` 直接取出任意消息并返回其 title/summary/newsId，全程没有校验 `m.user_id is None or m.user_id == user.id`。任何已登录用户（甚至未登录——依赖用的是 get_current_user_optional，与 docstring「需登录」不符）只要知道或猜到消息 id 就能读取他人定向消息内容（种子 id 形如 m1/m2 极易枚举），属越权/IDOR，违反不变量 7。建议改用 get_current_user 强制登录，取出后先按 visible_condition(current) 校验可见性（不可见一律返回 404），再决定是否写入已读。

---

## 3. Medium（56 条，按类别分组）

### 3.1 安全（5 条）

- **`.github/workflows/ci.yml:1`** — 工作流未声明 permissions，GitHub 默认授予 GITHUB_TOKEN 较宽权限（同一仓库的 push/PR 场景下通常包含 contents: write）。CI 只做只读校验（checkout、npm ci、pytest），应遵循最小权限原则，在顶层加 permissions: contents: read（如需在 j…
- **`.github/workflows/ci.yml:61`** — 第 61-69 行的「契约检查」步骤同时存在两处问题：1) step 级 env 里硬编码了 DEMO_PASSWORD: '123456'（工作流中的明文口令，违反「CI 中不得出现硬编码凭证」）；2) 紧接着第 68-69 行又用 pw=$(python -c "from app.config import DEMO_PASSWORD…
- **`mingri-toutiao-vue/src/utils/image.ts:32`** — 协议白名单校验顺序有缺陷：`HAS_SCHEME` / `SAFE_SCHEME` 的判定发生在去除控制字符**之前**，且 `safeImageUrl` 完全不处理换行/制表符，`sanitizeImageUrl` 也只在判定之后才删除 `\r\n`（且不删 `\t`）。因此形如 `java\nscript:alert(1)` 的输入：…
- **`mingri-toutiao-vue/src/views/NewsDetail.vue:92`** — 视频/封面 URL 未经协议白名单过滤，违反不变量 #2。`<video :src="item.videoUrl">` 与 `:poster="item.cover || undefined"` 直接绑定后端字段；poster 本质是图片地址，应经 `safeImageUrl()`，videoUrl 同样应做 http(s)/相对路径白名…
- **`server-api/main.py:88`** — client_ip 取 X-Forwarded-For 的**首段**，在标准「代理追加」部署下可被客户端伪造绕过限流。若反向代理把真实 IP 追加到已有 XFF 末尾，客户端自行发送 'X-Forwarded-For: 1.2.3.4' 后，头部会变成 '1.2.3.4, <真实IP>'，取首段即得到伪造值，攻击者只需轮换该值即可绕过按…

### 3.2 缺陷（32 条）

- **`mingri-toutiao-vue/mock-server/server.mjs:219`** — GET /news 与真实后端契约漂移：1) 真实 news.py:_page_payload 返回 { list, total, page, pageSize, nextCursor, hasMore }，mock 只返回 { list, total, page }，缺 pageSize/nextCursor/hasMore；2) mo…
- **`mingri-toutiao-vue/src/api/me.ts:24`** — `EMPTY` 是模块级共享常量，mock 分支 `return mockDelay({ ...EMPTY, list: [] })` 只新建了 `list`，`ids` 仍指向同一个共享数组实例。这正是不变量 #6 明令禁止的『浅拷贝共享数组实例』反模式：一旦调用方对返回的 `payload.ids` 做 push/splice，就会污…
- **`mingri-toutiao-vue/src/composables/useCarousel.ts:68`** — `shift()` 使用 `window.setTimeout(finishShift, transitionMs + 120)` 兜底，`finishShift()` 使用 `requestAnimationFrame`，但两者都没有在组件卸载时清理（未使用 `onBeforeUnmount`）。后果：1) 组件卸载后定时器/rAF 仍…
- **`mingri-toutiao-vue/src/composables/useNewsDetail.ts:19`** — `resolve()` 在 id 变化时由 `watch(id, resolve)` 再次触发，但没有任何请求序号守卫：若 id 从 A 快速切到 B，A 的 `loadDetail(A)` 可能晚于 B 返回，导致 `item.value` 被 A 覆盖（显示上一篇内容）
- **`mingri-toutiao-vue/src/stores/comments.ts:12`** — `load()` 只判断 `raw` 是否存在就 `JSON.parse` 并 `as Record<string, Comment[]>`，缺少结构校验（违反不变量 #10 的『try/catch + 结构校验』要求）。若 localStorage 被写入非对象值（如 `"123"`、`"null"`、数组），`byArticle.va…
- **`mingri-toutiao-vue/src/stores/comments.ts:40`** — `ensure()` 在 key 不存在时先写入本地种子，再异步 `listComments(id)`，成功后用 `byArticle.value[id] = remote` **无条件整体覆盖**。若在这段 await 期间用户执行了 `add()`（本地 unshift）或 `toggleLike()`（改 liked/likes），…
- **`mingri-toutiao-vue/src/stores/comments.ts:67`** — 真实模式下 `add()` 先本地乐观插入一条 id 为 `c${Date.now()}` 的假评论，随后 `void createComment(id, content).catch(() => {})` **丢弃了服务端返回的真实评论**（含真实 id）。而 `ensure()` 仅在 key 不存在时才拉取远端，此时 key 已存在…
- **`mingri-toutiao-vue/src/stores/comments.ts:98`** — `remove()` 在 `await` 前捕获 `const arr = byArticle.value[id]`，回滚时对 `arr.splice(idx, 0, backup)` 操作。但 `ensure()` 的远端拉取成功后执行 `byArticle.value[id] = remote` **整体替换数组实例**；若该替换发生…
- **`mingri-toutiao-vue/src/views/Comments.vue:103`** — 空态里的「去评论」按钮绑定的是 `@click="send"`，但 send() 读取的是 `draft`，此刻草稿为空会直接 `return`，点击无任何反应（死按钮）
- **`mingri-toutiao-vue/src/views/Favorites.vue:16`** — 收藏列表用 `user.favorites.map((id) => news.getById(id)).filter(x => x !== null)` 静默丢弃无法解析的条目。收藏可能来自云端（不变量 #6），只有在 syncMarks 成功且 applyPayload→cacheMany 写入缓存后才能命中；若同步失败或尚未完成，这些…
- **`mingri-toutiao-vue/src/views/History.vue:24`** — 「清空」按钮直接调用 `user.clearHistory()`，无二次确认。该方法会清空本地历史并同步调用远端 clearHistoryRemote（不可逆），误触即丢失全部浏览记录，且无撤销
- **`mingri-toutiao-vue/src/views/HomeFeed.vue:60`** — 首屏骨架实际上是死分支：news store 的 `list` 初始化为 `mockNews`（见 stores/news.ts:18），首屏渲染时 list 非空，`news.loading && !news.list.length` 恒为 false，骨架永远不会出现，用户会先看到 mock 假数据再被真实数据替换（内容闪烁）；同理首…
- **`mingri-toutiao-vue/src/views/Messages.vue:23`** — `TYPE_META` 用 `Record<Message['type'], ...>` 索引，但模板中 `TYPE_META[m.type].bg` / `.label`（第 53-54 行）未做兜底。m.type 来自后端 `/api/messages`，若服务端新增了未知 type（或字段漂移），`TYPE_META[m.type]…
- **`mingri-toutiao-vue/src/views/NewsDetail.vue:59`** — 相关推荐请求存在竞态：`related.value = []; if (val) related.value = await fetchRelated(val.id)`。当快速从 /news/1 跳转到 /news/2（或点击相关推荐连续跳转）时，两个异步请求可能乱序返回，旧文章的响应会覆盖新文章的列表，出现「标题是 B、相关推荐却是 A…
- **`mingri-toutiao-vue/src/views/Videos.vue:27`** — 视频页只有一次 `fetchNews('video')`，既没有 `news.resetPaging()`，也没有接入 useInfiniteList / loadMore，页面注释却写着「列表仍取 video 频道全量」。结果是：仅展示第一页、无触底加载；若此前首页已分页，store 的 cursor/hasMore 状态还会被带入。同…
- **`server-api/app/feeds/sources.py:37`** — 第 37 行注释称这些源「探测不可用（超时/404），保留配置但不启用」，但其下的 zaobao 却写的是 enabled: True（thepaper 才是 False），注释与配置自相矛盾。请确认 zaobao 是否应启用：若注释准确则改为 False，否则把注释与 zaobao 分开，避免误导运维与后续判断。
- **`server-api/app/models.py:49`** — News.comments 关系声明了 cascade='all, delete-orphan' 且 passive_deletes=True，但 Comment.news_id（第 79 行）的外键**没有** ondelete='CASCADE'。passive_deletes=True 的语义是「删除父行时不加载/不级联子行，交由数…
- **`server-api/app/routers/auth.py:28`** — 注册存在 check-then-insert 竞态：先 `query(...).first()` 判重、再 `db.add+commit`。两个并发请求携带同一手机号时都能通过判重，随后第二次 commit 触发唯一约束 IntegrityError。由于 main.py 只注册了 HTTPException / RequestValid…
- **`server-api/app/routers/comments.py:160`** — 点赞是「读-改-写」：先 list(c.liked_by) 到内存、修改、再整体写回 JSON 列并自增/自减 likes。并发请求下会丢失更新：同一用户并发两次点赞可能都判定 `user.id not in liked_by` 从而重复 append 并把 likes 多加 1；不同用户并发也会互相覆盖 liked_by（last-wr…
- **`server-api/app/routers/comments.py:200`** — delete_comment 的 docstring 声称「幂等：重复删除同一条返回成功」，但第 200 行对 `c.is_deleted` 直接 raise 404，第二次删除返回 404 而非成功，与文档/契约不符（tests/test_api.py:506 的 test_delete_is_idempotent 实际只删了一次，未覆…
- **`server-api/app/routers/me.py:136`** — _merge 只做集合判重、未对入参 ids 去重：`candidates = [nid for nid in ids if nid not in existing and nid in valid]`，若 body.ids 含重复项（如 ["a","a"]），会为同一 (user_id, news_id) 生成两条插入，commit 时…
- **`server-api/app/routers/news.py:96`** — _decode_cursor 只捕获 (binascii.Error, ValueError, UnicodeDecodeError)，但合法 JSON 且长度为 3 的载荷若首元素非数字（如 base64 编码的 [[1],"","x"]），`int(top)` 会抛 TypeError 未被捕获，导致 500 裸 JSON（main.…
- **`server-api/app/scheduler.py:68`** — 会话级 advisory lock 与连接池交互存在锁泄漏风险。pg_try_advisory_lock 是**会话/连接级**锁，而 _run_once 里 run_ingest 内部会 session.commit()；SQLAlchemy 在 commit 后会把连接归还连接池，之后的 _release_lock 会在（可能不同的）…
- **`server-api/app/seed.py:122`** — 种子播种是「先查后插」的非原子操作，多 worker / 多实例同时启动时会竞争。init_db 在 lifespan 中对每个 worker 各执行一次，两个进程可能同时判断 n1/c1/m1 不存在而各自插入，触发主键唯一冲突导致其中一个 worker 启动失败（demo 账号的 phone 唯一索引同理）
- **`server-api/main.py:197`** — 中间件顺序导致限流响应缺失 CORS 头与可观测性。add_middleware 后注册的中间件位于外层，实际栈为：rate_limit(外) → access_log → CORSMiddleware(内) → ExceptionMiddleware。因此 rate_limit_middleware 直接返回的 429 JSONResp…
- **`server-api/main.py:228`** — 缺少兜底 Exception 处理器，未处理异常会返回裸文本而非统一契约。当前仅注册了 HTTPException 与 RequestValidationError 两个处理器；任何未捕获异常（如 DB 异常、KeyError）会走 Starlette 默认 500，响应体是纯文本 'Internal Server Error'，前端 a…
- **`server-api/main.py:313`** — 手动抓取接口 /api/news/refresh 未复用调度器的多实例互斥锁。scheduler.py 通过 PG advisory lock 保证同一时刻只有一个实例执行抓取（M6），但 _run_refresh 直接用 SessionLocal 调 run_ingest/prune_old_news，完全没有 _try_lock/_r…
- **`server-api/migrations/env.py:20`** — config.set_main_option('sqlalchemy.url', DATABASE_URL) 在 DATABASE_URL 含 % 时（如 URL 编码口令 %40、%2F）会触发 configparser 插值错误（ValueError/InterpolationSyntaxError），使 alembic 直接无法启动
- **`server-api/migrations/versions/0002_news_published_at.py:21`** — 注释说「用种子数据的时间字段回填一部分」，但执行的 SQL 是 UPDATE news SET published_at = NULL WHERE published_at IS NULL —— 把本就为 NULL 的行再置 NULL，是空操作，没有回填任何数据。结果是历史资讯 published_at 全为 NULL，会被 prune_…
- **`server-api/migrations/versions/0005_news_search_trgm.py:38`** — _ensure_trgm 在同一个迁移事务里执行 op.execute('CREATE EXTENSION ...')，一旦因权限不足失败，PostgreSQL 会把当前事务置为 aborted 状态；此处仅 catch 后 return，随后 alembic 在同一事务内的提交会被 PG 回滚，导致 alembic_version 不前…
- **`server-api/scripts/migrate_sqlite_to_pg.py:35`** — docstring 称「读取 SQLite 全部表数据」，但 TABLE_MODELS 只覆盖 users/news/comments；messages、message_reads、user_favorites、user_history 不会被迁移，而这些表已由 0004/0009/0010 引入并承载消息与用户标记数据。按当前脚本迁移会…
- **`server-api/scripts/migrate_sqlite_to_pg.py:81`** — data = {k: v for k, v in data.items() if v is not None or k in JSON_COLUMNS}：JSON_COLUMNS 的键是表名（news/comments），而此处的 k 是列名（如 images/liked_by），k in JSON_COLUMNS 恒为 False，判断…

### 3.3 性能（6 条）

- **`server-api/app/feeds/ingest.py:68`** — 模块 docstring 写的是「多源并发」，但 collect 实际是 for 循环串行抓取（第 85-91 行）。每源 15s 超时、最多 3 次尝试（含退避 sleep），10 余个源最坏可达数分钟，而该函数被 /api/news/refresh 与定时任务直接调用
- **`server-api/app/feeds/ingest.py:216`** — prune_old_news 先把最多 budget 个 id 全量读入内存（第 216-226 行 .all()），再用于 Message/Comment/UserFavorite/UserHistory/News 共五处 IN(...)（第 233-237 行）。budget 很大（如数万）时会生成超长 IN 列表，可能触及 SQLi…
- **`server-api/app/notify.py:83`** — notify_comment_on_article 存在 N+1 查询：先查出该资讯下所有评论者，再对每个收件人调用 _create → _dedupe_exists 各发一次 SELECT（Message.user_id/type/summary/news_id）。热门资讯评论者可达数十上百人时，一次评论就产生上百次查询
- **`server-api/app/seed.py:163`** — _sync_orphans 在每次启动时把整表数据加载进内存做集合比对：db.query(Comment).all() 与 db.query(Message).all() 均为全表扫描 + 全量实例化，评论/消息量大时会造成启动内存与耗时激增（init_db 中第 132/142/149 行还各自 select 全表主键集合）
- **`server-api/scripts/migrate_sqlite_to_pg.py:64`** — copy_table 先 fetchall() 把整表读入内存，又对同一表再执行一次 SELECT * 仅为取列名（重复查询）。数据量大时内存与耗时都高
- **`server-api/scripts/migrate_sqlite_to_pg.py:84`** — 逐行 dst.get(model, pk) 判断存在后再 add/setattr，是典型 N+1（每行至少 1 次 SELECT，可能再 1 次 INSERT/UPDATE），大表迁移很慢

### 3.4 样式/规范（9 条）

- **`mingri-toutiao-vue/src/components/BannerCarousel.vue:45`** — 违反项目不变量 2：第 45-50 行的 palette 是硬编码的浅色渐变（#4e8bff/#2f6bd8/#5b93ff/#3a74e0 等），用于无封面时的兜底背景。深色模式下这些高亮渐变会显得刺眼、与 #121316 的页面底色严重不搭，且无法随主题切换
- **`mingri-toutiao-vue/src/views/Comments.vue:25`** — 硬编码色值，违反不变量 #3：组件内 `palette` 数组硬编码了 4 组渐变（第 25-30 行），且与 utils/image.ts 的 PALETTE 重复实现；点赞图标也硬编码 `:stroke="c.liked ? '#e5484d' : '#9a9fa8'"`、`:fill="c.liked ? '#e5484d' : '…
- **`mingri-toutiao-vue/src/views/Gallery.vue:153`** — 硬编码色值，违反不变量 #3：`.gallery { background: #111418 }`（第 153 行）、`.gallery__count { color: #fff }`（第 179 行）、`.gallery__arrow { background: rgba(0,0,0,0.35) }`（第 238 行）以及多处 SVG …
- **`mingri-toutiao-vue/src/views/Login.vue:125`** — 硬编码色值，违反不变量 #3：`.login__logo { background: linear-gradient(135deg, #4e8bff, #1e4fd8); color: #fff; }`，以及 `.login__error-icon { color: #fff }`（第 201 行）。深色模式下不随主题变化，建议改用 to…
- **`mingri-toutiao-vue/src/views/Messages.vue:24`** — 硬编码色值，违反不变量 #3：`TYPE_META` 中三个渐变（#2d6cff/#6a9bff、#ff8a3d/#ffb066、#22b07d/#5ad3a3）与 `.msg__avatar { color: #fff }`（第 148 行）均为硬编码，深色模式下不随主题调整
- **`mingri-toutiao-vue/src/views/NewsDetail.vue:229`** — 硬编码色值，违反不变量 #3（深色模式下会露馅）：`.detail__video { background: #000 }`（第 229 行）、`.detail__atlas { background: rgba(0,0,0,0.55); color: #fff }`（第 240-241 行）
- **`mingri-toutiao-vue/src/views/Profile.vue:111`** — 硬编码色值，违反不变量 #3：`.profile__avatar { background: linear-gradient(135deg, #4e8bff, #1e4fd8); }`（第 115 行）与菜单箭头 `stroke="#c5c8cd"`（第 80 行）均为硬编码。主题切换（本页核心功能）后这些固定色不会跟随，深色模式下箭头对…
- **`mingri-toutiao-vue/src/views/Search.vue:30`** — 返回箭头硬编码 `stroke="#222"`（第 32 行），违反不变量 #3：该图标位于 `--color-card-bg` 卡片背景上，深色模式下卡片变深、#222 箭头几乎不可见
- **`mingri-toutiao-vue/src/views/Showcase.vue:93`** — 陈列面板大量硬编码色值：`.showcase { background: #fff; box-shadow: 0 12px 40px rgba(17,24,39,0.12) }`（第 97、100 行）、`.sc-h { color: #222 }`（第 110 行）、`.sc-token__name { color: #222 }`（第…

### 3.5 其他（4 条）

- **`mingri-toutiao-vue/src/components/BannerCarousel.vue:120`** — 无障碍问题：.banner__slide 是带 @click 的 <div>，没有 role/tabindex，也没有键盘（Enter/Space）处理，无法通过键盘激活跳转；而下方指示点却是真正的 <button>
- **`mingri-toutiao-vue/src/components/ChannelTabs.vue:33`** — 无障碍问题：激活态只通过颜色与 ::after 下划线表达，读屏用户无法感知当前选中项；<nav> 也没有可访问名称
- **`mingri-toutiao-vue/src/components/NewsCard.vue:29`** — 无障碍问题：整卡可点击的 <article class="mt-card" @click> 没有 role/tabindex/键盘处理，键盘与读屏用户无法打开资讯；同样地，第 63-77 行的三图 <img>/<div> 绑定了 @click.stop 却没有可聚焦与键盘支持
- **`mingri-toutiao-vue/src/views/NewsDetail.vue:108`** — 图集缩略图 `.detail__thumb` 用 `<div @click>` 实现跳转，不可聚焦、无键盘可达性、无 role/aria-label，屏幕阅读器也无法识别为可交互元素（无障碍问题）

---

## 4. Low（99 条，仅汇总）

| 类别 | 数量 |
| --- | --- |
| 可维护性 | 38 |
| 缺陷 | 28 |
| 性能 | 10 |
| 安全 | 8 |
| 其他 | 6 |
| 测试/契约 | 5 |
| 样式/规范 | 3 |
| 文档 | 1 |

主要为：命名与注释措辞、JSDoc/类型标注补全、CSS 类命名一致性、`v-for` key 细节、
mock 数据与真实数据字段对齐、脚本可读性等。**不影响功能正确性**，可在后续迭代中随手清理。

---

## 5. 不变量核对（已确认成立）

| 不变量 | 结论 |
| --- | --- |
| 1. 响应统一 `{ code, message, data }` | **部分违反**：缺兜底 `Exception` 处理器（见 M-后端正确性），已注册的两个处理器本身实现正确 |
| 2. 资讯排序 `is_top → published_at → id` | 排序实现正确并已复用；但 `published_at` 的**写入基准不统一**（见 H1/H2） |
| 3. 游标分页（keyset），`page` 仅兼容 | **成立**；游标 base64 不透明，keyset 条件用 `is_()` 展开布尔列 |
| 4. 排序用绝对时间字段 | **部分违反**：`now_str()` 本地时间 vs UTC 混用（H1）；`Message` 表缺绝对时间列（H4） |
| 5. 迁移方言守卫 / SQLite `batch_alter_table` | **成立**：0003/0005/0007/0009/0010 守卫正确，0004 用 batch 删列 |
| 6. 配置走 `_env()`，无硬编码密钥 | **成立**：全仓扫描未见真实密钥（命中均为测试桩与构建产物） |
| 7. 前端请求走 `api/http.ts::request` | **成立**：视图层无直接 `fetch` |
| 8. 图片 URL 经协议白名单 | **部分违反**：`NewsDetail` 的 `videoUrl`/`poster` 未过滤（M-安全）；白名单判定顺序可被控制字符绕过（M-安全） |
| 9. 字段归一化 + 运行时校验 | **成立**：各 `api/*.ts` 集中 `normalize`，`validate.ts` 开发期校验 |
| 10. 颜色仅用 CSS 变量 | **部分违反**：7 个视图 + 3 个组件存在硬编码色值（H9/H10 与 M-样式） |
| 11. Alembic 单轨（无 `create_all`） | **成立**：`init_db` 仅校验表存在 |
| 12. `/api/me/*` 契约 | **部分违反**：合并未去重可致主键冲突（M-缺陷）；可见性/登录要求本身正确 |
| 13. 本地标记按账号命名空间隔离 | **成立**：`emptyMarks()` 工厂函数、作用域切换前落盘、序号守卫均正确实现 |
| 14. PBKDF2-SHA256 + HS256 + `token_version` | **成立** |
| 15. 评论软删除并过滤 | **成立**；仅 docstring 与实现的幂等语义不一致（M-缺陷） |

---

## 6. 建议的修复批次

**批次 1（时间与契约，约半天）**
- 统一时间基准：`now_str()` 改为 UTC aware（或全库统一为 UTC naive 并显式转换），并加一条迁移/回填脚本；
- 修 `_parse_time` 的 `%z` 分支（不要覆盖已解析出的 tzinfo）；
- 补 `Exception` 全局处理器，统一返回 `{ code: 50000, message, data }`；
- `Message` 增 `created_at` 列 + 迁移，列表改按 `(created_at DESC, id DESC)` 排序。

**批次 2（安全与越权，约半天）**
- `mark_read` 改 `get_current_user` + 可见性校验（不可见一律 404）；
- `/api/me/*` 合并入参去重；`_decode_cursor` 补捕 `TypeError`；注册改 `ON CONFLICT DO NOTHING`；
- 图片/视频 URL 统一经 `safeImageUrl()`；白名单判定移到控制字符清洗之后；
- `client_ip` 取 XFF 时改为「最后一个不受信跳」策略；CI 移除硬编码口令并加 `permissions: contents: read`。

**批次 3（前端状态与体验，约 1 天）**
- `news` store 按频道分桶（或视频页独立 store），消除串页；
- `fetchNews`/`loadMore`/`search` 引入请求序号守卫或 `AbortController`；
- 搜索「加载更多」改追加 + 去重；`Videos` 接入无限滚动；
- 清理 `mingri-toutiao-vue/tmp/` 残留并在 `.gitignore` 忽略 `tmp/`；
- 硬编码色值收敛为 tokens 变量（含 SVG `stroke`/`fill` 改 `currentColor`）。

---

## 附录：审查方法

```bash
# 1) 委派预览（枚举可审查文件）
ocr delegate preview --format json > .tmp/ocr_preview.json
# 2) 规则解析
ocr delegate rule --format json <one file per extension> > .tmp/ocr_rules.json
# 3) 按架构分批 → 6 个并行子代理逐文件完整审查 → 汇总
```

原始发现：`.tmp/findings-batch-{A..F}.json`（166 条）；去重合并：`.tmp/findings-merged.json`。
