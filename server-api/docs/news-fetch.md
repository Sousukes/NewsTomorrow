# 新闻抓取与字段映射

本文档说明「公开 RSS 源 → `News` 表 → 前端 `NewsItem`」的完整链路与字段对应关系。

---

## 1. 架构概览

```
RSS 源（免费公开，无需 Key）
   │  app/feeds/sources.py   源清单（频道归属、权重、启用状态）
   ▼
抓取 + 解析
   │  app/feeds/fetcher.py   httpx 抓取 → XML 解析 → RawItem 归一化
   ▼
编排 + 入库
   │  app/feeds/ingest.py    多源合并去重 → 按 id upsert → 时效清理
   ▼
PostgreSQL.news
   │
   ▼
前端 NewsItem（src/types.ts）
```

**触发方式**
| 方式 | 入口 |
| --- | --- |
| 定时任务（默认 30 分钟） | `app/scheduler.py`，随服务启动 |
| 手动接口 | `POST /api/news/refresh?channel=&keyword=` |
| 命令行 | `python scripts/fetch_news.py` |

---

## 2. 字段映射表

### 2.1 RSS 原始字段 → `RawItem`（`app/feeds/fetcher.py`）

| RSS 字段（多候选路径） | `RawItem` | 处理方式 |
| --- | --- | --- |
| `title` / `atom:title` | `title` | 去 HTML 标签、解实体、压缩空白、截断 255 |
| `description` / `atom:summary` | `summary` | 同上，截断 300 |
| `link` / `atom:link@href` | `url` | 用于计算稳定 id |
| `pubDate` / `dc:date` / `atom:published` | `published_at` | 兼容 RFC822 与 ISO8601，转 UTC |
| （同上） | `time_text` | 转「刚刚 / N分钟前 / N小时前 / N天前」 |
| `description` 内的 `<img src>` / `content:encoded` | `images` / `cover` | 正则提取，**最多 3 张**（对齐三图变体） |
| —（源配置） | `source` | 取源清单里的中文名，如「人民网·时政」 |
| —（源配置） | `channel` | 取源清单里的频道归属 |
| —（推导） | `variant` | 见下表 |
| —（推导） | `id` | `r_` + SHA1(link 或 title)[:16]，保证多源幂等 |

### 2.2 变体自动判定（`NewsVariant`）

| 条件 | `variant` | 前端展示 |
| --- | --- | --- |
| 描述含 `.mp4` / `.m3u8` | `video` | 封面 + 播放数（`videoUrl` 需另行填充） |
| 提取到 ≥ 3 张图 | `triple` | 三图变体 |
| 提取到 1–2 张图 | `left-right` | 左文右图 |
| 无图 | `text` | 纯文字（显示摘要） |

### 2.3 `RawItem` → `news` 表（`app/feeds/ingest.py`）

| 表字段 | 来源 | 增量更新策略 |
| --- | --- | --- |
| `id` | `RawItem.id` | **主键**，判新增/更新的依据 |
| `title` / `summary` / `source` | 同名 | 每次覆盖 |
| `time` | `RawItem.time_text` | 非空才覆盖 |
| `images` / `cover` | 同名 | **非空才覆盖**（避免丢图） |
| `variant` | 同名 | 非空才覆盖 |
| `published_at` | ISO 字符串 | 用于时效排序与清理 |
| `comment_count` | — | **不覆盖**（保留已积累的互动数据） |
| `play_count` / `video_url` | — | RSS 不提供，保持空 |
| `is_top` | 源配置 | 抓取源一律 `False`（置顶由运营/编辑位控制） |

### 2.4 `news` 表 → 前端 `NewsItem`（`app/routers/news.py`）

| 接口字段 | 表字段 | 备注 |
| --- | --- | --- |
| `id` `title` `summary` `source` | 同名 | |
| `time` | `time` | 相对时间文案，前端直接展示 |
| `images` | `images` | 空值归一为 `null` |
| `cover` | `cover` | 前端 `bgFor()` 真实优先、渐变兜底 |
| `videoUrl` | `video_url` | |
| `playCount` | `play_count` | |
| `isTop` | `is_top` | |
| `channel` | `channel` | 与前端 `ChannelTabs` 的 key 对齐 |
| `variant` | `variant` | 决定卡片形态 |
| `commentCount` | `comment_count` | 与评论 store 的实际条数合并展示 |

---

## 3. 关键设计点

### 3.1 幂等（增量更新的基础）
`id = "r_" + sha1(link或 title)[:16]`：同一篇文章无论抓多少次、来自几个源，id 相同 → `upsert` 只更新不重复插入。实测二次全量抓取「新增 0 / 更新 293」。

