# 明日头条 · 后端 API（FastAPI + PostgreSQL / SQLite）

为「明日头条」Vue 前端提供资讯 / 评论 / 认证接口。响应统一为 `{ code, message, data }`（`code: 0` 为成功），与前端 `src/api/http.ts` 的解包约定一致。

## 快速开始

> **数据源默认 PostgreSQL**（本地 `toutiao` 库），SQLite 仅作为迁移来源/回退保留。

```bash
# 1) 安装依赖（建议用虚拟环境）
python -m venv .venv
.venv/Scripts/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) 配置环境（复制示例并按需修改）
copy .env.example .env        # Windows；macOS/Linux: cp .env.example .env

# 3) 初始化数据库（schema 由 Alembic 单轨管理，必须先执行）
alembic upgrade head

# 4) 启动（默认 http://127.0.0.1:8000）
uvicorn main:app --reload --port 8000
```

启动时会写入种子数据（6 条示例资讯 + n4 的 3 条评论 + 4 条消息 + 1 个演示账号，
由 `SEED_SAMPLE_DATA` 控制，生产默认关闭）。**注意：启动不再自动建表**，
表结构由第 3 步的迁移负责；缺表时会报错并提示执行 `alembic upgrade head`。

- 交互式接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：`GET /api/health`
- 连接信息见 `.env` 的 `DATABASE_URL`；演示账号见 `DEMO_PHONE` / `DEMO_PASSWORD`

## 从 SQLite 迁移数据到 PostgreSQL

若已有 SQLite 库（`toutiao.db`），用迁移脚本全量搬运（**幂等，可重复执行**，按主键 upsert）：

```bash
python scripts/migrate_sqlite_to_pg.py \
  --source ./toutiao.db \
  --target "postgresql+psycopg://toutiao:***@127.0.0.1:5432/toutiao"
```

脚本会：建表（PG 上 JSON 字段自动落为 `JSONB`）→ 逐表 upsert → 打印并校验行数一致。`images` JSON 字段会自动解析转换；`comment_likes` 关系表随表搬运，旧库残留的 `comments.liked_by` JSON 数组会**去重、跳过悬空/非整数 user_id 后回填为关系行**（0014 起该列已从模型与迁移中移除）。SQLite 源库不会被修改，随时可回退。

## 与前端联调

```bash
# 终端 1：启动本后端
uvicorn main:app --reload --port 8000

# 终端 2：前端以 api 模式启动（自动读取 mingri-toutiao-vue/.env.api）
cd ../mingri-toutiao-vue
npm run dev:api
```

契约自检脚本（复刻前端 `api/http.ts` + `api/news.ts` 逻辑，校验字段/错误码/鉴权）：

```bash
node scripts/contract-check.mjs
```

## 生产化配置

通过环境变量切换（见 `.env.example`）：

| 变量 | 说明 |
| --- | --- |
| `ENVIRONMENT` | `development`（默认，放开 Swagger / CORS `*`）\| `production`（收紧） |
| `JWT_SECRET` | **生产必填**。未设置或仍为默认值时，production 模式**启动即失败**（fail-fast），避免用弱密钥上线 |
| `DATABASE_URL` | **PostgreSQL**：`postgresql+psycopg://user:pass@host:5432/toutiao`（默认）；需回退 SQLite 时改为 `sqlite:///./toutiao.db` |
| `CORS_ORIGINS` | 逗号分隔的允许来源。production 默认**同源**（不跨域），需显式配置 |
| `RATE_LIMIT_*` | 限流阈值，见下|
| `TRUSTED_PROXIES` | 逗号分隔的受信代理网段（IP 或 CIDR，如 `10.0.0.0/8,127.0.0.1`）。**仅当直连对端命中该网段时**才采信 `X-Forwarded-For`；留空表示不信任任何代理头，防止伪造 XFF 绕过限流 |
| `ADMIN_PHONES` | 管理能力白名单（逗号分隔）：`/api/news/refresh`、`/api/admin/*` 与登录响应 `isAdmin` 均按 **`ADMIN_PHONES` ∪ `admin_users` 表** 判定（P1-14）。env 保留为 break-glass（重启即生效）；日常增删管理员在应用内「管理员管理」页操作，无需重启。两边都为空 = 该能力整体禁用（403）。**生产建议至少保留一条**：DB 故障 / 白名单行被误删 / 迁移回滚都可能让库白名单整体失效，届时 env 是唯一入口（缺失时启动日志告警、`/api/health.adminBreakGlass=false`、管理员页红色提示） |
| `SEED_SAMPLE_DATA` | 是否播种示例数据（演示账号 + 示例资讯/评论/消息）。**生产默认 `false`**，测试建议置 `false` 保证确定性 |
| `LOG_LEVEL` | 访问日志级别 |

production 模式下自动生效的加固项：

1. **强制 `JWT_SECRET`**：默认值直接 `RuntimeError` 拒绝启动。
2. **关闭接口文档**：`/docs`、`/redoc`、`/openapi.json` 全部 404。
3. **CORS 收紧**：默认同源，避免 `*` 带来的越权风险。
4. **限流 + 访问日志**：见下。
5. **不播种演示账号**：`SEED_SAMPLE_DATA` 默认关闭，避免弱口令默认账号上线。

生产启动示例：

```bash
export ENVIRONMENT=production
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export DATABASE_URL='postgresql+psycopg://toutiao:***@localhost:5432/toutiao'
export CORS_ORIGINS='https://your-domain.com'
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

> ⚠️ 限流为**进程内**实现，多 worker 时每个进程各算各的；多实例部署请把 `app/ratelimit.py` 换成 Redis 等共享实现。

## 限流与日志

- **限流**：按**客户端 IP** 固定窗口计数（登录类单独一类，阈值更严）。
  - `RATE_LIMIT_PER_MINUTE`（默认 120）、`LOGIN_RATE_LIMIT_PER_MINUTE`（默认 10）、`RATE_LIMIT_WINDOW_SECONDS`（默认 60）
  - 超限返回 `429` + `Retry-After` 头，错误码 `42900`（普通）/ `42901`（登录）
  - **不按路径分桶**：所有非登录接口共用同一 IP 计数，避免轮换路径绕过总量限制
  - **XFF 采信受 `TRUSTED_PROXIES` 约束**：仅当直连对端在受信网段内才读取 `X-Forwarded-For` 首段，否则用 socket 对端地址（防伪造）
- **访问日志**：`[请求ID] 方法 / 路径 / 状态码 / 耗时(ms) / 客户端 IP`；响应头附
  `X-Process-Time-Ms` 与 `X-Request-ID`（若请求已带则原样透传，便于全链路追踪）；未捕获异常会连同堆栈记录。
- **指标**：`GET /api/metrics` 暴露 Prometheus 文本指标（进程内；多实例请在网关侧聚合）。

启动横幅会打印当前模式与加固项状态：

```
启动完成 | env=production | docs=关闭 | rate_limit=on(120/min, login 10/min) | cors=同源 | trusted_proxies=2 | admins=1
```

## 连接池

PostgreSQL 走 SQLAlchemy `QueuePool`，参数全部可用环境变量覆盖（见 `.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DB_POOL_SIZE` | 5 | 常驻连接数 |
| `DB_MAX_OVERFLOW` | 10 | 峰值可额外创建的连接；**峰值并发 = 两者之和** |
| `DB_POOL_TIMEOUT` | 30 | 等待可用连接的最长秒数，超时抛 `TimeoutError` |
| `DB_POOL_RECYCLE` | 1800 | 连接最大存活秒数，**应小于 PG 的 `idle_timeout`**，防静默断开 |
| `DB_POOL_PRE_PING` | true | 取连接前探活，避免把死连接交给业务 |
| `DB_CONNECT_TIMEOUT` | 10 | 建连超时秒数 |
| `DB_APP_NAME` | toutiao-api | 便于在 `pg_stat_activity` 中定位本应用 |

要点：
- **LIFO 回收**（`pool_use_lifo`）：让闲置连接尽早释放，减少 PG 侧 idle 连接数。
- **异常回滚**：`get_db()` 在异常时先 `rollback()` 再归还连接，避免未提交事务污染复用。
- **优雅关停**：`shutdown` 钩子调用 `engine.dispose()` 释放全部连接。
- **可观测**：`GET /api/health` 返回 `pool` 状态（`size` / `checked_in` / `checked_out` / `overflow`），可直接判断池是否打满或泄漏。

```bash
# 观测连接池
curl -s http://127.0.0.1:8000/api/health
# {"code":0,"data":{"status":"up","pool":{"dialect":"postgresql","size":5,"checked_in":1,"checked_out":0,"overflow":-4}}}

