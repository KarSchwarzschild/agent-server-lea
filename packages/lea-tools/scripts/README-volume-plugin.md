# cube-volume-s3 — 通用 S3 兼容 Volume 插件

给 CubeSandbox v0.6.0+ 用的 Volume 插件（e2b 兼容协议）。**同一份脚本，
开发环境接 MinIO，生产环境接华为云 OBS / 腾讯云 COS / AWS S3**，靠
`volume-s3.conf` 里的 `ENDPOINT` 切换。

参考自官方 `examples/volume/cos/binary/cube-volume-cos.sh`，主要改造：

| 项 | COS 原版 | 本插件 |
|---|---|---|
| FUSE 驱动 | `cosfs` | `s3fs-fuse` |
| 后端 CRUD | `coscmd` CLI | `curl --aws-sigv4` 直连 S3 REST |
| 密钥文件格式 | `BUCKET:AK:SK` | `AK:SK`（s3fs 标准）|
| Endpoint | 硬编码 `cos.<region>.myqcloud.com` | 配置文件 |
| Path style | 无 | `USE_PATH_STYLE=1`（MinIO 必须） |

---

## 文件

```
scripts/
├── cube-volume-s3.sh           # 主脚本（Cube 平台 fork 这个）
├── volume-s3.conf.example      # 配置模板
└── README-volume-plugin.md     # 本文件
```

## 依赖（Cube 所有节点上均需装）

- `s3fs-fuse` — FUSE 挂载
- `curl` ≥ 7.75 — 走 S3 REST 建/删对象（Ubuntu 22.04 自带 7.81 满足）
- `jq` — JSON/XML 解析

Ubuntu:
```bash
sudo apt update
sudo apt install -y s3fs jq
# curl 系统已带，无需装
```

**不需要 python / pip / boto3**。所有 S3 后端调用走 `curl --aws-sigv4` 直连。

## 部署步骤（在 Cube 节点 `10.192.241.137` 上执行）

> **路径约定**：跟官方 COS 参考插件对齐，一份放 CubeMaster，一份放 Cubelet
> （即使同机部署，两处目录也各放一份 —— one-click 就是这个布局）。

```
/usr/local/services/cubetoolbox/
├── CubeMaster/plugin/
│   ├── cube-volume-s3     ← 脚本本体（可以是 symlink）
│   └── volume-s3.conf     ← 凭证，600
└── Cubelet/plugin/
    ├── cube-volume-s3
    └── volume-s3.conf
```

### 1. 拷脚本 + 建配置（两侧各一份）

```bash
PREFIX=/usr/local/services/cubetoolbox

# CubeMaster 侧
sudo install -m 0755 cube-volume-s3.sh "$PREFIX/CubeMaster/plugin/cube-volume-s3"
sudo install -m 0600 volume-s3.conf.example "$PREFIX/CubeMaster/plugin/volume-s3.conf"

# Cubelet 侧（同机就用 symlink，避免两份漂移）
sudo install -m 0755 cube-volume-s3.sh "$PREFIX/Cubelet/plugin/cube-volume-s3"
sudo install -m 0600 volume-s3.conf.example "$PREFIX/Cubelet/plugin/volume-s3.conf"

# 编辑凭证（两处都要，或者两份指到同一文件）
sudo ${EDITOR:-vi} "$PREFIX/CubeMaster/plugin/volume-s3.conf"
sudo ${EDITOR:-vi} "$PREFIX/Cubelet/plugin/volume-s3.conf"
```

### 2. 注册插件到 Cube

**CubeMaster 侧**（改 `conf.yaml`）：
```yaml
volume_plugins:
  - name: s3
    type: binary
    binary_path: /usr/local/services/cubetoolbox/CubeMaster/plugin/cube-volume-s3
```

**Cubelet 侧**（改 `config.toml`）：
```toml
[[plugins."io.cubelet.internal.v1.storage".volume_plugins]]
  name        = "s3"
  type        = "binary"
  binary_path = "/usr/local/services/cubetoolbox/Cubelet/plugin/cube-volume-s3"
```

