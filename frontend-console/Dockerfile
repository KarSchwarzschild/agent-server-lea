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
