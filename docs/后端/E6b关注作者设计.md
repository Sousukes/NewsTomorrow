# E6b 关注作者 · 设计方案（已定案）

> 状态：**4 个决策点已确认并落地后端**（P1-68）。D1=按 `source_key`、D2=首页「关注」tab +「我的关注」双入口、
> D3=不做榜单、D4=不做通知、上限 200。前端部分见 P1-69。
> 来源：backlog E6b「关注作者」（由 E6 拆分而来）。
> 前置事实：本批**没有**现成的作者实体——这一点决定了方案形态，先看第一节。

## 一、现状核实（设计前提，均已读代码确认）

| 事实 | 依据 | 影响 |
| --- | --- | --- |
| **没有作者实体** | `news` 只有 `source`（`String(50)`，源名称）；RSS 解析（`feeds/fetcher.py::parse_feed`）不读 `<author>` | 所谓「关注作者」在本项目里实际是**关注来源/媒体**。要做真正的作者粒度，得先有作者数据源——当前没有 |
| **源的稳定标识是 `rss_sources.key`**（主键），`name` 可被运营改名 | `models.RssSource`；管理端 `PATCH /api/admin/sources/{key}` 支持改 `name` | 关注应按 `key` 落库，否则改名即静默失效 |
| **`news` 不存 `source_key`**，只存名称快照 | `models.News.source`；`feeds/fetcher.py:237` 写入的是 `source["name"]` | 按名称关注会「只关注一半」：改名前的旧行是旧名、改名后的新行是新名 |
| 关注按钮 UI **已存在但未接线** | `AppNav.vue:79-82` 渲染 `author.followed` 并 `emit('follow')`；`NewsDetail.vue` 未监听 | 又一处死按钮（与 P1-66 修掉的「更多」同类） |
| `CHANNELS` 是频道**单一来源**，且被后端 `schemas` 用于校验运营填的频道 | `constants/channels.ts`；`feeds/sources.py::CHANNEL_KEYS` | **关注流不能做成 channel**，否则管理端源配置会多出一个非法频道选项 |
| `/api/admin/sources` 仅管理员可用 | `routers/admin/sources.py` | 前端要展示「我关注了谁」，需要一个**公开**的源目录接口 |

## 二、4 个决策点（待确认）

### D1 关注对象：`source_key` 还是 `source` 名称？

| 方案 | 做法 | 成本 | 代价 |
| --- | --- | --- | --- |
| **A 名称** | `user_follows(user_id, source)`；关注流 `WHERE source IN (...)` | 1 迁移 + 3 接口 | **改名即静默失效**：运营把「人民网·时政」改成「人民网·要闻」，已关注用户从此收不到新内容，且界面仍显示「已关注」——最糟的一类 bug：看起来正常 |
| **B 标识（推荐）** | 关注表存 `source_key`；给 `news` 补 `source_key` 列并回填；抓取时写入 | 2 迁移 + 抓取改一行 + `to_out` 加字段 | 需要回填；改名前的旧行若名称已对不上，回填为 NULL（不进关注流） |
| C 新建 `authors` 表 | 造一层作者实体 | L | **无数据来源**：RSS 不提供作者，等于造假数据。不做 |

**推荐 B**。理由：`rss_sources.key` 已经是稳定标识，不用它反而要自己造一套名称匹配规则；而「改名后关注悄悄失效」在资讯类产品里是会被用户投诉、又极难定位的问题。

### D2 关注流入口？

| 选项 | 说明 |
| --- | --- |
| **首页频道栏最左插入「关注」**（推荐） | 与「推荐/热点/视频…」同一行，符合资讯类产品惯例；但**不能**进 `CHANNELS` 常量（见第一节最后一条），要用 UI 级 `HOME_TABS = [{key:'following'}, ...CHANNELS]` |
| 独立页面（`/following`） | 少一次「切 tab」的误解，但入口更深，需要新增底部 tab 或「我的」入口 |
| 只在「我的」里 | 最省事，但关注功能没有日常消费场景，等于白做 |

**推荐**：首页频道栏插入「关注」+ 「我的」里加一条「我的关注」（复用同一数据源，两处入口不重复实现）。

