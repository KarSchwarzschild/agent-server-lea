# frontend-lab —— 生产部署镜像。
#
# 两阶段：Node 编译静态资源 → Nginx 托管 + 反代 API。
#
# 为什么要反代：src/api.ts 里 apiBase = ''，所有请求都是同源相对路径
# （跟 vite dev server 的 proxy 配置一个思路，见 vite.config.ts）。
# vite dev 用的是 vite 自己的 proxy，生产环境静态文件没有这层，
# 必须由 Nginx 顶上，把这些路径转给 lea-gateway，否则浏览器直接对着
# 静态文件服务器请求 /api/... 只会拿到 404。
#
# 构建（上下文是 frontend-lab 自己这个目录）：
#     docker build -f frontend-lab/Dockerfile -t lea-frontend-lab:test frontend-lab

FROM node:20-alpine AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:80/ || exit 1