# 在 PG 侧查看本应用的连接
psql -c "select pid, state, application_name from pg_stat_activity where application_name='toutiao-api';"
```

> ⚠️ **worker 数 × 池大小不要超过 PG 的 `max_connections`**。例如 `--workers 4` 且 `DB_POOL_SIZE=5` 时，最多可达 4×15=60 条连接。

## 接口清单

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | — | 健康检查；返回 `{ status, pool, adminBreakGlass }`——`adminBreakGlass` 是**应急通道是否配置**的布尔标记（只下发真假、不下发号码） |
| GET | `/api/metrics` | — | Prometheus 文本指标（uptime / 请求数 / 在途 / 状态码 / 路径 / 连接池） |
| POST | `/api/news/refresh` | ✅ 管理员 | 手动触发抓取（`?channel=&keyword=`）。需管理能力（`ADMIN_PHONES` ∪ `admin_users` 表），**异步执行**返回 202 |
| GET | `/api/news/search?q=` | — | 服务端搜索（标题/摘要匹配，**按相关度排序**见「搜索与热词」），返回 `{ list, total, page, hasMore }`；**旁路记录**搜索词供热词统计 |
| GET | `/api/news/search/hot?limit=&days=` | — | 热搜词（近 `days` 天真实统计），返回 `{ list: [{ term, count }], days }`；无数据时为空数组 |
| POST | `/api/auth/register` | — | 注册（手机号 + 密码 ≥6 位），返回 token |
| POST | `/api/auth/login` | — | 登录，成功返回 `{ token, user }`；`user` 含 `isAdmin`（布尔值，按 `ADMIN_PHONES` ∪ `admin_users` 表白名单判定后下发，前端据此决定是否展示管理入口，**不下发白名单本身**） |
| POST | `/api/auth/logout` | ✅ | 退出（无状态 JWT，前端清本地 token） |
| GET | `/api/news?channel=&sort=&page=&pageSize=&cursor=` | — | 资讯列表。**推荐游标分页**：传 `cursor` 走 keyset，返回 `{ list, nextCursor, hasMore }`；不传则 offset 分页并附 `total`。`sort` 取 `latest`（默认）/ `hot` / `for-you`（见「排序与个性化」），非法值 422 |
| GET | `/api/news/{id}` | — | 资讯详情 |
| GET | `/api/news/{id}/comments?limit=&cursor=` | —（可选带token） | **主评论**列表（`root_id IS NULL`，游标分页，**新的在前**）；每条附 `replyCount` 与最早 2 条回复预览；带 token 时 `liked` 按当前用户计算 |
| GET | `/api/news/{id}/comments/{cid}/replies?limit=&cursor=` | —（可选带token） | 某条主评论的**回复**列表（时间**正序**，游标分页；root 已软删除时返回空列表——主评论消失后失去语境的回复不再裸露） |
| POST | `/api/news/{id}/comments` | ✅ | 发表评论；带 `parentId` 时为**回复**（被回复评论须存在且未删除，否则 40003；回复的回复拍平到同一 root，`parentId` 仅用于展示「回复 @谁」） |
| PATCH | `/api/news/{id}/comments/{cid}` | ✅ | 点赞 / 取消点赞（`comment_likes` 关系表按用户去重，幂等） |
| GET | `/api/messages?limit=&cursor=` | —（可选带 token） | 消息中心列表（游标分页，返回 `{ list, hasMore, nextCursor }`） |
| GET | `/api/messages/unread-count` | — | 未读数（给 TabBar 徽标；关联表索引计数，不全表遍历） |
| PATCH | `/api/messages/{id}` | —（可选） | 标记单条已读（写入 `message_reads` 关联表，幂等） |
| POST | `/api/messages/read-all` | —（可选） | 全部标记已读 |
| GET | `/api/me/favorites` | ✅ | 我的收藏（按收藏时间倒序），返回 `{ ids, list }` |
| POST | `/api/me/favorites` | ✅ | 收藏一条资讯（幂等；资讯不存在 404） |
| PUT | `/api/me/favorites` | ✅ | **并集合并**（登录时把本地收藏并入云端，只增不删） |
| DELETE | `/api/me/favorites/{newsId}` | ✅ | 取消收藏（幂等） |
| GET | `/api/me/history` | ✅ | 浏览历史（按浏览时间倒序，最多 50 条），返回 `{ ids, list }` |
| POST | `/api/me/history` | ✅ | 记录浏览（同一资讯只刷新时间；资讯不存在时静默忽略） |
| PUT | `/api/me/history` | ✅ | **并集合并**本地历史，随后按上限裁剪 |
| DELETE | `/api/me/history` | ✅ | 清空浏览历史 |
| GET | `/api/admin/sources` | ✅ 管理员 | RSS 源列表（含抓取健康状态、`respectRobots` 开关与**近期健康摘要**），返回 `{ list, total, healthDays }`；读取前自动播种内置源。`list[].health` 为 `{ attempts, rate, avgItems }` 或 `null`（窗口内没抓过），`rate` 为 `null` 表示**没有尝试**而非"全失败" |
| GET | `/api/admin/sources/{key}/health?days=` | ✅ 管理员 | **单源健康趋势**（P1-19）：返回 `{ key, days, keepDays, summary, points, failures }`。`days` 会被收敛到保留期；`points` 按天分桶且**缺的天不补零**（空白 = 那天没抓它）；`failures` 只列失败并带 `kind` |
| POST | `/api/admin/sources` | ✅ 管理员 | 新增源；`key` 冲突 409、非 http(s) 地址 400 |
| PATCH | `/api/admin/sources/{key}` | ✅ 管理员 | 局部更新（名字/频道/地址/权重/启停/是否遵守 robots），未传字段保持不变；不存在 404 |
| DELETE | `/api/admin/sources/{key}` | ✅ 管理员 | 删除源；不存在 404 |
| POST | `/api/admin/sources/reset` | ✅ 管理员 | 重置为内置源（丢弃自定义源，用于恢复出厂配置） |
| GET | `/api/admin/users` | ✅ 管理员 | 管理员白名单（`admin_users` 表），返回 `{ list, total, envAdminCount }`；行含 `registered`（已注册/预授权）与 `addedBy`（审计） |
| POST | `/api/admin/users` | ✅ 管理员 | 添加管理员（支持**预授权**：手机号无需先注册）；已在任一来源 40903、形态非法 422 |
| DELETE | `/api/admin/users/{phone}` | ✅ 管理员 | 移除管理员；不存在 40403；**末位保护**：移除后任何来源都不再有管理员时 40904 |
| GET | `/api/admin/audit?limit=&cursor=&target=` | ✅ 管理员 | **运营审计时间线**（P1-15）：返回 `{ list, hasMore, nextCursor }`，新的在前；游标为自增 `id`，可 `target` 过滤（"这个对象被谁动过"） |

> ⚠️ `/api/news/search` 必须声明在 `/api/news/{news_id}` 之前，否则会被当成 id 匹配。
> 📌 统一错误契约：所有错误（含 422 参数校验）均返回 `{ code, message, data }`；错误码见文末。

## 新闻抓取（RSS）

内置**免费公开 RSS 抓取**，无需任何 API Key，默认每 30 分钟自动执行。

```bash
# 手动执行
python scripts/fetch_news.py --list-sources        # 查看源清单
python scripts/fetch_news.py                       # 全量抓取
python scripts/fetch_news.py --channel finance     # 按频道
python scripts/fetch_news.py --keyword 暴雨# 按关键词
python scripts/fetch_news.py --prune               # 抓取 + 清理过期

# 手动触发接口（需管理员 token；异步执行，立即返回 202）
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800138000","password":"123456"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
curl -X POST "http://127.0.0.1:8000/api/news/refresh?channel=tech" \
  -H "Authorization: Bearer $TOKEN"
