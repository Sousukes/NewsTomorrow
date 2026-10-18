## 批次 L（低优先级）
### L1 生产禁用演示账号（在批次 H 中一并完成）

- `SEED_SAMPLE_DATA` 默认：生产 `false`、开发 `true`；统一控制演示账号与示例资讯/评论/消息播种。
- **测试**：测试环境显式置 `false` 保证断言确定性。

### L2 死代码清理与配置生效

- 删除未使用的 `app/response.py::fail()`。
- 删除未使用的 `sources.py::KEYWORD_SOURCE_KEYS`。
- 让此前"定义了却没用"的 `weight` 字段**真正生效**：`sources_for_channel` / 新增
  `all_enabled_sources()` 按 weight 降序返回，多源转载同一稿件时高权重源先入库（`collect` 先到先得）。
- 清理 `scheduler.py` 未使用的 `init_db` / `asynccontextmanager` 导入。

### L3 Tab 级列表页缓存（keep-alive）

- **改动**：`App.vue` 用 `<router-view v-slot>` + `<KeepAlive :include="['HomeFeed','Videos','Messages']">`，
  切换 Tab 后返回时保留滚动位置与已加载列表（此前每次返回都重新挂载并重拉首屏）。
- **迁移/测试**：无（构建验证）。
- **文档**：本记录。

### L4 组件测试与无障碍

- 新增 `src/components/NewsCard.spec.ts`（7 项）：`<img>` 懒加载/`alt`/危险协议回退/三图/置顶/点击事件/播放数格式化。
- 无障碍：`NewsCard` 配图补 `alt`；`BannerCarousel` 配图补 `alt`（指示点此前已有 `aria-label`）。

### L5 可观测性：请求 ID 与 /api/metrics

- **改动**：访问日志中间件生成/透传 `X-Request-ID` 并回写响应头，日志行前缀请求 ID；
  新增 `GET /api/metrics`（Prometheus 文本：uptime / 请求总数 / 在途 / 按状态码 / 按路径 Top20 / 连接池）。
- **测试**：`test_request_id_header`、`test_metrics_endpoint`。
- **文档**：README 接口清单 + 限流与日志章节。

### L6 深色模式

- **改动**：`tokens.css` 增加 `@media (prefers-color-scheme: dark)` 覆盖颜色类令牌。
  组件均通过 `var(--color-*)` 取色，**无需改动任何组件**即随系统深浅色切换。
- **迁移/测试**：无。
- **文档**：本记录。

---

