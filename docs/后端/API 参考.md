# 明日头条 · 后端 API（FastAPI + PostgreSQL / SQLite）

为「明日头条」Vue 前端提供资讯 / 评论 / 认证接口。响应统一为 `{ code, message, data }`（`code: 0` 为成功），与前端 `src/api/http.ts` 的解包约定一致。

> 本文由 `server-api/README.md` 拆分而来（API 参考.md）。

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
| GET | `/api/news?channel=&sort=&page=&pageSize=&cursor=&feed=` | —（`feed=following` 需 ✅） | 资讯列表。**推荐游标分页**：传 `cursor` 走 keyset，返回 `{ list, nextCursor, hasMore }`；不传则 offset 分页并附 `total`。`sort` 取 `latest`（默认）/ `hot` / `for-you`（见「排序与个性化」），非法值 422。`feed=following` 为**关注流**（E6b）：需登录（否则 401）、**忽略 `channel`**、只返回已关注来源的内容；非法 feed 422/42200。列表项含 `sourceKey`（可空，来源稳定标识） |
| GET | `/api/news/{id}` | — | 资讯详情。比列表多返回 `content`（正文，按段落以 `\n` 分隔；无正文为 `null`，前端显示「暂无正文」而非占位文案）——**列表接口刻意不带正文**，见「资讯正文（E1）」 |
| GET | `/api/news/{id}/comments?limit=&cursor=&sort=` | —（可选带token） | **主评论**列表（`root_id IS NULL`，游标分页）；`sort=latest`（默认，`created_at` 倒序，**新的在前**）/ `sort=hot`（`likes` 倒序，时间 + id 兜底），非法值 422/42201；每条附 `replyCount` 与最早 2 条回复预览；带 token 时 `liked` 按当前用户计算。**游标不透明且携带 sort**：跨排序误用会被作废（退回第一页），不会错位续接；v1 裸 id 游标仍按 latest 兼容 |
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
| GET | `/api/sources?channel=` | —（公开） | **来源目录**（E6b）：`{ list: [{ key, name, channel, newsCount }] }`，只返回 `enabled` 源。`newsCount` 为该源**当前可见**资讯数（不含已下架）。**不返回 `url` 与健康状态**——那是运营信息 |
| GET | `/api/me/follows` | ✅ | 我关注的来源（按关注时间倒序），返回 `{ keys, list }`；`list[]` 为 `{ key, name, channel, enabled, newsCount }`。源被删除时该行不出现在列表（关注记录保留） |
| POST | `/api/me/follows` | ✅ | 关注来源（幂等），body `{ sourceKey }`，返回完整集合。来源不存在或已停用 **40410**；空 key / 超过 200 个 **42207** |
| DELETE | `/api/me/follows/{key}` | ✅ | 取消关注（幂等，返回完整集合） |
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


## 图片代理（B4）
`GET /api/img?url=<原地址>&w=<目标宽度>`

资讯配图此前由浏览器**直连源站**，于是有三件事同时不成立：源站一挂就裂图；
CSP 的 `img-src` 必须放行 `https:`（而 CSP 恰恰挡不住
`<img src=https://evil/x>` 这类**外泄型载荷**——用户 IP 与浏览行为泄露给几十个
无关站点）；没有尺寸协商，小卡片里照原样下载 2000px 的封面。

**它同时是教科书式的 SSRF 攻击面**（把"能发请求的服务器"交给用户指定目标），
所以 `app/routers/img.py` 里**防线比功能多**：

| 防线 | 为什么 |
| --- | --- |
| 协议白名单（仅 http/https） | 挡 `file://` / `gopher://` / `data:` |
| **逐条**检查 DNS 结果，非公网即拒 | 攻击者给域名配多条 A 记录（一条公网一条内网）正是为了蒙混过关；`169.254.169.254`（云元数据）在 `is_link_local` 里 |
| **跳转逐跳复检**（≤3 跳，手动跟随） | 外站 302 到内网是最常见的绕过；只查入口 URL 等于给它留门 |
| 5MB 体积上限 + 连接/读取超时 | 内存炸弹 |
| 只放行图片 `Content-Type`（**不含 SVG**） | 代理与站点同源，能转发 HTML/JS 就等于开了存储型 XSS；SVG 可内嵌脚本 |
| `nosniff` + `Content-Disposition: inline` | 同一道防线的第二层 |
| 缓存键 = (URL, 宽度)，宽度走白名单 | 任意 `w` 就是把缓存撑爆的入口 |

