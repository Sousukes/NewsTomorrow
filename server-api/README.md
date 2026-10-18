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
- **登录失败锁定（C2）**：按**账号**（手机号）计数，与 IP 限流互补——IP 维度防不住「换 IP 打同一个账号」的分布式爆破。
  - `LOGIN_FAIL_LIMIT`（默认 5）、`LOGIN_FAIL_WINDOW_SECONDS`（默认 900）
  - 连续失败达上限即锁定整个窗口：锁定期间**连密码验证都不做**（先于查库/验密，成本趋零），且继续尝试不会给锁定期续期（预检只窥探、不计次）；响应 `429` + 错误码 **42902** + `Retry-After` 头
  - **不存在的账号同样计数**：锁定检查先于用户查询，响应只有「42902 已锁」与「40101 密码错」两种——若只锁已存在账号，「同号有时 429 有时 401」就是账号枚举 oracle
  - **成功登录即清零**：失败计数只反映「连续」失败，不让历史手滑累积成锁定
  - 跟随 `RATE_LIMIT_ENABLED` 总开关；多实例走同一套 Redis 共享计数（`REDIS_URL`）
  - 代价（账号锁定的固有取舍）：持正确密码的用户可能被攻击者故意锁一个窗口——所以默认阈值给到 5 次（真人手滑空间），不宜再低
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
# {"code":0,"data":{"status":"up","pool":{"dialect":"postgresql","size":5,"checked_in":1,"checked_out":0,"overflow":-4},"adminBreakGlass":true,"hotRecompute":{"enabled":true,"lastAt":"2026-10-15T09:30:00","lastCount":12,"lastError":null}}}