```

**能力**：多源并发 · 指数退避重试 · 按 id 幂等 upsert（增量更新）· 时效清理 · 空数据兜底 · 图片自动提取与卡片变体自动判定。

**完整字段映射表与故障排查**见 [`docs/news-fetch.md`](docs/news-fetch.md)。

> 源说明：人民网/新华网的 RSS 为历史存档（时间戳较旧），**中新网为实时源**（分钟级）。若产品需要最新资讯，建议以中新网为主力源。
> 另含 **AIHOT**（`https://aihot.news/feed.xml`，AI 垂直资讯，单源约 20–50 条），归入 `tech` 频道。

### 源可配置化（迁移 0013）

源原先**硬编码**在 `app/feeds/sources.py`，新增/停用/调权重都要改代码重新部署。现在：

- **实际抓取读库**（`rss_sources` 表），运营通过 `/api/admin/sources` 增删改；
- `BUILTIN_SOURCES` 退化为两个用途：**播种**（表为空时写入，新环境开箱可用）与
  **回退**（无数据库会话时，单测/脚本仍可运行）；
- **只在表为空时播种**——否则运营的停用/调权重会被下次启动覆盖；
- **健康状态回写**：每轮抓取把 `last_status / last_error / last_item_count / last_fetched_at`
  写回源表。源变多后"哪个源挂了"是首要排障问题，靠翻日志很低效；
- **URL 协议白名单**（仅 http/https）：抓取器会真的请求该地址，必须挡住 `file://` 之类；
- **频道白名单校验**：`channel` 必须是 `CHANNEL_KEYS`（与前端 7 个频道一致）之一，否则 422。
  拼错的频道（如 `tecn`）会让源匹配不到任何频道，而"频道无专属源时回退全部"的兜底
  会把它静默投递到**每一个**频道——校验是兜底逻辑的必要配套；
- **停用优先于删除**：删除能力保留（加错了源时确实需要），但默认建议停用。

**抓取失败必须可辨识**：`fetch_source()` 失败时抛 `FetchError` 而**不是**静默返回空列表——
空列表无法区分"源不可达"与"源正常但没有新条目"，健康状态就永远是 ok，源悄悄烂掉也无人发现。
`_fetch_with_retry()` 重试仍失败则向上抛，由 `collect()` 记为该源故障（`ok=False` + 原因），
**单个源失败不影响其他源**，整轮抓取也不会因此抛错。`NotModified`（304，内容未变更）
是**唯一**返回空列表的正常路径。

**管理判定单一来源**：`admin.is_admin(phone, db)` 是唯一判定（**`ADMIN_PHONES` ∪ `admin_users` 表**，两边都为空 = 整体关闭），
`/api/news/refresh`、`/api/admin/*` 与**登录/注册/续期响应里的 `isAdmin` 字段**共用它——
曾出现 `main.py` 自己复制一份 `if not ADMIN_PHONES or ...` 判断，两处逻辑迟早漂移。

### 运营审计（迁移 0016）

权限体系回答「谁能进后台」，审计回答「**谁干了什么**」——源被停用 / 换地址 / 删除、
管理员被加入或移除，此前全部无痕迹，多管理员共用后台时事后只能靠猜。

- **动作命名**：`对象.动作`（`source.create` / `source.update` / `source.delete` /
  `source.reset` / `admin.add` / `admin.remove`），前端据此映射中文标签；
- **明细**：更新记字段级 `changes`（旧→新），**只记真正变化的字段**（前端表单常整体提交，
  全量记录会灌满时间线）；增删记 `snapshot`（删完行就没了，快照必须在删除前取）；
- **同事务写入**：`record_audit()` 不提交，由调用方与业务变更一起 commit——
  「改成功了但没留痕」比「改失败」更糟，故刻意不做 try/except 静默；
- **游标用自增 `id` 而非时间戳**：`created_at` 是秒级精度，同秒多条会让 keyset 比较
  出现重复/遗漏（评论分页踩过同一个坑）。

### 管理员白名单（迁移 0015）

「指定账号才能访问后台」此前只由 `ADMIN_PHONES` 环境变量承载——加/撤一名管理员要改部署配置
并重启服务，运营无法自助。现在白名单落入 `admin_users` 表，应用内（「管理员管理」页）可增删：

- **双源并集**：env 保留为 break-glass（改配置重启即生效、不依赖库），库白名单供日常运营
  （增删即时生效）；两边都为空 = 管理能力整体关闭，生产安全语义不变；
- **预授权**：手机号即主键且不设 `users` 外键——账号尚未注册也可先加入名单，其注册/登录时
  `isAdmin` 即为 true；列表行带 `registered` 区分「已注册 / 预授权」；
- **末位保护**：移除后若任何来源都不再有任何管理员，返回 40904——否则生产环境可能把自己
  锁在门外，只能改环境变量重启来恢复；
- **审计**：`added_by` 记录操作者手机号，回答「谁能授权」的追问；
- **应急通道提示（软提示，不阻断启动）**：生产环境 `ADMIN_PHONES` 为空时，
  启动日志告警、`/api/health.adminBreakGlass` 为 `false`、管理员页整张说明卡转红色——
  库白名单是完整的日常通道，缺的只是"数据库不可用时仍能进后台"这条兜底路径。

### 抓取链路治理（迁移 0017）：并发化 + 礼貌抓取 + 健康告警

抓取链路此前是**串行 + 退避重试**：源变多后一轮耗时线性增长。但"直接开并发"会踩到另一个
问题——N 个源同时打出去，等于把压力转嫁给对方站点。所以这两件事**必须成对落地**，
告警则是独立的一小块，搭同一班车（P1-16）。

**① 并发但不失序**：`collect()` 改成两阶段——先线程池**并发抓取**，再按 `srcs`
（权重降序）**串行合并**。合并之所以不能并发：「同 id 保留先到的一条」是先到先得的语义，
若合并顺序随机化，"**多源转载同一稿件时权重高者先入库**"这条规则就失效了。同理
`stats`（健康状态回写顺序）也按权重序而非完成序，避免审计/列表顺序随网络快慢漂移。

**② 按 host 分组**：同站点的多个源归为一组、**组内串行 + 间隙 `FEED_HOST_GAP_SECONDS`**，
组间才并发。并发的意义是省掉等待时间，不是把同一个站点打 N 遍。

**③ robots.txt**（`respect_robots`，逐源可关）：
- **取不到就放行**（404 / 不可达 / 格式不认识）——大量站点不提供 robots.txt，
  把"没有声明"当成"禁止"会让抓取整体停摆；
- **按 host 缓存**（TTL `FEED_ROBOTS_TTL_HOURS`）：robots 是整站声明，同站多源共享一次解析
  （取 robots 本身就是一次请求）；缓存带 TTL，对方改规则最多滞后一个周期；
- **被禁记为异常**（不静默抓取）：`last_status=error`、原因写 `robots.txt 禁止抓取`，
  在源管理页可见；
- **逐源开关**：个别源 robots 写得过严（甚至整站 `Disallow`）却实际公开提供 RSS，
  一刀切会让这些源失去作用，故允许管理侧关掉——**这是主动破例，列表上以琥珀色
  「忽略 robots」标记可见**，避免悄悄累积成无人记得的特例。

**④ 单源最小抓取间隔**（`FEED_MIN_INTERVAL_MINUTES`）：定时任务与手动触发叠加时，
不重复打同一个源。**按礼仪跳过的源本轮不刷新健康状态**——刷新会让"多久没成功"永远归零。

> ⚠️ **入参的多词字段一律驼峰**（`respectRobots`，与 `CommentCreateIn.parentId` /
> `MarkIn.newsId` 同惯例，也与响应侧 `lastStatus` / `envAdminCount` 一致）。
> 写成蛇形会让 pydantic 按 `extra='ignore'` **静默丢弃**该字段——
> 「开关看得见、点得动，但永远不生效」，且前后端各自的单测都不会失败。
> 本批第一次就踩了这个坑（详见《优化落地记录.md》），已由
> `test_respect_robots_round_trips_through_api` 从 API 打到数据库焊死。

**⑤ 健康告警**（`app/feeds/alert.py`）：健康字段此前只落库，源悄悄烂掉没人被通知。
现在每轮抓取收尾检查一次：

- **判定**：`last_status=error`，或 `last_fetched_at` 为空 / 早于 `FEED_ALERT_STALE_HOURS`
  （后者覆盖"定时任务根本没跑到这个源"）。**只看启用中的源**——禁用的源不抓是预期行为；
