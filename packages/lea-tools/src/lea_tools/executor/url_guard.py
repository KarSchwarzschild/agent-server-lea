"""出网地址白名单 —— 挡住指向内网的 endpoint。

## 为什么现在需要它

`mcp_servers.endpoint` 的值会被**服务端**拿去发请求，所以它一直是个 SSRF 面。
以前这个面是可接受的：能建 server 的只有管理员（lea-config 的 CRUD 六处硬过滤
`owner_scope == platform`），而 `_validate_endpoint` 那段注释也明写着「今天能建
server 的只有管理员，风险面是已知的」。

放开用户私有 MCP server 会直接推翻那个前提：任何用户都能让服务端去打任意 URL。
那时候没有这道门就意味着 ——

    http://169.254.169.254/latest/meta-data/iam/...   云主机的凭证
    http://10.x.x.x:8500/internal/agent-config        绕过 internal_auth 打内部服务
    http://localhost:6379                             Redis（会话锁、控制总线都在里面）
    http://[::1]:3306                                 MySQL

所以这道门是**放开 CRUD 的前置**，不是配套改进。顺序反了中间就有一个真实可利用的
窗口，而且是任何登录用户都能用的。

## 判据：解析出的每一个 IP 都必须是公网地址

不能只看主机名字面。`localhost` 好挡，但 `foo.example.com` 完全可以 A 记录指向
`10.0.0.5`，而那种域名看起来毫无问题。所以必须**解析 DNS**，然后逐个检查解析结果。

`getaddrinfo` 会返回多条记录（v4 + v6、多个 A 记录）。**每一条都要过** —— 只查第一条
的话，攻击者让域名同时解析到一个公网 IP 和一个内网 IP 就能绕过（顺序不由我们决定）。

## ⚠️ 这道门挡不住的两件事

**TOCTOU（检查完到真的连接之间 DNS 变了）。** 我们在这里解析一次、httpx 之后自己
再解析一次，两次结果可以不同（DNS rebinding）。真正堵死它要在 socket 层把连接固定到
已检查的 IP 上 —— httpx 支持不了，得换 transport 或者上代理。

**重定向。** 远端可以返回 302 指向内网。`_get_http_pool()` 的 follow_redirects 设置
决定这条路通不通；这个模块看不到它。

两件都记在这里而不是"以后再说"：它们让这道门的强度是「挡住直接写内网地址」，不是
「彻底关掉 SSRF」。对「用户填自己的 MCP 服务地址」这个场景够用，但不要拿它当完整防护
去支撑更危险的功能。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# 允许的 scheme。跟 lea-config 的 _validate_endpoint 一致（那边挡的是同一件事的
# 上游一半），故意各写一份：两个服务分开部署，不互相 import。
ALLOWED_SCHEMES = ("http", "https")


def _ip_rejection(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """这个 IP 该不该拒绝，返回理由或 None。

    判据见下面那段注释：is_global 取反 + 多播，不是手写网段表。手写的失效方式是静默的
    —— 漏一段就是一条可用的 SSRF 通道，而测试只覆盖想到的那几条。
    """
    # v4-mapped 的 v6 地址换算回 v4。
    #
    # ⚠️ 这**不是**正确性所需 —— `::ffff:10.0.0.1` 的 is_global 本身就是 False，
    # 下面那条判据独立挡得住它（变异测试验过：删掉这两行，"拦不拦"那条照样绿）。
    # 它今天的作用是**错误信息可读**：不换算报出来是 `::ffff:a00:1`，谁也看不出那是
    # 10.0.0.1。留着的第二个理由是万一判据换回逐条列举，换算就重新变成正确性的一部分。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    # 判据是 **is_global 取反**，加一条多播。不是逐条列举内网段。
    #
    # 一开始写的是 `is_loopback or is_private or is_link_local or is_reserved or
    # is_multicast`。那样能用，但它是一张"我记得的清单"，而这种清单的失效方式是静默的：
    # 漏一条就是一个可用的 SSRF 通道，而测试只覆盖我想到的那几条。
    #
    # is_global 反过来：**默认拒绝，只放行明确是公网的**。IANA 新划一个特殊段时，
    # Python 更新后这里自动跟上，不需要有人记得回来改。
    #
    # ⚠️ 但 is_global 不是全集，**多播必须单独补**：224.0.0.1 的 is_global 是 True
    # （239.x 也是）。我换成 is_global 时以为它覆盖多播，测试才抓出来 —— 所以"单靠
    # 一个属性"同样不够，只是它漏的比手写清单少。
    #
    # 另一个现成例子：`203.0.113.0/24`（RFC 5737 文档地址块）的 is_private 是 True。
    # 我原本拿它当"保证公网"的测试地址，假 DNS 一注入就全红了。
    if not ip.is_global or ip.is_multicast:
        # 分类只为了让错误信息有用 —— 判据已经在上面定了。
        if ip.is_loopback:
            what = "回环地址"
        elif ip.is_link_local:
            what = "链路本地地址（云元数据服务住这儿）"
        elif ip.is_private:
            what = "内网/保留地址"
        elif ip.is_multicast:
            what = "多播地址"
        else:
            what = "非公网地址"
        return f"{ip} 是{what}"
    return None


def check_outbound_url(url: str, *, resolve: bool = True) -> str | None:
    """能不能拿这个 URL 出网。返回拒绝理由，或 None 表示放行。

    返回理由字符串而不是抛异常，跟 `check_transport` 保持一致的形状：调用方决定这是
    400 还是 502，而这个函数只回答"行不行、为什么不行"。

    resolve=False 只做字面检查（scheme + 主机名是不是直接写的内网 IP），跳过 DNS。
    给单测和「只想快速筛掉明显非法的」用 —— **不要在真的要出网的路径上关掉它**，
    那等于只挡住了老实写 10.0.0.5 的人。
    """
    parts = urlsplit(url)

    if parts.scheme not in ALLOWED_SCHEMES:
        # file:// 能读本地文件；其余 scheme httpx 直接抛，表现为一个看不懂的 500。
        return f"scheme {parts.scheme!r} 不允许，只能是 {'/'.join(ALLOWED_SCHEMES)}"

    host = parts.hostname
    if not host:
        return "URL 里没有主机名"

    # 主机名本身就是 IP 的情况：不需要 DNS，直接判。
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if reason := _ip_rejection(literal):
            return reason
        return None

    # localhost 这类不一定在 DNS 里（可能只在 hosts 文件），而且 resolve=False 时也
    # 该挡住 —— 它是最常见的一个写法。
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return "不允许 localhost"

    if not resolve:
        return None

    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        # 解析不了就拒绝，不是放行。fail-closed：解析失败可能是域名不存在（那这个
        # endpoint 本来也没用），也可能是 DNS 抖动 —— 两种情况下"先连了再说"都不对。
        #
        # ⚠️ 单测不要靠真域名绕过这条。用 monkeypatch 换掉 socket.getaddrinfo，
        # 或者传 resolve=False。让测试依赖 DNS 会让它们在断网时变红，而那种红跟代码
        # 无关 —— 更糟的是会有人为了让它变绿而放宽这里的判据。
        return f"主机名 {host!r} 解析失败: {e}"

    if not infos:
        return f"主机名 {host!r} 没有解析结果"

    # **每一条都要过。** 只查第一条的话，让域名同时解析到一个公网 IP 和一个内网 IP
    # 就能绕过 —— 返回顺序不由我们决定。
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return f"解析出无法识别的地址 {addr!r}"
        if reason := _ip_rejection(ip):
            return f"{host} 解析到 {reason}"

    return None