# 在 PG 侧查看本应用的连接
psql -c "select pid, state, application_name from pg_stat_activity where application_name='toutiao-api';"
```

> ⚠️ **worker 数 × 池大小不要超过 PG 的 `max_connections`**。例如 `--workers 4` 且 `DB_POOL_SIZE=5` 时，最多可达 4×15=60 条连接。

## 接口清单

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 🔑 运维 | 健康检查；返回 `{ status, pool, adminBreakGlass, hotRecompute }`——`adminBreakGlass` 是**应急通道是否配置**的布尔标记（只下发真假、不下发号码）；`hotRecompute` 是热度重算 cron 的进程内状态（`enabled`/`lastAt`/`lastCount`/`lastError`），抓取关停时据此判断衰减仍在推进。**访问控制见「运维接口鉴权」** |
| GET | `/api/metrics` | 🔑 运维 | Prometheus 文本指标（uptime / 请求数 / 在途 / 状态码 / 路径 / 连接池）。**访问控制见「运维接口鉴权」** |
| POST | `/api/news/refresh` | ✅ 管理员 | 手动触发抓取（`?channel=&keyword=`）。需管理能力（`ADMIN_PHONES` ∪ `admin_users` 表），**异步执行**返回 202 |
| GET | `/api/news/search?q=` | — | 服务端搜索（标题/摘要匹配，**按相关度排序**见「搜索与热词」），返回 `{ list, total, page, hasMore }`；**旁路记录**搜索词供热词统计 |
| GET | `/api/news/search/hot?limit=&days=` | — | 热搜词（近 `days` 天真实统计），返回 `{ list: [{ term, count }], days }`；无数据时为空数组 |
| POST | `/api/auth/register` | — | 注册（手机号 + 密码 ≥6 位），返回 token |
| POST | `/api/auth/login` | — | 登录，成功返回 `{ token, user }`；`user` 含 `isAdmin`（布尔值，按 `ADMIN_PHONES` ∪ `admin_users` 表白名单判定后下发，前端据此决定是否展示管理入口，**不下发白名单本身**） |
| POST | `/api/auth/logout` | ✅ | 退出（无状态 JWT，前端清本地 token） |
| GET | `/api/news?channel=&sort=&page=&pageSize=&cursor=` | — | 资讯列表。**推荐游标分页**：传 `cursor` 走 keyset，返回 `{ list, nextCursor, hasMore }`；不传则 offset 分页并附 `total`。`sort` 取 `latest`（默认）/ `hot` / `for-you`（见「排序与个性化」），非法值 422 |
| GET | `/api/news/{id}` | — | 资讯详情。比列表多返回 `content`（正文，按段落以 `\n` 分隔；无正文为 `null`，前端显示「暂无正文」而非占位文案）——**列表接口刻意不带正文**，见「资讯正文（E1）」 |
| GET | `/api/news/{id}/comments?limit=&cursor=` | —（可选带token） | **主评论**列表（`root_id IS NULL`，游标分页，**新的在前**）；每条附 `replyCount` 与最早 2 条回复预览；带 token 时 `liked` 按当前用户计算 |
| GET | `/api/news/{id}/comments/{cid}/replies?limit=&cursor=` | —（可选带token） | 某条主评论的**回复**列表（时间**正序**，游标分页；root 已软删除时返回空列表——主评论消失后失去语境的回复不再裸露） |
| POST | `/api/news/{id}/comments` | ✅ | 发表评论；带 `parentId` 时为**回复**（被回复评论须存在且未删除，否则 40003；回复的回复拍平到同一 root，`parentId` 仅用于展示「回复 @谁」） |
| PATCH | `/api/news/{id}/comments/{cid}` | ✅ | 点赞 / 取消点赞（`comment_likes` 关系表按用户去重，幂等） |
| GET | `/api/messages?limit=&cursor=` | —（可选带 token） | 消息中心列表（游标分页，返回 `{ list, hasMore, nextCursor }`）。按 `(created_at DESC, id DESC)` 排序——**游标是上一页末条的 id**，服务端回查其排序键取「严格更早」的一页（A2） |
| GET | `/api/messages/unread-count` | — | 未读数（给 TabBar 徽标；关联表索引计数，不全表遍历） |
| PATCH | `/api/messages/{id}` | ✅ | 标记单条已读（写入 `message_reads` 关联表，幂等）。**需登录**（40100）；「不存在」与「不可见」同码 **40400**（H11：不泄露他人定向消息） |
| POST | `/api/messages/read-all` | —（可选） | 全部标记已读 |
| GET | `/api/me/favorites` | ✅ | 我的收藏（按收藏时间倒序），返回 `{ ids, list }` |
| POST | `/api/me/favorites` | ✅ | 收藏一条资讯（幂等；资讯不存在 404） |
| PUT | `/api/me/favorites` | ✅ | **并集合并**（登录时把本地收藏并入云端，只增不删） |
| DELETE | `/api/me/favorites/{newsId}` | ✅ | 取消收藏（幂等） |
| GET | `/api/me/history` | ✅ | 浏览历史（按浏览时间倒序，最多 50 条），返回 `{ ids, list }` |
| POST | `/api/me/history` | ✅ | 记录浏览（同一资讯只刷新时间；资讯不存在时静默忽略） |
| PUT | `/api/me/history` | ✅ | **并集合并**本地历史，随后按上限裁剪 |
| DELETE | `/api/me/history` | ✅ | 清空浏览历史 |
| PUT | `/api/me/nickname` | ✅ | **修改昵称**（P1-24，与注册同一套校验）：形状不合法 **42203**（空/纯空白/越界/非法字符/纯符号，消息即面向用户的提示）；命中 `block` 级词 **40006**（提示**不回显命中词**）；命中 `review` 级词**放行**并置 `name_pending_review` 进人工队列；通过则原样保存并**清除**待复核标志。返回 `{ name, result, reason, pendingReview }` |
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
| GET | `/api/admin/news?status=&keyword=&channel=&limit=&cursor=` | ✅ 管理员 | **管理端资讯列表**（P1-20）：`status` 取 `all`（默认，**含已下架**——运营要能核验自己的下架操作）/ `visible` / `hidden`，非法值 422；`keyword` 标题/摘要 ILIKE；排序 `published_at DESC, id DESC`；`total` 始终返回（界面显示"共 N 条"） |
| GET | `/api/admin/news/{id}` | ✅ 管理员 | 管理端单条资讯（含下架三件套）；不存在 40404（与公开端 40400 刻意分开） |
| POST | `/api/admin/news/{id}/hide` | ✅ 管理员 | **软下架**（可恢复）：理由必填（空/纯空白 422——「留痕是准入条件」），≤300 字；**幂等**（重复下架不覆盖原理由、不重复记审计） |
| POST | `/api/admin/news/{id}/restore` | ✅ 管理员 | 恢复已下架资讯（**幂等**）；清空 `hidden_at`/`hidden_reason`；公开四路查询实时恢复可见，无需等下一轮抓取 |
| PATCH | `/api/admin/news/{id}` | ✅ 管理员 | 局部更新（当前仅 `isTop` 置顶开关）；字段级 diff 审计，**无变化不记** |
| GET | `/api/admin/comments?status=&keyword=&newsId=&limit=&cursor=` | ✅ 管理员 | **管理端评论列表**（P1-22/23）：`status` 取 `all`（默认，**含已隐藏**）/ `visible` / `hidden` / `pending`（待复核），非法值 422；`keyword` 评论内容 ILIKE；`newsId` 按所属资讯过滤；已软删除一概不返回；排序 `created_at DESC, id DESC`；`total` 始终返回 |
| GET | `/api/admin/comments/{id}` | ✅ 管理员 | 管理端单条评论（含隐藏三件套与所属资讯标题）；不存在 40405（与公开端 40401 刻意分开） |
| POST | `/api/admin/comments/{id}/hide` | ✅ 管理员 | **软隐藏**（可恢复）：理由必填（空/纯空白 422——「留痕是准入条件」），≤300 字；**幂等**（重复隐藏不覆盖原理由、不重复记审计）；隐藏后公开五路（主列表/回复/预览/计数/不可交互）实时不可见 |
| POST | `/api/admin/comments/{id}/restore` | ✅ 管理员 | 恢复已隐藏评论（**幂等**）；清空 `hidden_at`/`hidden_reason`；计数与热度分同步加回，公开查询实时恢复可见 |
| POST | `/api/admin/comments/{id}/review` | ✅ 管理员 | **通过人工复核**（P1-23，幂等）：只清 `pending_review` 并写 `reviewed_at`，不改可见性与计数——脱敏评论本来就可见，复核是"人看过了"这个事实；仍不合适应走 `hide` |
| GET | `/api/admin/words?keyword=&action=&enabled=&limit=&offset=` | ✅ 管理员 | **敏感词库**（P1-23）：`action` 取 `block`/`mask`/`allow`，非法值 422；按 `hit_count DESC, id DESC`（高频词要么是热点、要么是误杀大户）；`total` 为筛选后的总数 |
| POST | `/api/admin/words` | ✅ 管理员 | 新增敏感词：归一化后不足 `MODERATION_MIN_WORD_LEN` 个有效字符 → 42202（空格与标点不计）；重复 → 40905；**保存即生效**（本进程缓存立即失效） |
| PATCH | `/api/admin/words/{id}` | ✅ 管理员 | 局部更新（动作/分组/启用），**不允许改词本身**；字段级 diff 审计，无变化不记 |
| DELETE | `/api/admin/words/{id}` | ✅ 管理员 | 删除敏感词（审计留快照；停用通常比删除更合适）；不存在 40406 |
| POST | `/api/admin/words/reload` | ✅ 管理员 | 强制重建**本进程**词库索引（其他实例靠 TTL 兜底，需即时同步时调它） |
| GET | `/api/admin/moderation/hits?word=&action=&field=&limit=&cursor=` | ✅ 管理员 | **命中日志**（P1-23，只读）：`field` 取 `content`/`name`；`snippet` 为**原文**片段（脱敏不可逆，误杀复盘与申诉的唯一证据）；被拦截的命中 `commentId` 为 `NULL` |
| GET | `/api/admin/nickname-words?keyword=&action=&enabled=&limit=&offset=` | ✅ 管理员 | **昵称敏感词库**（P1-24）：`action` 取 `block`/`review`/`allow`，非法值 422；`keyword` 只匹配主词（别名在每行 `aliases` 字段可见）；按 `hit_count DESC, id DESC`；`total` 为筛选后总数 |
| POST | `/api/admin/nickname-words` | ✅ 管理员 | 新增昵称敏感词：`{word, action, category, aliases, enabled}`；主词或**任一别名**归一化后不足 `NICKNAME_MIN_WORD_LEN` 个有效字符 → 42204；重复 → 40906；**保存即生效**（本进程缓存立即失效） |
| PATCH | `/api/admin/nickname-words/{id}` | ✅ 管理员 | 局部更新（动作/分组/别名/启用），**不允许改词本身**；`aliases` 传空数组 = 清空，不传 = 不动；字段级 diff 审计，无变化不记 |
| DELETE | `/api/admin/nickname-words/{id}` | ✅ 管理员 | 删除昵称敏感词（审计留快照；停用通常比删除更合适）；不存在 40407 |
| POST | `/api/admin/nickname-words/reload` | ✅ 管理员 | 强制重建**本进程**昵称词库索引（返回生效条数；其他实例靠 TTL 兜底） |
| GET | `/api/admin/nickname-hits?word=&action=&source=&userId=&limit=&cursor=` | ✅ 管理员 | **昵称命中日志**（P1-24，只读）：`source` 取 `register`/`update`；`matched` 记**实际命中的模式**（主词或某条别名——回答"哪条别名拦住的"）；`snippet` 为当次提交的昵称原文；游标为 id keyset |
| GET | `/api/admin/nickname-reviews?keyword=&limit=&offset=` | ✅ 管理员 | **昵称待复核队列**（P1-24）：`name_pending_review=true` 的用户；`keyword` 按**当前**昵称/手机号筛；行含 `flaggedAt`/`flaggedName`/`flaggedMatched`（取该用户最近一条 review 级命中——队列回答"哪个提交需要人看"，不是"他现在叫什么"）；按最近被标记降序 |
| POST | `/api/admin/nickname-reviews/{userId}` | ✅ 管理员 | **通过昵称人工复核**（幂等）：只清 `name_pending_review` 并写 `name_reviewed_at`，**不改昵称本身**（复核是"人看过了"这个事实，不是运营替他改名）；用户不存在 40408 |

> ⚠️ `/api/news/search` 必须声明在 `/api/news/{news_id}` 之前，否则会被当成 id 匹配。
> 📌 统一错误契约：所有错误（含 422 参数校验）均返回 `{ code, message, data }`；错误码见文末。

## 令牌双轨（C1，access / refresh）

此前全站只有一张 7 天令牌。风险不在「7 天」这个数字，而在**只有一张令牌、
没有任何缩短手段**：一旦泄露（XSS / 日志 / 共享设备），攻击窗口就是整整 7 天。
现拆为两类（`app/security.py`）：

| | 类型 `typ` | 默认有效期 | 用途 | 配置 |
| --- | --- | --- | --- | --- |
| access | `access` | **30 分钟** | 访问业务接口 | `JWT_ACCESS_EXPIRE_MINUTES` |
| refresh | `refresh` | **7 天** | **只能**调 `POST /api/auth/refresh` 换发 access | `JWT_REFRESH_EXPIRE_MINUTES` |

- **类型校验是安全前提**：`decode_token_full(expect_type=...)` 按类型拒绝。
  refresh 若能被业务接口接受，攻击者拿到它就能当永久 access 用，「短 access」形同虚设。
  缺 `typ` 的历史令牌按 access 处理（迁移期兼容，它们最多再活到自身过期）。
- **有效期按类型取默认**：`create_token(type=refresh)` 不传 `expires_minutes` 时
  取 refresh 的时长，而不是一律 30 分钟——否则 refresh 被静默签成短命的，
  会话 30 分钟即死，且从代码上看不出错（类型对、签名对，只是短）。
- **兼容旧变量**：`JWT_EXPIRE_MINUTES` 的历史语义就是那张长令牌，故映射到 refresh。
  老部署升级后 refresh 时长不变，access 自动收窄为 30 分钟。
- **登录 / 注册 / 续期**均下发 `{ token, refreshToken, expiresIn, user }`。
  新增两项**刻意可选**：已部署的旧客户端只认 `token`，设为必填等于宣布它们不合规。
- **续期**：`POST /api/auth/refresh` 优先用 `body.refreshToken`；不带 body 时
  回退到 `Authorization` 的 access 滑动续期（旧客户端 / 契约脚本）。
  refresh 无效/过期/类型不符 → **401 / 40100，且不回退**——否则「长令牌已失效」
  会被掩盖成「还能续」，用户能靠一张过期令牌无限滑下去。续期返回**新的一对**
  令牌（滑动，活跃用户不会在 7 天时被硬登出）。
- **吊销不分类型**：`/logout` 自增 `token_version`，两类令牌同时失效。
- **已知取舍**：refresh **未做一次性重放检测**（无状态；换发后旧 refresh 在自身
  过期前仍可用）。要做检测需引入令牌表或 jti 黑名单，当前依赖 `token_version`
  提供「用户主动登出即全部失效」的能力。

前端配套见 `mingri-toutiao-vue/src/api/http.ts`：三键同写同清
（`token` / `refresh-token` / `token-exp`）、到期前 60 秒主动续期（并发单飞）、
401 时先续期重试一次、仍失败才清登录态。

## 高敏操作二次校验（C10）

**问题**：会话令牌被偷（XSS / 共享设备 / 日志泄露）后，攻击者拿到的不是
「只读权限」，而是**与本人同等的能力**——包括把管理后台交出去、删掉 RSS 源、
顶着别人的名字说话。这些操作的共同点是**不可逆或影响全局**。

**机制**（`app/step_up.py`）：

```
POST /api/auth/step-up   { password }        →  { stepUpToken, expiresIn }
受护端点                   + X-Step-Up-Token   →  放行 / 40304
```

- **前置是已登录**（access 有效）：身份由令牌给出，body 里**不收手机号**——
  否则这个端点就成了「可指定账号的登录端点」，猜密码的人顺带获得账号枚举。
- **复用登录的账号维度锁定**（`login_guard`，scope=`stepup`）：二次校验若不做
  防爆破，它本身就是密码 oracle——猜对一次即可删源。scope 隔离让「二次校验
  失败」不会把用户的登录也锁掉（两件事），但阈值/窗口共用一套配置。
- **复用 JWT 类型校验**（`typ=stepup`）：与 access/refresh 同源签发，三者互不
  通用；有效期按类型取默认（5 分钟）。
- **复用 `token_version` 吊销**：登出即自增版本号，step-up 令牌同时失效。
- **403 / 40304 而非 401**：401 在本项目代表「会话失效」（前端会清登录态跳
  登录页），而这里用户的登录态是好的，只是缺一张二次校验令牌。

**覆盖范围**（按「不可逆或影响全局」这条线划定）：

| 端点 | 为什么算高敏 |
| --- | --- |
| `PUT /me/nickname` | 用户侧唯一改身份的操作，且影响人工复核状态 |
| `POST /api/admin/users` | 授权管理员：一次误操作 = 把管理后台交出去 |
| `DELETE /api/admin/users/{phone}` | 回收管理员：影响面在「人」不在数据 |
| `DELETE /api/admin/sources/{key}` | 影响全站抓取，恢复要靠审计快照手工重建 |
| `POST /api/admin/sources/reset` | 丢弃全部自定义源，恢复出厂 |

**未覆盖的及理由**：内容治理类（资讯/评论的 hide/restore/review、词库增删）
**可逆且有审计日志**（`admin_audit_logs`）——检测能力已具备，预防的边际收益
低于「每个页面都要加弹窗」的成本。机制就绪后，纳入一个端点只是加一个
`Depends(require_step_up)`。

**顺序是有意的**：管理端点同时声明 `require_admin`（在前）与 `require_step_up`
（在后），未授权者拿到的是 **40301**（权限不足）而不是 40304——不向外透露
站内有哪类操作需要二次校验。

## 资讯列表的两处性能债（B2 / B3）

### B2：默认走 keyset 分页，首页不再全表 `count()`

`GET /api/news` 的 `page` 参数现在是**可选**的：

| 请求 | 行为 |
| --- | --- |
| 不传 `page`（默认） | keyset 分页：首页与续页同一套查询，**不做全表 count**，`total`/`page` 返回 `null` |
| 传 `page`（兼容） | 旧的 offset 分页：`total` 有意义，深分页仍 O(offset) |

此前首页（还没拿到 cursor 时）落在 offset 分支里，于是**每次打开页面都做一次
全表 `count()`**——那条路径上的用户占比是 100%。现在前端不再发 `page`
（`src/api/news.ts` 有断言守住），首页与翻页都走 keyset。

保留 offset 分支是刻意的：仍需要「第 N 页 / 共多少条」语义的调用方存在，
而且改掉会让老客户端直接崩。

`total` 为 `null` 时前端回落 `res.total ?? res.list.length`——用户侧界面本来
就不消费列表的 `total`（只有管理端与搜索页在用），所以没有可见影响。

**测试断言的是"不出现 COUNT 语句"，不是响应形状**：
只把 `total` 改成 `null` 而底下照样 `count()`，契约看不出差别，债还留着。
`tests/test_list_perf.py` 用 SQLAlchemy 事件抓真实执行的 SQL 来守这条。

### B3：频道偏好（`for-you`）缓存

`ranking.channel_preferences()` 原本是 `for-you` 每请求两次 JOIN + GROUP BY，
而一次列表翻页可能打十几个请求——同一秒里算十几次完全相同的聚合。

- 缓存 5 分钟（`CHANNEL_PREF_TTL_SECONDS`），实现在 `app/cache.py`；
- **失效优先于过期**：收藏 / 浏览历史的任一写入路径都会
  `invalidate_channel_preferences(user.id)`（键的拼法只在这一个函数里，
  散开写迟早拼错，而"拼错"的后果是缓存永不失效且**没有任何报错**）；
- **空偏好不缓存**：那是"还没产生行为"的多数访客，缓存它只在缓存里堆一堆
  永不命中的键。

`app/cache.py` 与 `ratelimit` 同一套口径：进程内 / Redis 双实现、Redis 探活失败
降级到进程内并只告警一次、**任何缓存异常都不许冒泡**（读失败 = 未命中，
写失败 = 不缓存）。缓存是这个系统里唯一可以随时失效的组件，它出错必须无感。

键前缀 `cache:` 与限流的 `rl:` 分开——两个组件共用同一个 Redis 键空间，
撞了会互相污染。`CACHE_ENABLED` 在**调用时**读 `config` 的模块属性，
测试环境在 `conftest.py` 里钉 false（否则跨文件的进程内缓存会串味：
每个测试文件都重建 sqlite，`user_id` 又从 1 开始重新分配）。

## 修改密码与管理员代重置（C4）

此前**没有任何改密通道**：用户忘记密码只能无解，管理员也只能新建账号
（旧号的数据与身份就此悬空）。本项目**没有可送达的凭证通道**（无短信、无邮箱，
见 backlog C3），所以最小可用解法是「管理员线下核实身份 + 代发一次性临时密码 +
强制本人改密」——把「验证身份」放在人身上，而不是假装系统能确认你是谁。

| 接口 | 说明 |
| --- | --- |
| `PUT /api/auth/password` | 自助改密。入参 `{ oldPassword, newPassword }`，需登录 + 二次校验（C10） |
| `POST /api/admin/users/{phone}/reset-password` | 管理员代重置。需管理员 + 二次校验，返回一次性临时密码 |

**自助改密的两道证明**：`require_step_up`（令牌 + 密码）**和** `oldPassword`。
单靠 step-up 不够——用户被诱导弹窗时输入的仍是他自己的密码，而「旧密码错误」
这道校验能挡住其中一部分。

**改密后自增 `token_version`**：改密的语义就是「作废旧的一切凭证」，其他设备上
的 access/refresh 立即失效。调用方手上的令牌也因此失效，所以响应**直接下发新的一对**
——否则用户改完密会被自己踢下线。

**强制改密（受限态）**：`users.must_change_password`（迁移 0028）为真时，
账号进入受限态——

| 允许 | 拦截（403 + 40305） |
| --- | --- |
| 浏览资讯、搜索、看消息、改自己的资料、**改密码** | 发评论 / 点赞 / 删评论、收藏与浏览历史的写、改昵称、**进任何管理端** |

拦截放在**两个地方**而不是每个页面：
- 用户侧写操作用 `deps.require_password_current` 依赖（`comments.py` / `me.py`）；
- 管理端**全部**端点收敛在 `admin.require_admin` 一处判断——新增端点自动继承，
  不会漏。拿临时密码管后台 = 临时密码长期有效，「强制改密」就名存实亡。

只拦写、不拦读：把账号整个锁死除了让人焦急，没有安全收益；真正要防的是
「拿着临时密码对外产生影响」。

**代重置的三处刻意**：
- **临时密码由服务端生成**（`security.generate_temp_password`，字符集剔除 `0/O`、`1/l/I`
  等易混字符——它靠口头/手写传达，抄错率必须压到最低），管理员不该替用户挑密码；
- **不能重置自己**（40007）：那是一条把自己踢下线的路，自助改密才是正解；
- **审计不含明文**（`user.password.reset`）：审计表长期留存，写进明文等于给任何
  只读权限的人发一份密码清单。

用户不存在返回 **40400**（与公开接口同码）：不能靠错误码区分「手机号是否已注册」。

## 管理端鉴权（router 级，C6）

管理端鉴权**不在端点函数体里**，而是挂在 router 上：

```python
router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)
```

- `require_admin`（`app/routers/admin.py`）：未登录 → **401**，已登录但非管理员 → **403 / 40301**，
  判定复用 `is_admin()`（`ADMIN_PHONES` 白名单 ∪ `admin_users` 表，双源并集）。
- **为什么在 router 级**：此前 34 个端点各自在函数体首行写 `_require_admin(user, db)`。
  每个都写了，但这是「靠人记住」的约束——新增端点忘写一行就直接成为越权面，
  而 34 个端点的 review 很难逐条核对。`dependencies` 对路由表里**每个**端点生效，
  包括未来新增、以及忘记声明 `user` 参数的端点，**漏写在结构上不再可能**。
- **需要 actor 的端点**（写审计/日志，21 个）用 `user: User = Depends(require_admin)` 取到管理员；
  FastAPI 在同一请求内缓存依赖结果，与 router 级那次共用实例，不会重复查库。
  只读列表端点（13 个）不再声明 `user` 参数。
- **结构性守卫**：`tests/test_admin_guard.py` 用 `app.openapi()["paths"]` 枚举全部
  `/api/admin` 操作（26 路径 × 34 操作），逐个断言「未登录 401 / 非管理员 403+40301」，
  并断言源码中不再存在逐端点守卫调用（防重构回退）。
  > 取清单必须用 `app.openapi()["paths"]`：`app.routes` 里的 `include_router` 是
  > `_IncludedRouter` 包装（`path=None`），直接遍历只能拿到少量直连路由。

## 运维接口鉴权（C5）

`/api/health` 与 `/api/metrics` 会暴露**连接池状态**与 **Top20 路径分布**，
不应公开可读。两者统一走 `require_ops_access` 依赖（`app/deps.py`）。

| 配置 | 行为 |
| --- | --- |
| 设置 `OPS_TOKEN` | 必须携带 `Authorization: Bearer <token>` **或** `X-Ops-Token: <token>`；缺失/错误 → **403 / 40303** |
| 未设置 + `ENVIRONMENT=development` | **放行**（本地、CI、契约检查零改动） |
| 未设置 + `ENVIRONMENT=production` | **拒绝（403 / 40303）**——fail-closed，避免忘记配置就把内部状态挂公网；启动时会打印告警 |

- 令牌比较用 `secrets.compare_digest`（定时安全，不按字符泄露前缀）。
- 刻意返回 **403 而非 401**：本项目里 401 代表「会话失效」，前端据此清登录态并跳登录页；
  运维接口与用户会话无关，用 401 会误导客户端。用户 JWT **不能**当运维令牌用（不同信任域）。
- 生产探活配置示例：

```bash
# 生成令牌
python -c "import secrets;print(secrets.token_urlsafe(32))"
# 负载均衡 / Prometheus 探针
curl -H "X-Ops-Token: $OPS_TOKEN" http://127.0.0.1:8000/api/health
```

> 生产未配置 `OPS_TOKEN` 时 `/api/health` 也会 403 —— 这是刻意的：健康检查里带着池状态，
> 与指标同属「内部可观测面」。若探针不便带令牌，请改由内网独占该路径，
> 或改用不含内部状态的独立存活探针。

## 安全响应头（C9）

C9 分两半落：**前端文档**（承载 `localStorage` 令牌的那个 HTML）的 CSP 由构建期
注入 `index.html`（见前端 README），**后端响应**由 `app/security_headers.py` 处理。

| 响应头 | 值 | 作用 |
| --- | --- | --- |
| `X-Content-Type-Options` | `nosniff` | 关闭 MIME 嗅探：否则一个"看起来像 HTML"的 JSON 响应可能被当页面解析 |
| `Referrer-Policy` | `no-referrer` | 不外泄来源 URL（接口路径本身带业务语义） |
| `X-Frame-Options` | `DENY` | 禁止被嵌套；与 CSP `frame-ancestors` 互补（覆盖老浏览器） |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` | **仅 `/api/*`** |