### D3 是否做「关注数 / 来源榜」？

- 第一版**不做**榜单与推荐关注：关注数需要额外聚合与缓存，且容易变成攀比指标。
- 只做：关注列表（我关注了谁）+ 源目录（可关注哪些），列表项显示 `newsCount`（该源现有资讯数），让用户判断值不值得关注。

### D4 是否为关注源的新内容发通知？

- **不做**。需要 fan-out（一条新资讯 × N 个关注者）与消息去重，属于消息体系的一次改造；当前 `messages` 是「评论/点赞/回复」事件型，形态不同。
- 若将来要做，应走 outbox（backlog D7），而不是在抓取循环里逐个 insert。

## 三、数据模型

### 迁移 0032：`user_follows`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | Integer FK `users.id` **ON DELETE CASCADE** | 账号注销时关注关系一并清理（关注是纯个人偏好，无审计价值） |
| `source_key` | String(64) | 对应 `rss_sources.key` |
| `created_at` | String(30) | 绝对时间（与全站口径一致，UTC aware ISO） |

- 主键 `(user_id, source_key)`：**天然幂等**，重复关注由主键冲突兜住（与 `user_favorites` 同处方）。
- 索引 `ix_user_follows_source(source_key)`：支撑「某源被多少人关注」（第一版不用，但建起来很便宜；不建的话将来加榜单要重建表）。
- **不建外键到 `rss_sources`**：源可被删除或重建（`key` 可能复用）。关注行保留，查询时 join 过滤——目标消失不该连带丢历史（与 `reports.target_id` 不建外键同理）。

### 迁移 0033：`news.source_key`

- `source_key VARCHAR(64) NULL` + 索引 `ix_news_source_key`。
- **可空**：回填匹配不到的旧行保持 NULL（它们不进关注流，但列表/详情照常展示）。
- 回填（仅 PG）：`UPDATE news SET source_key = rs.key FROM rss_sources rs WHERE news.source = rs.name`；SQLite 用相关子查询版本，并按既有约定加方言守卫。
- 抓取侧：`RawItem` 增 `source_key`，`parse_feed` 从 `source["key"]` 取，`ingest` 落库写入。**这一步不做，回填之后新数据又会变 NULL**。
- 管理端改名（`PATCH /admin/sources/{key}`）**不需要**改 `news.source`（历史快照保持原样），因为 `source_key` 已是稳定值。

## 四、接口契约

### 公开

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sources?channel=` | 源目录：`{ list: [{ key, name, channel, newsCount }] }`。**只返回 `enabled` 的源**；不返回 `url`/健康状态（那些是运营信息）。`newsCount` 用一次 `GROUP BY` 聚合，**不做 N+1** |

### 需登录

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/me/follows` | `{ keys: [...], list: [{ key, name, channel, newsCount }] }`（与 `/me/favorites` 同形：`ids`→`keys`，因为主键不是资讯 id） |
| POST | `/api/me/follows` | body `{ sourceKey }`，幂等，返回**完整集合** |
| DELETE | `/api/me/follows/{key}` | 返回**完整集合**（与收藏删除一致：服务端即事实源） |
| GET | `/api/news?feed=following` | 关注流：与列表同形 `{ list, hasMore, nextCursor }`，复用既有 keyset 与排序契约（`is_top DESC → published_at DESC → id DESC`）。**未登录 401**；关注为空返回空 `list` 而非 404 |

**参数互斥**：`feed=following` 时忽略 `channel`（并在文档写明），不报 422——前端切换 tab 时会带着上一次的 channel，报错只会制造无意义的失败。

**错误码**（已查占用：40409/42206 已被举报占用，其余相邻号空闲）：

- `40410`：来源不存在或已停用（POST 时 `key` 查不到 / `enabled=false`）
- `42207`：`sourceKey` 为空或格式非法
- 超过关注上限（200）→ `42207` 带明确文案（不为一个上限单开码）

### 列表响应的新字段

`to_out` 增 `sourceKey`（可空）。前端 `NewsItem` 同步加可选字段——**缺省不报错**，mock 与旧后端都能跑（与 E1 的 `content` 同处理）。