- **收件人**：管理能力（`ADMIN_PHONES` ∪ `admin_users`）解析成**已注册**用户投递到消息中心；
  未注册（预授权）的管理员没有账号可收，跳过而不报错；
- **节流**：同一源两次告警至少间隔 `FEED_ALERT_MIN_INTERVAL_HOURS`，锚点落
  `rss_sources.last_alerted_at`（**落库而非进程内变量**，多实例共享窗口、重启不失效）；
- **无收件人时不推进锚点**：没通知到人就不该占用窗口，否则管理员一注册得白等一个周期；
- **旁路逻辑**：告警失败由调用方吞掉，绝不影响抓取结果。

**⑥ 告警去重聚合**（P1-17，阈值 `FEED_ALERT_AGGREGATE_MIN`，默认 3）：
断网或对方站点整体挂掉时十几个源会**同时**异常，逐源发一条就是把同一个坏消息重复 N 遍——
而收件人对 N 条独立消息也做不了逐个处置，他要的信息是「出事了、多少条、去看源管理」。
达到阈值即合并为**一条**汇总；低于阈值仍逐源直发（一两条时点开就是名字，比先读汇总省事）。

- **文案按原因分组而非罗列源名**：`抓取失败 3 条（A、B、C）；从未成功 2 条（D、E）`。
  原因分组本身携带了"这是不是系统性问题"——「超时未抓取 8 条」已经说明该去查定时任务
  或网络，不需要再补一句"建议排查网络"；
- **顺序即优先级**：分组按严重度（抓取失败 > 超时未抓取 > 从未成功），组内按 `weight`
  降序（与抓取合并的权重优先同一套语义，被列出来的名字就是最该先看的几个）；
- **数量放标题**：消息中心预览是 `white-space: nowrap` + 省略号，摘要尾部必然被截断，
  只有标题能稳定显示，所以「汇总（N 条）」挂在标题上；
- **截断后去重**：源名超过 10 字截成同一前缀时并列出来是纯噪音（常见于同一站点的多个
  频道），去重但**总量不变**；
- **两个分支都要推进节流锚点**：漏掉会让汇总消息每轮重发。

```bash
FEED_MAX_CONCURRENCY=4            # 并发抓取的线程数（默认 4）
FEED_HOST_GAP_SECONDS=1.5         # 同站点两次请求的最小间隔（秒）
FEED_MIN_INTERVAL_MINUTES=10      # 单源最小抓取间隔（分钟）
FEED_ROBOTS_TTL_HOURS=24          # robots.txt 缓存时长（小时）
FEED_ALERT_ENABLED=true           # 源健康告警开关
FEED_ALERT_STALE_HOURS=24         # 超过该时长未成功抓取即告警
FEED_ALERT_MIN_INTERVAL_HOURS=6   # 同一源的告警节流窗口
FEED_ALERT_AGGREGATE_MIN=3        # 待告警源达到该数量即合并成一条汇总
FEED_ALERT_AGGREGATE_LIST=5       # 汇总里每类原因最多列几个源名

FEED_RETRY_TRANSIENT=2            # 「可重试」失败的重试次数（默认 2，即最多 3 次尝试）
FEED_RETRY_BACKOFF=0.6            # 重试退避基数（秒，指数退避）
FEED_CIRCUIT_THRESHOLD=5          # 连续失败达此次数即熔断（0 = 不熔断）
FEED_CIRCUIT_COOLDOWN_MINUTES=30  # 熔断冷却时长（分钟）
FEED_AUTO_DISABLE_PERMANENT=0     # 连续「永久性」失败达此次数则自动禁用（0 = 从不自动禁用）
```

`GET /api/metrics` 暴露 `toutiao_sources_unhealthy`（异常 + 超时未成功计数；`-1` 表示
查询失败——用一个不可能的计数表达"未知"，而不是假装健康）。

### 失败分级与熔断（迁移 0018）

告警聚合（P1-17）解决了"N 个源同时挂"时的消息噪音，但它没解决更前置的一个问题：
**所有失败在此前被当成同一件事处理**。404 和超时被同等重试、同等计数、同等告警——
于是真正该做的两件不同的事（"等它自己恢复" 与 "去改配置"）都被含糊成了"再试一次"。

**① 分级**（`fetcher.FetchError.kind`）：

| 性质 | 触发 | 处置 |
| --- | --- | --- |
| `transient` 可重试 | 超时 / 连接重置 / 5xx / 429 | 退避重试，系统自己会好 |
| `permanent` 永久 | 4xx（401/403/404…）/ robots 拒绝 / 解析后无条目 | 重试无意义，需人工改配置或换源 |

> `kind` 必须作为**结构化数据**穿过线程池：异常被 `str(e)` 拍平后就没法再分类了，
> 而用元组返回会在半路丢掉它。所以它是 `FetchError` 的属性，一路带到 `record_fetch_result`。
>
> 判定的边界：`429` 归可重试（对方只是限流，等一会儿就能过），其余 4xx 归永久；
> 「解析成功但 0 条」归永久——拿不到条目的地址不会自己变好。

**② 只有可重试的才重试**：`_fetch_with_retry` 遇到 `permanent` 立即跳出，不再消耗剩余次数。
此前对 404 也重试 3 次，纯属把对方站点的错误日志刷一遍。

**③ 计数落库**（`rss_sources.consecutive_failures` / `failure_kind`）：进程内的计数器
多实例之间不共享、重启即清零，熔断阈值就永远判不准。成功一次即归零。

**④ 熔断**（`FEED_CIRCUIT_THRESHOLD`，默认 5）：连续失败达阈值后写
`next_retry_at`，**冷却期内整轮跳过该源**；到期自动恢复尝试，不需要人工干预。
与礼仪跳过同一条规则：**被跳过的源本轮不刷新健康状态**，否则"多久没成功"永远归零。

> 熔断的意义是"别再给一个正在故障的站点送请求"，不是"这个源不要了"。
> 所以它自愈、也不改 `enabled`——改 `enabled` 是运营决策，不能由抓取器代劳。

**⑤ 自动禁用默认关闭**（`FEED_AUTO_DISABLE_PERMANENT=0`）：
连续**永久**失败达阈值时可自动置 `enabled=False`，但**默认永不触发**。
一个源 404 一周就被静默关掉、且没人记得重新打开，比一直重试更糟；
"自动禁用"必须由运维主动选择。开启后**每次触发都写审计**（`source.auto_disable`，
`actor_phone="system"`，detail 带连续次数与最后错误）——
「谁把这个源关了」事后查不到是最难排查的一类问题，留痕是这项功能的准入条件。

**⑥ 告警带上性质**（P1-17 的分组在此扩展为 5 类）：

```
永久失败 4 条（旧源A、旧源B、旧源C、旧源D）        ← 等不会好，得去改配置
临时失败 3 条（新浪科技、cnBeta、虎嗅）            ← 系统会在冷却后自己重试
```

单源原因也区分对待：`连续 7 次失败：HTTP 404（永久性，需人工处理）` vs
`连续 3 次失败：ReadTimeout（可重试，系统会自动继续尝试）`。
老数据没有 `failure_kind` 时退化为 `连续 N 次失败`，不猜。

### 源健康历史与趋势（迁移 0019）

P1-16~P1-18 把「当前状态」做到了位，但那始终只是**一个点**：
`last_status` 只有"现在挂没挂"，`last_item_count` 只有"最近一次抓到几条"，
`consecutive_failures` 只有"连着挂了几次"。没有时间维度，这几个问题就都答不了：

| 问题 | 为什么当前字段答不了 |
| --- | --- |
| 它是今天才开始烂的，还是已经烂了两周？ | 没有历史，只有最后一次的结果 |
| 是不是每天凌晨固定抖？ | 单点看不出规律，只会以为是随机抖动 |
| 上次改了地址/权重，有好转吗？ | 改动前的基线没有留存 |
| **一直返回成功，但条数从 30 掉到 3 条？** | **它永远是绿的——结构性看不见** |

**① 存原始事件，不存每日聚合**（`rss_source_health_events`，每轮一行）。
理由与 `SearchLog` 同源：聚合表只能表达"历史累计"，事后想换个窗口（7 天 / 30 天）
重看就没数据了。代价是需要清理，见 ③。