- **为什么 API 的 CSP 是 `default-src 'none'`**：JSON 响应没有文档上下文，
  就没有任何资源需要加载——任何加载都是异常。即便某响应被塞进注入内容，
  浏览器也不会执行其中的脚本或外链（"本不该有"正是策略要兜住的那类事）。
- **为什么只给 `/api/*`**：`/docs`（Swagger UI）依赖 CDN 脚本与内联脚本，
  套上 `default-src 'none'` 会直接打不开。docs 只在 development 暴露
  （`DOCS_URL` 生产为 `None`），不为它放宽策略即可；非 `/api` 路径仍拿基础头。
- **注册顺序是有意的**：Starlette 的 `add_middleware` 往栈头插，**后注册者在最外层**，
  所以本中间件写在限流之后——限流直接返回的 429 也带得上这些头
  （`tests/test_security_headers.py` 用源码顺序断言守住这一点）。
- **开关**：`SECURITY_HEADERS_ENABLED`（默认开）。关闭仅用于前置网关已统一发这些头时，
  避免重复或冲突。

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

### 资讯审核与下架（迁移 0020）

内容管道的最后一环：**进来了不等于能留着**。此前只有源级开关（整个信源停用），
没有条目级处置——发现单条问题资讯（来源不可靠 / 涉嫌侵权 / 内容错误）时，
运营只能去改数据库，或把整个源停掉（误伤同源的好内容）。

**① 软下架，不硬删**（对齐 `Comment.is_deleted` 先例）。合规上要"保存记录"，
运营上要"误下架可恢复"；硬删两者都做不到。`hidden` / `hidden_at` / `hidden_reason`
三列，`hidden` 非空且 `server_default=false`——**存量行必须"可见"而不是 NULL**，
否则过滤条件 `hidden IS FALSE` 会漏掉它们，老资讯会从公开列表里凭空消失。

**② 理由必填是准入条件**。schema 层 `min_length=1` + strip 后判空（≤300 字），
空则 422。下架不留理由，审计时间线上就只剩一个光秃秃的 id——
"谁、为什么、下架了什么"三问只答得上一个，时间线就白记了。

**③ 后审核模式，不改内容管线**。RSS 聚合按 id 去重，恢复即实时可见，
无需等下一轮抓取；热度重算仍然计算下架行——恢复那一刻拿到的就是新鲜分，
不需要为它写任何特殊分支。

**④ 公开四路过滤 + 评论 404**：列表 / 详情 / 搜索 / 相关推荐，发评论也 404。
已下架与"不存在"**同码 40400**——对外不暴露"这条被下了"，
避免用户（和爬虫）从错误码差异里推断出处置状态。管理端用 40404 区分：
运营查的是"库里有没有这条"，不存在就是不存在。

**⑤ 不给 `hidden` 建索引**。下架集预期很小（运营动作，不是批量状态），
建索引只会让每条热门 feed 查询多带一个索引条件；全表过滤在现有量级上更便宜。

**⑥ 幂等语义**（与「无变化不记」同思路）：
重复下架**不覆盖第一次的理由、不重复记审计**（重复下架要么是界面 Bug，
要么是网络重试，都不该再造一条痕迹）；恢复则清空理由/时间——
理由只对下架状态有意义，留着会让下一次下架的审计记录读起来张冠李戴。