## 五、前端交互

- `api/follows.ts`：`fetchSourceDirectory` / `fetchFollows` / `followSource` / `unfollowSource`。
- `stores/follows.ts`：`keys`（Set）+ `list`；关注/取关**乐观更新 + 失败回滚**（关注是单条增删，不走 `/me/favorites` 那种并集合并语义）。未登录时 `keys` 为空、不请求。
- `NewsDetail.vue`：`@follow` 接线，`author.followed` 由 `follows.keys.has(item.sourceKey)` 派生。**未登录点关注**：不跳登录页，先提示「登录后可关注」（与举报弹窗同一取舍：别让用户在最后一步才被 401 打回）。
- `HomeFeed.vue`：`HOME_TABS = [{ key: 'following', label: '关注' }, ...CHANNELS]`；切到「关注」时按 `feed=following` 取流，**沿用现有分桶与代次守卫**（`bucketOf` 的 key 用 `'following'`，不污染 `Channel` 类型）。
- 空态三种，文案必须分开（同 `useNewsDetail` 的失败/不存在分流原则）：
  1. 未登录 → 「登录后查看关注的内容」+ 去登录；
  2. 已登录但未关注任何源 → 「还没有关注任何来源」+ 去发现（跳源目录）；
  3. 已关注但源暂无内容 → 「关注的来源暂时没有新内容」。
- 源目录页（`/sources`）：列表 + 关注按钮，`newsCount` 辅助决策。

## 六、刻意不做

- 关注数榜 / 推荐关注 / 「关注的人也关注」——需要聚合与画像，第一版无收益。
- 关注源的新内容通知（见 D4）。
- 关注流的个性化加权（关注源在 `for-you` 里额外加分）——与 E3 负反馈一起做才有意义，单独做只会让排序解释不清。
- 作者粒度（人名）关注——无数据源。

## 七、拆批建议（2 批）

1. **P1-68 后端**：迁移 0032/0033 + `UserFollow` 模型 + 公开源目录 + `/me/follows` 三件套 + `feed=following` + 抓取写入 `source_key` + 测试（含回填、幂等、上限、未登录 401、停用源） + 契约。
2. **P1-69 前端**：`api/follows` + `stores/follows` + 详情页接线 + 关注 tab + 源目录页 + 三种空态 + 测试。

## 八、验收标准

- 关注后**立即**在「关注」tab 看到该源内容；取关后立即消失（乐观更新 + 失败回滚）。
- 运营把源改名后，已关注用户的关注关系**仍然有效**，关注流不空（这条是 D1 选 B 的唯一理由，必须有测试钉住）。
- 重复关注/重复取关幂等；并发双击不产生重复行（主键兜底）。
- 停用（`enabled=false`）的源不出现在源目录，也不出现在关注流的可关注项里；已关注的行保留但流中无内容（空态文案正确）。
- 未登录访问 `/api/me/follows` 与 `feed=following` 均 401；前端引导登录而非静默空列表。

## 九、风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 回填匹配不到（改名过的源） | 旧行 `source_key` 为 NULL，不进关注流；抓取侧从此写入 key，不会继续产生 NULL |
| 关注表膨胀 | 上限 200/人，`POST` 时校验 |
| 源目录被爬 | 只暴露 `key/name/channel/newsCount`，不含 `url` 与健康状态；限流沿用全局 |
| `newsCount` 聚合变慢 | 一次 `GROUP BY` 取全部源计数（源数量级是几十），不做逐源查询 |
| 关注流走 `IN (子查询)` 在大表上变慢 | `news.source_key` 建索引；关注数 ≤200，`IN` 列表可控 |

## 十、待你确认

1. **D1 选 B（`source_key`，推荐）还是 A（名称，省一次迁移）？**
2. **D2 入口：首页频道栏插「关注」+「我的关注」双入口（推荐），还是只做其一？**
3. **D3 是否要「来源榜 / 关注数」？**（建议第一版不做）
4. **D4 通知**（建议不做）与**关注上限 200**（是否调整）？