### 3.2 失败重试与超时
- 双重超时：`FEED_TIMEOUT`（读）/ `FEED_CONNECT_TIMEOUT`（连接）。
- 重试：每源最多 `2` 次，**指数退避 + 随机抖动**（`0.6 × 2^n + jitter`），避免多源同时重试打爆对端。
- 单源失败**不抛异常**，返回空列表继续下一个源；整体异常也被`_job` 捕获，不终止调度循环。

### 3.3 空数据兜底（三层）
1. 抓取为空 **且** 库为空 → 写入内置种子数据，保证首屏有内容。
2. 抓取为空但库中有数据 → **保留现有数据**，仅记警告（绝不因一次网络故障清空库）。
3. 前端 `api/news.ts` 在无后端时自动降级到内置 mock（`USE_MOCK`）。

### 3.4 时效清理
`prune_old_news(keep_days=30, keep_min=200)`：
- 超过 `keep_days` 的按 `published_at` 清理；
- 无`published_at` 的历史数据优先淘汰，但**至少保留 `keep_min` 条**；
- 删除前先把 `messages.news_id` 置空（`ON DELETE SET NULL`）并删除关联评论，避免外键报错、且**消息不会被误删**。

---

## 4. 运行方式

```bash
# 定时任务（默认随服务启动，30 分钟一次）
FEED_CRON_ENABLED=true FEED_CRON_INTERVAL_MINUTES=30 uvicorn main:app

# 手动接口
curl -X POST "http://127.0.0.1:8000/api/news/refresh"
curl -X POST "http://127.0.0.1:8000/api/news/refresh?channel=finance"
curl -X POST "http://127.0.0.1:8000/api/news/refresh?keyword=暴雨"

# 命令行
python scripts/fetch_news.py --list-sources
python scripts/fetch_news.py                                  # 全量
python scripts/fetch_news.py --channel finance                # 按频道
python scripts/fetch_news.py --keyword 暴雨                    # 按关键词
python scripts/fetch_news.py --limit-per-source 50 --prune    # 大批量 + 清理
```

---

## 5. 源清单与可用性

配置在 `app/feeds/sources.py`，`enabled=False` 的源在实测中不可用（超时/404），保留配置便于后续启用。

| 源 | 频道 | 单次条数 | 备注 |
| --- | --- | --- | --- |
| 人民网（时政/财经/科技/体育/社会/国际） | recommend/finance/tech/sports/hot | ~100 | 分类全、量大；**内容为存档（时间较旧）** |
| 新华网（时政/财经） | recommend/finance | ~300 | 量最大；**内容为存档** |
| 中新网（要闻/财经/体育） | hot/finance/sports | ~30 | **内容最新**（分钟级），量小 |
| 联合早报·中国 | recommend | ~24 | 繁体，需注意 |
| 澎湃新闻 | recommend | — | 实测超时，已停用 |

> **重要**：人民网/新华网的 RSS 是历史存档（时间戳可能已是数月前），中新网才是实时源。若产品需要"最新资讯"，建议以中新网为主力源，或后续接入商用新闻 API。

---

## 6. 故障排查

| 现象 | 排查 |
| --- | --- |
| 抓取 0 条 | `python scripts/fetch_news.py --list-sources` 确认源是否 `enabled`；看日志 `抓取失败 [源名]` |
| 某源一直失败 | 多数为网络/反爬；可调大 `FEED_CONNECT_TIMEOUT`，或把该源 `enabled=False` |
| 解析出 0 条但 HTTP 200 | 源改了 XML 结构；用 `--list-sources` 抓一份原始 XML 对比 `item` 节点 |
| 图片全是空 | 源在描述里用 `<img>` 但被清洗掉；检查 `_extract_images` 的扩展名白名单 |
| 时间显示"489天前" | 源自身时间戳陈旧（见第 5 节），非代码问题 |
| 入库缓慢 | 源多+条数大时正常；可调小 `FEED_LIMIT_PER_SOURCE` |
| 重复插入 | `id` 生成逻辑被改动；检查 `RawItem.id` 是否仍为 `r_`+sha1 |

---

## 7. 扩展：接入新闻 API（带 Key）

当前为**无Key** 方案。若改用 NewsAPI / GNews 等，只需新增一个 fetcher 实现 `RawItem` 产出，再在 `ingest.collect` 里按开关选择数据源即可，**入库与前端零改动**。

密钥等敏感配置统一走**环境变量**（见 `.env.example`），不写入代码库；`server-api/.gitignore` 已排除 `.env`。
