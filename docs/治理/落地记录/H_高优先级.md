## 批次 H（高优先级）
### H1 修复信息流排序

- **问题**：`list_news` 按字符串主键 `id` 排序（种子 `n1`、抓取 `r_<sha1>`），顺序既非时间也非热度，`is_top` 置顶完全失效。
- **改动**：`server-api/app/routers/news.py` 新增 `_order_by()`，统一为
  `ORDER BY is_top DESC, COALESCE(published_at,'') DESC, id DESC`。
  搜索接口同样复用该排序。`recommend` 频道语义明确为"聚合所有频道"。
- **测试**：`tests/test_api.py::TestNews::test_feed_order_top_then_time_desc`（置顶第一、较新在前）。
- **迁移**：`0004_feed_order_reads` 建复合索引 `ix_news_feed(channel, is_top, published_at, id)`。
- **文档**：README「接口清单」+ 速查表「GET /news」补排序契约。

### H2 修复评论排序

- **问题**：评论按展示文案 `time` 排序后反转。种子为 `"1小时前"`、新评论为 ISO 时间，两种格式字典序错乱 → 顺序错误。
- **改动**：`models.py` 新增 `Comment.created_at`（ISO 绝对时间，带索引）；
  `routers/comments.py` 改为 `ORDER BY created_at DESC, id DESC`；`seed.py` 为种子评论生成 `created_at`。
- **测试**：`TestComments::test_comment_order_newest_first`。
- **迁移**：`0004` 增列 + 索引，历史行回填基准值（配合 `id DESC` 兜底保证稳定）。
- **文档**：README 接口清单标注"新的在前，按 created_at 倒序"。

### H3 限流加固

- **问题**：① 无条件信任 `X-Forwarded-For` 首段，客户端可伪造以绕过限流；
  ② 限流键为 `ip:path`，按路径分桶，轮换路径即可绕过总量限制。
- **改动**：`main.py` 新增 `TRUSTED_PROXIES` 解析（`ipaddress`，支持 IP/CIDR）与 `_is_trusted()`；
  `client_ip()` 仅当直连对端命中受信网段才采信 XFF。限流键改为**纯 IP**（登录为 `login:<ip>`）。
- **测试**：`tests/test_hardening.py::TestClientIp`（3 例：忽略 XFF / 受信时采信 / 不受信忽略）。
- **迁移**：无（配置项）。
- **文档**：README「限流与日志」重写；`.env` 新增 `TRUSTED_PROXIES`（见下）。

### H4 抓取接口鉴权 + 异步化

- **问题**：`POST /api/news/refresh` 无鉴权、同步阻塞 worker，可被滥用为 DoS 放大器。
- **改动**：改为需登录且手机号在 `ADMIN_PHONES` 白名单内（白名单为空则禁用）；
  经 `BackgroundTasks` 投递后台执行，立即返回 `202`；抓取逻辑抽到独立会话 `_run_refresh()`。
- **测试**：`tests/test_api.py::TestRefreshAuth`（401 未登录 / 403 非管理员 / 202 管理员，后台任务打桩）。
- **迁移**：无。
- **文档**：README 接口清单与 curl 示例（带 token）。

### H5 游标分页 + 索引

- **问题**：`count()` + `offset/limit` 每页全表 count，深分页线性扫描。
- **改动**：`routers/news.py` 支持 `cursor`（base64(JSON[is_top, published_at, id])），
  keyset 条件用 `is_()` 展开布尔列（规避 SQLAlchemy 布尔比较限制），返回 `nextCursor` + `hasMore`；
  保留 `page` 兼容。前端 `api/news.ts` 新增 `fetchNewsPage()`（`NewsPage` 契约），
  `stores/news.ts` 改用游标续接（无游标时回退 offset）。
- **测试**：后端 `test_cursor_pagination_no_overlap`、`test_cursor_invalid`、`TestCursor`（4 例）；
  前端 `stores.spec.ts` 全量改为游标契约 + 断言第二次请求带 `cursor`。
- **迁移**：`0004` 复合索引。
- **文档**：README + 速查表更新分页契约。

### H6 前端 HTTP 加固

- **问题**：无超时、无重试、401 不清理登录态（token 过期后持续 401 且显示已登录）。
- **改动**：`api/http.ts` 新增 `AbortController` 超时（默认 15s）、GET 指数退避重试（默认 2 次）、
  401 时清 token 并广播 `UNAUTHORIZED_EVENT`；`stores/user.ts` 监听该事件清理登录态；
  `main.ts` 监听后跳转登录页。
- **测试**：`src/api/http.spec.ts`（6 例：解包 / 401 清 token+广播 / GET 重试 / POST 不重试 / 透传 message / 业务码抛错）。
- **迁移**：无。
- **文档**：README「前端联调」补充说明（见 `mingri-toutiao-vue/README.md`）。

### 新增环境变量（批次 H）

```dotenv
# 仅当直连对端命中该网段时才采信 X-Forwarded-For（留空=不信任任何代理头）
TRUSTED_PROXIES=127.0.0.1,::1,10.0.0.0/8
# 可触发 /api/news/refresh 的管理员手机号（留空=生产禁用该接口）
ADMIN_PHONES=
# 是否播种示例数据（生产默认 false）
SEED_SAMPLE_DATA=false
```

---