**② 只记真实尝试**。按礼仪或熔断跳过的源本轮根本没发请求，
给它记一行会伪造出"尝试过"的痕迹——与「跳过的源不刷新健康状态」是**同一条口径**
（`run_ingest` 的 `finally` 里与 `record_fetch_result` 相邻，两处各自提交、各自吞异常）。
因此趋势图上的空白 = 那时没抓它，**不是失败**；前端据此铺格子，缺的天不补零
（补零会把"没抓"伪装成"抓了但全失败"）。

**③ 按时间清理，不设条数保底**（`FEED_HEALTH_KEEP_DAYS`，默认 30 天）。
与 `prune_old_news` 的 `keep_min` 刻意不同：健康历史是排障取证用的，
保底条数只会留下一堆最旧的噪音，而"最近 N 天"就是它的全部价值。
同样刻意不建 `at` 单列索引——清理查询在两万行量级上全表扫描只要几毫秒，
不值得为此拖慢每轮十几次写入；只建复合索引 `(source_key, at)`。

**④ `avgItems` 取成功轮次的平均**，不是全部轮次：失败轮天然 0 条，
混进来会让"条数退化"被失败率掩盖——而这正是本批要抓的第二类隐形故障。
`rate` 在无尝试时返回 **None 而不是 0**：`0` 会把"没抓过"伪装成"全挂了"，
运营会去排查一个根本没跑过的源。

**⑤ 列表只放摘要，趋势放单源**。`GET /api/admin/sources` 每行附 `health`
（`attempts` / `rate` / `avgItems`，一条 GROUP BY 查全部源，避免 N+1）与
`healthDays`；列表回答"哪个源值得点进去"，`GET /api/admin/sources/{key}/health`
才回答"**它到底怎么了**"——三段数据各有分工：

```
summary  窗口总览（成功率 / 成功轮均条数）  → 是抖动，还是持续恶化？
points   按天序列                            → 是不是每天固定挂（对方维护窗口）
failures 最近失败原文 + kind                 → 要不要去改 URL / 换源
```

`days` 由 `window_days()` 收敛到保留期：请求 90 天而只留 30 天，只会得到 60 个空格子。
响应回传实际生效的 `days` 与 `keepDays`，前端才能正确标注时间范围、
并向运营解释"为什么最多只能看这么久"。

**⑥ 环境变量**（默认 `30` / `7`）：

```bash
FEED_HEALTH_KEEP_DAYS=30   # 健康事件保留天数；过期即无意义，按时间删不保底
FEED_HEALTH_LIST_DAYS=7    # 列表页摘要窗口（短于趋势窗口：列表看"最近怎么样"）
```

## 数据库迁移（Alembic）

```bash
alembic upgrade head                # 应用全部迁移
alembic upgrade head --sql          # 只生成 SQL（不执行）
alembic revision --autogenerate -m "xxx"   # 模型变更后生成新迁移
alembic current                     # 当前版本
alembic history                     # 迁移历史
alembic check                       # 检测 models 与迁移是否漂移
```

> ⚠️ **schema 由 Alembic 单轨管理**：应用启动**不再**自动建表。
> 新环境必须先执行 `alembic upgrade head`，否则启动即报错并给出本提示。

- `migrations/env.py` 复用 `app.config.DATABASE_URL` 与 `Base.metadata`（**单一配置源**）。
- 已含迁移：`0001_init`（基线全量建表）、`0002_news_published_at`、`0003_msg_fk_set_null`、
  `0004_feed_order_reads`（复合索引 `ix_news_feed` + `comments.created_at` + `message_reads`）、
  `0005_news_search_trgm`（pg_trgm GIN 搜索索引）、`0006_user_token_version`、
  `0007_comment_user_id`（评论归属）、`0008_comment_soft_delete`（评论软删除）、
  `0009_message_recipient`（消息定向）、`0010_user_marks`（收藏 / 浏览历史云端化）、
  `0011_news_hot_score`（热度分落库与 `ix_news_hot` 索引）、`0012_search_logs`（搜索词日志）、
  `0013_rss_sources`（RSS 源可配置化 + 抓取健康字段与 `ix_rss_sources_channel_enabled`）、
  `0014_comment_likes_and_replies`（点赞关系表 `comment_likes` + 评论父子列
  `parent_id`/`root_id`，移除 `comments.liked_by` JSON 列）、
  `0015_admin_whitelist`（管理员白名单 `admin_users`，应用内管理「指定账号」）、
  `0016_admin_audit_logs`（运营审计 `admin_audit_logs`，记录谁改了什么）、
  `0017_fetch_politeness`（抓取礼仪 `rss_sources.respect_robots` + 告警节流 `last_alerted_at`）、
  `0018_fetch_failure_grading`（失败分级与熔断 `consecutive_failures` / `failure_kind` /
  `next_retry_at`）、
  `0019_source_health_events`（源健康历史 `rss_source_health_events`，每轮抓取一行 +
  复合索引 `ix_rss_health_key_at`，随源级联删除）。
- **为何移除 `create_all()`**：此前 `init_db()` 在启动时就地建表，与 Alembic 形成双轨管理——
  create_all 补建的物件不写入 `alembic_version`，导致后续 `upgrade head` 因"对象已存在"失败，
  且各环境结构分叉（历史上曾出现"库停在 0003、代码已到 0006"，索引优化实际未生效）。
  现在 `init_db()` 只**校验**表是否存在，缺表即抛错并提示执行迁移。
- 测试环境无需迁移：`tests/` 在 fixture 内自行 `Base.metadata.create_all`（内存库）。
- ⚠️ `alembic.ini` 保持**纯 ASCII**：中文注释会触发 Windows GBK 解码错误。

## 相关推荐

`GET /api/news/{id}/related?limit=6` —— 详情页「相关推荐」，三级召回逐级兜底，
**保证任何环境都有内容，不会开天窗**：

1. **标题相似度**：PostgreSQL + `pg_trgm` 时用 `similarity(title, 当前标题)` 召回，
   并用阈值（0.05）过滤，避免把毫不相干的内容当成"相关"；
2. **同频道最新**：相似度结果不足时补齐（**也是 SQLite / 无扩展时的主策略**）；
3. **全站最新**：仍不足时兜底。

约束：始终排除当前资讯自身；三级结果按 id 去重；`limit` 取值 1–20（越界为 422）。

> 为何必须运行时探测 `pg_trgm`：迁移 0005 在**权限不足时会跳过**建扩展，
> SQLite 则根本没有该函数——无条件使用 `func.similarity()` 会直接报错。
> 探测结果进程内缓存（扩展存在性不会在运行时变化）。

## 搜索与热词

| 接口 | 说明 |
| --- | --- |
| `GET /api/news/search?q=` | 搜索；**旁路记录**搜索词（失败不影响搜索结果） |
| `GET /api/news/search/hot?limit=&days=` | 热搜词：近 `days`（默认 7）天被搜索最多的词 |

**记原始日志而非聚合计数**：聚合表（`term` + `count`）只能算"历史累计最热"，
旧词会永久占榜；`search_logs`（迁移 0012）配合时间窗口才是真正的"近期热词"。
代价是日志会增长，故由定时任务 `prune_search_logs()` 清理 30 天前的数据。

**防刷**：同一用户同一词 60 秒内重复搜索不重复计数（`user_id` 去重窗口）。
未登录无 `user_id` 可依，无法施加窗口——此类记录每次都记，
但单人重复搜索的绝对量远小于真实群体行为，不足以左右榜单。

**隐私边界**：搜索词日志只用于统计热词，`user_id` 仅作去重依据，不对外暴露
"谁搜了什么"的查询接口。

### 搜索相关度排序（P1-11）

旧实现是 `ILIKE` 召回 + 纯时间倒序——搜「暴雨」时一篇标题含「暴雨」的旧文
可能排在一堆只是发得新的弱相关文章后面。现在的排序信号分三层：

```
score = 100 × 标题整词命中          # 最强：搜索词出现在标题里，基本就是用户要找的那篇
      +  10 × 摘要整词命中          # 弱一层
      +  2 × similarity(标题, 词)   # pg_trgm 字符三元组相似度，只在层内细排
      +  1 × similarity(摘要, 词)
ORDER BY score DESC, published_at DESC, id DESC
```

加分（100/10）远大于相似度的最大贡献（3），因此**层级严格成立**：标题命中的文章
一定排在仅摘要命中的前面；层内按相似度细排（标题更短、关键词占比更高的排前）。
SQLite / 无 pg_trgm 环境退化为「标题命中优先 + 时间倒序」，语义同构、确定性可比。