**⑦ 审计三个新动作**：`news.hide`（detail 带 `{reason, title}`——target 只有 id，
运营认标题不认 id）/ `news.restore`（`{title}`）/ `news.update`（`{changes, title}`，
字段级 diff，对齐 `source.update`）。

置顶/取消置顶走 `PATCH` + 字段级 diff：无变化的 PATCH（前端常因整体提交触发）
不该灌进时间线。多词入参一律驼峰（`isTop`）——蛇形会被 pydantic `extra='ignore'`
静默丢弃，表现为"开关永不生效"且前后端各自单测都不红，已用
「HTTP 入参 → 数据库」断言焊死。

### 热度重算独立调度（迁移 0021）

热度分的时间衰减此前**寄生在抓取 cron 里**（`_run_once` 抓完顺带全量重算）。
三个后果：`FEED_CRON_ENABLED=false` 时衰减永不推进（hot / for-you 排序冻结）；
重算节奏被抓取周期绑定（抓取 30 分钟一轮，衰减也只剩 30 分钟粒度）；
`run_ingest` 抛异常时重算被连带跳过。P1-21 把它拆成**独立 cron**。

**① 独立开关 / 周期 / 第二把 advisory lock**：

```bash
HOT_CRON_ENABLED=true           # 热度重算开关（独立于 FEED_CRON_ENABLED）
HOT_CRON_INTERVAL_MINUTES=15    # 重算周期
HOT_STALE_MINUTES=60            # staleness 窗口（见下）
```

两条 cron 各持一把会话级 advisory lock，**键不同**（抓取 `0x746F7574` "tout"、
热度 `0x686F74` "hot"）——热度任务不该因为另一实例正在抓取而整轮跳过，
那等于把拆开的又粘回去。

**② 增量重算 + 锚点列**（`news.hot_score_at`，可空、**不建索引**）。
只有锚点为 NULL（迁移前存量 / 新入库未算分）或早于 `now - HOT_STALE_MINUTES`
的行才参与本轮。窗口（60）大于周期（15）时，每行每窗口最多重写一次——
无变动也全表重写是纯写入放大（dead tuple / WAL），而半衰期 48h 下
60 分钟约 2% 衰减，排序无感。互动 bump（评论/收藏）与 `me.py` 的批量合并
（登录时稀有事件，行为不变）都会刷新锚点，刚算过的行不会被重复扫到。

**③ 为何是字符串锚点**：与 `now_str()` 同格式（naive、秒精度）的 ISO 串
**字典序即时间序**，staleness 判定免一次类型转换，也规避 SQLite/PG 时区语义差异。

**④ `/api/health` 增 `hotRecompute`**（`enabled` / `lastAt` / `lastCount` /
`lastError`，进程内状态、重启清零）。**抓取被关停时，这是热度任务仍活着的
唯一信号**；`lastError` 非空即上轮失败（热度是派生数据，失败只记录不终止循环）。

### 评论治理（迁移 0022）

讨论区的最后一环：**进来了不等于能留着**。此前只有作者删自己的评论（`is_deleted`，
不可逆、无留痕），运营面对违规评论（广告 / 人身攻击 / 与文章无关）只能整篇删资讯
或改数据库。P1-22 复刻 P1-20 的处方，把「隐藏」做成**可恢复、留痕、实时生效**的
运营动作。

**① 软隐藏，不硬删，与 `is_deleted` 分工**：作者删 = 不可逆（没有可恢复的状态，
治理视图同样不返回）；运营隐藏 = 可恢复。`hidden` / `hidden_at` / `hidden_reason`
三列，`hidden` 非空且 `server_default=false`——**存量行必须"可见"而不是 NULL**，
否则 `hidden IS FALSE` 会漏掉它们，老评论会从公开列表里凭空消失（与 0020/0018
同款教训，功能测试走新建行看不出来，由迁移守卫测试钉住）。

**② 理由必填是准入条件**（与下架同一处方）：schema 层 strip 后判空、≤300 字，
空则 422。隐藏不留理由，审计时间线上就只剩一个光秃秃的 id——评论没有标题，
时间线认**内容片段 + 资讯标题 + 作者名**（运营认内容不认 id）。

**③ 公开五路实时过滤**（后审核模式，不动内容管线）：主列表 / 回复列表 / 主评论
预览 / `comment_count` 计数 / 不可交互（对已隐藏评论点赞 40401、回复 40003——
与"不存在"同码同文案，不暴露"它被隐藏了"）。**主评论隐藏后其回复亦不可见**：
失去语境的回复不该继续裸露在讨论区。恢复即实时回来，无需等任何定时任务。

**④ 40405 与公开端 40401 刻意分开**：管理端查的是"库里有没有这条"，
已软删除对治理视图同样不存在；公开端统统一码"不存在"。

**⑤ 计数与热度联动**：隐藏 -1、恢复 +1（下限 0，防历史脏数据算成负数），
并 `bump_hot_score`。作者删**已隐藏**评论不重复扣（`was_visible` 守卫）——
「显示 N 条却只看到 N-1 条」是 P1-12 修过的旧 bug，不能换个入口复犯。

**⑥ 不给 `hidden` 建索引**：隐藏集预期很小（运营动作，不是批量状态），
与 `is_deleted` 同理；全表过滤在现有量级上更便宜。

**⑦ 幂等语义**：重复隐藏不覆盖第一次的理由、不重复记审计；恢复清空理由/时间
（留着会让下一次隐藏的审计记录读起来张冠李戴）。审计两个新动作：
`comment.hide`（detail 带 `{reason, content[:50], newsTitle, userName}`）/
`comment.restore`（`{content[:50], newsTitle, userName}`）。

**⑧ 多词查询参数一律驼峰**（`newsId`）：FastAPI 对未知查询参数**静默忽略**
（不报错、不过滤），写成蛇形 `news_id` 会表现为"按文章筛选永不生效"
而前后端各自单测都不红——与 body 的 pydantic `extra='ignore'` 同款缝，
已用「蛇形不过滤也不报错」的用例焊死（功能测试 + 契约第 15 段双重）。

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

> **CI 已内置两道迁移校验**（F1）：① `alembic upgrade head`（SQLite，兼作启动前置——
> 没有它，CI 上的后端其实起不来）；② `alembic check`（**PostgreSQL 服务容器**）。
> 第二道必须在 PG 上跑：SQLite 无法反射外键与 GIN 索引，`alembic check` 会把
> `comments` 的 FK、`ix_news_title_trgm` 等误报成「新增迁移操作」（本地已实测复现）。
> 另：CI 的「启动后端」步骤在探活失败时**显式失败并打印日志**——此前探活失败也会被
> 当成成功，契约检查可能跑在一个根本没起来的后端上。

> **CI 已内置依赖漏洞审计**（C8）：后端 job 在装完依赖后跑 `pip-audit`，
> 前端 job 在 `npm ci` 后跑 `npm audit --audit-level=high`。两个实现细节：
> ① `pip-audit` 审计的是**整个已安装环境**，venv 自带的 pip 也在范围内——
> 所以步骤里先 `pip install --upgrade pip`，否则 CI 会红在与项目无关的
> 旧版 pip 上（PYSEC-2026-3721），这种「狼来了」最伤审计可信度；
> ② `npm audit` 走 npmjs 官方 registry 的审计端点，CI 默认 registry 即可，
> **本机用 npmmirror 的同学跑不了**（镜像不实现 `/-/npm/v1/security/*`，
> 报 `NOT_IMPLEMENTED`）——需要本地自查时临时加
> `--registry=https://registry.npmjs.org`。阈值取 high：低危噪音交给
> dependabot / 人工，不让 CI 变成天天红的狼来了。

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
  复合索引 `ix_rss_health_key_at`，随源级联删除）、
  `0020_news_soft_hide`（资讯软下架 `news.hidden` / `hidden_at` / `hidden_reason`，
  `hidden` 非空 `server_default=false`，**刻意不建索引**——下架集小，避免热 feed 索引膨胀）、
  `0021_hot_score_at`（热度重算锚点 `news.hot_score_at`，可空无默认——NULL 即
  「从未算过」天然覆盖存量；**刻意不建索引**，表有 prune 上限，全表找过期行足够便宜）、
  `0022_comment_soft_hide`（评论软隐藏 `comments.hidden` / `hidden_at` / `hidden_reason`，
  `hidden` 非空 `server_default=false`，**刻意不建索引**——隐藏集小，与 `is_deleted` 同理）、
  `0023_sensitive_words`（敏感词库 `sensitive_words` + 命中日志 `sensitive_hits`，
  `word` 唯一索引、日志记词本身而非外键；`comments` 增 `pending_review` / `reviewed_at`，
  `pending_review` 非空 `server_default=false` 且**不建索引**——它与 `hidden` 正交且短暂）。
  `0024_nickname_moderation`（昵称词库 `nickname_words`（`aliases` JSON 列，PG 上 JSONB）+
  命中日志 `nickname_hits`（记 `matched` 实际命中模式）；`users` 增 `name_pending_review`
  （非空 `server_default=false`，**不建索引**——待复核集小，避免拖慢每次昵称写入）/
  `name_reviewed_at`）。
  `0025_news_content`（资讯正文 `news.content`，Text **可空**——NULL 即「暂无正文」，
  详情接口据此如实返回 null；**不建索引**：只按 id 点查，无按正文检索需求）。
  `0026_message_created_at`（消息排序键 `messages.created_at`，**可空、不设 server_default**——
  存量行无可信时间源（`time` 是「1小时前」相对文案，回填等于编造数据），排序用
  `coalesce(created_at, '')` 兜底让存量行沉底；建 `ix_messages_created_at` 服务 ORDER BY，
  **刻意不做 `(user_id, created_at)` 复合索引**——可见范围是 OR，走不了复合索引）。
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
  定时任务走**独立热度 cron**（P1-21）`recompute_hot_scores(only_stale=True)`
  增量刷新时间衰减——只扫锚点（`hot_score_at`）过期的行，抓取停不停都按时推进。
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

接口 `GET /messages`、`GET /messages/unread-count`、`PATCH /messages/{id}`、
`POST /messages/read-all` 均只统计**可见范围**内的消息（定向给自己 + 广播）。

**`PATCH /messages/{id}` 要求登录，且「不存在」与「不可见」同码 40400**（H11 修复）：
此前该端点用 `db.get(Message, id)` 直接取行并返回 `title` / `summary` / `newsId`，
依赖还是 `get_current_user_optional`——任何登录用户（乃至未登录）只要拿到或猜到
消息 id 就能读到他人定向消息的内容，而种子 id 形如 `m1..m4` 可直接枚举。
现在先按 `visible_condition` 过滤再操作：不可见一律 40400，对外不暴露
「它存在但你看不到」（与资讯软下架同码同一条口径）。

> 前端配套：`stores/messages.ts` 在**无登录态时只落本地已读、不发起该请求**——
> 否则后端 401 会经 `api/http.ts` 触发全局未授权跳转，把游客踢到登录页。
> 同时 `http.ts` 的 401 处理改为**仅在原本持有 token 时**广播未授权事件。

