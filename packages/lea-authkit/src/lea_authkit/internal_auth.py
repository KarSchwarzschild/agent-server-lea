"""服务间鉴权 —— 只挡"集群里的其他东西"，不做用户身份。

被调方用 `install_internal_auth(app, service=...)`，调用方用 `internal_headers()`。

## 为什么内网服务也要挡

多租户隔离**目前完全建立在"打不到这些端口"上**。lea-agent 在边界上验 JWT，之后把
tenant_id / user_id 当**普通请求参数**往下游传 —— 下游不验、也没法验，只能信。于是
任何能连上 :8300 / :8400 / :8500 的东西，把 tenant_id 换成别人的就能读别人的知识库、
记忆和工作区。这不是"纵深防御"，是一个具体的越权，只差一个能发 HTTP 的立足点。

三个服务各自的 blast radius：

    lea-tools   跑任意代码（code_interpreter → 沙箱）、读写用户工作区、下载产物
    lea-rag     任意租户的知识库/记忆读写，外加 /admin/knowledge-bases/* 建库删库
    lea-config  Agent / 技能 / 模型配置，含模型 API key，以及 users / mcp_servers

"内网假设"真正的问题不是它今天成不成立，而是**它失效时没有任何信号** —— 不是"某个
接口返 401"，而是"某个接口本来就该被调用"。它最常见的失效方式是集群里另一个服务被
拿下、顺手扫一圈内网端口，而一个共享密钥就能挡住这种。

## 为什么不是 JWT

服务间调用没有用户身份。套 JWT 等于把一个人的登录凭证当机器凭证用，那是另一类问题
（凭证过期、作用域、谁的 sub）。这里要的只是"你是我们自己的服务"。

用户身份仍然由 lea-agent 在边界上验（它有真 JWT 鉴权），并通过请求参数
tenant_id / user_id 往下传 —— 那部分不变。

## 这只是一个插槽，共享密钥是今天插进去的东西

校验必须发生在资源所在的地方，所以不管上层选什么方案，lea-rag 收到请求时总得有一段
东西判断"这个调用方可不可信"。独立鉴权服务改变的是**校验的内容**，不是校验的位置：

    今天              比对一个共享密钥（本文件）
    换成机器 token     验一条 JWKS 签名 —— 换掉下面 _check 的判断即可
    换成 service mesh  信任 sidecar 给的 mTLS 对端身份 —— 整个包删掉

抽成共享包的一个具体好处就是最后那条：迁 mesh 时删一个包，而不是从三个服务里各挖
一份拷贝出来。

## 没配 token 时放行，但会大声说

`LEA_INTERNAL_TOKEN` 没设时放行所有请求，只在启动时打一条 warning。

看起来像是把安全做成了可选，但理由是具体的：**滚动更新期间调用方和被调方的版本
会共存。** 如果被调方先变成"没 token 就拒绝"，而调用方还没开始带这个头，整条链会
立刻断掉 —— 而且是在部署中途。放行让上线顺序无关紧要。

两边都发布之后，把 `LEA_REQUIRE_INTERNAL_TOKEN=1` 打开（k8s 的 ConfigMap 里），
这时没配 token 会**拒绝启动**而不是静默放行。生产必须开这个。
"""

from __future__ import annotations

import os
import secrets

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger()

HEADER = "X-Internal-Token"

# 探针和采集器要用，不能鉴权 —— k8s 的 readiness/liveness 和 Prometheus 的 scrape
# 都不会带自定义头。Prometheus 的 scrape_configs 压根**不支持**自定义请求头（只有
# Basic / Bearer / mTLS），所以 /metrics 想被采集就只能走白名单。
#
# /docs 和 /openapi.json **故意不放**，它们会把整个接口面（包括 admin 那几组）
# 暴露给任何能连上端口的东西。
#
# /metrics 放出来之前确认过内容：标签只有路由模板、method、状态码和 kb_id，没有
# 租户或用户标识（见各服务 metrics.py 的 _endpoint_label —— 它取的是路由模板而不
# 是实际路径，既防基数爆炸也顺带不泄资源 ID）。暴露面是"有哪些端点、各自多少次
# 多快"，在集群内网里是监控系统的正常输入。
#
# 仍然**不该让它出集群**：infra/k8s/networkpolicy.yaml 限制了谁能连这些端口，
# 加端点时别把 /metrics 一起放进公网 Ingress。
EXEMPT_PATHS = frozenset({"/health", "/metrics"})