**为何不用 `tsvector` + `ts_rank`**（路线图原方案）：PostgreSQL 内置分词器
**不做中文分词**——连续汉字整体算一个 token，`to_tsquery('simple', '暴雨')`
匹配不到「暴雨蓝色预警」；引入 zhparser 需编译扩展，超出当前部署能力。
pg_trgm 按**字符三元组**计算相似度，对中文天然有效。

**召回为何用 ILIKE 而非 pg_trgm 的 `%` 运算符**：`%` 默认阈值 0.3 对中文短词
几乎召不回（两字词与长标题的共同三元组占比极低），调低阈值又会因大小写敏感
（`AI` vs `ai`）和长词稀释造成**召回缺口**（实测 `AI` 少 2 条、单字「暴」少 2 条）。
而 **ILIKE 可直接吃 0005 的 GIN 索引**（EXPLAIN 实测 `Bitmap Index Scan on
ix_news_title_trgm`），大小写不敏感、无阈值、与旧行为召回口径完全一致；
只有短词（<3 字）退化为全表扫描，毫秒级、结果正确。

**顺带修复**：ILIKE 模式串未转义——用户输入 `%` 会被当成通配符，"搜一个字符
匹配全部"。现已统一 `_escape_like()` + `ESCAPE '\'`。

**验证**：`PYTHONPATH=. python scripts/verify_search_ranking.py` 在真实 PG 库上
复核召回口径、分层不变式、分数单调性与索引命中（P1-11 验收条件）。
本批次**零迁移**（复用 0005 的索引），响应契约不变。

## 排序与个性化

`GET /api/news?sort=...` 支持三种排序，统一为「置顶 → 排序键倒序 → id 倒序」：

| `sort` | 排序键 | 说明 |
| --- | --- | --- |
| `latest`（默认） | `published_at` | 时间倒序，与 H1 契约一致 |
| `hot` | `hot_score` | 互动加权 × 时间衰减 |
| `for-you` | `hot_score + 频道偏好加分` | 需登录；未登录或无行为数据时退化为 `hot` |

**热度分**（`app/ranking.py`）：

```
hot = (评论数 × 3 + 收藏数 × 2 + 播放数 ÷ 1000) × 时间衰减 × 100
时间衰减 = 1 / (1 + 已发布小时数 ÷ 48)      # 48 小时半衰
```

- **落库而非查询时计算**：查询时算要 JOIN 聚合收藏数，既慢又无法建索引，
  且游标分页要求排序键是**稳定标量**。落库后可建 `ix_news_hot`（迁移 0011）。
- **播放权重刻意压低**（÷1000）：取 ÷100 时 10 万播放会压过数百条评论，
  实测出现「零评论的高播放视频霸榜」，与"热议"语义不符。
- **时间可注入**：所有计算接受 `now` 参数，测试不依赖真实时钟。
- **更新时机**：评论/收藏等互动发生即 `bump_hot_score()` 增量重算（即时可见）；
  定时任务在抓取后 `recompute_hot_scores()` 全量重算（刷新时间衰减）。
  热度是派生数据，更新失败仅记 warning，**不影响评论/收藏主流程**。

**个性化**：由 `UserFavorite`（权重 3）与 `UserHistory`（权重 1）统计频道分布，
归一化后作为加分（上限 300，约"10 条评论"的量级）——**偏好只提升次序，
不足以让零互动内容压过真正热门的内容**。

采用加分而非"偏好频道配额"：配额要在应用层组装候选，与 keyset 游标分页不兼容。

**游标必须携带 sort**：v2 游标为 `[2, sort, is_top, key, id]`。若不带 sort，
`hot` 游标会被当成 `published_at` 解析而错位。v1（3 元素）仍按 `latest` 兼容解析，
升级前下发到客户端的游标不会失效。

## 消息中心（事件驱动）

消息分两类，由 `messages.user_id` 区分：

| 类型 | `user_id` | 语义 | 例子 |
| --- | --- | --- | --- |
| **广播** | `NULL` | 全员可见 | 系统通知、安全提醒（种子消息） |
| **定向** | 具体用户 id | 仅该用户可见 | 有人评论了我评论过的资讯、有人赞了我的评论 |

事件由互动行为触发（`app/notify.py`）：

- 发表**主评论** → 通知**同样评论过该资讯**的其他用户（排除自己）
- 发表**回复** → 只通知**被回复者**（一级通知，不走广播——避免同一个人收到两条语义重复的消息）
- 点赞评论 → 通知该评论作者（**仅新增点赞**时，取消赞不打扰）

约束：
- **触发者永不收到自己行为的通知**
- **去重**：相同（收件人, 类型, 资讯, 摘要）只记一条——点赞可反复切换，不去重会刷屏
- **旁路**：通知失败不影响评论/点赞主流程（记录 warning 后继续）

接口 `GET /messages`、`GET /messages/unread-count`、`POST /messages/read-all`
均只统计**可见范围**内的消息（定向给自己 + 广播）。

## 收藏 / 浏览历史（云端同步）

此前收藏与历史**只存在前端 localStorage**：换设备、清缓存即丢失，也无法跨端一致。
现落到 `user_favorites` / `user_history` 两张关联表（迁移 `0010_user_marks`）。

**表结构**

| 表 | 主键 | 时间列 | 语义 |
| --- | --- | --- | --- |
| `user_favorites` | `(user_id, news_id)` | `created_at` | 收藏；复合主键保证同一用户同一资讯只有一条（幂等基础） |
| `user_history` | `(user_id, news_id)` | `viewed_at` | 浏览历史；重复浏览只刷新时间，不产生多行 |

**契约要点**

- 所有写操作都返回**完整的最新集合** `{ ids, list }`——服务端是唯一事实源，
  客户端"调用即可，结果即真相"，无需自行推导增量；`list` 为完整资讯对象，
  使**新设备**无需额外请求即可渲染收藏/历史页。
- **合并（PUT）语义为并集**：只增不删。本地为空时不会误删云端数据；
  删除一律走显式 `DELETE`，意图明确可审计。
- 合并时并入条目被视为"最近发生"（客户端未携带真实时间戳），时间基准取
  `max(now, 云端最大时间) + N 秒`，保证合并后顺序**完全确定**：先按传入顺序排列并入项，
  再排云端原有项。
- 合并跳过不存在的资讯 id（客户端可能持有已下架/已清理的 id）；单条 `POST /favorites`
  对不存在的资讯返回 404，而 `POST /history` 静默忽略（详情页被动副作用，
  避免与时效清理竞态时抛无意义错误）。
- **数据隔离**：全部按 `user_id` 过滤，跨用户不可见。
- **级联清理**：PG 走 `ON DELETE CASCADE`；SQLite 默认不强制外键，
  由 `prune_old_news` 在清理资讯时显式删除标记（与评论/消息同一策略）。

**前端策略（离线优先 + 按账号命名空间隔离）**：localStorage 仍是本地缓存（未登录也能收藏/记历史），
但**按作用域分键存储**（`marks:anonymous` / `marks:u<id>`）：

- 未登录时落在**匿名作用域**，登出不会丢失；
- 各账号的本地缓存互不可见，**不存在换账号串数据**；
- 登录时把「匿名期数据 + 该账号本地缓存」**采纳**为该账号数据（匿名作用域随即清空，
  因其已归属该账号，留着会重复计数），再与服务端做**并集合并**；
- 登出仅切回匿名作用域，**账号数据保留在本地**，重新登录即恢复（无需等待网络）；
- 写操作本地乐观生效 + 后台推送，推送失败不回滚（下次合并自然收敛）。

## 评论（两级结构）

**结构契约**：主评论 `root_id IS NULL`；回复 `root_id = 所属主评论 id`。
**回复的回复拍平到同一 root**（`root = parent.root_id or parent.id`）——讨论区只有
「主评论 + 一层回复」两級，`parent_id` 仅用于展示「回复 @谁」。

- **列表分工**：`GET /comments` 只返回主评论（附 `replyCount` 与最早 2 条预览）；
  完整回复走 `GET /comments/{cid}/replies`，按时间**正序**（讨论先后）游标分页。