### 列表排序：按真实时间，不按随机 id（A2）

此前列表按 `Message.id DESC` 排序、游标用 `Message.id < cursor`，而互动消息的
id 是 `msg_{uuid4hex}`——**随机串**。后果：排序与真实时间无关（最旧的消息
可能排在最前，顺序与 `time` 文案脱节），keyset 游标语义失效（「末条之后」
翻出来的可能比它更晚）。

迁移 **0026** 增加 `messages.created_at`（可空 + `ix_messages_created_at`）：

- **可空、不设 server_default**：存量行没有可信时间源——`time` 列存的是
  「1小时前」这类相对文案，不能反推绝对时间，**回填等于编造数据**；
- **排序键 `coalesce(created_at, '')`**：与 `news.published_at` 的 `_PUB`
  同款处方——空串在 DESC 下排最后，存量行自然沉底，且 PG 与 SQLite 行为一致
  （不依赖 `NULLS LAST`，两者语法支持度不同）；
- **模型侧 `default=now_str`**：`notify.py` / `alert.py` / 种子等所有新建路径
  无需逐个改动即自动写入时间戳，单一来源；
- **单列索引而非 `(user_id, created_at)` 复合索引**：可见范围是
  `user_id = X OR user_id IS NULL`（OR 走不了复合索引），该索引服务的是
  「排序」而非「过滤」——避免每页全表排序。

**游标格式不变**：仍是上一页末条的 id。服务端按该 id（同样带可见范围过滤）
回查排序键，取「严格更早」的一页——老格式游标继续可用、前端零改动，
且客户端改游标也翻不出可见范围。锚点行不存在（被删/不可见）时忽略游标
返回第一页：消息没有清理任务，这只在手工删数据后出现，「从头开始」
比报错更不影响使用。

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
- **预览取「最早 2 条」（P1-26 已把 LIMIT 下推到 SQL）**：窗口函数 `row_number() OVER
  (PARTITION BY root_id ORDER BY created_at, id)` 在**子查询内**算 `rn`，外层
  `WHERE rn <= 2` 过滤——SQL 不允许 `WHERE` 直接引用窗口列，所以必须套一层子查询。
  早期实现在同一个 SELECT 里算 `rn` 却在 Python 侧截断，等于把每个主评论的
  **全部回复**捞进内存（爆款评论数百上千条回复 × 一页 20 条主评论）。
  `liked` 判定一次 IN 查询覆盖主评论 + 预览回复（无 N+1）。
- **点赞关系表化**（迁移 `0014`）：`comments.liked_by` JSON 数组 → `comment_likes`
  关系表（`(comment_id, user_id)` 复合主键，`ON DELETE CASCADE`）。复合主键天然防重复点赞，
  `likes` 冗余计数随行的插入/删除同步维护；批量判定同理一次 IN 查询。
- **删除语境**：主评论软删除后，其回复**不再可见**（replies 接口返回空列表）——
  主评论消失后，失去语境的回复不应继续裸露在讨论区；回复行本身保留，可追溯。
- **通知**：主评论广播给同样评论过该资讯的其他用户；回复只通知被回复者。

## 评论内容审核：敏感词（P1-23）

> 迁移 `0023_sensitive_words`；引擎 `app/moderation.py`；管理端 `/api/admin/words*`。

### 接入位置：只在**写路径**的服务端

唯一写入口 `POST /api/news/{news_id}/comments`（主评论与回复都走它），落库**之前**检测：

- **为什么不在读路径**：列表/详情每秒被读很多次，评论一天写不了几条——成本放在写路径，读路径零开销；
- **为什么不在前端**：前端校验只防误触，防不了直接调 API。服务端是唯一可信边界；
- **为什么不在落库后补救**：block 的语义是"这条不该存在"，先落库再删会留下可观测的时间窗。

### 三种动作

| 动作 | 语义 | 适用 |
| --- | --- | --- |
| `block` | 拒绝提交（**不落库**），返回 40005 | 高危内容 |
| `mask` | 替换为 `*`，**照常发布**并置 `pending_review` | 默认（`MODERATION_DEFAULT_ACTION`） |
| `allow` | 白名单：被它**完全包裹**的命中区间豁免 | 降误杀（如「福利待遇」豁免「福利」） |

默认 mask 而非 block：机器的判断是概率性的，"先脱敏 + 让人看一眼"的代价远小于
"把用户的话直接吞掉"——后者不可逆。

### 匹配策略（对抗四类绕过）

归一化由 `moderation.normalize()` 完成，返回 `(归一化串, 原文下标映射)`：

1. **大小写**：`casefold()`（比 `lower` 更彻底，覆盖 ß → ss）；
2. **全角/半角与兼容字符**：**逐字符** NFKC（整串会改变长度、丢下标）；
3. **空格 / 标点 / 零宽 / emoji 干扰**：只保留 Unicode 类别 `L*`（字母，含 CJK/假名/谚文）
   与 `N*`（数字），其余全部丢弃——「傻 逼」「傻-逼」「傻​逼」归一后与「傻逼」同形；
4. **重复与变形**（如「好好好好好」压缩）**不做**：误杀率高，交给人工复核。

脱敏改的是**原文**（靠下标映射定位），长度取**词长**而非原文区间长度——
否则等于告诉对方"绕过去几个字符就能过"。

算法是 **Aho-Corasick 自动机**（`_Automaton`），`O(n + 命中数)`，不是"逐词 `in` 判断"。

### 性能

- 评论正文上限 500 字，单次扫描纯内存、数十微秒级；
- **检测路径不查库**：词库缓存在进程内，只有 TTL（默认 30s）到期才回库查一次
  **指纹**（`COUNT(*) + MAX(updated_at)`），指纹没变继续用旧索引；
- 重建索引是唯一有成本的动作（2000 词约几十毫秒），且只发生在**改词时**，
  不在请求关键路径上。

### 热更新

- 管理端增删改词 → `engine.invalidate()` → **本进程**下一次检测即重建（保存即生效）；
- 其他实例靠 TTL 兜底（30s 内感知），需要即时同步时调 `POST /admin/words/reload`。
  刻意不做跨进程广播：为一个"晚 30 秒生效"换一个 Redis 依赖不划算。

### 误杀控制（每条都被真实场景逼出来）

- 默认 mask；`allow` 白名单豁免；
- **最短词长** `MODERATION_MIN_WORD_LEN`（默认 2）挡在入库（42202）——1 字词在正常表达里遍地都是；
- **昵称命中一律降级为 mask**：昵称是账号级属性，用户没法在评论框里改它；
  因为昵称命中而拒绝发言 = 让用户带着"我什么都没做错"的困惑被永久禁言；
- **block 提示不回显命中词**：只说"包含不当内容"——回显等于给绕过者一份对照表；
- **命中日志留原文片段**（`snippet`，命中位置 ±30 字）：脱敏不可逆，误杀复盘与申诉只能靠它；
- **停用优于删除**：删除后命中日志里那个词会失去对应配置，复盘时读不出它当年是 block 还是 mask。

### 数据结构（迁移 0023）

- `sensitive_words`：`word`(唯一索引) / `action` / `category` / `enabled` / `hit_count` /
  `created_by` / `created_at` / `updated_at`（`updated_at` 参与指纹）；
- `sensitive_hits`：记**词本身**而非 `word_id`（删词后日志仍可读）；`field` 区分正文/昵称；
  `comment_id` 在 block 时为 `NULL`；`snippet` 为原文片段；
- `comments` 增 `pending_review` + `reviewed_at`。与 `hidden` **正交**：
  `hidden` = 已处置、不可见；`pending_review` = 未处置、可见（只是被脱敏）。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODERATION_ENABLED` | `true` | 总开关（关闭后评论完全不检测，走人工治理） |
| `MODERATION_CHECK_NAME` | `true` | 是否检测昵称（命中只脱敏，绝不阻断） |
| `MODERATION_CACHE_TTL_SECONDS` | `30` | 词库指纹检查间隔（0 = 每次请求都查库，仅测试用） |
| `MODERATION_MIN_WORD_LEN` | `2` | 入库最短有效字符数（归一化后计） |
| `MODERATION_MASK_CHAR` | `*` | 脱敏替换字符 |
| `MODERATION_DEFAULT_ACTION` | `mask` | 新增词的默认动作 |
| `MODERATION_MAX_WORDS` | `20000` | 词库上限（超出截断并告警，防误导入拖垮重建） |

### 管理端接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/admin/words` | 词库（keyword/action/enabled 筛选，limit+offset，按 `hit_count` 降序） |
| POST | `/api/admin/words` | 新增（太短 42202 / 重复 40905），保存即生效 |
| PATCH | `/api/admin/words/{id}` | 改动作/分组/启用（**不许改词本身**；字段级 diff 审计） |
| DELETE | `/api/admin/words/{id}` | 删除（审计留快照） |
| POST | `/api/admin/words/reload` | 强制重建本进程索引 |
| GET | `/api/admin/moderation/hits` | 命中日志（只读；word/action/field 筛选 + 游标分页） |
| POST | `/api/admin/comments/{id}/review` | 通过人工复核（幂等，只清 `pending_review`） |

审计动作：`word.create` / `word.update` / `word.delete` / `word.reload` / `comment.review`。

## 用户昵称敏感词校验（P1-24）

> 迁移 `0024_nickname_moderation`；引擎 `app/nickname_moderation.py`；管理端 `/api/admin/nickname-words*`、`/api/admin/nickname-hits`、`/api/admin/nickname-reviews*`。

### 接入位置：注册与改名同一条链

两个写入口共用同一个引擎入口，**规则不搞两套**：

- 注册（`auth.py`，`body.name or 默认名`）；
- 改名（`PUT /api/me/nickname`，P1-24 新增——此前昵称**没有任何修改入口**，
  一个脏昵称会跟着用户的每一条评论、每一个通知展示出去，等于让绕过者换个地方贴）。

**为什么另起一套而不复用 P1-23 的 `sensitive_words`**（不同语义的资源不挤进同一张表，
否则"这个词是拦评论还是拦昵称"永远说不清）：

1. **动作集不同**：评论有 `mask`（脱敏后照常发布）——昵称没有这个语义，
   昵称是账号级属性，"脱敏后发布"意味着改掉用户自己起的名字，而他此刻改不了。
   昵称的动作是 `block`（拒绝设置）与 `review`（放行 + 待人工复核）；
2. **归一化强度不同**：评论刻意**不做**重复压缩（误杀率高，见 `moderation.py`）；
   昵称场景要求覆盖"重复字符"绕过，且昵称短、无语义（不是句子），压缩的误杀面小得多；
3. **别名只对昵称有意义**：拼音全拼/首字母/谐音是昵称绕过的主力（"shabi"、"sb"），
   评论正文走自然语言不需要它。

引擎**复用** `moderation.AhoCorasick` 自动机与指纹缓存模式（不复制第二份实现）。

