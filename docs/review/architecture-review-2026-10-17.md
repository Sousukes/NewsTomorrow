# 架构与代码库全面审查 · 明日头条（2026-10-17）

## 0. 结论（先说判断）

**总体评价：工程治理能力显著高于功能完整度。** 迁移 24 个版本、契约测试 253 项双端对齐、软删除/审计/熔断/限流/抓取礼仪一应俱全，代码注释把「为什么这样设计」写进了源码——这是多数同规模项目不具备的底座。

**但存在一处结构性失衡：功能侧连续推进 9 个批次（P1-16 ~ P1-24），债务侧零偿还。** 我逐条复核了 10-11 那轮审查的 11 项 High，**确认 7 项至今未修复**（H2/H3 硬编码色值、H4 前端列表无竞态守卫、H5 搜索「加载更多」整段替换、H6 跨 Tab 串页、H8/H9 时间基准不统一、H10 消息按随机 id 排序、H11 消息越权读取），仅 H7（临时文件入库）已修复。其中 H11 是**可被任意登录用户枚举读取他人定向消息**的越权缺陷，属于当前代码库中唯一可直接造成数据泄露的入口，应最优先处理。

**另有一项产品级问题：资讯模型没有正文字段，详情页正文是前端写死的占位文案**（`NewsDetail.vue:29-33` 的 `body` 常量，三段「据相关方面介绍…」）。演示数据正在冒充真实功能，这比任何性能问题都更影响产品可信度。

优先级判据：**P0 = 已确认可被用户/攻击者观测到的错误行为；P1 = 影响正确性或扩展成本但不立即可见；P2 = 长期演进项。**
成本判据：**S = 单点改动（≤1 天）；M = 跨模块但边界清晰（2~5 天）；L = 涉及数据迁移或架构调整（>5 天）。**

---

> **处置进度（2026-10-18 更新）**：本文件是 2026-10-17 的**历史快照**——保留完整
> 分析与证据，但**开放项的跟踪已移交 `backlog.md`**（F5 的产出，唯一跟踪入口，
> 含「每个功能批次固定偿还 1~2 项」规则）。两处各记一半必然漂移，故此处不再
> 增量维护开放项，只留已偿还记录：
> - **A1** 消息越权 → P1-25 已处置（提交 `98d77f2`）
> - **A2** 消息列表排序与游标 → P1-29 已处置（迁移 0026；按
>   `(created_at DESC, id DESC)` 排序，游标格式不变）
> - **A5** 搜索加载更多、**B1** 回复预览下推 → P1-26 已处置
> - **A6** 图标配色去硬编码、**F1** CI 迁移校验 → P1-27 已处置
> - **C2** 登录失败锁定 → P1-30 已处置（账号维度失败计数 5 次/15 分钟，
>   42902 + Retry-After；无迁移，计数走限流器）
> - **C8** CI 依赖漏洞审计 → P1-31 已处置（pip-audit + npm audit
>   --audit-level=high；pip-audit 前置升级 pip，npmmirror 无审计端点）
> - **E1** 资讯正文落地 → P1-28 已处置（迁移 0025；详情接口返回 `content`，
>   无正文显示「暂无正文」而非占位文案）
> - **F5** 债务 backlog 机制 → P1-32 已处置（`backlog.md` + 每批偿还规则；
>   开放项 30 项的跟踪入口）
> - **A3** 时间基准统一 → P1-33 已处置（迁移 0027；`now_str`+`parse_dt`
>   双入口，比较侧全量排查；回填只补偏移不改数字）
> - **A4** 前端列表状态跨视图共享 → P1-34 已处置（按 频道×排序 分桶 +
>   代次守卫；删掉「隐式当前频道」这一根因）
> - **C6** 管理端鉴权逐端点调用 → P1-35 已处置（`APIRouter(dependencies=...)`，
>   漏写在结构上不再可能）
> - **C5** 运维接口无鉴权 → P1-36 已处置（`OPS_TOKEN`；未配置时生产 403
>   fail-closed，开发放行）
> - **C7** 命中日志留存 → P1-37 已处置（90 天保留期 + 启动/cron 双触发清理）
> - **C1** 单张 7 天令牌 → P1-38 已处置（access 30 分钟 / refresh 7 天且
>   只能换发；类型校验 + 前端自动续期。CSP 与高敏二次校验拆为 C9/C10）
> - **C9** 无 CSP → P1-39 已处置（SPA 策略构建期注入 `index.html`；`/api/*`
>   发 `default-src 'none'`；img/media 放行 https 的收紧依赖 B4 图片代理）
> - **C10** 高敏操作无二次校验 → P1-40 已处置（step-up 令牌 5 分钟；
>   改名/删源/重置源/管理员增删五类；内容治理类可逆且有审计，未覆盖）
> - **C4** 无改密/找回通道 → P1-41 已处置（管理员代重置 + 强制改密受限态，
>   迁移 0028；短信验证码那一半仍是 C3，需要外部服务）
> - **B2** 公开列表 offset 模式全表 `count()` → P1-42 已处置（`page` 改可选，
>   不传即 keyset，首页不再 count；offset 分支保留给需要「第 N 页」的调用方）
> - **B3** `for-you` 每请求 2 次 JOIN 聚合 → P1-42 已处置（`app/cache.py`
>   5 分钟 TTL + 收藏/浏览写入主动失效）
> - **B4** 图片直连源站 / CSP 收不紧 → P1-43 已处置（`GET /api/img` 代理，
>   SSRF 防线逐条写在 `app/routers/img.py` 顶部；有后端时 `img-src` 收掉
>   `https:`；`media-src` 保留是**视频**未代理，已开 B7 单独跟踪）
> - **D8** 无容器化 / 无迁移前置钩子 → P1-44 已处置（compose 四服务 +
>   `migrate` 一次性服务 + `docs/deploy.md`）。**边界**：本机无 Docker 守护
>   进程，镜像**未在 CI 验证**，已开「CI 验证镜像可构建」单独跟踪
> - **F2** 无覆盖率门槛与趋势 → P1-45 已处置（`.coveragerc` `fail_under=91`，
>   实测 92.55%；CI 出 XML 构件）。已知缺口（`seed.py` 56%、
>   `scheduler.py`/`ratelimit.py` 68%）拆为 F5，前端覆盖率拆为 F4'

