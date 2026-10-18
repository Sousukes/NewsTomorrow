# 明日头条 · 后端 API（FastAPI + PostgreSQL / SQLite）

为「明日头条」Vue 前端提供资讯 / 评论 / 认证接口。响应统一为 `{ code, message, data }`（`code: 0` 为成功），与前端 `src/api/http.ts` 的解包约定一致。

> 本文件是**入口与索引**。深度内容按主题拆在 [`docs/后端/`](../docs/后端/)，
> 一篇一个主题，便于检索。全站索引见 [`docs/README.md`](../docs/README.md)。

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

**启动不再自动建表**，表结构由第 3 步的迁移负责；缺表时会报错并提示执行
`alembic upgrade head`。容器部署时这一步由 entrypoint 自动完成（见 `deploy.md`）。

```bash
pytest -q                       # 单元测试（快，不触发覆盖率门槛）
pytest -q --cov                # 带覆盖率（门槛 91，实测 92.55%）
node ../mingri-toutiao-vue/scripts/contract-check.mjs   # 契约检查
```

- 交互式接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：`GET /api/health`（生产需 `OPS_TOKEN`，fail-closed）
- 连接信息见 `.env` 的 `DATABASE_URL`；演示账号见 `DEMO_PHONE` / `DEMO_PASSWORD`

## 文档索引

| 主题 | 文档 |
| --- | --- |
| 环境变量、限流、连接池、覆盖率门槛、部署、目录结构 | [快速开始与配置](../docs/后端/快速开始与配置.md) |
| 接口清单、令牌双轨、二次校验、改密与代重置、图片代理、管理端鉴权、运维鉴权、安全响应头、性能债、错误码、时间口径 | [API 参考](../docs/后端/API%20参考.md) |
| RSS 抓取、Alembic、相关推荐、搜索与热词、排序个性化、消息中心、正文 | [抓取与内容](../docs/后端/抓取与内容.md) |
| 收藏与浏览历史、评论、敏感词审核、昵称校验、命中日志留存 | [内容治理与用户数据](../docs/后端/内容治理与用户数据.md) |
| **部署**（compose 四服务、迁移前置钩子、密钥与 `TRUSTED_PROXIES`） | [deploy.md](../docs/deploy.md) |

## 几条最容易踩的约定

- **schema 只有 Alembic 一个事实源**：改表必须写迁移，别在 `create_all` 上做文章。
- **时间写库一律 `now_str()`**（UTC aware），读取走 `models.parse_dt()`；
  禁止 `datetime.now()`。详见 [API 参考 · 时间口径](../docs/后端/API%20参考.md)。
- **管理端鉴权挂在 router 级**（`admin.py`），新增端点自动受保护，
  不要在端点体里另写判断。
- **入参多词字段一律驼峰**：蛇形会被 pydantic `extra='ignore'` 静默丢弃，
  症状是"开关永不生效"且两端单测都不失败。
- **新增错误码前先查占用**：错误码是前端文案分发键。