### 三种动作

| 动作 | 语义 | 适用 |
| --- | --- | --- |
| `block` | 拒绝设置（**不保存**），返回 40006 | 高危内容 |
| `review` | **放行**并置 `name_pending_review` 进人工队列 | 默认（`NicknameWord.action` 缺省） |
| `allow` | 白名单：被它**完全包裹**的命中区间豁免 | 降误杀（如「射手」豁免「射」） |
| `pass` | 未命中，原样保存并清除待复核标志 | — |

默认 review 而非 block：机器的判断是概率性的，"先放行 + 让人看一眼"的代价远小于
"把用户的名字直接吞掉"——后者不可逆，且用户此刻无法在输入框里改一个不让他保存的名字。

### 匹配策略（对抗七类绕过）

归一化由 `nickname_moderation.normalize_forms()` 完成，产出**四个形态**，
每个形态各自带 `index_map`（命中后能回到原文定位）：

1. **大小写**：`casefold()`（比 `lower` 更彻底，ß → ss）；
2. **全角/半角与兼容字符**：**逐字符** NFKC（`ｆｕｃｋ` → `fuck`、`①` → `1`）；
3. **空格 / 标点 / 零宽 / emoji 干扰**：只保留 Unicode 类别 `L*` 与 `N*`，
   其余全部丢弃——「傻-逼」「傻 逼」归一后与「傻逼」同形；
4. **重复字符**：`collapsed` 形态压缩连续重复段（`aabbcc` → `abc`，
   对付逐字重复使子串断裂的场景；`NICKNAME_COLLAPSE_REPEATS` 可关）；
5. **字母数字混淆**（leetspeak）：`4/@→a、3→e、1/!/|/l→i、0→o、5/$→s、
   7/+→t、8→b、6/9→g、2→z`。模式串与文本走**同一条管道**，任意一对一归并都安全；
6. **数字插字**：`nodigits` 形态把数字来源字符整体去掉（"博0彩" → "博彩"）——
   与 leetspeak 互补：leetspeak 把 0 当字母 o 用，本形态把 0 当噪声删掉；
7. **繁简体**：内置 curated 繁→简映射表（**非完整 OpenCC 表**——零运行时依赖，
   未覆盖的繁体变体由运营配成词条别名）。

**拼音与谐音**没有字符级算法可解（同音字替换需要字→拼音表），走**词条别名**
（`nickname_words.aliases`，拼音全拼/首字母/谐音/变形），与主词走同一条归一化管道——
这是零新依赖下覆盖"字母写法绕过"的路线。

### 性能

- 昵称上限 24 码点，四形态各扫一遍仍是微秒级；
- **检测路径不查库**：词库缓存在进程内，只有 TTL（默认 30s）到期才回库查一次
  **指纹**（`COUNT(*) + MAX(updated_at)`），指纹没变继续用旧索引；
- 重建索引只发生在**改词时**，不在请求关键路径上。

### 热更新

与 P1-23 同构：管理端增删改词（**含改别名**——别名是模式串的一部分）→
`nickname_engine.invalidate()` → 本进程保存即生效；其他实例靠 TTL 兜底，
需要即时同步时调 `POST /admin/nickname-words/reload`。

### 误杀控制

- 默认 review；`allow` 白名单豁免；
- **最短词长** `NICKNAME_MIN_WORD_LEN`（默认 2）挡在入库（42204，
  主词与**每个别名逐个查**——引擎会静默跳过过短形态，"配了却没生效"比报错难发现）；
- **block 提示不回显命中词**：只说"包含违规内容"——回显等于给绕过者一份对照表；
- **命中日志留 `snippet`**（当次提交的昵称原文）：昵称整体即上下文，
  误杀复盘与申诉靠它；
- **停用优于删除**：删除后命中日志里那个词会失去对应配置；
- **未知 action 兜底 review**（`FALLBACK_ACTION`）：数据异常时降级为
  "放行 + 让人看一眼"，而不是把用户的昵称直接吞掉；
- **不做追溯式回填**：词库事后变严时，老名字在**下一次保存时**同样被校验，
  复核队列针对「新提交」，不回头翻旧账。

### 数据结构（迁移 0024）

- `nickname_words`：`word`（唯一索引）/ `action` / `category` /
  `aliases`（JSON 数组，拼音/谐音/变形）/ `enabled` / `hit_count` /
  `created_by` / `created_at` / `updated_at`（`updated_at` 参与指纹，有索引）；
- `nickname_hits`：一次提交一行，记**词本身**（删词后日志仍可读）+
  `matched`（实际命中的模式：主词或某条别名）/ `action` /
  `source`（`register`/`update`）/ `user_id` / `user_name` /
  `snippet`（昵称原文）/ `created_at`。**block 也留痕**——
  「拦了多少次改名」不能是黑箱，也识别不出反复尝试绕过；
- `users` 增 `name_pending_review`（非空默认 false，**不建索引**——
  待复核集预期很小，索引只会拖慢每次昵称写入）+ `name_reviewed_at`（复核时间）。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `NICKNAME_MODERATION_ENABLED` | `true` | 总开关（关闭后昵称不检测，走人工治理） |
| `NICKNAME_MIN_LENGTH` | `2` | 昵称最短长度（按 Unicode 码点计） |
| `NICKNAME_MAX_LENGTH` | `24` | 昵称最长长度（码点；emoji 记 1） |
| `NICKNAME_MIN_WORD_LEN` | `2` | 入库最短有效字符数（归一化后计，主词与别名都查） |
| `NICKNAME_CACHE_TTL_SECONDS` | `30` | 词库指纹检查间隔（0 = 每次请求都查库，仅测试用） |
| `NICKNAME_MAX_WORDS` | `20000` | 词库上限（超出截断并告警，防误导入拖垮重建） |
| `NICKNAME_COLLAPSE_REPEATS` | `true` | 是否启用重复压缩形态（collapsed 两形态） |
| `NICKNAME_ALLOWED_SYMBOLS` | `_-·.・` | 昵称符号白名单（空格在代码内补——`_env` 会 strip 整个值） |

### 输入边界（`validate_nickname_shape`）

长度 2~24（码点）；字符白名单 `L*`/`N*` + 有限符号 `_ - · . ・` + 内部空格；
空值/纯空白拒绝（先 trim 再校验）；代理字符（lone surrogate）与控制字符（Cc）拒绝；
emoji/符号（So）拒绝。**顺序刻意是「字符集先于长度」**——字符根本不能用，
比"太长"更根本。全部失败统一以 **42203** 返回，消息即面向用户的提示。

### 管理端接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/admin/nickname-words` | 词库（keyword 只匹配主词 / action / enabled 筛选，limit+offset，按 `hit_count` 降序） |
| POST | `/api/admin/nickname-words` | 新增（主词或任一别名过短 42204 / 重复 40906），保存即生效 |
| PATCH | `/api/admin/nickname-words/{id}` | 改动作/分组/别名/启用（**不许改词本身**；字段级 diff 审计） |
| DELETE | `/api/admin/nickname-words/{id}` | 删除（审计留快照；不存在 40407） |
| POST | `/api/admin/nickname-words/reload` | 强制重建本进程索引（返回生效条数） |
| GET | `/api/admin/nickname-hits` | 命中日志（只读；word/action/source/userId 筛选 + id keyset 游标） |
| GET | `/api/admin/nickname-reviews` | 待复核队列（keyword 按当前昵称/手机号筛；limit+offset；按最近被标记降序） |
| POST | `/api/admin/nickname-reviews/{userId}` | 通过人工复核（幂等；只清标志 + 写 `name_reviewed_at`，不改昵称；40408） |

审计动作：`nicknameWord.create` / `nicknameWord.update` / `nicknameWord.delete` /
`nicknameWord.reload` / `nickname.review`。

## 命中日志留存（C7，合规）

`sensitive_hits` / `nickname_hits` 的 `snippet` 存的是**用户原文**：脱敏不可逆，
它是判断"是否误杀"与受理申诉的**唯一证据**。但证据价值随时间衰减——
无限期保存用户原话本身就是合规风险（数据最小化原则）。

| 项 | 取值 |
| --- | --- |
| 保留期 | `HIT_LOG_KEEP_DAYS`，默认 **90 天**；`0` = 不清理（仅取证期临时使用） |
| 保底条数 | **无**（与 `prune_old_news` 的 `keep_min` 不同：保留期就是它的全部价值，保底反而留下一批最旧的用户原话） |
| 清理时机 | **每次启动扫一遍**（不依赖 cron，`FEED_CRON_ENABLED=false` 也会执行）+ 抓取 cron 每轮顺带清理 |
| 可见范围 | 仅 `GET /api/admin/moderation/hits`、`GET /api/admin/nickname-hits`，二者受 **router 级管理员鉴权**保护（C6）；公开接口不返回 `snippet` |

- 实现：`app/hit_logs.py::prune_hit_logs()`，按 `created_at < now - keep_days` 删除
  （A3 之后时间列同为 UTC aware，字典序即时间序）。
- 累计计数 `nickname_words.hit_count` **不随日志删除而回退**：前者用于发现高频词/误杀大户，
  后者是申诉材料，两个问题不同。
- 启动清理失败只告警、不阻断启动（合规尽力而为，但不该让服务起不来）。

## 资讯正文（E1）

> 迁移 `0025_news_content`（`news.content`，Text 可空）；抓取侧 `app/feeds/fetcher.py`；
> 详情接口 `GET /api/news/{id}` 多返回 `content`。

**要解决的问题**：详情页此前展示的是前端写死的占位文案（三段「据相关方面介绍……」），
而 RSS 的 `content:encoded` 早在 fetcher 里就被读取——只用来提图，**正文本身被丢弃**。
用户点进任何一条资讯看到的都是同一段假文本，这是产品可信度问题：界面在假装有内容。

### 数据流

1. **抓取**：`parse_feed` 优先取 RSS `content:encoded` / Atom `content`，缺失回退
   `description`（很多源只给摘要字段，但那也是真实文本，比「暂无正文」更有信息量）；
2. **清洗**：`_clean_paragraphs` 去标签、解实体、**保留段落**（块级标签与 `<br>` → 换行），
   段内压空白——与 `_clean`（压成一行，只适合摘要）刻意分工；
3. **截断**：`_truncate_paragraphs` 按**段落边界**截到 8000 字符（宁可少一段，
   不在句子中间砍断；单段即超限时退化为硬截断，防几千字无换行的源把库撑爆）；
4. **入库**：`upsert_news` 新增时写 `content`，增量刷新时**非空才覆盖**
   （某轮抓到无正文的条目不该把已有正文抹掉）；空正文落 `NULL` 而非空串；
5. **下发**：`to_out`（列表/搜索/相关推荐）**刻意不含** `content`——一页 20 条
   几 KB 一条是纯浪费；只有详情接口单独返回，无正文为 `null`。

### 前端

