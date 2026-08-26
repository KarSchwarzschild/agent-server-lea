# Sandbox NFS 持久化存储方案

> 状态：v2 定稿（2026-07-28）
> 前置：[Cube Sandbox Lifecycle](https://cubesandbox.com/zh/guide/lifecycle.html) · [Persistent Storage / Host Mount](https://cubesandbox.com/zh/guide/persistent-storage.html)
> 关联：[TOOL_GATEWAY.md](./TOOL_GATEWAY.md) · Memory [[project-cube-volume-artifacts]] · [[reference-cube-lifecycle]]
> 变更历史：v1 只挂 output + S3 兜底；v2 workspace 全挂 + 环境隔离 + 冷热分层

## 1. 背景与目标

### 1.1 现状（2026-07-28）

Sandbox 产物走"目录剪枝后全推 S3"（详见 [[project-cube-volume-artifacts]]），沙箱不挂 volume。已知短板：
1. Push 是**事后**的，前端抽屉看不到实时进展
2. 多节点扩容后老 sandbox 无法在别节点 resume（Cube pause 绑单节点）
3. 依赖不持久：`pip install` 装的东西随 sandbox 死亡消失
4. Skill bundle 每次冷启走 envd files 端口逐文件上传，几十文件累加

### 1.2 目标

多节点 Cube 生产部署上一个**真正跨节点、跨 session 的文件持久化层**，同时把**用户产物 / Python 依赖 / Skill bundle** 三类数据分开管，各走各的挂载点、各有各的生命周期。

### 1.3 核心决策（v2）

| 决策 | 选择 | 理由 |
|---|---|---|
| 持久化技术 | **NFS + Host Mount**（Cube 原生） | Kernel bind-mount，完整 POSIX；v0.5.11 已验证 |
| 挂载布局 | **3 组 mount：workspace RW + platform skills RO 共享 + user skills RO 独立** | 见 [Section 3](#3-架构) |
| 依赖策略 | **Template 预装 + 现场 `pip install` 不持久** | Coze/ChatGPT 同款体验；冷门包重装 10-20s 用户可接受；避免 NFS 小文件写灾难 |
| Quota | **session workspace 500MB hard；skills 50MB** | 底层 fs project quota；防单 session 撑爆 |
| 冷热分层 | **30 天热层 NFS，冷层归档 S3** | 1000 用户级容量控制；见 [Section 8](#8-冷热分层) |
| **NFS 后端** | **Phase 1 自建（10.192.241.137）→ Phase X 华为云 SFS Turbo** | 开发内测阶段用自建；生产切云托管 |
| Skill 版本 | **无版本，冷启全量覆盖** | 几十 KB 拷贝比 exists 检查还便宜 |
| Skill 路线 | **S3 为唯一真相 → 按 sha 缓存 → 按 agent 绑定投递到 session，RO 挂 `/opt/skills`** | 不共享挂载：没绑定的 skill 沙箱里不存在，避免白送 |
| 产物展示约定 | **不强制落点：整个 workspace 都在 NFS，抽屉展示完整树 + output 高亮徽章 + 隐藏 `__pycache__` 类** | 都在 NFS 上无防丢需求，抽屉分类靠展示层过滤 |
| 挂载点路径 | 所有节点统一 `/data/shared/lea` | 跨节点行为一致 |
| 租户/用户隔离 | 目录规划 + 应用层正则校验 + Cube 白名单 | 沙箱 UID=0 (root)，chown 隔离无效，纯靠挂载隔离 |
| Skill 代码保护 | RO 挂载 + 抽屉视野不含 skill 目录 + 提示词软引导 LLM 别泄露 | 威胁模型"防手滑不防主动扒"（详见 [Section 14.1](#141-skill-代码威胁模型)）|

## 2. 关键约束（跟文档标准场景的差异）

| 约束项 | 文档标准场景 | lea-platform 现实 |
|---|---|---|
| Sandbox 内 UID | 各租户可不同 | **UID=0 (root)**（Phase 0 实测确认，所有沙箱同 root） |
| 租户模型 | 单节点手工配 | Multi-tenant SaaS，tenant_id / session_id / user_id 从 JWT |
| Cube 节点数 | 单节点示例 | 目标多节点 |
| 挂载路径构造者 | 运维手写 fstab | lea-tools 运行时动态生成 |
| 挂载点持久性 | 沙箱生命周期内 | 沙箱死了文件必须在 |

**关键点**：沙箱以 **root (UID=0)** 运行。文档"chown 1001 隔离 tenant-a"的方案对我们**失效**（root 能读一切）。我们靠**目录不给挂 → 沙箱根本看不到别的租户目录**来隔离，责任推到 lea-tools 拼路径的正确性 + Cube `allowed_host_mount_prefixes` 白名单兜底。

## 3. 架构

### 3.1 拓扑

```
                            ┌──────────────────────────────────┐
                            │  NFS Server (10.192.241.137)     │
                            │  XFS + prjquota                   │
                            │                                   │
                            │  /export/lea → 导出               │
                            └──────────┬───────────────────────┘
                                       │ NFS export
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
              ▼                        ▼                        ▼
    ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
    │  Cube 节点 A     │    │  Cube 节点 B     │    │  lea-tools 服务器 │
    │                  │    │                  │    │                  │
    │  mount /data/    │    │  mount /data/    │    │  mount /data/    │
    │    shared/lea    │    │    shared/lea    │    │    shared/lea    │
    │                  │    │                  │    │                  │
    │  Sandbox VM      │    │  Sandbox VM      │    │  api/files.py    │
    │    ├─/workspace  │    │    ├─/workspace  │    │    ├─readdir NFS │
    │    ├─/opt/venv   │    │    ├─/opt/venv   │    │    │             │
    │    ├─/opt/node_  │    │    ├─/opt/node_  │    │  archive_worker  │
    │    │  modules    │    │    │  modules    │    │    ├─tar.gz→S3   │
    │    └─/workspace/ │    │    └─/workspace/ │    │    └─S3→NFS      │
    │      skill (RO)  │    │      skill (RO)  │    │                  │
    └──────────────────┘    └──────────────────┘    └──────────────────┘
```

**关键点**：所有节点 mount 到**完全同一路径** `/data/shared/lea`。Host Mount 的 `hostPath` 是 Cube 节点视角，路径不一致会导致沙箱调度到不同节点行为不同。

### 3.2 目录结构

```
/data/shared/lea/
├── tenants/
│   └── <tenant_id>/
│       ├── sessions/
│       │   └── <session_id>/
│       │       ├── workspace/                ← RW 挂到 /workspace，quota 500MB
│       │       └── skills/                    ← RO 挂到 /opt/skills（按 agent 绑定投递）
│       ├── users/
│       │   └── <user_id>/
│       │       └── skills/                    ← 用户上传 skill 源，quota 500MB
│       └── shared/
│           └── skills/                        ← 租户级共享 skill（未来预留）
├── cache/
│   └── skills/                                ← S3 bundle 解压缓存，**不挂给沙箱**
│       ├── <sha256>/                           ← 内容寻址：sha 来自 bundle_uri
│       └── ...                                     故不需失效判断（见 §5）
├── archive/                                    ← 归档索引目录（一般文件走 S3，此处存元数据）
└── _tombstones/                                ← 主动删除的 buffer（7 天清理）
```

### 3.3 挂载 metadata（lea-tools 生成）

```python
{
    "templateID": "...",
    "timeout": 900,
    "lifecycle": {"on_timeout": "pause", "auto_resume": True},
    "metadata": {
        "host-mount": json.dumps([
            # 1) Session workspace：用户产物落这里
            {"hostPath": f"/data/shared/lea/tenants/{tid}/sessions/{sid}/workspace",
             "mountPath": "/workspace", "readOnly": False},

            # 2) 该 session 的 skill：只含 agent 绑定的那几个，RO
            #    建沙箱前由 skill_provisioner 从 S3 投递进来（见 §5）
            {"hostPath": f"/data/shared/lea/tenants/{tid}/sessions/{sid}/skills",
             "mountPath": "/opt/skills", "readOnly": True},
        ])
    }
}
```

**2 组挂载点无嵌套关系**（`/workspace`、`/opt/skills`），Cube 侧不需要额外嵌套 mount 能力。

不挂全平台共享 skill 目录是有意的：共享挂载会让任意 session `ls /opt/skills`
列出平台所有 skill（含该用户没绑定/没购买的），等于白送。

## 4. 依赖策略（不持久化）

### 4.1 决策

**Template 预装 + 现场 `pip install` 不持久**，跟 ChatGPT / Coze code interpreter 体验一致。

**理由**：
- **持久化代价大**：venv 挂 NFS 装 pandas 要 60s（几千小文件写），比本地慢 2-3 倍
- **quota 冲突**：一个 torch 2GB，跟用户产物抢 500MB workspace quota 立刻爆
- **收益低**：用户主要通过 LLM 用 code interpreter，不是 Jupyter 常驻，重装 10-20s 无感
- **平台预装覆盖 95%**：数据分析 / 文档生成 / 常用可视化包全部预装

### 4.2 Template 现状 + 运行时注入策略

**决策：不重打 Template，用现有 template（sprint F 时已预装 pandas / numpy / matplotlib / pptx 等日常包，够用）。** 环境约定通过**运行时 code 注入**做到，让 sandbox 行为对齐我们的期望，避免"重打 template → 推 registry → 停下手上事情"的成本。

**运行时注入代码块**（`_cube_sandbox.run_code` 前 prepend）：

```python
_RUNTIME_INJECT = """
import os, sys

# Python：禁 .pyc / __pycache__ 生成，避免 import workspace 里的 .py 时污染 NFS
sys.dont_write_bytecode = True

# 建 output 目录 + 改 cwd 到 output（相对路径 plt.savefig("plot.png") 自然落 output）
os.makedirs('/workspace/output', exist_ok=True)
os.chdir('/workspace/output')
os.environ['OUTPUT_DIR'] = '/workspace/output'

# Node：npm 全局 prefix + cache 强制走镜像层 /tmp，避免污染 workspace NFS
# 注意：npm install <pkg>（非 -g）默认还是装 cwd 下的 node_modules/，
# 这个只能靠系统提示词软引导 LLM 先 cd /tmp。quota 是最终防线。
os.environ['NPM_CONFIG_PREFIX'] = '/tmp/npm-global'
os.environ['NPM_CONFIG_CACHE']  = '/tmp/npm-cache'
os.environ['npm_config_prefix'] = '/tmp/npm-global'
os.environ['npm_config_cache']  = '/tmp/npm-cache'
"""

def run_code(self, code, ...):
    code = _RUNTIME_INJECT + "\n" + code
    ...
```

**注意 Cube bind-mount 会自动建父目录** —— `/opt/skills` 不需要 template 里 mkdir。

### 4.3 现场装冷门包的行为

**Python**：`pip install some-rare-lib` 装到镜像层 `site-packages`（不在 NFS，sandbox 死了消失）。一次 10-20s，跟 ChatGPT 体验一致。

**Node**：`npm install xxx` 默认装 cwd 下 `node_modules/`。为避免污染 workspace NFS：
- 环境变量 `NPM_CONFIG_PREFIX=/tmp/npm-global` 已注入（挡 `npm install -g` 和缓存）
- 系统提示词（Section 10.1）告诉 LLM：**普通 `npm install` 前先 `cd /tmp/npm-work`**
- 如果 LLM 手贱在 cwd 装 → 500MB workspace quota 挡住（`ENOSPC`），LLM 看到错误自己重试到 `/tmp`

**发现某冷门包被反复安装** → 加进未来 template 预装清单（下次重打时一起做），运维层解决。

### 4.4 未来什么时候重打 Template

不做过早优化。**触发条件**（任一）：
- 观测到多个用户反复装同一冷门包 → 加进预装清单
- 运行时注入 hack 想固化到镜像层（`ENV PYTHONDONTWRITEBYTECODE=1` 等）
- 需要放 npm wrapper 之类的更硬拦截

重打时的 Dockerfile 参考（未来用）：

```dockerfile
# 系统 Python（在现有基础上追加）
RUN pip install --no-cache-dir \
    pandas numpy scipy scikit-learn \
    matplotlib seaborn plotly \
    python-pptx openpyxl reportlab python-docx \
    pillow requests beautifulsoup4 lxml \
    sympy networkx jieba wordcloud pyecharts

WORKDIR /workspace/output
ENV OUTPUT_DIR=/workspace/output
ENV PYTHONDONTWRITEBYTECODE=1
ENV NPM_CONFIG_PREFIX=/tmp/npm-global
ENV NPM_CONFIG_CACHE=/tmp/npm-cache
```

### 4.4 未来（不做）

**依赖持久化**：如果后续用户反馈"每次都要重装 XX 包"高频出现（比如 torch 类几百 MB 的重量级依赖），再考虑挂 `/opt/venv` 到 user 级 NFS。当前不做的理由：
- Coze/ChatGPT 都没这么做，产品心智一致
- 多挂 3 组 mount 增 Cube 复杂度 + user 级 quota 治理成本
- 90% 的痛点靠"加进 template 预装"解决

## 5. Skill 交付：S3 真相 → NFS 缓存 → 按绑定投递

不分平台/商店/用户三类，**所有 skill 一条路径**：上传即入 S3（`skills` 表
`bundle_uri`），沙箱要用时按 agent 绑定投递。

```
S3 (bundle_uri)  ──下载解压一次──▶  cache/skills/<sha>/
                                         │  按 agent 绑定 copytree
                                         ▼
                                tenants/<tid>/sessions/<sid>/skills/<name>/
                                         │  RO host-mount
                                         ▼
                                     沙箱 /opt/skills/<name>/
```

**为什么 S3 是真相而不是 NFS 上摆一份**：`bundle_uri` 的 key 形如
`skills/<tenant>/<name>/<sha256>.zip` —— sha 在路径里，**包因此不可变**。缓存
目录以 sha 命名，一次解压永久有效，不需要任何失效判断；skill 改了就是新 sha、
新 key、新缓存目录。NFS 单一目录没有版本标识，只能靠 mtime 比对或手动清缓存。
另外上传侧（lea-config `POST /admin/skills/upload`）已经在写 S3，改动为零。

**为什么 cache 目录不直接挂给沙箱**：那等于共享挂载，任意 session 都能 `ls` 到
平台全部 skill。按绑定拷一份到 session 目录，未绑定的 skill 在沙箱里根本不存在
—— 拷贝成本换隔离，这是 skill 层唯一的隔离手段。

**实现**：`lea_tools/services/skill_provisioner.py`

| 函数 | 职责 |
|---|---|
| `parse_bundle_uri` | `s3://bucket/key` → `(bucket, key)`。bucket 一律从 uri 解析 —— skill 在 `lea-uploads`，lea-tools 自己的 `S3_BUCKET` 是 `lea-sandbox-volumes`，两个不同 bucket |
| `sha_from_key` | key 尾部取 sha 当缓存目录名，非 hex 直接拒 |
| `ensure_cached` | 命中即返；miss 则下载 → 解压到 `.tmp-<sha>` → 原子 rename（防并发读到半解压状态） |
| `provision_session_skills` | 逐个 `ensure_cached` → copytree 到 session 目录。每个 skill 独立 try |

调用点在 `SandboxSessionManager.create`，**必须在建 backend 之前** —— Cube 的
host-mount 在沙箱创建那一刻生效，之后再往 hostPath 写，已起的沙箱看不到。

投递失败不阻塞沙箱创建：LLM 读不到 skill 文件是降级，沙箱起不来是彻底不可用。

**解压防护**（`_safe_extract`）：逐段白名单拒 `..` / 空段 / 含 `:` 的段，再叠一层
resolve 边界检查；按 `file_size` 累加预算挡解压炸弹（上限 `NFS_SKILLS_QUOTA_MB`）。
不单靠 resolve 是因为它依赖平台如何解析盘符/UNC —— Windows 会把 `evil/C:/x` 里的
`C:` 静默吞掉，结果虽仍在界内却不是 zip 里声明的路径。

**LLM 怎么知道去哪读**：`activate_skill` 注入 SKILL.md 正文的同时，明确告知
`/opt/skills/<name>/` 并给出读 reference 的 `open()` 示例。这一步不能省 ——
SKILL.md 满篇是 `references/xxx.md` 相对链接，不告知绝对路径，LLM 会去猜，猜不到
就跳过依赖这些文件的步骤（实测：pptx 的调研/配色/页型规划全被跳过，只剩 SKILL.md
正文里自带的步骤）。

## 6. 多租户隔离

### 6.1 三层防线

**Layer 1 · 目录规划**（[3.2](#32-目录结构)）—— sandbox 内**只看到自己的挂载点**

**Layer 2 · 应用层强制拼接**（真正的防线）

```python
import re

_ID_RE = re.compile(r"^[0-9a-f\-]+$")

def _assert_id(name: str, value: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")

def build_host_mount(tenant_id: str, session_id: str) -> str:
    _assert_id("tenant_id", tenant_id)
    _assert_id("session_id", session_id)
    
    tid, sid = tenant_id, session_id
    mounts = [
        {"hostPath": f"/data/shared/lea/tenants/{tid}/sessions/{sid}/workspace",
         "mountPath": "/workspace", "readOnly": False},
        {"hostPath": f"/data/shared/lea/tenants/{tid}/sessions/{sid}/skills",
         "mountPath": "/opt/skills", "readOnly": True},
    ]
    return json.dumps(mounts)
```

**Layer 3 · Cube `allowed_host_mount_prefixes` 白名单**

```yaml
extra_conf:
  allowed_host_mount_prefixes:
    - "/data/shared/lea/tenants/"
    - "/data/shared/lea/platform/"
```

### 6.2 权限设置

Phase 0 实测确认：沙箱内 **UID=0（root）**。root 能读一切，所以 chown 隔离完全无效。权限模型简化为：

```bash
# NFS server 侧建目录时直接 root:root + 0700
# lea-tools ensure_session_dir() 负责 mkdir -p，默认 root 即可
sudo mkdir -p /data/shared/lea/tenants/<tid>/sessions/<sid>/workspace
sudo chmod 0700 /data/shared/lea/tenants/<tid>/sessions/<sid>/
```

**隔离完全靠「不挂别人的目录」实现**，不靠文件权限。

## 7. Quota（防单 session / 单 user 撑爆）

### 7.1 分级

| 挂载点 | Quota 归谁 | Hard | 超限行为 |
|---|---|---|---|
| `/workspace` | session | **500MB** | `ENOSPC`（skill/LLM 感知） |
| `/opt/skills` (RO) | session | **50MB** | 同上（一般拷不满） |

**tenant 级总容量** = Σ session，由目录 quota 叠加控制。依赖不持久（[Section 4](#4-依赖策略不持久化)），不占 NFS 空间。`cache/skills/` 按 sha 全局单份，不进 tenant quota。

### 7.2 Quota 分层澄清（NFS 和 XFS 的关系）

> **重要**：quota 是**文件系统层**的能力，不是 NFS 协议层的。NFS 协议本身没有目录 quota 机制，客户端只会收到 `ENOSPC`。
>
> - **NFS server 与 client 之间**：NFS 协议，负责跨机器挂载
> - **NFS server 数据落盘处**：某个本地文件系统（XFS / ext4 / 云 NFS 自带）
> - **Quota 只在数据落盘处生效**：Cube 节点感知不到底层 fs 是什么

### 7.3 后端接口化（阶段无关）

`lea-tools/src/lea_tools/services/nfs_provisioner.py` 抽出 QuotaBackend 协议：

```python
from typing import Protocol

class QuotaBackend(Protocol):
    def register_project(self, project_id: str, path: str, hard_bytes: int) -> None: ...
    def revoke_project(self, project_id: str) -> None: ...
    def usage_bytes(self, project_id: str) -> int: ...
```

三个实现，按 `QUOTA_BACKEND` 配置切换：

- `XFSQuotaBackend`（**Phase 1 自建 NFS 用**）：调 `xfs_quota` 命令行
- `HuaweiSFSQuotaBackend`（**Phase X 云切换用**）：调华为云 SFS Turbo API 配置目录配额
- `NoopQuotaBackend`（fallback）：不做 quota，只记账，方便本地开发

### 7.4 Phase 1 自建 NFS 的 XFS quota 配置

前提：10.192.241.137 上 `/export/lea` 数据盘用 **XFS 格式化**（部署前 `df -T /export/lea` 确认；不是 XFS 则重格式化，因为要 project quota）。

```bash
# NFS server 侧
sudo mount -o remount,prjquota /export/lea

# 每建一个 session dir，登记 project
next_id=$(cat /var/lib/lea/quota-next.txt)
echo "$next_id:/export/lea/tenants/<tid>/sessions/<sid>/workspace" | sudo tee -a /etc/projects
echo "sess-<sid>:$next_id" | sudo tee -a /etc/projid
sudo xfs_quota -x -c "project -s sess-<sid>" /export/lea
sudo xfs_quota -x -c "limit -p bhard=500m sess-<sid>" /export/lea

# user 级 project 同理
```

lea-tools 侧 `ensure_session_dir()` / `ensure_user_env()` 建目录时自动调 `XFSQuotaBackend.register_project()`。

### 7.5 容量估算（1000 用户）

| 层 | 单实体大小 | 数量 | 总量 |
|---|---|---|---|
| Session workspace | 平均 30MB（P99 500MB） | 10 热 × 1000 用户 | ~300 GB |
| Session skills | 几十 KB × N | 10 热 × 1000 | ~5 GB |
| Platform skills 缓存 | 100KB × N | 全平台 100 skill | 忽略 |
| **NFS 热层小计** | | | **~300 GB** |

依赖不持久 → 不占 NFS。热层容量比 v1 版估算（含 venv/node_modules 的 ~2 TB）缩到 300GB 级。

冷层归档到 S3（见 [Section 8](#8-冷热分层)）。

## 8. NFS 后端路径（Phase 1 自建 → Phase X 华为云）

### 8.1 分阶段策略

**Phase 1（开发/内测）· 10.192.241.137 自建 NFS**

- `nfs-kernel-server` + XFS + prjquota
- `/export/lea` 底层是 XFS 格式化数据盘
- 所有节点 mount `10.192.241.137:/export/lea` → `/data/shared/lea`
- 单点，接受故障；用来验方案、跑内测
- Quota backend: `XFSQuotaBackend`

**Phase X（生产上量）· 华为云 SFS Turbo**

- 华为云托管 NFS 服务，天然多可用区高可用
- 挂载路径**不变**（客户端 `mount -t nfs sfs-turbo-endpoint:/lea /data/shared/lea`）
- SFS Turbo 支持子目录配额（控制台或 API 配），粒度到目录级
- Quota backend: `HuaweiSFSQuotaBackend`（调华为云 SFS API）
- Cube 节点 / lea-tools 服务器**零代码改动**（应用层挂载路径不变）
- 只有运维部署脚本 + `QUOTA_BACKEND=huawei` 环境变量切换

### 8.2 切换 timing

不做过早优化。切云触发条件：
- 单节点 NFS 出现故障影响生产（HA 需求）
- Cube 节点跨可用区（自建 NFS 不方便跨 AZ）
- 数据量 > 10 TB（自建成本 vs 云托管性价比反转）

### 8.3 备选云 NFS 对比

| 云 NFS | 目录级 quota | 备注 |
|---|---|---|
| **华为云 SFS Turbo** | ✅ | 已选定，跟平台其他服务生态一致 |
| 阿里云 NAS Plus | ✅（`dir_quota` 特性） | 备选 |
| 腾讯云 CFS | ❌（只有 fs 级） | 不推荐 |

## 9. 冷热分层

### 9.1 分层策略

```
┌─ 热层 (NFS) ────────────────────────────────┐
│  session.last_used_at ≥ now - 30 天         │
│  RW 挂载给沙箱，读写 < 10ms                 │
│  ~2 TB 总量（1000 用户级）                   │
└─────────────────────────────────────────────┘
                ↓ 30 天空闲
┌─ 冷层 (S3/MinIO) ──────────────────────────┐
│  tar.gz 打包 workspace + skills → OBS       │
│  用户点开 → 后台异步恢复（30s spinner）      │
│  几 TB 无所谓（S3 单价约 NFS 1/10）          │
└─────────────────────────────────────────────┘
                ↓ 180 天空闲
┌─ 归档层（可选删除） ────────────────────────┐
│  按 tenant 策略：删或长期归档                │
└─────────────────────────────────────────────┘
```

**关键决策**：只归档 session workspace + skills（用户产物 + 临时 skill 副本），**user 级环境（venv/node_modules）不归档** —— 反正下次装依赖会重建。

### 9.2 归档 worker（每晚 cron）

```python
# lea-tools/src/lea_tools/services/session_archiver.py

async def archive_stale_sessions(db, s3, threshold_days=30, batch=100):
    """每晚扫，把 30 天没交互的 session 打包到 S3，删 NFS 目录。"""
    stale = await db.query(
        "SELECT id, tenant_id, user_id FROM chat_sessions "
        "WHERE last_used_at < ? AND status = 'active' "
        "LIMIT ?",
        [datetime.now() - timedelta(days=threshold_days), batch],
    )
    for sess in stale:
        nfs_path = Path(f"/data/shared/lea/tenants/{sess.tenant_id}"
                        f"/sessions/{sess.id}")
        if not nfs_path.exists():
            await db.execute("UPDATE chat_sessions SET status='archived' WHERE id=?",
                             [sess.id])
            continue
        # tar.gz workspace + skills
        tar_path = f"/tmp/{sess.id}.tar.gz"
        subprocess.run(["tar", "-czf", tar_path, "-C", str(nfs_path), "."], check=True)
        # 上传 S3
        s3_key = f"archive/{sess.tenant_id}/{sess.id}.tar.gz"
        s3.upload_file(tar_path, s3_key)
        # 更新 DB
        await db.execute(
            "UPDATE chat_sessions SET status='archived', "
            "archive_key=?, archived_at=? WHERE id=?",
            [s3_key, datetime.now(), sess.id],
        )
        # 删 NFS + 撤销 quota
        shutil.rmtree(nfs_path)
        subprocess.run(["xfs_quota", "-x", "-c", f"remove -p sess-{sess.id}",
                        "/export/lea"])
        Path(tar_path).unlink()
```

### 9.3 恢复流程

用户点开 archived session：
1. 前端 GET `/api/sessions/<sid>` → 后端返 `status: "archived"` + 触发异步恢复
2. lea-tools worker：S3 pull → 解压 NFS → 重新登记 quota → DB `status='active'`
3. 前端 poll `/api/sessions/<sid>` 到 `status: "active"` → 允许打开
4. 用户体验：spinner "会话恢复中..."，30MB session 5-10s，500MB session 30-60s

### 9.4 DB Schema 扩展

`chat_sessions` 表增字段：

```sql
ALTER TABLE chat_sessions ADD COLUMN status VARCHAR(16) DEFAULT 'active';
  -- active / archiving / archived / restoring
ALTER TABLE chat_sessions ADD COLUMN archive_key VARCHAR(512) NULL;
ALTER TABLE chat_sessions ADD COLUMN archived_at DATETIME NULL;
```

## 10. 产物展示约定（不做强制落点）

由于 `/workspace` 整挂 NFS，**所有文件都在 NFS 上、都能持久、都能跨 session 恢复**。不再有"防丢"需求，剩下的只是**抽屉里给用户看什么**。

抽屉视野 = **整个 workspace 文件树**（跟本地开发看 IDE 文件树同一个感觉）。output/ 只是 UI 层的高亮徽章，不做后端搬文件。

### 10.1 Layer 1 · 系统提示词（分类信号，非强制）

Code interpreter 系统提示词加：

```
## 文件建议

用户产物（用户会想下载/查看的最终结果）建议放到 /workspace/output/，
方便用户在文件抽屉里第一时间看到（output 会加"推荐"徽章）。

中间/临时文件可随便放（写到 /workspace/tmp/、/workspace/.cache/ 都行），
最终它们也都在工作区里，用户可以浏览。

## Skill 使用约定

Skill 代码位置（只读挂载，只含本 agent 绑定的 skill）：
- /opt/skills/<name>/

可以 import 使用，但**不要主动 cat 或复制 skill 代码到 /workspace/**
（skill 代码是平台/发布者知识产权，不应展示给用户）。

## 依赖安装

- Python: `pip install` 直接跑（会装到系统 site-packages，不落 workspace）
- Node: `npm install` 之前**先 `cd /tmp/npm-work`**（避免 node_modules 污染工作区 NFS）
  正确: `cd /tmp/npm-work && npm install lodash && node -e "..."` 
  错误: 在 /workspace/output/ 里直接 `npm install`（几千小文件会撑爆 500MB quota）
```

**意义**：不是"必须"，是**分类信号**——让 LLM 知道 `output/` 是"公共展示区"，别把 debug/temp 写进去。其他位置是 skill 的"私密工作区"。

### 10.2 Layer 2 · Template 层设定（一次配到位）

Template 一次性做好两件事：

- `WORKDIR /workspace/output` + `ENV OUTPUT_DIR=/workspace/output` —— 相对路径 `plt.savefig("plot.png")` 自然落 output
- `ENV PYTHONDONTWRITEBYTECODE=1` —— 禁 Python 生成 `.pyc` / `__pycache__`，防止 import workspace 里的 `.py` 时污染 NFS

**Layer 3 差集扫兜底砍掉** —— 都在 NFS 上，不需要抢救。

### 10.3 抽屉展示折叠规则

`readdir /workspace/` 时，前端 UI 层剪枝这些约定俗成的目录/文件（后端返回时打 `hidden: true` 标签）：

```python
_HIDDEN_PATTERNS = {
    # 目录
    "__pycache__", ".ipynb_checkpoints", ".cache",
    ".pytest_cache", ".mypy_cache", ".git", ".venv",
    "node_modules", ".next", "dist", "build",
    # 文件后缀
    "*.pyc", "*.pyo", "*.log",
}
```

**注意**：这不是搬文件逻辑，只是**展示层过滤**。文件真身还在 NFS 上，占 quota。用户可以 `?show_hidden=true` 强制显示。

**S3 push 逻辑下线** —— NFS 就是唯一真源，抽屉纯 readdir NFS。

## 11. 生命周期整合

现有三段式生命周期（详见 [[project-cube-volume-artifacts]]）在 v2 下**不变**：

| 阶段 | Idle | Cube 动作 | 数据在哪 |
|---|---|---|---|
| Running | 0-15min | 正常执行 | Sandbox 本地 fs + NFS 实时可见 |
| Paused | 15min-1h | Cube 自动 pause | 内存冻结；NFS 数据独立存在 |
| Killed | 1h+ | 本地主动 kill | Sandbox 消失；NFS 数据仍在 |
| Archived | 30d+ 空闲 | archive worker 打包上 S3 | NFS 删除；S3 tar.gz 存 |
| Restoring | 用户点开 | worker 拉 S3 解压回 NFS | 30s spinner，回到 Running |

**关键约束**（文档明确）：Host Mount 数据不进 snapshot。这对我们**恰好合适** —— snapshot 里没有产物，跨节点 resume 不需要担心。

## 12. 落地阶段

### Phase 0 · 冒烟验证（Task #223，1 天）

**目的**：验证多个技术假设

- [ ] 沙箱建成功，metadata.host-mount 生效（POST 200 + sandboxID）
- [ ] 沙箱内 `id` 输出确切 UID（记入 [[reference-cube-sandbox]] memory）
- [ ] **2 组独立挂载** `/workspace` + `/opt/skills` 同时生效（无嵌套关系）
- [ ] 两个挂载点各写一个文件 → NFS 服务器上都存在
- [ ] XFS project quota 生效：`dd bs=1M count=600 of=/workspace/big` 撞 500MB hard 报 `ENOSPC`
- [ ] DELETE 沙箱后 NFS 上两个目录数据仍在
- [ ] Template 预装的 `import pandas` 秒级成功（不涉及 NFS）

### Phase 1 · NFS Server + Template 重打（Task #224，2 天）

- 10.192.241.137 装 `nfs-kernel-server`，`/export/lea` 用 XFS + `prjquota`
- `/etc/exports` 配置 + `exportfs -a`
- 所有节点 mount 到 `/data/shared/lea`（loopback + NFS）
- Cube template 重打：`WORKDIR=/workspace/output` + `pip install` 平台预装包一步到位
- CubeMaster `conf.yaml` 加 `allowed_host_mount_prefixes`

### Phase 2 · lea-tools 接入 host-mount（Task #225，2 天）

- `_cube_sandbox.py`：`__init__` 接受 tenant_id / session_id / agent_skills；拼 2 组 mount；`_ensure_skills`
- `sandbox_session.py`：`create()` 参数补齐往下传
- 新增 `nfs_provisioner.py`：`ensure_session_dir` + XFS quota 登记
- `config.py` 加 `NFS_ROOT`
- 系统提示词加"文件输出到 /workspace/output/"（Layer 1）
- `_post_run_normalize`：orphans 搬进 `output/_orphans/`（Layer 3）

### Phase 3 · 抽屉切 NFS + S3 push 下线（Task #226，2 天）

- `api/files.py`：`GET /api/files/list` 纯 readdir NFS，分组 `output/*` vs `output/_orphans/*`
- `GET /api/files/download`：直接读 NFS 返
- **删除**：`ArtifactPusher` 走 S3 那套（转为归档 worker 用）
- 前端 SessionFileDrawer：两栏"产物"+"意外产物"

### Phase 4 · 冷热分层（Task #228，3-4 天）

- DB 迁移：`chat_sessions` 加 `status / archive_key / archived_at`
- 新增 `session_archiver.py`：cron 每晚扫，tar.gz 上 S3
- 恢复端点：`GET /api/sessions/<sid>` 感知 archived → 异步恢复
- 前端：archived session 显示 spinner，poll status 到 active

### Phase 5 · GC + 观测（未来 sprint）

- `_tombstones/` 7 天清理
- Prometheus：NFS 空间水位、archive worker 队列长度、跨 session hit rate
- Quota 告警：单 session 到 400MB 提醒清理（避免打满）

## 13. 未决问题

1. **Cube template 重打 timing** —— Phase 1 独立时间线，还是跟 Phase 2 一起？（当前 template 没有 pandas 等预装包，Phase 0 T7 验证失败）
2. **归档触发是否手动** —— 除了 30 天 cron，是否给 tenant admin "立即归档" 按钮？
3. **恢复失败降级** —— S3 拉不到 tar.gz 时？（数据丢失还是软 fallback 到空 session？）
4. ~~**Sandbox UID 若不是 1000**~~ → **已解决：UID=0 (root)**，chown 隔离无效，纯靠挂载隔离（见 6.2）
5. **Template 预装清单最终版** —— 现 template 连 pandas 都没装，Phase 1 重打时需完整包列表

## 14. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| NFS server 挂 | 所有沙箱 write 阻塞 | Phase 1 接受单点；出事后升 HA 或切云（[Section 8](#8-nfs-后端路径phase-1-自建--phase-x-华为云)） |
| Cube 不支持 2 层嵌套 mount | 方案不成立 | Phase 0 冒烟验；不支持则退到 1 层（skill 目录改用 rsync 方式复制进 workspace 子目录） |
| 归档 tar.gz 打包时 session 恰好活跃 | 数据不一致 | 归档前 DB `status='archiving'` 加锁，跳过刚 touch 的 |
| XFS quota 客户端不生效 | 用户撑爆 NFS 不报错 | Phase 0 dd 实测；NFS server 侧强制 |
| 冷门包用户反复重装 | 用户投诉"每次都要等 20s" | 观测 pip install 高频包 → 加进 template 预装清单，运维层解决 |

**回滚路径**：任一阶段失败 → `_cube_sandbox.py` 里 `metadata.host-mount` 拿掉，回到 v1 "目录剪枝后全推 S3"方案。代码影响面小。

## 14.1 Skill 代码威胁模型

Skill 挂到 `/opt/skills/` RO、且只含本 agent 绑定的那几个，是**分层防御**：

| 威胁场景 | 挡不挡得住 |
|---|---|
| 用户从文件抽屉手滑看到 skill 代码 | ✅ 挡（skill 不在 workspace 下，抽屉根本看不到）|
| 用户在 sandbox 里 `rm -rf /opt/skills` 破坏 skill | ✅ 挡（RO 挂载；且只是 session 副本，源在 S3）|
| 用户让 LLM 主动 `cat /opt/skills/xxx/main.py` 输出到聊天 | ⚠️ 部分挡（系统提示词约束 LLM 别主动做）|
| 用户 `ls /opt/skills` 摸清平台有哪些 skill | ✅ 挡（只投递本 agent 绑定的，没绑定的不存在）|
| A 租户用户看到 B 租户的 skill | ✅ 挡（session 独立目录，按绑定投递）|
| A 用户看到 B 用户上传的 skill（同 tenant 内） | ✅ 挡（同上）|
| 用户绕过 LLM 直接 exec 命令扒代码 | ❌ **挡不住**（sandbox 是用户领地，能执行任意代码）|
| 商店 skill 被其他发布者抄袭 | ❌ **挡不住**（依赖商店内容审核 + 举报 + TOS）|

**我们的选择**：**防手滑，不防主动扒**。跟 pip / npm 包同一个安全模型（本地能读代码，商业价值在服务/更新/生态上）。

**未来 IP 敏感 skill** 可以走远程 RPC 方案（skill 逻辑不进 sandbox，只暴露 API），但那是完全不同的架构，工程量 10 倍，本方案不做。

## 15. 与其他决策的关系

- **不影响 pause/auto_resume**：Cube lifecycle 参数照传（[[reference-cube-lifecycle]]）
- **不影响 Cube v0.6.0 Volume Plugin**（Task #191）：Host Mount 是 v0.5 就有，Volume Plugin 是另一层
- **替代 [[project-cube-volume-artifacts]] 的 S3 push 主角地位**：v2 里 NFS 是唯一真源，S3 只做冷层归档

## 16. Sources

- [Cube Sandbox Persistent Storage · 中文](https://cubesandbox.com/zh/guide/persistent-storage.html)
- [Cube Sandbox Lifecycle · 中文](https://cubesandbox.com/zh/guide/lifecycle.html)
- Memory: [[project-cube-volume-artifacts]] · [[reference-cube-lifecycle]] · [[reference-cube-sandbox]] · [[reference-cube-v060]]
- Tasks: #223 Phase 0 · #224 Phase 1 · #225 Phase 2 · #226 Phase 3 · #228 Phase 4 归档
