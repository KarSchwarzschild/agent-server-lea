# 测试服部署手册（202.205.160.9）

这是**当前实际在用**的部署路径。根目录那几份 `DEPLOYMENT_*.md` 描述的是更早的
方案，端口、compose 文件和服务清单都对不上，不要照着做。

生产（CCE/k8s）那条路见 `infra/k8s/README.md`，跟这里是两套东西。

- 栈名（compose project）：`lea-test`
- 编排文件：`infra/docker-compose.test-server.yml`
- 服务器上的代码目录：`/root/lea-platform`（**不是 git 仓库**，靠 tar 同步，见下）

## 对外入口

| 地址 | 是什么 |
|---|---|
| `http://202.205.160.9:8880` | frontend-lab，主前端。nginx 把 `/api/` 反代到 `gateway:8080`，所以 API 也能从这个端口走 |
| `http://202.205.160.9:8890` | frontend-console，管理控制台（发布审批、审计） |
| `http://202.205.160.9:8840` | gateway，直连 |

`minio`、`milvus`、`temporal-ui` 一律绑 `127.0.0.1`，外网访问不到 —— 要看就
ssh 端口转发。这是有意的：minio 用的是 `.env` 里那套口令，暴露出去等于把对象
存储和向量库送人。

## 服务和镜像的对应关系

**14 个容器，但只有 7 个镜像由本仓库构建。**最容易踩的坑是两对服务共用镜像 ——
只重启一个，另一个还在跑旧代码：

| 镜像 | 用这个镜像的服务 |
|---|---|
| `lea-agent:test` | `agent`、**`agent-worker`** |
| `lea-rag:test` | `rag`、**`retrieval-worker`** |
| `lea-gateway:test` | `gateway` |
| `lea-config:test` | `config` |
| `lea-parser:test` | `parser` |
| `lea-frontend-lab:test` | `frontend` |
| `lea-frontend-console:test` | `admin-console` |

改了 Go 的 agent 代码只重启 `agent` 而忘了 `agent-worker`，表现是"同步聊天正常、
排队执行的长任务行为还是旧的"，而这个差别在界面上看不出来。

外部依赖（**不在栈内，不要试图在这里重启**）：

- MySQL `202.205.160.9:3307`
- Redis `202.205.160.9:16379/9`
- lea-tools `10.192.241.137:8400`
- Cube 沙箱 `10.192.241.137:3000` + 其 S3
- 中转 API `tenantapi-ai.ouchn.edu.cn`
- 本地大模型 `202.205.160.8:11434`

栈内自带 `minio` / `milvus`(+`etcd`) / `temporal`。

## 部署步骤

### 0. 前置：这台机器上必须有的东西

`${VAR:?}` 引用的这几个变量缺任何一个，compose 直接报错（不会静默起一个坏服务）：
`DB_HOST`、`DB_USER`、`DB_PASSWORD`、`LEA_INTERNAL_TOKEN`、`MINIO_ACCESS_KEY`、
`MINIO_SECRET_KEY`。

另外 `agentloop` 必须是本仓库的**兄弟目录**（`../../agentloop`）。`go.mod` 里的
`replace` 指向它，少了构建上下文 buildkit 会把 agentloop 当 Docker Hub 镜像去拉，
报 `failed size validation` —— 完全看不出是缺目录。

### 1. 同步代码

服务器上的 `/root/lea-platform` 不是 git 仓库，用打包 + scp。Windows 本地没有
rsync，所以走 `git ls-files` + tar（只带版本控制认识的文件，不会把 `.venv`、
`node_modules`、本地 `.env` 带上去）：

```bash
# 本地（仓库根目录）
git ls-files -z --cached --others --exclude-standard > /tmp/filelist.txt
tar --null -T /tmp/filelist.txt -czf /tmp/lea-sync.tar.gz
scp /tmp/lea-sync.tar.gz root@202.205.160.9:/tmp/

# 服务器
cd /root/lea-platform && tar -xzf /tmp/lea-sync.tar.gz
```

tar 是**覆盖解压**，不删除已经不在仓库里的文件。重命名或删文件之后，服务器上会
留着旧的那一份。

### 2. 先跑数据库迁移