两侧 `name` 必须一致。省略 `driver` 调 `Volume.create()` 时取 `volume_plugins` **列表第一项**。

### 3. 重启 + 确认加载

```bash
sudo systemctl restart cube-sandbox-cubemaster
sudo systemctl restart cube-sandbox-cubelet
sudo systemctl restart cube-sandbox-cube-api   # 若有

sleep 5
systemctl is-active cube-sandbox-cubemaster cube-sandbox-cubelet

# 关键：日志里应能看到插件已加载
grep -aF '[volume] registered' /data/log/CubeMaster/cubemaster-req.log | tail -5
grep -aF '[plugin_volume] initialized' /data/log/Cubelet/Cubelet-req.log | tail -5
```

期望输出：
```
[volume] registered binary plugin "s3" at /usr/local/services/cubetoolbox/CubeMaster/plugin/cube-volume-s3
[plugin_volume] initialized binary plugin "s3" at /usr/local/services/cubetoolbox/Cubelet/plugin/cube-volume-s3
```

### 4. 手动冒烟测试（不经 Cube 平台）

```bash
PLUGIN=/usr/local/services/cubetoolbox/Cubelet/plugin/cube-volume-s3

# Create — 应该在 bucket 里写一个 volumes/probe/.keep
"$PLUGIN" --op create --volume-id probe --name probe

# Attach — 应该 s3fs 挂到 /data/cube-shared/volume/s3-probe/
"$PLUGIN" --op attach \
  --sandbox-id sb-test --namespace default \
  --volume-id probe --ref-count 0 \
  --volume-base-dir /data/cube-shared/volume

# 验证挂载
mount | grep s3fs
ls -la /data/cube-shared/volume/s3-probe/

# 写入
echo "hello from host" | sudo tee /data/cube-shared/volume/s3-probe/probe.txt

# 从 MinIO 侧验证文件真的到了 bucket
sudo docker run --rm --network host \
  -e MC_HOST_local=http://minioadmin:minioadmin@127.0.0.1:9000 \
  minio/mc ls local/lea-sandbox-volumes/volumes/probe/

# Detach — 应该 umount + 删挂载点
"$PLUGIN" --op detach \
  --sandbox-id sb-test --namespace default \
  --volume-id probe --ref-count 0 \
  --metadata '{"mount_dir":"/data/cube-shared/volume/s3-probe"}'

# Destroy — 应该从 bucket 删 volumes/probe/*
"$PLUGIN" --op destroy --volume-id probe
```

四步全通 = 插件可用。

### 5. 通过 Cube SDK 端到端验证（复用官方脚本）

官方 `verify_volume.py` 已经覆盖：单 driver 生命周期、多沙箱共用、负向场景、
HTTP 契约。**不用自己写 demo，跑它就行**：

```bash
git clone https://github.com/TencentCloud/CubeSandbox.git /tmp/cube
cd /tmp/cube/examples/volume/cos

pip install 'cubesandbox>=0.6.0' requests

export CUBE_API_URL=http://127.0.0.1:3000
export CUBE_TEMPLATE_ID=tpl-a58d55bcc1a54b5bbfbb4c00
export CUBE_PROXY_NODE_IP=127.0.0.1
export CUBE_VOLUME_DRIVERS=s3          # ← 关键：告诉脚本用我们注册的 s3 driver

python3 verify_volume.py
```

脚本会输出分组报告；任一断言失败退出码非 0。

> **注意 SDK 选择**：应用侧用 `cubesandbox`（Cube 官方 e2b 兼容 SDK），
> **不要**用 `pip install e2b` — e2b 官方 SDK 硬编码 e2b.cloud，跑不通
> 自建 CubeSandbox。lea-tools 里 `_cube_sandbox.py` 已经是 `cubesandbox`，
> 无需改动。

## 生产部署 Checklist（上线前必查）