## 1. 资产快照（实测）

| 维度 | 数值 | 备注 |
| --- | --- | --- |
| 后端 Python | 16,252 行（app 约 5,000 + tests 约 7,300） | `admin.py` 1,640 行 / 34 端点为最大单文件 |
| 前端 src | 22,006 行（含 spec 约 7,000） | 8 个 `Admin*.vue` 合计约 6,010 行 |
| 测试 | pytest 787 / vitest 532 / 契约 72（双端 72-0；CHECK_ADMIN 档 285-0，2026-10-18 更新） | 后端覆盖率 **92.55%**，门槛 91（P1-45，`.coveragerc`） |
| 迁移 | 28 个版本（0001 ~ 0028） | `alembic check` 当前无漂移 |
| mock 后端 | `server.mjs` 3,007 行 | **后端逻辑的第二实现**（含昵称引擎副本） |
| 文档 | README 102KB / 优化落地记录 139KB | 单文件体积已影响可检索性 |
| CI | 前端 依赖审计+type+lint+format+test+build+storybook；后端 import+pytest(+覆盖率门槛)+启动+契约+迁移校验 | **不含前端覆盖率**（F4'）、**不含 docker build**（P1-44 已登记） |

---

## 2. P0：已确认的错误行为（建议立即处理）

| # | 问题 | 证据 | 影响 | 建议方向 | 成本 |
| --- | --- | --- | --- | --- | --- |
| A1 | **消息越权读取（IDOR）**：`mark_read` 未校验可见性即返回消息全文 | `messages.py:108-128`（`db.get` 后直接 `to_out`，用的是 `get_current_user_optional`，与 docstring「需登录」不符） | 任意用户凭消息 id 可读他人定向消息；种子 id 形如 `m1..m4` 可直接枚举 | 改用 `get_current_user`；取出后先按 `visible_condition(user)` 校验，不可见一律 404 | **S** · ✅ **P1-25 已处置** |
| A2 | **消息列表排序与游标按随机 id** | `messages.py:71-72`（`Message.id < cursor`、`order_by(Message.id.desc())`），id 为 `msg_{uuid4hex}` | 排序与真实时间无关，keyset 游标语义失效，最旧的消息可能排在最前 | 增 `created_at` 列（迁移），改按 `(created_at DESC, id DESC)` 排序与分页 | **S** · ✅ **P1-29 已处置**（游标格式不变，服务端回查锚点键；存量行不编造时间，coalesce 兜底沉底） |
| A3 | **时间基准不统一**：运行时用本地 naive 时间，抓取/种子写 UTC aware | `models.py:26`（`datetime.now().isoformat()`）、`feeds/ingest.py`、`seed.py` | 同一列混存两种口径，字符串排序即时间序的契约被破坏；`ranking._to_naive_utc` 只是在读侧打补丁 | 短期：`now_str()` 统一为 UTC aware 并回填存量；长期见 D1 | **M** · ✅ **P1-33 已处置**（实为三种口径；`now_str`+`parse_dt` 双入口、比较侧全量排查、迁移 0027 只补偏移不改数字） |
| A4 | **前端列表状态跨视图共享且无竞态守卫** | `stores/news.ts:20,40`（单一 `list` + `currentChannel`）、`Videos.vue:29` | 切频道与 `loadMore` 并发时旧频道数据被追加进新列表；视频页与首页串页 | 引入递增 `seq` / `AbortController`；按频道分桶 `listByChannel` | **M** |
| A5 | **搜索「加载更多」整段替换结果** | `SearchResults.vue:41`（`results.value = res.list`） | 用户永远只能看到最后一页；按钮文案与实际行为不符 | `page === 1` 时赋值、否则追加并按 id 去重；改用 `hasMore` 判断终止 | **S** · ✅ **P1-26 已处置** |
| B1 | **回复预览把全部回复捞进内存** | `comments.py:144-160`：窗口函数算出 `rn` 但 `WHERE` 不能引用窗口列，Python 侧才 `if rn <= 2` 过滤 | 单页 20 条主评论若各带数百条回复 → 一次拉数千行，是评论页最陡的性能悬崖 | 改为 `LATERAL` / 子查询在 SQL 侧每组 `LIMIT 2`，或按 root 分批 `UNION ALL` | **S** · ✅ **P1-26 已处置**（子查询 + `aliased`） |
| E1 | **资讯无正文，详情页使用硬编码假正文** | `models.py` News 表仅 `title/summary/images`；`NewsDetail.vue:29-33` 的 `body` 常量 | 演示文案冒充真实内容，一旦被用户/评审发现即信任崩塌；搜索也只能覆盖标题+摘要 | 抓取层存正文（`content` 列 + 文本清洗），详情页渲染真实正文，无正文时显式显示「暂无正文」而非占位文案 | **M** · ✅ **P1-28 已处置**（迁移 0025 + 段落清洗/截断 + 详情接口下发 `content`） |
| F1 | **CI 不含 `alembic check`** | `.github/workflows/ci.yml` | P1-24 已因「新迁移未 upgrade 导致 uvicorn 起不来」踩坑；CI 缺这一道就会继续漏 | CI 增加 `alembic upgrade head && alembic check` 步骤 | **S** · ✅ **P1-27 已处置**（check 须跑在 PG 上，SQLite 会误报） |
| F5 | **审查发现无跟踪机制** | 本轮复核：10-11 的 11 项 High 中 7 项仍存活 | 审查产出无法闭环，债务只增不减 | 建 `docs/review/backlog.md`，每个功能批次固定偿还 1~2 项债务 | **S** · ✅ **P1-32 已处置**（backlog.md 建立：开放项 31 项 + 偿还规则；审查报告自身转为历史快照） |