def _configured_token() -> str:
    return os.getenv("LEA_INTERNAL_TOKEN", "").strip()


def _required() -> bool:
    return os.getenv("LEA_REQUIRE_INTERNAL_TOKEN", "").strip().lower() in (
        "1", "true", "yes",
    )


def internal_headers() -> dict[str, str]:
    """调用方一侧：发往**我们自己的服务**时带上的头。没配密钥时返回空 dict。

    ⚠️ 按调用点用，**别挂到共享的 httpx.AsyncClient 上。**

    lea-agent 那边（runtime/internal_token.go）是直接换掉 http.Client 的 Transport
    统一注入的，因为那五个 client 只跟我们自己的服务说话。Python 侧不能照抄：
    lea-tools 的 `executor/router.py` 里一个 pool 被三条路共用 ——

        _execute_internal      → lea-rag            自己的服务，该带
        _execute_sandbox       → Cube/E2B           第三方，有自己的鉴权，不带
        _execute_http_adapter  → config["endpoint"]  **工具配置里的任意 URL**，绝不能带

    挂到 pool 上的后果是集群密钥被贴到每一个外部工具的 endpoint 上，也就是把"加鉴权"
    这件事本身变成一个凭证外泄通道。

    附带一个好处值得记下来：正因为通用 HTTP 适配器拿不到这个头，"把某个工具的
    endpoint 配成 http://lea-rag:8300/admin/... 去打内网"这条 SSRF 路径也一并被堵住
    —— 它到了对面同样是 401。
    """
    token = _configured_token()
    return {HEADER: token} if token else {}


def install_internal_auth(app: FastAPI, service: str) -> None:
    """给 app 挂上服务间鉴权。

    位置上**只需要在 app 开始服务之前** —— Starlette 的 `add_middleware` 在 app
    启动后会直接抛 RuntimeError，而中间件对路由的覆盖跟 include_router / @app.get
    的先后无关（它包的是整个 app，不是某几条路由）。所以"漏掉了后面用 @app.get
    直接挂的那几条"这种事不会发生。习惯上放在模块最后。
    """
    token = _configured_token()

    if not token:
        if _required():
            # 拒绝启动，而不是起来了但不设防。生产环境宁可起不来 —— 起不来会被
            # 立刻发现，静默不设防不会。
            raise RuntimeError(
                f"{service}: LEA_REQUIRE_INTERNAL_TOKEN=1 但 LEA_INTERNAL_TOKEN 没配。"
                "生产必须配；本地调试就别设 LEA_REQUIRE_INTERNAL_TOKEN。"
            )
        logger.warning(
            "internal_auth_disabled",
            service=service,
            detail="LEA_INTERNAL_TOKEN 未配置，本服务当前接受任何来源的请求。"
                   "生产请配置该密钥并设 LEA_REQUIRE_INTERNAL_TOKEN=1。",
        )
        return

    @app.middleware("http")
    async def _check(request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        supplied = request.headers.get(HEADER, "")
        # compare_digest 而不是 == ：避免按字节提前返回泄漏前缀信息。
        # 这条路径能被高频探测（内网里没有速率限制），所以值得。
        if not supplied or not secrets.compare_digest(supplied, token):
            # 日志里不带 supplied 的内容 —— 那可能是别处的真密钥，写进日志等于
            # 又泄漏一遍。只记来源和路径，够定位了。
            logger.warning(
                "internal_auth_rejected",
                service=service,
                path=request.url.path,
                client=request.client.host if request.client else "?",
                had_header=bool(supplied),
            )
            return JSONResponse(
                status_code=401,
                content={"error": "invalid or missing internal token"},
            )
        return await call_next(request)

    logger.info("internal_auth_enabled", service=service)
