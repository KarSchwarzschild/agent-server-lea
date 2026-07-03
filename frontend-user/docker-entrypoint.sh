#!/bin/sh
# 前端容器启动脚本
# 使用环境变量替换 nginx 配置中的占位符

# 默认值
BACKEND_HOST=${BACKEND_HOST:-lifelong-edu-backend}
BACKEND_PORT=${BACKEND_PORT:-8000}

# 导出变量供 envsubst 使用
export BACKEND_HOST BACKEND_PORT

# 替换模板中的变量，生成实际配置
envsubst '${BACKEND_HOST} ${BACKEND_PORT}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

echo "Nginx config generated with BACKEND_HOST=${BACKEND_HOST}, BACKEND_PORT=${BACKEND_PORT}"

# 启动 nginx
exec nginx -g 'daemon off;'
