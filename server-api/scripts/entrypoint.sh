#!/bin/sh
# 容器启动前置：迁移先行，然后才起服务（D8）。
#
# 为什么必须有这一步：本项目**只有一个 schema 事实源**（Alembic）。没跑迁移
# 就起服务，症状是「列不存在」——报错指向某处业务查询，而真正的原因是
# 环境没升级。这类环境债在 CI 里看不见（CI 自己会先 upgrade），只在部署时炸。
#
# 关键约束：**迁移失败就必须停**。用 `set -e` 而不是「迁移失败也照样起服务」——
# 起了服务也只会返回一堆 500，且会把「环境没升级」伪装成「代码有 bug」。
# `exec` 让 uvicorn 接管 PID 1，容器能正常收到 SIGTERM 并优雅退出。
set -e

# 多副本部署时不该每个副本都抢着迁移（并发跑同一份 DDL 会互相锁死甚至写坏）。
# compose 里用一个一次性的 `migrate` 服务跑迁移，backend 设 RUN_MIGRATIONS=false
# 并 depends_on 该服务 —— 两种用法都支持，本文件是它们的共同实现。
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "[entrypoint] 等待数据库并执行迁移（alembic upgrade head）…"
  python -m alembic upgrade head
  echo "[entrypoint] 迁移完成"
fi

PORT="${PORT:-8000}"
# WORKERS 默认 2：图片代理（B4）是**同步**端点，会占用线程池；
# 只有 1 个 worker 时，一张慢图就能把整个 API 拖住（单进程内并发被线程池串行化）。
# 与 compose 里的资源 limits 对照调整。
WORKERS="${WEB_CONCURRENCY:-2}"

echo "[entrypoint] 启动 uvicorn（0.0.0.0:${PORT}，${WORKERS} workers）"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT}" --workers "${WORKERS}"