- **预览取「最早 2 条」**：窗口函数 `row_number() OVER (PARTITION BY root_id ORDER BY created_at, id)`
  按组取前 N，避免把全部回复捞进内存；`liked` 判定一次 IN 查询覆盖主评论 + 预览回复（无 N+1）。
- **点赞关系表化**（迁移 `0014`）：`comments.liked_by` JSON 数组 → `comment_likes`
  关系表（`(comment_id, user_id)` 复合主键，`ON DELETE CASCADE`）。复合主键天然防重复点赞，
  `likes` 冗余计数随行的插入/删除同步维护；批量判定同理一次 IN 查询。
- **删除语境**：主评论软删除后，其回复**不再可见**（replies 接口返回空列表）——
  主评论消失后，失去语境的回复不应继续裸露在讨论区；回复行本身保留，可追溯。
- **通知**：主评论广播给同样评论过该资讯的其他用户；回复只通知被回复者。

## 单元测试

```bash
pytest -q          # 385 项
```

> **测试环境由根级 `conftest.py` 统一钉死**（`DATABASE_URL=sqlite:///:memory:` 等）：
> `app.config` 在**首次导入时**读取环境变量，若由各测试文件自行设置，
> 谁的 import 先发生谁说话——全套跑与单文件跑结果不一致（曾出现整类用例时绿时红）。
> 用**赋值**而非 `setdefault`，确保开发者本机 `.env`（如指向 PostgreSQL）不会渗进测试。

- `tests/test_units.py`（25 项）：限流器（阈值/隔离/窗口过期）、PBKDF2 密码哈希与加盐、JWT 往返、RSS 解析与字段映射（RSS 2.0 / Atom / 图片提取 / 变体判定 / 稳定 id / 相对时间）。
- `tests/test_hardening.py`（14 项）：受信代理 IP 解析（XFF 采信/忽略/回退）、分页游标编解码与非法输入、**schema 单轨守卫**（缺表时抛错且**不就地建表**、`init_db` 源码不含 `create_all`）。
- `tests/test_fetch_governance.py`（53 项）：**抓取链路治理**（P1-16 + P1-17 + P1-18）——分组（保持权重序 /
  异站分组 / 最小间隔判定）、并发与合并顺序（**高权重源在重复稿件上胜出**、
  stats 顺序不随网络快慢漂移、同站并发峰值恒为 1、异站确实并行）、礼貌抓取
  （robots 禁止记为异常且**不发正文请求**、取不到 robots 放行、逐源可关、
  robots 按 host 缓存只取一次、最小间隔跳过且零请求）、超时告警
  （异常 / 从未成功 / 超时未成功判为不健康、健康与禁用源不报、通知已注册管理员、
  节流窗口内外、**无收件人不占用节流锚点**、env-only 管理员同样收件、不健康计数）、
  **告警去重聚合**（阈值上下分别走逐源 / 汇总、N 源 × M 管理员坍缩成 M 条、
  按原因分组且严重度在前、同因成批体现系统性问题、长名列举截断与**去重后总量不变**、
  摘要不超 300 列宽、汇总同样推进全部锚点、节流源不进汇总、权重决定列举顺序）、
  **失败分级与熔断**（超时/5xx/429 判可重试、404/robots 拒绝/解析无条目判永久、
  永久失败**不消耗重试次数**、成功即清零、阈值前后 `next_retry_at` 有无、
  冷却期内不抓且不刷新健康、到期自愈、自动禁用默认关闭且开启时**必写审计**、
  告警文案区分永久/临时并带连续次数）。
- `tests/test_health_history.py`（43 项）：**源健康历史与趋势**（P1-19）——记录（成败都记、
  **错误截断到列宽 300**、纯空白存 null、**写失败不抛**、清理失败返回 0）、
  窗口汇总（**无尝试时 `rate` 为 `None` 而非 0**、`avgItems` 只算成功轮次、
  最后一次成功/失败分开记、窗口外事件不计入）、按天分桶（升序、同天合并、
  **缺的天不补零**、无事件返空）、最近失败（只列失败且新的在前、limit、
  `kind` 透传）、清理（只删过期的、`keep_days` 参数、无删除返 0）、
  窗口收敛（默认/超出/合法/垃圾天数）、多源一次查（单条 GROUP BY、
  **无事件的源不出现在结果里**、空入参短路）、
  **两类隐形故障**（成功率满分但条数 30→3 单调下滑、
  失败集中在同一钟点的规律抖动）、接口（三段数据齐全、`days` 收敛、
  显式 `days` 生效、未知源 404、非管理员 403、匿名 401）、
  列表（带 `health` 与 `healthDays`、**新源 `health` 为 null**、列表窗口短于趋势窗口）、
  写入侧集成（**每个真实尝试写一行**、礼仪跳过与熔断跳过**都不写**）。
- `tests/test_migrations.py`（19 项）：**迁移链守卫**。在临时 SQLite 库上跑 `upgrade head` →
  `downgrade base` → `upgrade head`，校验全部表（含 `search_logs` / `rss_sources` / `comment_likes` /
  `admin_users`）、`comments.user_id`、`ix_news_feed`、`users.token_version`、`news.hot_score`
  与 `ix_news_hot`、`rss_sources` 的列与主键（含 0017 的 `respect_robots` **非空**与
  `last_alerted_at`、0018 的 `consecutive_failures` **非空**与 `failure_kind` / `next_retry_at`）、
  `rss_source_health_events` 的列与 `item_count` 非空、复合索引 `ix_rss_health_key_at`
  与外键（0019）。`TestCommentLikesBackfill`（2 项）专测 0014 回填：
  秒级时间戳下**同一条评论同一用户重复点赞只留一行**、悬空/非整数 user_id 被跳过、
  删除列后计数对齐、`downgrade` 可还原 `liked_by`。⚠️ 必须在**子进程**中执行（见文件头说明）：
  `migrations/env.py` 读的是 `app.config.DATABASE_URL`，**不会**读 alembic Config 的
  `sqlalchemy.url`，进程内直接调 `command.upgrade()` 会作用到**真实数据库**。
- `tests/test_audit.py`（13 项）：**运营审计**（P1-15）——鉴权（401/403）、
  源增删/重置留痕、更新只记变化字段（无变化不留痕）、删除快照在删前取、
  管理员增删留痕且顺序为「移除在前」、时间线倒序、游标分页无重复无遗漏、
  `target` 过滤、非法游标 40003、审计与业务同事务。
- `tests/test_sources.py`（52 项）：**RSS 源可配置化**——播种只在空表时发生（不覆盖运营配置）、
  内置源含 `aihot_picks`、按权重降序、频道无专属源时回退全部、无库会话回退内置源；
  健康状态回写（成功 / 失败 / 错误截断 / 未知 key 静默）；
  **抓取健壮性**（坏源不拖垮好源、真实不可达地址记为 `ok=False` 且带原因、显式传入的源去重并按权重降序）；
  **robots 开关端到端**（`respectRobots` 经 POST/PATCH 真的落库、不传即默认遵守、
  抓取侧 `_to_dict` 的蛇形键同步——**这条是字段名对齐的回归防线**）；
  管理接口（未登录 401 / 非管理员 403 / 白名单为空整体关闭 / 列表 / 新增 / **重复 key 40902** /
  **非 http(s) 地址 40004** / 局部更新不清空未传字段 / 启停 / 删除 / 不存在 404 / 重置恢复内置源）；
  **管理员白名单**（`TestAdminWhitelist`，13 项）——env ∪ DB 双源判定、库管理员登录即 `isAdmin`
  且可完整管理（预授权链路）、重复/env 内号码 40903、形态非法 422、`added_by` 审计、
  `registered` 随注册翻转、移除后标志回落、不存在 40403、**末位管理员 40904**（env 为空时），
  以及「env 非空时库中最后一条可移除」的对照面；
  **`isAdmin` 下发**（`TestAdminFlagInAuthPayload`，6 项）——白名单账号登录/注册/续期响应
  `user.isAdmin=true`、普通账号为 false、白名单变动后标志跟随（只下发布尔值，不下发白名单）、
  **库白名单（非 env）号码同样下发 true**；
  **入参校验**（4 项）——未知频道 422（`errors[0].loc[-1] == 'channel'`）、`key` 格式非法 422、
  更新时同样校验频道。