---

## 3. P1：正确性与扩展成本

### 3.1 性能

| # | 问题 | 证据 | 建议 | 成本 |
| --- | --- | --- | --- | --- |
| B2 | 公开列表默认路径做全表 `count()` | `news.py:340`（cursor 模式已免 count，offset 模式仍 count） | 前端默认走 cursor（当前 `api/news.ts` 已支持）；或缓存/估算 total | S |
| B3 | `for-you` 每次请求执行 2 次 JOIN 聚合算频道偏好 | `ranking.py:191-221` | 缓存 5~10 分钟（Redis 已用于限流，可直接复用），收藏/浏览变更时失效 | S |
| B4 | 图片直连源站、无尺寸/格式协商、外链失效即裂图 | `NewsCard.vue`（`loading="lazy"` 已有） | 图片代理 + 缩略图尺寸参数 + WebP 转码；失败回退占位（现有 `bgFor` 可复用） | M |
| B5 | 抓取与 API 同进程、共用线程池与连接池 | `scheduler.py:77`（`run_in_executor`） | 中期拆独立 worker 进程（或独立 cron 容器），避免长抓取挤压请求侧连接 | M |
| B6 | 热度重算全表扫描 | `ranking.py:164` | 已有 `only_stale` 缓解，仅在大表量级下需关注 | — |

### 3.2 安全