**顺序很要紧**：迁移必须在重启服务之前跑完。新代码经常无条件依赖新列，迁移没跑
就发版的表现是 `Unknown column`，而那通常意味着整个功能直接不可用（例：035 加
`chat_sessions.model`，不跑就是聊天打不开）。

这台机器上没有 mysql 客户端，用一次性容器。口令只经环境变量传，不出现在命令行里
（命令行参数会进 `ps` 和 shell history）：

```bash
cd /root/lea-platform
set -a && . ./.env && set +a
docker run --rm -i -e MYSQL_PWD="$DB_PASSWORD" mysql:8 \
  mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" \
  < migrations/0XX_whatever.sql
```

**迁移大多不幂等**，跑之前先确认该不该跑。`CREATE TABLE IF NOT EXISTS` 安全，但
`ADD COLUMN` 一般没防重，重复执行报 duplicate column。确认某一列在不在：

```sql
SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='chat_sessions' AND COLUMN_NAME='model';
```

001–030 在这台 MySQL 上早就跑过了（一直是同一个库）。

### 3. 构建镜像

```bash
cd /root/lea-platform/infra
docker compose -p lea-test --env-file ../.env -f docker-compose.test-server.yml \
  build agent config rag frontend
```

`-p lea-test` 和 `--env-file` **两个都不能省**。省了 `--env-file` 报
`required variable LEA_INTERNAL_TOKEN is missing a value`；省了 `-p` 会建出
另一个 compose project，等于起第二套栈。

compose 的 `${VAR}` 插值**不读 `env_file:`**。`env_file` 只负责把变量注进容器，
插值读的是 `--env-file` 指的那一份。所以路径要相对于你所在目录：在 `infra/` 下是
`../.env`，在仓库根目录下是 `.env`。

只构建改动涉及的镜像。全量 build 很慢而且没必要。

### 4. 重启服务

```bash
docker compose -p lea-test --env-file ../.env -f docker-compose.test-server.yml \
  up -d agent agent-worker config rag retrieval-worker frontend
```

按第二节的对应表把共用镜像的服务一起列上。`gateway` 会因为依赖链被带着重启，正常。

### 5. 确认

```bash
docker ps --filter label=com.docker.compose.project=lea-test \
  --format '{{.Names}}\t{{.Status}}' | sort
```

14 个容器。其中 10 个带 healthcheck，应该显示 `(healthy)`；
`agent-worker`、`retrieval-worker`、`frontend-console`、`temporal-ui` 这 4 个没有
healthcheck，只显示 `Up`，正常。

再看启动日志里有没有真错误：

```bash
for c in lea-agent-test lea-config-test lea-rag-test lea-agent-worker-test; do
  echo "=== $c ==="
  docker logs $c --since 3m 2>&1 | grep -iE 'error|panic|fatal|traceback|unknown column' \
    | grep -viE 'error_rate|errors=0|ErrorHandler'
done
```

**容器 healthy 不等于部署成功。**健康检查只证明进程起来了、端口在听。真正的
判据是按下一节跑一遍真调用。

## 真调用验证

网关走 OIDC/JWKS 验签 RS256，**token 必须由真的登录流程签发**，本地签不出来
（`scripts/` 里那些 `mint_token()` 用的是 dev HS256 secret，对这台机器无效）。
拿 token 的办法是在前端登录后从浏览器开发者工具里取，或者让有账号的人给一个。

token 存文件、别写进命令行：

```bash
TOK=$(tr -d '\r\n' < /tmp/tok.txt)
curl -s -H "Authorization: Bearer $TOK" http://202.205.160.9:8880/api/tenants
```

`X-Selected-Tenant-ID` 头选租户。多数 `/api/user/*` 接口要它。

两个坑：

- **Windows 的 curl 发中文 JSON 必须 `--data-binary @文件`**，用 `-d` 会编码错乱。
- `curl -d` 默认是 POST。改方法要显式 `-X PUT` / `-X DELETE`，否则服务端收到的
  是 POST，返回 405，而你以为是路由没注册。

验证点挑能覆盖这次改动的。例如会话级模型（035 那次）：

```bash
# 建会话 → 发消息带 model → 查库确认落库 → 换 model 再发 → 确认字段变了
curl -s -X POST -H "Authorization: Bearer $TOK" -H "X-Selected-Tenant-ID: wjtest" \
  -H "Content-Type: application/json" -d '{"agent_id":158,"model":"qwen-plus"}' \
  http://202.205.160.9:8880/api/sessions
```