### 网络隔离

- [ ] **`lea-tools`（8400）严禁暴露公网**：它是内部服务，没有 JWT，只信任
  `X-Tenant-ID` / `X-User-ID` header —— 任何能直连它的东西都能伪造身份读任意
  产物。生产必须 iptables / 安全组 allowlist 仅放行 `lea-agent` / `lea-server`。
- [ ] `S3_ENDPOINT` / `S3_INTERNAL_ENDPOINT` 是**内网地址**（不走公网），否则
  下载走公网出口，钱都烧在流量上。
- [ ] `S3_PUBLIC_ENDPOINT` 保留给后续预签 URL 用（浏览器直下），当前 sprint 走
  lea-tools 中转所以可以不设。

### 存储

- [ ] `USE_CACHE_DIR` **必须落数据盘**，如 `/data/cubelet/s3fs-cache`。**默认
  空 → 每次直连 S3，慢但省本地空间**；**设错到系统盘 → s3fs 缓存会把 root fs
  撑爆**（Cubelet 宿主机上跑几天就 disk full）。
- [ ] Bucket 生命周期策略：`volumes/` 前缀下的对象没有自动 GC（Volume destroy
  才删）。建议 OBS/S3 上加 90 天冷数据归档规则，或对 archived session 主动
  destroy volume。

### 保护性上限

- [ ] `lea-tools` 单文件下载上限：`MAX_ARTIFACT_MB`（默认 500）。超过 413。
- [ ] `lea-agent` 每用户下载速率：60s / 30 次内存滑动窗口。多副本部署时**内存
  版失效**，需要换 Redis 分布式限流。
- [ ] Cube volume plugin 的 `USE_CACHE_DIR` 磁盘监控告警（10% 阈值即报）。

### 密钥

- [ ] `volume-s3.conf` 权限 `chmod 600`，跟 Cube 服务同 owner。
- [ ] `.env` 里 `S3_SECRET_KEY` 不进 git（`.gitignore` 已覆盖）。
- [ ] AK/SK 走**独立的**沙箱专用 subaccount，只授权 `volumes/*` 前缀，不给别的
  bucket 权限；跟 KB / skill bundle 的 MinIO 账号隔离。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `no plugin registered for driver s3` | Cubelet 未配置或名字不一致 | 检查 `config.toml`，重启 Cubelet |
| `s3fs mount failed` + `Access Denied` | 密钥错或 bucket 不存在 | 校对 `volume-s3.conf` |
| MinIO 路径错误 | `USE_PATH_STYLE` 没开 | 设为 `1` |
| 系统盘满 | `USE_CACHE_DIR` 落系统盘 | 改到 `/data/*` |
| 沙箱内看不到文件 | 手动 `mount` 走了宿主机 mntns | 让 Cubelet fork 插件，不要手动 mount |

排错命令：
```bash
# 看 Cubelet mntns 里的挂载
CPID=$(pgrep -f "cubelet --config" | head -1)
sudo nsenter -t "$CPID" -m -- mount | grep s3fs

# 看插件日志（stderr 里的 log 会带回 Cubelet 日志）
sudo journalctl -u cubelet -f | grep cube-volume-s3
```

## 生产环境（OBS）切换

只改 `volume-s3.conf` 的四行：
```
ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
BUCKET=lea-sandbox-volumes-prod
ACCESS_KEY=<OBS_AK>
SECRET_KEY=<OBS_SK>
REGION=cn-north-4
```
其余不变。`USE_PATH_STYLE=1` 对 OBS 也 OK。

## 关联

- Cube v0.6.0 Volume Plugin 官方文档：<https://github.com/TencentCloud/CubeSandbox/blob/master/docs/zh/guide/volume-plugin.md>
- COS 参考插件：<https://github.com/TencentCloud/CubeSandbox/tree/master/examples/volume/cos>
- lea-platform memory：`[[reference-cube-v060]]` · `[[project-cube-volume-artifacts]]`
