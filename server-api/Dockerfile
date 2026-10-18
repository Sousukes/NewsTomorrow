# 后端镜像（D8）
#
# 刻意保持单阶段：依赖是纯 Python 包（psycopg[binary]、redis 都是 wheel），
# 多阶段省不了多少体积却多一层可出错的东西。需要 slim 镜像时在 CI 里量一下
# 收益再改，别为了「看起来更专业」引入构建期复杂度。
FROM python:3.13-slim

# curl 只用于 compose 的 healthcheck；不装就只能把探活改成 Python 脚本，
# 但那会让人以为容器里没有别的办法（其实有）。一行成本，可接受。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 时区/编码：抓取源站返回的中文标题要在容器里正确解码
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖再拷代码：改业务代码时这一层仍可复用缓存（构建快得多）。
# 用 requirements.txt 而不是 requirements-optional.txt：Pillow 是 native 依赖，
# 由部署方决定是否安装（见 README「图片代理（B4）」）。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 迁移不在构建期跑：构建是**无状态**的，构建期连不上运行期的库，
# 迁移必须由 entrypoint 在启动时对着真实库执行。
RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

# 探活用 /api/health，且**必须带 OPS_TOKEN**：C5 要求生产环境对该接口
# fail-closed（未配置令牌时返回 403）。所以生产不设 OPS_TOKEN 的后果不只是
# "运维接口不可访问"，而是**容器永远不健康**、依赖它的 frontend 永不启动——
# 这是刻意的：宁可起不来，也不要一个看起来正常、实际运维接口裸奔的服务。
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
    CMD curl -fsS -H "X-Ops-Token: ${OPS_TOKEN:-}" http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["./scripts/entrypoint.sh"]