# 部署指南（D8）

本项目**不托管前端**：`server-api` 只提供 API，SPA 由静态服务器托管。
`docker-compose.yml` 给出一套可直接跑起来的本地/单机部署形态：

```
nginx(SPA + /api 反代)  →  uvicorn(API)  →  postgres
                                       ↘  redis
```

四个服务：`db`、`redis`、`migrate`（一次性）、`backend`、`frontend`。

## 为什么迁移是「一次性服务」而不是「每个副本都跑」

本项目**只有一个 schema 事实源**（Alembic）。没跑迁移就起服务，症状是
「列不存在」——报错指向某处业务查询，真正原因却是环境没升级。这类债在 CI 里
看不见（CI 自己会先 `upgrade`），只在部署时炸。

多副本各自跑迁移会互相锁死同一份 DDL，极端情况下写坏。所以：

1. `migrate` 服务跑一次 `alembic upgrade head` 然后退出；
2. `backend` 设 `RUN_MIGRATIONS=false`，
   `depends_on: migrate: condition: service_completed_successfully`。

于是**迁移失败会阻止服务启动**，而不是起一个满屏 500 的服务——后者会把
「环境没升级」伪装成「代码有 bug」。若不用 compose（例如裸 Docker 单容器），
entrypoint 默认 `RUN_MIGRATIONS=true`，它会自己先迁移。

## 启动

```bash
# 1. 准备环境变量（仓库根目录）
cp .env.example .env      # 密钥类变量必须改，compose 用 ${VAR:?} 强制拦截
```

`.env` 里**必须**设置：

| 变量 | 说明 |
| --- | --- |
| `POSTGRES_PASSWORD` | 数据库密码（compose 用 `${VAR:?}` 拦截，漏了直接报错退出） |
| `JWT_SECRET` | 令牌签名密钥，**至少 32 字节**。漏了服务起不来——这是有意的 |
| `OPS_TOKEN` | 运维接口令牌。**生产必设**，见下节 |

**强烈建议**设置 `ADMIN_PHONES`（逗号分隔的手机号）：env 管理员是
**break-glass 应急通道**——库被误删、白名单行被删、迁移回滚时，它是唯一还能进
管理后台的入口。应用内白名单（`admin_users` 表）适合日常增删，但那份数据在库里。

```bash
# 2. 构建并启动
docker compose up -d --build

# 3. 看状态与日志
docker compose ps
docker compose logs -f backend
```

## 健康检查与 OPS_TOKEN（C5 的 fail-closed）

`/api/health` 与 `/api/metrics` 走 `require_ops_access`：

- `OPS_TOKEN` 已配置：必须带 `Authorization: Bearer <token>` 或 `X-Ops-Token: <token>`；
- **未配置**：`development` 放行，**`production` 一律 403**。

容器 healthcheck 用的就是 `/api/health`，且带上了 `OPS_TOKEN`。于是
**生产环境不设 `OPS_TOKEN` 的后果是容器永远 unhealthy**，依赖它的 frontend
也不会启动。这是刻意的：宁可起不来，也不要一个看起来正常、实际运维接口
无鉴权的服务。

排查：

```bash
docker compose exec backend sh -c 'curl -s -H "X-Ops-Token: $OPS_TOKEN" http://127.0.0.1:8000/api/health'
```

## 反向代理与客户端 IP（限流依赖）

限流按客户端 IP 计数，而后端**只在 `TRUSTED_PROXIES` 命中的网段采信
`X-Forwarded-For`**（防止任何人伪造这个头绕过限流）。compose 里 backend 默认
`TRUSTED_PROXIES=`（空 = 不采信任何代理头）：

- **compose 默认形态**：nginx 与 backend 在同一网络，nginx 传的是真实对端地址，
  限流按 nginx 的 IP 计数——所有用户共享一个计数桶，**生产不可接受**；
- 正确做法：给 nginx 单独一个 Docker 网络名/网段，并把它写进 `TRUSTED_PROXIES`
  （如 `172.20.0.0/16`，用 `docker network inspect` 确认实际网段）。

不做这一步的症状：一旦某个 IP 触发限流，**全站所有人一起被限流**。

## 升级

```bash
git pull
docker compose up -d --build      # migrate 服务会先跑完迁移，backend 才起
```

回滚代码请**先回滚镜像再回滚迁移**（`alembic downgrade` 需人工确认，
多数改动不可逆——schema 回滚比代码回滚危险得多）。

## 前端构建的两个参数（容易静默出错）

| 参数 | 值 | 为什么 |
| --- | --- | --- |
| `VITE_API_BASE` | 留空 | 请求走相对路径 `/api/…`，由 nginx 反代 → 前后端同源，CSP 能收得最紧、无 CORS 预检 |
| `VITE_USE_MOCK` | **`false`（必须显式传）** | 其默认值是「没配 `VITE_API_BASE` 就当演示态」。不显式传的话**生产镜像会静默跑起内置 mock**——界面看起来完全正常，只是所有数据都是假的 |

## 可选：图片缩略图依赖

图片代理（B4）装了 Pillow 才会出缩略图与 WebP，未装则原样透传（代理功能本身
不受影响）。它是 native 依赖，**不在 `requirements.txt` 里**：

```dockerfile
# 在 server-api/Dockerfile 的 pip install 那行之后加：
RUN pip install --no-cache-dir Pillow>=10.0
```

## 镜像可运维性说明

CI 的 `docker` job 会 `docker compose build`（D8）——**镜像构建在推 PR 时就验证**，
`Dockerfile` / `nginx.conf` / `docker-compose.yml` 的语法漂移会在 CI 直接红。

当前保证分三层：

- **CI 构建**：`docker compose build` 验证「四个镜像都能构建成功」；
- entrypoint 的执行顺序与失败即停，由 `tests/test_deploy.py` **实际执行**验证；
- compose / Dockerfile / nginx.conf 的关键不变量（迁移顺序、`/api` 不做 SPA
  fallback、healthcheck 带令牌、禁 mock 等）由**静态断言**守住。

诚实边界：**构建通过 ≠ 能跑起来**。CI 验证的是"镜像可构建"，不是"容器能正常
启动并服务"——后者仍没有端到端验证（需要真实 PG/Redis 运行态，属后续项）。