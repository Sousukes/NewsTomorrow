## 批次 P1-1（2026-10-11）：评论归属 + SQLite 迁移链修复
### 阻塞点 1：Comment.user_id

- **问题**：评论只有 `user_name` 字符串，无法判定归属（重名即越权），
  卡住「删除自己的评论」「我的评论」「评论治理」。
- **改动**：`models.Comment` 增 `user_id`（可空 FK→users.id，ondelete SET NULL，建索引）；
  迁移 `0007_comment_user_id` 加列 + 索引 +（仅 PG）外键，并**按用户名唯一匹配回填**
  （重名不回填）。实测 12 条：9 条归属 user 1，3 条种子评论保持 NULL——
  历史评论作者未知，任何人不得以作者身份操作。
- **接口**：`create_comment` 写入 `user_id`；`to_out` 增 `userId` 与 `mine`
  （`mine = 当前用户 == 作者`，未登录/历史评论恒 false）。
- **前端同步**：`types.ts` / `validate.ts` / `api/comments.ts` / `stores/comments.ts` /
  `data/mockComments.ts` 补齐两个字段（由 `vue-tsc` 全量暴露，逐一修正）。

### SQLite 迁移链修复（P0 单轨化暴露的遗留风险）

- **问题**：`0003_msg_fk_set_null` 用了裸 `op.drop_constraint`，而
  **SQLite 不支持 ALTER 约束**；且 0001 的外键**未显式命名**，SQLite 中找不到
  `messages_news_id_fkey`。此前被启动时 `create_all()` 掩盖，P0 单轨化后
  **默认配置（SQLite）完全无法启动**。
- **改动**：`0003` 与 `0007` 改为**方言感知**——仅在 PostgreSQL 上改/加外键约束；
  SQLite 侧由应用层维护引用完整性（`seed._sync_orphans`、
  `ingest.prune_old_news` 里显式的解除关联），且 SQLite 默认不启用外键约束。
- **测试**：新增 `tests/test_migrations.py`（6 项）作为长期守卫，
  在临时 SQLite 库上跑 `upgrade head → downgrade base → upgrade head`。
- **⚠️ 实施事故**：首版该测试在**进程内**调用 `command.upgrade()`，因
  `migrations/env.py` 读的是 `app.config.DATABASE_URL`（不读 Config 的
  `sqlalchemy.url`），实际把**真实 PG 库回滚到 base、删除全部表**。
  已改为**子进程 + 覆盖 DATABASE_URL 环境变量**隔离，并在文件头写明该约束。
  数据库已恢复：`upgrade head` + 种子 + 重抓（现 301 条资讯）。
- **加固**：`migrations/env.py` 增加"迁移目标（隐藏口令）"日志，便于确认作用库。

- **迁移**：`0007_comment_user_id`；`0003` / `0007` 兼容性改写。
- **测试**：后端 90 → 100 项；前端 84 → 87 项。
- **文档**：README 测试清单与迁移章节同步。

---

