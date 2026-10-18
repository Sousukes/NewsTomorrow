# 前端镜像：构建 → nginx 静态托管（D8）
#
# 部署形态刻意选**同源**：nginx 把 /api/ 反代到后端，而前端构建时不带
# VITE_API_BASE（留空 → 相对路径请求）。于是浏览器只跟一个源打交道——
# CSP 可以收得最紧（img-src 不需要放行任何外域）、没有 CORS 预检、
# 也不需要把后端地址硬编码进构建产物。
#
# 注意 VITE_USE_MOCK=false 是**必须显式传**的：USE_MOCK 的默认值是
# `API_BASE ? 'false' : 'true'`（没配后端地址就当演示态），
# 生产镜像不显式传就会静默跑起内置 mock —— 界面上完全看不出来。

# ---- 构建阶段 ----
FROM node:22-alpine AS build
WORKDIR /app

# 先只拷依赖清单，利用 Docker 层缓存：业务代码改动不重装依赖
COPY package.json package-lock.json ./
# 本机实测：npm install 的 postinstall（esbuild）会 spawn node，
# 被实时防护占句柄后报 EBUSY。node:alpine 没有这个防护问题，
# 但 --ignore-scripts 会导致 esbuild 平台二进制装不上，所以这里用常规安装。
RUN npm ci --no-fund --no-audit

COPY . .

ARG VITE_API_BASE=""
ARG VITE_USE_MOCK="false"
ENV VITE_API_BASE=$VITE_API_BASE \
    VITE_USE_MOCK=$VITE_USE_MOCK
RUN npm run build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]