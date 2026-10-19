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

# 探测**穿过反代**，不是只摸静态文件。
#
# 原来探的是 `/`，那是 nginx 自己托管的静态页面 —— 2026-10-19 那次 API 全线
# 502（nginx 攥着 gateway 的旧 IP，见 nginx.conf 顶部）期间，这条检查一路绿着
# 返 200，所以坏了快一周没人发现。检查在它该失败的那个方向上失不了败，等于没有。
#
# 401 算健康：它说明请求真的到了 gateway 并被认证拦住，也就是反代这条链是通的。
# 502/504 和连不上都会落到 grep 之外 → 非 0 退出 → unhealthy，正是要抓的那类故障。
#
# 代价说清楚：gateway 挂了会把这个前端也标成 unhealthy，哪怕 nginx 自己好着。
# 这是故意的——这个容器的价值就是能把请求送到后端，送不到就不算健康。standalone
# Docker 的 restart 策略只看退出码、不看 healthcheck，所以不会因此重启循环，
# 纯粹是个给人看的信号（docker ps 是出事时第一眼看的地方）。
#
# start-period 从 10s 提到 40s：现在依赖 gateway 就绪，而它要验 JWKS、连 Redis。
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD wget -qS -O /dev/null http://127.0.0.1/api/user/agents 2>&1 | grep -qE "HTTP/1.1 (200|401|403)"