| # | 问题 | 证据 | 建议 | 成本 |
| --- | --- | --- | --- | --- |
| C2 | 登录失败仅按 IP 限流，无账号维度锁定 | `main.py:228`（`login:<ip>`） | 增账号维度失败计数 + 指数退避/临时锁定（可用现有 Redis 限流器） | S · ✅ **P1-30 已处置**（账号维度 5 次/15 分钟锁定；预检不计次防续期、不存在账号同计防空枚举、成功清零；无迁移） |
| C1 | access token 有效期 7 天且存 localStorage | `.env.example:30`（`JWT_EXPIRE_MINUTES=10080`）、`http.ts:24` | 拆 access（短）/ refresh（长、可吊销）；加 CSP；高敏操作二次校验 | **M** · ✅ **P1-38 已处置**（令牌双轨 + 类型校验 + 前端自动续期；CSP 与高敏二次校验拆为 **C9 / C10** 挂 backlog） |
| C4 | 无密码找回/重置通道 | `auth.py` 仅注册/登录/登出/续期 | 至少提供「管理员代重置 + 强制改密」或接入短信验证码 | M |
| C8 | CI 无依赖漏洞审计 | `ci.yml` | 加 `pip-audit` / `npm audit --audit-level=high` | S · ✅ **P1-31 已处置**（两 job 各加一道；pip-audit 前置升级 pip 防「红在与项目无关的包上」；本地干净 venv 复验零发现） |
| C6 | 管理端鉴权为逐端点调用（34 端点 / 35 次调用） | `admin.py:141` `_require_admin` | 改 router 级 `dependencies=[Depends(require_admin)]`，杜绝新增端点漏判 | S |
| C5 | `/api/health`、`/api/metrics` 无鉴权 | `main.py:301,329`（暴露池状态、Top20 路径分布） | 内网暴露或加 Basic Auth | S |
| C7 | 命中日志留存用户原文片段，无保留期限 | `models.py:491` `snippet` | 定保留期（如 90 天）+ 清理任务 + 最小可见范围（合规） | S |

### 3.3 架构与技术债

| # | 问题 | 证据 | 建议 | 成本 |
| --- | --- | --- | --- | --- |
| D2 | `admin.py` 单文件 1,640 行 / 34 端点，跨 6 个业务域 | — | 拆 `routers/admin/{sources,news,comments,words,nickname,users,audit}.py`，共享 `_require_admin`/`record_audit` | M |
| D3 | 8 个 Admin 页面约 6,010 行，`load/error/denied/toast/筛选/分页` 样板逐页复制 | 如 `AdminNicknameWords.vue:51-75` | 抽 `useAdminGuard` + `usePagedList` composable + 统一 `<AdminTable>`/`<FilterBar>`；预计可减 30~40% 代码量 | M |
| D1 | 时间一律存 `String(30)` ISO 字符串 | `models.py` 全表 | 改 `TIMESTAMP`/`DateTime(timezone=True)`：排序由数据库保证、可用时间函数、索引更紧凑。需一次性迁移 + 双写灰度 | L |
| D8 | 无 Docker / 部署编排 / 迁移前置钩子 | 仓库无 Dockerfile | Dockerfile + compose；启动前 `alembic upgrade head`（直接消除 P1-24 那类环境债） | S |
| F3 | 测试分布倒挂：11 个用户侧视图无 spec，8 个管理页全覆盖 | `src/views` 仅 Admin* 与 Profile 有 spec | 优先补 `HomeFeed / NewsDetail / Comments / Login / SearchResults` 五个核心路径 | M |
| F2 | 无覆盖率门槛与趋势 | CI | 设 `--cov-fail-under` 下限并在 CI 出趋势（不追求高数字，先防下跌） | S |

---

## 4. P2：长期演进与能力扩展

