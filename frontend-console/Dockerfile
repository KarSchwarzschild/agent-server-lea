FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# 变量名是 NGINX_ENVSUBST_FILTER，不是 VARS —— 入口脚本
# (/docker-entrypoint.d/20-envsubst-on-templates.sh) 只认 FILTER / OUTPUT_DIR /
# TEMPLATE_DIR / TEMPLATE_SUFFIX 这几个。写错名字是空操作：filter 为空时 envsubst
# 会把**所有**已定义的环境变量都当替换目标，碰巧也替掉了 ${LEA_GATEWAY_URL}，
# 所以一直没露馅。但那样任何恰好与 nginx 变量同名的环境变量（${host} 之类）都会
# 被误替，所以收紧成只替这一个。
ENV NGINX_ENVSUBST_FILTER=LEA_GATEWAY_URL
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80

# 这个镜像原来**完全没有** healthcheck，docker ps 只显示 Up —— 2026-10-19 那次
# API 全线 502（nginx 攥着 gateway 的旧 IP，见 nginx.conf.template 顶部）就是这么
# 躲过一周的。探测必须穿过反代，只摸静态文件的话 502 期间照样返 200。
#
# 401 算健康：说明请求真到了 gateway 并被认证拦住，也就是反代这条链通的。
# 502/504 和连不上都落在 grep 之外 → 非 0 → unhealthy，正是要抓的那类故障。
# 判定两个方向都在真容器里验过（401 通过；502/504/连不上全部失败）。
#
# 代价：gateway 挂了这个前端也会被标 unhealthy，哪怕 nginx 自己好着。这是故意的
# ——容器的价值就是能把请求送到后端。standalone Docker 的 restart 只看退出码、
# 不看 healthcheck，所以不会因此重启循环，纯粹是个给人看的信号。
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD wget -qS -O /dev/null http://127.0.0.1/api/agents 2>&1 | grep -qE "HTTP/1.1 (200|401|403)"