- `tests/test_api.py`（88 项）：含**评论归属**（`TestCommentOwnership`）——新评论写入
  `userId`、`mine` 仅作者为 true、历史评论 `userId` 为 null 且任何人都不是作者；
  **评论删除**（`TestCommentDelete`）——作者可删、他人 403、未登录 401、
  无归属评论不可删、幂等、删后不可点赞、跨文章 404；
  **两级评论**（`TestCommentReplies`，9 项）——回复的回复 `rootId` 拍平、
  主列表只出主评论 + 预览 ≤2 + `replyCount` 排除已删、replies 正序游标分页、
  删 root 后回复不可见、回复目标不存在/跨文章/已删均 40003、**回复只通知被回复者不广播**；
  **点赞关系表**（`TestCommentLikesTable`，3 项）——关系行增删、复合主键防重、`liked` 按用户区分。
- `tests/test_feeds.py`（7 项）：RSS 解析 / 条件请求（304 / 条件头 / 不重试）/ 增量入库 / 时效清理（未知时间优先、keep_min 下限、子表清理）。
- `tests/test_search_stats.py`（21 项）：**搜索词统计**——记录与空白词忽略、**同用户同词 60s 内去重（防刷）**、窗口过后重新计数、不同用户不去重、未登录无去重窗口、超长词截断、按次数倒序、同次数按词升序（顺序稳定）、**窗口外的旧搜索不计入**、limit 生效、清理保留期内数据、接口契约与越界 422、搜索即记录、已登录去重。
- `tests/test_ranking.py`（29 项）：**排序与个性化**——热度纯函数（互动越多分越高、
  评论权重高于收藏、越旧分越低、48h 恰好半衰、无法解析时间取中性值、
  未来时间不放大、**带时区与 naive 混用不报错**）、三模式排序（时间 / 热度 / 偏好提升 /
  未登录退化 / 偏好压不过真实热度 / 历史也构成偏好）、置顶在热榜仍优先、
  非法 sort 为 422、`hot` 游标翻页无重复无遗漏、v1 游标仍可续接、
  **`comment_count` 随评论增删增减且不为负**、收藏抬升/取消降低热度、全量重算。
- `tests/test_api.py` 另含 `TestMessageEvents`（8 项）：**消息事件化**——广播对全员可见、
  定向消息仅收件人可见、评论通知其他评论者、**自身不通知自己**、点赞通知作者、
  **取消赞不打扰**、反复点赞去重、全部已读只标记可见消息。
- `tests/test_multi_instance.py`（3 项）：限流器工厂回退、非 PG 方言 advisory lock 行为。
- `tests/test_me.py`（18 项）：**收藏 / 浏览历史云端同步**——鉴权（6 个端点）、收藏增删幂等、
  并集合并且不丢数据/不误删、非法 id 跳过、历史去重与置顶、超限裁剪、被动副作用静默、
  **跨用户隔离**、**资讯清理时标记级联清除**。
- `tests/test_api.py` 其余：用 **SQLite 内存库** 隔离，不触碰开发/生产数据；覆盖 health / 请求ID / metrics、认证（含 422 统一契约、token 吊销与续期）、资讯（排序契约/频道/分页/**游标分页**/详情/搜索）、**抓取接口鉴权（401/403/202）**、评论（鉴权/点赞幂等/排序/分页/空内容）、消息（分页/未读数/已读）。

> 前端契约自检：`node scripts/contract-check.mjs`（**47 项**，含 18 项二级评论契约，需传 `DEMO_PASSWORD` 登录）。
> 加 `CHECK_ADMIN=1` 且在 `ADMIN_PHONES` 内的账号登录，可额外校验 `/api/admin/sources`
> 的增删改与协议白名单，`/api/admin/users` 的白名单增删、重复/自添加拦截、
> `addedBy` 与 404、`/api/admin/audit` 的留痕与游标分页，
> 以及 `/api/admin/sources/{key}/health` 的趋势形状、`days` 收敛与越界 422、未知源 404
> （共 83 项；mock 后端同样支持，见前端 README）。

## 错误码

> 完整接口清单见上方「接口清单」（唯一来源，避免多处维护导致漂移）。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | 40001 / 40002 / 40003 / 40004 | 手机号格式错误 / 评论内容为空 / 分页游标无效 / RSS 源地址不是 http(s) |
| 401 | 40100 | 未登录或 token 失效（含被 `token_version` 吊销） |
| 401 | 40101 | 手机号或密码错误 |
| 403 | 40301 | 无权限（非管理员调用 `/api/news/refresh`、`/api/admin/*`；白名单为空时该能力整体关闭） |
| 403 | 40302 | 只能删除自己发表的评论 |
| 404 | 40400 / 40401 / 40402 / 40403 | 资讯不存在 / 评论不存在 / RSS 源不存在 / 管理员不存在 |
| 409 | 40901 | 手机号已注册 |
| 409 | 40902 | RSS 源标识（`key`）已存在 |
| 409 | 40903 | 该手机号已是管理员（含 env 与库两个来源） |
| 409 | 40904 | 已是最后一位管理员，不能移除（末位保护） |
| 422 | 42200 / 42201 | 参数校验失败（统一 `{ code, message, data.errors }`）/ 排序方式不支持 |
| 429 | 42900 / 42901 | 触发限流（普通 / 登录） |

> 注：`40901`–`40904` 刻意分开——同一错误码承载多种语义会让前端文案错配。

## 技术说明

- **鉴权**：JWT（HS256，`PyJWT`），`Authorization: Bearer <token>`；密码用 **PBKDF2-SHA256**（标准库，12 万轮 + 随机盐）存储。
- **ORM**：SQLAlchemy 2.x（`DeclarativeBase` + `select` 风格）。JSON 字段通过 `with_variant` 在 PostgreSQL 上自动使用 **JSONB**（SQLite 下为 JSON）。
- **点赞去重**：`comment_likes` 关系表（`(comment_id, user_id)` 复合主键，迁移 0014）
  保证同一用户对同一评论只点一次；`likes` 冗余计数随行的插入/删除同步维护。
- **换数据库**：只改 `DATABASE_URL`（SQLite ⇄ PostgreSQL），模型无需改动。

## 目录结构

```
server-api/
├── main.py              # FastAPI 入口（CORS、限流/日志中间件、异常封装、生产加固、lifespan）
├── requirements.txt
├── .env.example         # 复制为 .env 后按需修改
├── .gitignore           # 排除 .env / *.db
├── alembic.ini          # 迁移配置（保持纯 ASCII）
├── conftest.py          # 测试环境钉子户（内存库/关限流/关定时任务/管理员白名单）
├── docs/
│   └── news-fetch.md    # 抓取链路 + 字段映射表 + 故障排查
├── migrations/          # Alembic 迁移
├── scripts/
│   ├── fetch_news.py                 # RSS 抓取 CLI
│   ├── migrate_sqlite_to_pg.py       # SQLite → PG 全量迁移（幂等）
│   ├── verify_search_ranking.py      # 搜索相关度排序的 PG 验证（一次性/复查）
│   └── verify_comment_likes_migration.py  # 0014 影子模式验证（回填/外键/级联/downgrade）
└── app/
    ├── config.py        # 环境配置（生产加固 + 连接池 + 抓取参数）
    ├── database.py      # 引擎/会话/Base + 连接池策略与观测
    ├── models.py        # User / News / Comment / Message / SearchLog / RssSource / AdminUser 等
    ├── schemas.py       # Pydantic 模型
    ├── security.py      # PBKDF2 + JWT
    ├── deps.py          # 鉴权依赖
    ├── ratelimit.py     # 固定窗口限流器
    ├── ranking.py       # 热度分 + 频道偏好（排序与个性化）
    ├── search_stats.py  # 搜索词日志 + 热词时间窗口聚合
    ├── notify.py        # 互动事件 → 消息
    ├── response.py      # { code, message, data } 封装
    ├── scheduler.py     # 定时抓取（asyncio lifespan）
    ├── seed.py          # 种子数据（幂等补齐 + 孤立引用自愈）
    ├── feeds/           # RSS 抓取层
    │   ├── sources.py   # 源清单（内置播种 + 读库回退）
    │   ├── fetcher.py   # 抓取 + 解析 + 字段映射
    │   └── ingest.py    # 编排 + 入库 + 清理
    ├── routers/         # auth / news / comments / messages / me / admin
    └── ../tests/        # pytest（内存 SQLite + 迁移链子进程守卫）
```