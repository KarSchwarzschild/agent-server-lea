# 集成测试 · workspace 文件链路

## 为什么必须在挂了共享存储的机器上跑

Cube 的 host-mount 有一条硬约束（Phase 0 实测）：

```
mount: special device /data/cubelet/lea/tenants/<tid>/.../workspace does not exist
```

**Cube 不会创建 hostPath**，目录必须事先存在于节点上。所以谁请求挂载，谁就得先能在共享存储上建出目录。lea-tools 看不到那块盘时 `ensure_session_dir()` 返回 None，代码自动不请求 host-mount（否则必然 500）。

结果就是：**真 NFS 路径只能在 lea-tools 挂了共享存储的机器上测**。本地 mock 目录（`.local/nfs-mock`）只是前端 fixture —— 文件是 `echo` 出来的，跳过了「沙箱写 → host-mount → readdir 读到」这一整环，不算集成测试。

## 覆盖矩阵

| 部署形态 | `store` | list/download/越权 | 沙箱死后存活 |
|---|---|---|---|
| lea-tools 与 Cube 同机 | `nfs` | 覆盖 | **覆盖** |
| ECS/CCE 挂 SFS Turbo（生产） | `nfs` | 覆盖 | **覆盖** |
| 笔记本 + 远端 Cube | `sandbox` | 覆盖 | SKIP |
| 笔记本 + mock 目录 | `nfs` | 仅 JSON 契约 | 无意义（无真沙箱） |

## 在 Cube 那台机上跑（10.192.241.137）

```bash
# 1. 目录（Cube 白名单已配 /data/cubelet/lea/）
mkdir -p /data/cubelet/lea/tenants /data/cubelet/lea/platform/skills

# 2. 依赖 —— 仓库是 uv workspace（members = packages/*），一次同步全部
cd ~/lea-platform
uv sync                      # 没装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 起服务 —— NFS_ROOT 指向共享存储挂载点
NFS_ROOT=/data/cubelet/lea \
CUBE_API_URL=http://127.0.0.1:3000 \
CUBE_TEMPLATE_ID=tpl-a58d55bcc1a54b5bbfbb4c00 \
SANDBOX_BACKEND=cube \
DB_HOST=sqlite \
  uv run uvicorn lea_tools.server:app --host 0.0.0.0 --port 8400

# 4. 另一个终端跑测试
cd ~/lea-platform && bash scripts/integ_test_files.sh
```

只想要 lea-tools 一个包（不同步整个 workspace）：

```bash
uv sync --package lea-tools
```

期望：`store = nfs`，且 survive 两条从 SKIP 变 PASS。

也可以从笔记本打服务器上的 lea-tools：`BASE=http://10.192.241.137:8400 bash scripts/integ_test_files.sh`

## 生产（CCE + SFS Turbo）

给 lea-tools 的 Pod 挂 SFS Turbo PVC 到 `/data/shared/lea`，Cube 节点挂**同一个** PVC 到**同一路径**（host-mount 的 hostPath 是节点视角，路径必须一致），然后 `NFS_ROOT=/data/shared/lea`。代码无需改动。

## 前端开发（不需要真 Cube）

```bash
SANDBOX_BACKEND=mock NFS_ROOT=./.local/nfs-mock DB_HOST=sqlite \
  uv run uvicorn lea_tools.server:app --port 8400
```

`/api/files/list` 返回的 JSON 与生产一致，够开发抽屉用。
