"""核验告警规则在真 Prometheus 上到底会不会出数。

promtool 和 /api/v1/rules 的 health=ok 都只证明 PromQL **语法**没问题。一条选了
永不出现的标签值的规则照样 health=ok，而且永远返回空 —— 这就是这个 session 里
反复踩的哑弹形状（histogram_quantile 喂非 _bucket、指标名不存在、死埋点）。

所以这里把每条规则的分子/分母分开查，用来区分三种状态：

  有数        分子分母都有序列 → 规则现在就在算真东西
  分子空      埋点在、但那条失败路径这段时间没发生 → 正常，不是哑弹
  分母空      整个指标族没有任何序列 → 对应代码路径从没跑过（或部署的二进制
              里压根没这个埋点），这条规则**今天不可能触发**

分母空不一定是缺陷，但必须说清楚，不能拿它当"已验证"。

跑法（在同一个 compose 网络里的 Python 服务容器里，走服务名访问 Prometheus）：
    docker cp scripts/verify_alert_rules_live.py lea-rag-test:/tmp/v.py
    docker exec lea-rag-test python /tmp/v.py
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

# prom/prometheus 是个最小镜像，**没有 python3** —— 所以这个脚本不在 prometheus
# 容器里跑，而是在同一个 compose 网络里的某个 Python 服务容器里跑（lea-rag-test），
# 通过服务名 prometheus:9090 访问。
PROM = os.getenv("PROM_URL", "http://prometheus:9090")

# (规则名, 分子, 分母)。分母 None 表示这条不是比率式规则。
RULES = [
    (
        "HighErrorRateParser",
        'sum(rate(lea_parser_http_requests_total{status="5xx"}[5m]))',
        "sum(rate(lea_parser_http_requests_total[5m]))",
    ),
    (
        "VectorDBErrors",
        'sum(rate(lea_rag_vector_db_queries_total{operation=~".*_error"}[5m]))',
        "sum(rate(lea_rag_vector_db_queries_total[5m]))",
    ),
    (
        "KBSearchPartialResults",
        'sum(rate(lea_rag_kb_search_total{status="partial"}[5m]))',
        "sum(rate(lea_rag_kb_search_total[5m]))",
    ),
    (
        "EmbeddingErrors",
        'sum(rate(lea_rag_embedding_total{status="error"}[5m]))',
        "sum(rate(lea_rag_embedding_total[5m]))",
    ),
    (
        "DocumentParseFailing",
        'sum(rate(lea_rag_document_parse_total{status="failed"}[15m]))',
        "sum(rate(lea_rag_document_parse_total[15m]))",
    ),
    (
        "MCPCallsFailing",
        'sum(rate(lea_tools_mcp_calls_total{status=~"error|unreachable|http_error"}[5m]))',
        "sum(rate(lea_tools_mcp_calls_total[5m]))",
    ),
    (
        "SandboxUnavailable",
        'increase(lea_tools_sandbox_executions_total{status="unavailable"}[5m])',
        None,
    ),
    (
        "SandboxUnavailable(file_operations 那一半)",
        'increase(lea_tools_file_operations_total{status="unavailable"}[5m])',
        None,
    ),
]

# 选择器自身的形状对不对：拿**已知存在**的标签值去查，确认精确匹配能命中。
# 这是为了排掉"我把 status 的取值约定搞错了"这类错误 —— 四个 Python 服务的
# status 是分桶的（2xx/4xx/5xx），不是三位数字。
SELECTOR_SANITY = [
    'sum(lea_parser_http_requests_total{status="2xx"})',
    'sum(lea_parser_http_requests_total{status="4xx"})',
    'sum(lea_parser_http_requests_total{status="500"})',  # 期望空：三位数字不存在
    'sum(lea_rag_vector_db_queries_total{operation=~".*_rrf"})',  # 期望有数
]

# 部署的 lea-tools 在远端主机上，我改的埋点没推上去。查它 /metrics 里有哪些
# 指标族，用来区分"没流量"和"部署的二进制里没这个埋点"。
TOOLS_FAMILIES = [
    "lea_tools_mcp_calls_total",
    "lea_tools_sandbox_executions_total",
    "lea_tools_file_operations_total",
    "lea_tools_tool_executions_total",
]


def q(expr: str):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(expr)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.loads(r.read().decode())
    return d["data"]["result"]


def one(expr: str) -> str:
    res = q(expr)
    if not res:
        return "空"
    return res[0]["value"][1]


def tools_scrape_url() -> str | None:
    with urllib.request.urlopen(f"{PROM}/api/v1/targets", timeout=20) as r:
        d = json.loads(r.read().decode())
    for t in d["data"]["activeTargets"]:
        if t["labels"].get("job") == "lea-tools":
            return t["scrapeUrl"]
    return None


def main() -> int:
    print("=== 每条新规则的分子 / 分母 ===")
    verdicts = {}
    for name, num, den in RULES:
        n = one(num)
        d = one(den) if den else "-"
        if den is None:
            verdict = "有数" if n != "空" else "分子空"
        elif d == "空":
            verdict = "分母空(指标族无序列)"
        elif n == "空":
            verdict = "分子空(埋点在,失败未发生)"
        else:
            verdict = "有数"
        verdicts[name] = verdict
        print(f"  {name:<42s} 分子={n:<10s} 分母={d:<10s} → {verdict}")

    print("\n=== 选择器形状自检（证明我没搞错 status 的取值约定）===")
    for expr in SELECTOR_SANITY:
        print(f"  {expr:<62s} = {one(expr)}")

    print("\n=== 部署的 lea-tools 定义了哪些指标族 ===")
    url = tools_scrape_url()
    if not url:
        print("  (targets 里没有 lea-tools)")
    else:
        print(f"  scrapeUrl = {url}")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
            for fam in TOOLS_FAMILIES:
                has_help = f"# HELP {fam}" in body
                # 有 HELP 说明部署的二进制定义了这个指标；带标签的指标在第一次
                # 观测前只有 HELP/TYPE 两行、没有序列，所以这两件事要分开看。
                n_series = sum(
                    1
                    for ln in body.splitlines()
                    if ln.startswith(fam + "{") and not ln.startswith("#")
                )
                defined = "有" if has_help else "**无**"
                print(f"  {fam:<40s} 定义={defined}  序列数={n_series}")
        except Exception as exc:
            print(f"  抓取失败: {exc}")

    print("\n=== 小结 ===")
    for k, v in verdicts.items():
        print(f"  {k:<42s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
