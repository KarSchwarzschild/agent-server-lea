# 用户端前端 Dockerfile
# 面向终身教育研究的科研型智能体 - User Frontend

# 构建阶段
FROM node:20-alpine AS builder

WORKDIR /app

# 复制依赖文件
COPY package*.json ./

# 安装依赖
RUN npm ci

# 复制源代码
COPY . .

# 构建参数 - API地址（空字符串表示使用相对路径）
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# 构建
RUN npm run build

# 生产阶段 - 使用 nginx 提供静态文件
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置模板
COPY nginx.conf.template /etc/nginx/conf.d/default.conf.template

# 复制启动脚本
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# 环境变量（默认值，可在部署时覆盖）
ENV BACKEND_HOST=lifelong-edu-backend
ENV BACKEND_PORT=8000

# 暴露端口
EXPOSE 80

# 使用启动脚本
ENTRYPOINT ["/docker-entrypoint.sh"]