`NewsDetail.vue` 按 `\n` 切段渲染 `<p>`；无正文时显示「暂无正文」——
**占位文案那条路已经堵死**（spec 里有不断言回归）。

## 单元测试

```bash
pytest -q          # 626 项
```

> **测试环境由根级 `conftest.py` 统一钉死**（`DATABASE_URL=sqlite:///:memory:` 等）：
> `app.config` 在**首次导入时**读取环境变量，若由各测试文件自行设置，
> 谁的 import 先发生谁说话——全套跑与单文件跑结果不一致（曾出现整类用例时绿时红）。
> 用**赋值**而非 `setdefault`，确保开发者本机 `.env`（如指向 PostgreSQL）不会渗进测试。

- `tests/test_units.py`（36 项）：限流器（阈值/隔离/窗口过期/路径分 key）、PBKDF2 密码哈希与加盐、JWT 往返、RSS 解析与字段映射（RSS 2.0 / Atom / 图片提取 / 变体判定 / 稳定 id / 相对时间）、**正文提取与清洗**（E1：`content:encoded` 优先/回退 description/去标签保段落/按段落边界截断/单段硬截断/空源回空串）、**`blocked` 窥探不计次与 `reset` 清零**（C2：锁定预检不能给计数器续期、成功即清零）。
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
- `tests/test_news_moderation.py`（33 项）：**资讯审核与下架**（P1-20）——管理端列表
  （鉴权 401/403、默认 all 含已下架、status 筛选、非法 status 422、关键词标题/摘要命中、
  `%` 转义后不误匹配、频道筛选与非法频道 422、游标翻页不重不漏、坏游标 40003、
  detail 含 hidden 字段、未知 40404）、下架/恢复（往返、理由三种非法 422 与 300 边界放行、
  trim、**幂等保原理由且审计仅 1 条**、恢复幂等不记审计、404、新行默认可见）、
  公开四路过滤（offset total 过滤 + 游标不出现、详情 404 同码 40400 且恢复后回来、
  搜索排除、相关推荐排除且隐藏目标 404、评论列表+发表 404）、
  审计（hide 记 reason/title/actor 且 target 可查、restore 两条、update 记
  `{"isTop": [false, true]}`、无变化不记、404）、置顶（往返+公开列表置顶可见、
  空 body 保持、**蛇形 `is_top` 被静默丢弃**焊死）。
- `tests/test_comment_admin.py`（34 项）：**评论治理**（P1-22）——管理端列表
  （鉴权 401/40301、默认 all 含已隐藏、status 三态筛选与非法 422、关键词命中、
  `%` 转义后不误匹配、`newsId` 过滤、**蛇形 `news_id` 被静默丢弃**焊死、
  游标翻页不重不漏、`replyCount` 只数可见回复、详情含资讯标题、未知 40405）、
  隐藏/恢复（往返、理由三种非法 422 与 300 边界放行、trim、**幂等保原理由且
  审计仅 1 条**、恢复幂等不记审计、已软删 40405、新评论默认可见）、
  计数联动（隐藏 -1 / 恢复 +1 / 下限 0 / **作者删已隐藏不重复扣**）、
  公开五路过滤（主列表 offset+cursor 排除、root 隐藏→replies 空且恢复后回来、
  隐藏回复不进 `replyCount` 与预览、回复已隐藏 40003、点赞已隐藏 40401、
  公开输出无 hidden 字段）、审计（hide 带 reason/content 截断/newsTitle/userName
  且 target 可查、restore 留痕）。
- `tests/test_migrations.py`（23 项）：**迁移链守卫**。在临时 SQLite 库上跑 `upgrade head` →
  `downgrade base` → `upgrade head`，校验全部表（含 `search_logs` / `rss_sources` / `comment_likes` /
  `admin_users`）、`comments.user_id`、`ix_news_feed`、`users.token_version`、`news.hot_score`
  与 `ix_news_hot`、`rss_sources` 的列与主键（含 0017 的 `respect_robots` **非空**与
  `last_alerted_at`、0018 的 `consecutive_failures` **非空**与 `failure_kind` / `next_retry_at`）、
  `rss_source_health_events` 的列与 `item_count` 非空、复合索引 `ix_rss_health_key_at`
  与外键（0019）、`news` 软下架三列且 `hidden` **非空**、**不含 hidden 的索引**（0020，
  刻意不建）、  `comments` 软隐藏三列且 `hidden` **非空且存量默认 false**、
  **不含 hidden 的索引**（0022，与 `is_deleted` 同理）、`news.content`（0025，E1：
  **可空**即「暂无正文」，且**无索引**——只按 id 点查）。`TestCommentLikesBackfill`（2 项）专测 0014 回填：
  秒级时间戳下**同一条评论同一用户重复点赞只留一行**、悬空/非整数 user_id 被跳过、
  删除列后计数对齐、`downgrade` 可还原 `liked_by`。⚠️ 必须在**子进程**中执行（见文件头说明）：
  `migrations/env.py` 读的是 `app.config.DATABASE_URL`，**不会**读 alembic Config 的
  `sqlalchemy.url`，进程内直接调 `command.upgrade()` 会作用到**真实数据库**。
- `tests/test_audit.py`（13 项）：**运营审计**（P1-15）——鉴权（401/403）、
  源增删/重置留痕、更新只记变化字段（无变化不留痕）、删除快照在删前取、
  管理员增删留痕且顺序为「移除在前」、时间线倒序、游标分页无重复无遗漏、
  `target` 过滤、非法游标 40003、审计与业务同事务。
- `tests/test_login_guard.py`（6 项）：**登录失败锁定（C2，账号维度）**——连续失败达上限后
  **即使密码正确也锁**（42902 + `Retry-After`，锁定先于验密）、**不存在的账号同样计数**
  （防「同号有时 429 有时 401」的枚举 oracle）、**成功登录即清零**（计数只反映连续失败）、
  窗口过后锁定解除、总开关关闭时不锁定、锁定按手机号隔离不影响其他账号。
  环境注意：根 conftest 把 `RATE_LIMIT_ENABLED` 钉 false，本文件在 fixture 里
  monkeypatch 运行期开关（模块顶部改 env 无效——`app.config` 首次 import 即定稿）。
- `tests/test_sources.py`（56 项）：**RSS 源可配置化**——播种只在空表时发生（不覆盖运营配置）、
  内置源含 `aihot_picks`、按权重降序、频道无专属源时回退全部、无库会话回退内置源；
  健康状态回写（成功 / 失败 / 错误截断 / 未知 key 静默）；
  **抓取健壮性**（坏源不拖垮好源、真实不可达地址记为 `ok=False` 且带原因、显式传入的源去重并按权重降序）；
  **正文入库**（`TestUpsertContent`，4 项，E1）：落库保留段落换行、增量刷新跟上源修订、
  **空正文不抹掉已有正文**、无正文落 `NULL`（详情接口据此返回 null）；
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
- `tests/test_api.py`（105 项）：含**评论归属**（`TestCommentOwnership`）——新评论写入
  `userId`、`mine` 仅作者为 true、历史评论 `userId` 为 null 且任何人都不是作者；
  **评论删除**（`TestCommentDelete`）——作者可删、他人 403、未登录 401、
  无归属评论不可删、幂等、删后不可点赞、跨文章 404；
  **两级评论**（`TestCommentReplies`，12 项）——回复的回复 `rootId` 拍平、
  主列表只出主评论 + 预览 ≤2 + `replyCount` 排除已删、replies 正序游标分页、
  删 root 后回复不可见、回复目标不存在/跨文章/已删均 40003、**回复只通知被回复者不广播**；
  **P1-26 回复预览下推**（3 项）——`row_number` 的 `rn <= N` 过滤**必须出现在执行的 SQL 里**
  （回退成 Python 侧截断即失败）、30 条回复时预览仍是**最早 2 条**且 `replyCount` 计全部、
  预览与计数同口径**跳过已删除与已隐藏**；
  **点赞关系表**（`TestCommentLikesTable`，3 项）——关系行增删、复合主键防重、`liked` 按用户区分；
  **正文下发**（E1，3 项）——列表响应**不含** `content`、详情返回按段落 `\n` 分隔的真实正文、
  无正文时 `content` 为 `null`（详情页据此显示「暂无正文」而非占位文案）。
- `tests/test_feeds.py`（7 项）：RSS 解析 / 条件请求（304 / 条件头 / 不重试）/ 增量入库 / 时效清理（未知时间优先、keep_min 下限、子表清理）。
- `tests/test_search_stats.py`（21 项）：**搜索词统计**——记录与空白词忽略、**同用户同词 60s 内去重（防刷）**、窗口过后重新计数、不同用户不去重、未登录无去重窗口、超长词截断、按次数倒序、同次数按词升序（顺序稳定）、**窗口外的旧搜索不计入**、limit 生效、清理保留期内数据、接口契约与越界 422、搜索即记录、已登录去重。
- `tests/test_ranking.py`（34 项）：**排序与个性化**——热度纯函数（互动越多分越高、
  评论权重高于收藏、越旧分越低、48h 恰好半衰、无法解析时间取中性值、
  未来时间不放大、**带时区与 naive 混用不报错**）、三模式排序（时间 / 热度 / 偏好提升 /
  未登录退化 / 偏好压不过真实热度 / 历史也构成偏好）、置顶在热榜仍优先、
  非法 sort 为 422、`hot` 游标翻页无重复无遗漏、v1 游标仍可续接、
  **`comment_count` 随评论增删增减且不为负**、收藏抬升/取消降低热度、全量重算；
  **P1-21 增量重算**（`TestIncrementalRecompute`，5 项）——增量只扫过期行
  （未过期的分数与锚点都不动）、窗口内行跳过、全量模式忽略 staleness、
  同一 now 下增量分数与全量一致、互动 bump 刷新锚点。
- `tests/test_api.py` 另含 `TestMessageEvents`（8 项）：**消息事件化**——广播对全员可见、
  定向消息仅收件人可见、评论通知其他评论者、**自身不通知自己**、点赞通知作者、
  **取消赞不打扰**、反复点赞去重、全部已读只标记可见消息。
- `tests/test_api.py` 另含 `TestMarkReadAuthorization`（4 项，H11 修复）：**标记已读的越权防护**——
  收件人标记自己的定向消息 200 并写入已读关系、他人标记返回 **40400** 且响应体
  **不回显任何字段**也不写已读关系、广播消息任何登录用户可标记、越权尝试不影响他人未读数；
  `TestMessages` 另有两项契约变更断言——消息不存在 **40400**（原为 200 静默）、
  未登录标记 **40100**。
- `tests/test_multi_instance.py`（4 项）：限流器工厂回退、非 PG 方言 advisory lock 行为、
  **抓取锁与热度锁键不同**（P1-21，防热度任务被抓取锁连带跳过）。