会话是**首次发消息才落库**的，建完立刻查 `chat_sessions` 查不到，不是 bug。

## 监控（Prometheus + Grafana）

跟业务栈分开一份 `infra/docker-compose.monitoring.yml`，但**必须挂同一个 compose
project**，否则进不去同一个网络、采集不到任何目标：

```bash
cd /root/lea-platform/infra
docker compose -p lea-test --env-file ../.env \
    -f docker-compose.test-server.yml -f docker-compose.monitoring.yml \
    up -d prometheus grafana
```

两个都绑 `127.0.0.1`，外网访问不到。要看就端口转发：

```bash
ssh -L 3003:127.0.0.1:3003 -L 9090:127.0.0.1:9090 root@202.205.160.9
# 然后本机开 http://127.0.0.1:3003 （Grafana，默认 admin/admin）
#            http://127.0.0.1:9090 （Prometheus）
```

Grafana 用宿主 **3003**：3001 和 3002 在这台机器上被跟本项目无关的容器占着
（`one-api-2` 等），3000 在 10.192.241.137 上是 Cube 沙箱。换端口前先
`ss -tln | grep :<port>`。

### 拉镜像的坑

这台机器**连不上 Docker Hub**（`registry-1.docker.io` TLS handshake timeout）。
`/etc/docker/daemon.json` 里配了两个加速站，但 daemon 没重启过所以没生效 ——
而重启 dockerd 会把这台机器上所有容器带下来，包括一堆跟本项目无关的，所以不要
那么做。

办法是显式指定镜像站地址拉，再 retag 成 compose 期望的名字：

```bash
docker pull docker.1ms.run/prom/prometheus:v3.1.0
docker tag  docker.1ms.run/prom/prometheus:v3.1.0 prom/prometheus:v3.1.0
```

### 当前采集到什么

5 个目标全 up：`lea-agent`、`lea-config`、`lea-rag`、`lea-tools`、`prometheus`
自身。15 条告警规则加载正常。

**三个服务没有指标端点**，所以没配 job（配了只会得到永远 down 的目标，而永远 down
的目标会训练人忽略面板）：

| 服务 | 情况 | 影响 |
|---|---|---|
| `lea-gateway` | 没有 `/metrics`（main.go 里没引 promhttp） | **最该先补**。网关是所有请求的必经之路和唯一对公网的服务，没有指标等于整体 QPS / 错误率 / 延迟三个最基本的问题都答不了 |
| `lea-agent-worker` | 不监听任何 HTTP 端口 | 排队执行那条链路完全没有可观测性 —— 长任务卡住、重试风暴、Task Queue 堆积都看不见 |
| `lea-parser` | `/metrics` 返 404 | 解析服务无指标 |

另外 Redis 和 MySQL 需要各自的 exporter，现在没部署（`prometheus.yml` 里留了注释
好的配置段，起 exporter 之后打开即可）。

## 回滚

镜像打的是 `:test` 这个固定 tag，`docker compose build` 会把上一版顶掉，所以
**没有"上一个镜像"可以切回去**。回滚等于把代码退回去重新走一遍：本地 checkout
到目标 commit → 重新同步 → 重新 build → 重新 up。

所以上线前确认本地是干净的、已提交的状态。要想真正能快速回滚，得先改成带版本号
的 tag，那是另一件事。

数据库迁移基本都不可逆。加列这类改动留着不删通常无害（旧代码不认识那一列而已）。

## 已知的坑

- `/root/lea-platform` 不是 git 仓库，所以服务器上查不了"现在跑的是哪个 commit"。
  想知道就对比文件内容，或者在同步时自己记一下。这是个值得改的地方。
- `.bash_history` 里有历史上用过的命令，但不要把它当文档 —— 里面混着失败的尝试。
- 老的 `lea-server` 已经退出调用链，`packages/lea-server-legacy` 是归档，不要接回去。
- `LEA_TEST_ADMIN_ROLE` 把任何已登录成员映射成指定管理角色，只在 `LEA_ENV=test`
  下生效。它现在是 `admin`，意味着**测试服上每个登录用户都是管理员** —— 控制台
  （8890）上的审批和审计因此对所有人开放。要恢复真实角色判断就设成 `disabled`。
  这是个待决事项，不是设计意图。