| # | 方向 | 说明 | 成本 |
| --- | --- | --- | --- |
| D4 | **消除 mock 第二实现** | `server.mjs` 3,007 行镜像后端（含昵称引擎副本），每新增一批功能就是双份实现 + 双份漂移风险。两条路：① 契约测试改为「真后端 + 种子数据」（推荐，单一真源）；② 由 OpenAPI 生成 mock | L |
| D6 | Redis 升级为二级缓存层 | 限流已支持 Redis；扩展至频道偏好、热搜词、热门列表、词库指纹 | M |
| D7 | 领域事件 outbox | 消息通知 / 审计 / 热度 bump 目前散落在路由里内联调用，改为事件表 + 异步消费，降低写路径耦合 | L |
| E2 | 全文检索与搜索联想 | `pg_trgm` 已就绪；补 `content` 后可做全文召回，并用 `similarity` 做「你是不是想搜」纠错与联想 | M |
| E5 | 用户举报入口 | 当前内容治理只有机器审核 + 运营后台，用户侧无反馈通道 | S |
| E3 | 负反馈（不感兴趣 / 减少此类）+ 曝光去重 | 个性化现在只有正向 boost，无法纠偏 | M |
| E4 | 多源转载去重 | 同源稿件多源入库会重复出现；已有 `similarity` 能力可直接做标题聚类 | M |
| E6 | 评论排序（最热/最新）、关注作者 | 互动深度不足 | S |
| E7 | 运营数据看板 | 把 `/api/metrics`、源健康、审核量、命中趋势做成管理端大盘页 | M |
| E8 | PWA / 离线阅读 | 收藏已服务端同步，具备离线基础 | M |
| E9 | a11y 与 i18n | 当前无系统性支持 | L |
| A6 | 导航/底栏硬编码色值（深色模式图标近乎不可见） | `AppNav.vue:47,90-135`、`TabBar.vue:58` | 改 `currentColor` + CSS 变量 | S · ✅ **P1-27 已处置**（含 `Search.vue` / `Profile.vue` 同类项） |
| C3 | 手机号未验证即可注册 | `auth.py:51` 仅校验 `1\d{10}` 形态 | 接入短信验证码（与 C4 同一条链路） | M |
| F4 | 文档体积失控 | README 102KB、落地记录 139KB | 按专题拆分到 `docs/`（接口/运维/治理/前端），主文档只留索引 | S |

---

## 5. 建议实施路线

**批次 A（止血 · 约 1 周）** — 目标是消灭「用户/攻击者可观测的错误」：
A1 消息越权 → A2 消息排序 → A5 搜索追加 → A6 色值 → B1 回复预览 → C2 账号锁定 → C8 依赖审计 → F1 CI 加 `alembic check` → F5 建债务 backlog。
验收：契约全绿 + 上述 9 项各有对应测试用例。
（✅ 批次 A 已全部处置：A1 / A2 / A5 / A6 / B1 / C2 / C8 / F1 / F5。
剩余开放项的跟踪与偿还顺序见 `backlog.md`。）

**批次 B（契约与结构 · 2~3 周）** — 目标是让下一个功能批次更便宜：
A3 时间基准统一 → A4 前端列表分桶与竞态守卫 → C1 token 拆分 → C4 找回通道 → C6 管理端鉴权收敛 → D2 admin 路由拆分 → D3 前端 Admin 抽象 → E5 举报。
（E1 正文落地已提前至 P1-28 完成。）
验收：Admin 页面代码量下降 30%+，新增一个管理实体只需 1 个新文件而非复制一个页面。

**批次 C（能力扩展 · 按需）**：D1 时间列类型改造、D4 消除 mock 双实现、D6 缓存层、E2/E3/E4 检索与个性化、F3 用户侧视图补测、D8 容器化。

---

## 6. 需要你决策的两个取舍

1. **时间列改造（D1）**：改 `TIMESTAMP` 是一劳永逸，但要一次性迁移 + 双写灰度，且会牵动所有排序/游标/清理路径；只做「统一 UTC aware 字符串」（A3）成本低得多，但保留了「字符串排序即时间序」这个需要靠注释维持的隐性契约。**建议先做 A3，在 D1 之前积累一次跨时区的线上验证。**

2. **mock 后端（D4）**：继续镜像意味着每个功能批次都是双份成本与双份漂移风险（P1-24 已出现「mock 静默失效、只有契约能暴露」的情况）；改为真后端 + 种子数据则契约检查要承担起数据准备职责。**建议下一个批次起，契约检查默认跑真后端，mock 仅保留给无数据库环境的快速预览。**

---

## 附录：审查方法与覆盖

- 后端：`main.py`、`config.py`(节选)、`models.py`、`schemas.py`、`security.py`、`deps.py`、`database.py`、`ratelimit.py`、`ranking.py`、`scheduler.py`、`routers/{auth,news,comments,me,messages,admin}.py`、`feeds/*`、迁移 0001~0024 链。
- 前端：`api/*`（含 `http.ts` 契约处理）、`router/index.ts`、`stores/*`、`views/*`（20 个视图）、`composables/*`、`vite.config.ts`、`package.json`。
- 流程：`.github/workflows/ci.yml`、`scripts/contract-check.mjs`、`.gitignore` 三处、`git log` 提交节奏。
- 历史对照：逐条复核 `docs/review/open-code-review-2026-10-11.md` 的 11 项 High 在当前代码中的存活状态（结论：7 项仍存活、1 项已修复、余项已部分改进）。
- 未覆盖：真实 PG 上的压测数据、生产配置实测值、管理端页面的真机交互验证。