- `tests/test_scheduler.py`（6 项）：**热度重算独立 cron**（P1-21）——核心回归
  「抓取关停时热度照建照跑、抓取一次不执行」、抓取单轮不再顺带重算热度
  （结构性解耦）、每轮异常都不杀循环、`_hot_job` 失败只落 `lastError` 恢复即清零、
  `/api/health.hotRecompute` 四键形状、`hot_status()` 返回副本。
- `tests/test_moderation.py`（38 项）：**敏感词自动审核**（P1-23）——归一化（5 种绕过变体：
  空格 / 标点 / 零宽 / 全角空格 / 全角+点号隔断，归一后与原文同形）、block 不落库但**仍留痕**
  （`commentId` 为 `NULL`）、`hit_count` 累加、mask 脱敏后发布且置 `pending_review`、
  **脱敏长度按词长**（变形不泄露"绕过去了几个字符"）、多词一次提交、同词两次只记一行、
  区间重叠取最严动作、**allow 完全包裹才豁免**（部分重叠仍命中）、allow 不写日志、
  **昵称命中只脱敏不阻断**（昵称不是用户此刻能改的）、昵称开关可关、
  **新词/停用立即生效**（热更新）、`reload`、CRUD 与审计、**太短 42202 / 重复 40905 /
  不存在 40406**、非管理员 40301、命中日志筛选、`pending` 筛选与复核（幂等 + 40405）、
  总开关关闭后不检测、回复同样受检、正常评论零影响。
- `tests/test_nickname_moderation.py`（81 项）：**用户昵称敏感词校验**（P1-24）——
  归一化（**全角/大小写/零宽/leetspeak/数字插字/繁简体/同音别名**七类绕过全拦、
  四形态与下标映射）、形状校验（六态拒绝 + 边界 accepted + lone surrogate/非字符串）、
  三动作语义（**block 拒绝且旧名不丢**、review 放行置位、**pass 清待复核标志**、
  **allow 完全包裹才豁免**、多词重叠取最严）、**一次提交同一词只记一行**、
  `hit_count` SQL 自增、**注册链路**（block 不建账号、review 置位并留痕、默认名、
  非法形状）、命中日志（筛选/游标/`matched` 记别名）、**改词立即生效**、
  `reload` 返回条数、CRUD 与审计、**主词/别名太短 42204 / 重复 40906 / 不存在 40407**、
  同名重设放行。
- `tests/test_me.py`（18 项）：**收藏 / 浏览历史云端同步**——鉴权（6 个端点）、收藏增删幂等、
  并集合并且不丢数据/不误删、非法 id 跳过、历史去重与置顶、超限裁剪、被动副作用静默、
  **跨用户隔离**、**资讯清理时标记级联清除**。
- `tests/test_api.py` 其余：用 **SQLite 内存库** 隔离，不触碰开发/生产数据；覆盖 health / 请求ID / metrics、认证（含 422 统一契约、token 吊销与续期）、资讯（排序契约/频道/分页/**游标分页**/详情/搜索）、**抓取接口鉴权（401/403/202）**、评论（鉴权/点赞幂等/排序/分页/空内容）、消息（分页/未读数/已读）。

> 前端契约自检：`node scripts/contract-check.mjs`（**47 项**，含 18 项二级评论契约，需传 `DEMO_PASSWORD` 登录）。
> 加 `CHECK_ADMIN=1` 且在 `ADMIN_PHONES` 内的账号登录，可额外校验 `/api/admin/sources`
> 的增删改与协议白名单，`/api/admin/users` 的白名单增删、重复/自添加拦截、
> `addedBy` 与 404，`/api/admin/audit` 的留痕与游标分页，
> `/api/admin/sources/{key}/health` 的趋势形状、`days` 收敛与越界 422、未知源 404，
> 以及 `/api/admin/news` 的列表形状与筛选、下架→恢复闭环（公开四路实时消失/回来、
> 重复下架不抹理由、审计条数）、置顶驼峰与蛇形忽略、游标分页，
> `/api/admin/comments` 的列表形状与筛选、隐藏→恢复闭环（公开五路实时消失/回来、
> 计数 -1/+1、点赞 40401 与回复 40003、重复隐藏不抹理由、审计条数、40405、
> 蛇形 `news_id` 被静默忽略），
> 以及 `/api/admin/words` 与 `/api/admin/moderation/hits` 的词库 CRUD 与命中日志
> （太短 42202 / 重复 40905 / 不存在 40406、**停用后立即不再命中**（热更新）、
> block 提示不回显命中词且仍留痕、mask 脱敏按词长、allow 豁免、**空格/标点/全角/
> 零宽隔断全部拦住**、命中筛选与游标、复核幂等 + 40405、收尾自销临时词与演示评论），
> 以及 `/me/nickname` 与 `/api/admin/nickname-*` 的昵称敏感词校验
> （形状六态 42203、block 40006 不回显且**旧昵称不被改写**、review 放行 + 待复核下发、
> 符号隔断与别名照样命中、命中日志四维筛选与 `matched`、复核队列按 flaggedAt 降序 +
> 复核幂等 + 40408、allow 豁免、hitCount 精确断言、**改词立即生效**、reload、
> 主词/别名太短 42204 / 重复 40906 / 不存在 40407、收尾删词并恢复原昵称）
> （共 **253 项**；mock 后端同样支持，见前端 README）。
>
> ⚠️ 真实后端跑全量契约（253 项 ≈ 250+ 请求）会撞上默认限流
> （`RATE_LIMIT_PER_MINUTE=120`，表现为后段检查报「请求过于频繁」而非真实结论）。
> 本地验证时以 `RATE_LIMIT_ENABLED=false` 启动后端即可——限流器本身由
> `tests/test_units.py` 覆盖，契约脚本不重复校验它。

## 错误码

> 完整接口清单见上方「接口清单」（唯一来源，避免多处维护导致漂移）。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | 40001 / 40002 / 40003 / 40004 / 40005 / 40006 | 手机号格式错误 / 评论内容为空 / 分页游标无效 / RSS 源地址不是 http(s) / **评论包含不当内容**（敏感词 block，**不回显命中词**）/ **昵称包含违规内容**（昵称敏感词 block，**不回显命中词**） |
| 401 | 40100 | 未登录或 token 失效（含被 `token_version` 吊销） |
| 401 | 40101 | 手机号或密码错误 |
| 403 | 40301 | 无权限（非管理员调用 `/api/news/refresh`、`/api/admin/*`；白名单为空时该能力整体关闭） |
| 403 | 40302 | 只能删除自己发表的评论 |
| 404 | 40400 / 40401 / 40402 / 40403 / 40404 / 40405 / 40406 / 40407 / 40408 | 资讯不存在（**已下架对外同码**）/ 评论不存在 / RSS 源不存在 / 管理员不存在 / 管理端资讯不存在 / 管理端评论不存在（含已软删除）/ 敏感词不存在 / **昵称敏感词不存在** / **用户不存在**（昵称复核）。**40400 亦用于消息「不存在或不可见」**——同码才不暴露存在性 |
| 409 | 40901 | 手机号已注册 |
| 409 | 40902 | RSS 源标识（`key`）已存在 |
| 409 | 40903 | 该手机号已是管理员（含 env 与库两个来源） |
| 409 | 40904 | 已是最后一位管理员，不能移除（末位保护） |
| 409 | 40905 | 该敏感词已存在 |
| 409 | 40906 | 该昵称敏感词已存在 |
| 422 | 42200 / 42201 / 42202 / 42203 / 42204 | 参数校验失败（统一 `{ code, message, data.errors }`）/ 排序方式不支持或频道不存在 / **评论敏感词有效字符不足**（`MODERATION_MIN_WORD_LEN`，空格与标点不计）/ **昵称形状不合法**（空/纯空白/越界/非法字符/纯符号，消息即面向用户的提示）/ **昵称敏感词有效字符不足**（`NICKNAME_MIN_WORD_LEN`，主词与别名都查） |
| 429 | 42900 / 42901 / 42902 | 触发限流（普通 / 登录 IP / 登录失败锁定·账号维度） |

> 注：`40901`–`40904` 刻意分开——同一错误码承载多种语义会让前端文案错配。

## 时间口径（A3）

**全库唯一口径：UTC aware ISO 串、秒精度**（`2026-10-18T06:30:00+00:00`）。
迁移 `0027_time_basis_utc` 完成存量回填。

统一前混着三种口径——`now_str()` 写**本地 naive**、`ranking._iso_seconds` 写
**naive-UTC**、抓取/种子写 **aware-UTC**。后果是「同格式 ISO 的字典序即时间序」
这条契约不成立：字符串排序与真实时间序脱节，读侧只能靠 `_to_naive_utc` 打补丁，
而补丁把本地时间当 UTC 解释，衰减计算有整小时 skew。

| 环节 | 唯一入口 | 说明 |
| --- | --- | --- |
| 写入 | `models.now_str()` / `datetime.now(timezone.utc)` | **禁止 `datetime.now()`**（本地 naive）写库 |
| 读取 | `models.parse_dt(value)` | aware → UTC；naive **按 UTC 解释**（与回填口径一致）；不可解析返回 None |
| 比较 | 字符串比较 | 同格式 ISO 的字典序即时间序，`hot_score_at < cutoff`、`created_at >= since` 都直接成立 |

- **例外**：`news.time` / `messages.time` / `comments.time` 存「1小时前」这类
  **相对展示文案**，不是时间戳——迁移白名单刻意排除，新增时间列别往这三列上靠。
- **回填口径（只补偏移、不改数字）**：naive 原样补 `+00:00`，aware 归一到 UTC，
  脏值保持原样并计数。为什么不按本地时区移位——存量 naive 的来源不可恢复
  （`hot_score_at` 本就存 naive-UTC 数字，移位会**错**；其余列是写入机器的本地
  时间，该时区无法从数据反推）。补偏移保住了唯一能验证的性质：**相对顺序精确
  保留**；绝对值偏差有界（≤ 原时区偏移），对 48h 半衰期的衰减影响小且随时间自愈。
- **不可逆**：0027 的 `downgrade` 是空操作——回填只改格式，无法区分某行原先是
  naive 还是本就 aware，强行回退只会把真 UTC 值改成假 naive。

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
    ├── moderation.py    # 评论敏感词引擎（P1-23，Aho-Corasick + 归一化）
    ├── nickname_moderation.py  # 昵称敏感词引擎（P1-24，四形态 + 别名 + leetspeak/繁简）
    ├── response.py      # { code, message, data } 封装
    ├── scheduler.py     # 双 cron：定时抓取 + 热度重算（asyncio lifespan，各持独立 advisory lock）
    ├── seed.py          # 种子数据（幂等补齐 + 孤立引用自愈）
    ├── feeds/           # RSS 抓取层
    │   ├── sources.py   # 源清单（内置播种 + 读库回退）
    │   ├── fetcher.py   # 抓取 + 解析 + 字段映射
    │   └── ingest.py    # 编排 + 入库 + 清理
    ├── routers/         # auth / news / comments / messages / me / admin
    └── ../tests/        # pytest（内存 SQLite + 迁移链子进程守卫）
```