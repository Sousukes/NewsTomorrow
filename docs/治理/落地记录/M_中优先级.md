## 批次 M（中优先级）
### M1 搜索索引（pg_trgm）

- **问题**：`title/summary ILIKE '%kw%'` 前缀通配使 B-tree 索引失效 → 全表扫描。
- **改动**：新增迁移 `0005_news_search_trgm`，在 PostgreSQL 上创建 `pg_trgm` 扩展与
  `ix_news_title_trgm` / `ix_news_summary_trgm` 两个 GIN 索引；非 PG 方言自动跳过。
  扩展创建权限不足时告警跳过、不阻断迁移。
- **测试**：迁移在 SQLite 上为 no-op，已随 `upgrade head` 链路验证。
- **迁移**：`0005_news_search_trgm`。
- **文档**：README 迁移清单。

### M2 评论 / 消息分页（在批次 H 中一并完成）

- 评论：`limit` + `cursor`（上一页末条 id），返回 `{ list, hasMore, nextCursor }`。
- 消息：同样游标分页；已读关系改 `message_reads` 关联表，未读数用两次索引计数替代全表遍历。
- **测试**：`test_comments_pagination`、`TestMessages` 系列。
- **迁移**：`0004`。

### M3 图片懒加载 / 响应式 / 无障碍

- **问题**：配图用 CSS `background-image`，无懒加载、无 `alt`、无尺寸约束（CLS）。
- **改动**：`utils/image.ts` 新增 `safeImageUrl()`（协议白名单，供 `<img src>`）；
  `NewsCard.vue` / `BannerCarousel.vue` 改为"有真实 URL 用 `<img loading="lazy" decoding="async" alt>`，
  否则渐变占位"。顺带修复 BannerCarousel 此前**未做 URL 白名单**的注入面。
- **测试**：`image.spec.ts` 新增 `safeImageUrl` 4 组用例（13 项）。
- **迁移**：无。
- **文档**：本记录 + 组件 README 说明。

### M4 统一错误契约（在批次 H 中一并完成）

- 新增 `RequestValidationError` 处理器，422 也返回 `{ code: 42200, message, data.errors }`。
- **测试**：`test_validation_error_unified_contract`。

### M5 Token 刷新与服务端吊销

- **问题**：JWT 无法吊销（`/logout` 服务端空操作），无续期。
- **改动**：`users.token_version`；JWT 载荷加 `ver` + `jti`；`deps` 校验版本一致；
  `/auth/logout` 自增版本即失效该用户全部 token；新增 `POST /auth/refresh` 滑动续期。
- **测试**：`test_logout_revokes_all_tokens`、`test_refresh_returns_usable_token`、`test_refresh_requires_auth`。
- **迁移**：`0006_user_token_version`。
- **文档**：README 接口清单 + 环境变量。

### M6 多实例正确性（限流 / 调度）

- **问题**：限流为进程内；调度器每个 worker 都启动 → 重复抓取。
- **改动**：`ratelimit.py` 新增 `RedisFixedWindowLimiter` + `build_limiter()` 工厂
  （`REDIS_URL` 存在则用 Redis，不可用自动回退内存，fail-open）；
  `scheduler.py` 用 PostgreSQL 会话级 advisory lock 保证同一时刻只有一个实例真正抓取，
  并把首轮执行提前到启动时（原先需等一个完整周期）。
- **测试**：`test_multi_instance.py`（工厂回退 + 非 PG 锁 no-op）。
- **迁移**：无。
- **文档**：README 限流与调度说明。

### M7 抓取省流与清理 SQL 化

- **问题**：`prune_old_news` 把全表载入内存；空数据兜底在同一会话内调用 `init_db` 有锁等待；
  重复抓取全量拉取，浪费带宽。
- **改动**：`fetcher.py` 支持**条件请求**（`If-None-Match` / `If-Modified-Since`），
  304 抛 `NotModified` 且不重试；`prune_old_news` 改为 SQL 侧筛选 + `LIMIT budget` 删除
  （未知时间优先淘汰，保证不低于 `keep_min`）；`run_ingest` 兜底改用独立会话。
- **测试**：`tests/test_feeds.py`（7 项：304 / 条件头 / 不重试 / 清理规则 / 子表清理）。
- **迁移**：无。
- **文档**：本记录。

### M8 前端运行时响应校验

- **问题**：`normalize()` 全靠 `as` 断言，字段漂移静默进入 store。
- **改动**：新增 `src/api/validate.ts`（零依赖校验器），在 news / messages / comments 的
  `normalize` 中开发期校验并告警（生产构建跳过）。若后续引入 zod 可整体替换。
- **测试**：`src/api/validate.spec.ts`（8 项）。
- **迁移**：无。
- **文档**：本记录。

### 新增环境变量（批次 M）

```dotenv
# 设置后限流改用 Redis 共享计数（多实例正确）；留空用进程内实现
REDIS_URL=
```

---

