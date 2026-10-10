# LEA Console

独立的 LEA Platform 管理控制台，不复用遗留 `frontend-admin` 的业务模型和 `/api/admin/*` 接口。

## 本地启动

```bash
npm install
npm run dev
```

默认访问 `http://127.0.0.1:9400`，并把 `/api` 代理到 `http://127.0.0.1:8080`。可通过 `VITE_GATEWAY_TARGET` 修改开发代理地址；测试服务器 Docker 部署仍由 Nginx 连接 Compose 内的 `gateway:8080`。

控制台首次打开时需要输入基座平台 JWT 和租户 ID。凭据只保存在当前浏览器的 `localStorage` 中。

## 构建

```bash
npm run build
```

Docker 运行时设置 `LEA_GATEWAY_URL`，例如 `http://gateway:8080`。