**已知边界（诚实）**：DNS 解析与 httpx 建连之间存在 TOCTOU 窗口（若能在这两个
时刻之间改 DNS 记录，仍可能绕过 IP 检查）。彻底消除需要自定义 transport 把连接
钉到已校验的 IP 上，代价是复杂度与兼容性；当前不接受这个复杂度，改以"仅供资讯
配图、上游为已抓取过的 RSS 源"来降低暴露面。

**缩略图 / WebP**：装了 Pillow 才生效（`try: import PIL`），否则原样透传。
它是 native 依赖，**刻意不进 `requirements.txt`**（见 `requirements-optional.txt`）——
未安装时代理功能不受影响，只是少了尺寸协商；且**不放大**（比原图小的宽度原样返回）。

**错误码**：`40008` 协议/主机/内网不合规 · `41501` 上游不是图片 · `41301` 超过体积
上限 · `42206` 缺 `url` 或 `w` 不在白名单 · `50201` 上游不可达或跳转过多。


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

- `require_admin`（`app/routers/admin/__init__.py`，D2 拆分后）：未登录 → **401**，已登录但非管理员 → **403 / 40301**，
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


## 错误码
> 完整接口清单见上方「接口清单」（唯一来源，避免多处维护导致漂移）。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | 40001 / 40002 / 40003 / 40004 / 40005 / 40006 | 手机号格式错误 / 评论内容为空 / 分页游标无效 / RSS 源地址不是 http(s) / **评论包含不当内容**（敏感词 block，**不回显命中词**）/ **昵称包含违规内容**（昵称敏感词 block，**不回显命中词**） |
| 401 | 40100 | 未登录或 token 失效（含被 `token_version` 吊销） |
| 401 | 40101 | 手机号或密码错误 |
| 403 | 40301 | 无权限（非管理员调用 `/api/news/refresh`、`/api/admin/*`；白名单为空时该能力整体关闭） |
| 403 | 40302 | 只能删除自己发表的评论 |
| 404 | 40400 / 40401 / 40402 / 40403 / 40404 / 40405 / 40406 / 40407 / 40408 / **40409 / 40410** | 资讯不存在（**已下架对外同码**）/ 评论不存在 / RSS 源不存在 / 管理员不存在 / 管理端资讯不存在 / 管理端评论不存在（含已软删除）/ 敏感词不存在 / **昵称敏感词不存在** / **用户不存在**（昵称复核）/ **举报目标不存在** / **来源不存在或已停用**（关注）。**40400 亦用于消息「不存在或不可见」**——同码才不暴露存在性 |
| 409 | 40901 | 手机号已注册 |
| 409 | 40902 | RSS 源标识（`key`）已存在 |
| 409 | 40903 | 该手机号已是管理员（含 env 与库两个来源） |
| 409 | 40904 | 已是最后一位管理员，不能移除（末位保护） |
| 409 | 40905 | 该敏感词已存在 |
| 409 | 40906 | 该昵称敏感词已存在 |
| 422 | 42200 / 42201 / 42202 / 42203 / 42204 / **42206 / 42207** | 参数校验失败（统一 `{ code, message, data.errors }`）/ 排序方式不支持或频道不存在 / **评论敏感词有效字符不足**（`MODERATION_MIN_WORD_LEN`，空格与标点不计）/ **昵称形状不合法**（空/纯空白/越界/非法字符/纯符号，消息即面向用户的提示）/ **昵称敏感词有效字符不足**（`NICKNAME_MIN_WORD_LEN`，主词与别名都查）/ **举报类型或理由非法** / **关注来源标识为空或超出 200 上限** |
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

