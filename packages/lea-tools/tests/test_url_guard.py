"""出网地址白名单 —— 放开用户私有 MCP server 的前置。

## 这道门挡的是什么

`mcp_servers.endpoint` 的值被**服务端**拿去发请求。以前能建 server 的只有管理员
（lea-config 的 CRUD 六处硬过滤 owner_scope == platform），所以那个 SSRF 面是
"已知且可接受"的 —— mcp_servers.py 的 _validate_endpoint 注释就是这么写的。

放开用户私有 server 会推翻那个前提。到那时没有这道门意味着任何登录用户都能让服务端去打：

    169.254.169.254   云主机元数据 → IAM 凭证
    10.x / 172.16.x   内部服务，绕过 internal_auth
    localhost:6379    Redis（会话锁、控制总线）
    127.0.0.1:3306    MySQL

所以这组用例不是"覆盖率"，是那道门本身。

## 这个文件**不打** DNS 的 patch

test_mcp_p0.py 有个 autouse fixture 把 getaddrinfo 换成固定公网地址，否则那边的假域名
（notion.example）会因为 fail-closed 全红。这里相反 —— 要验的正是解析这一层，所以
每条用例自己决定解析结果。
"""

from __future__ import annotations

import socket

import pytest

from lea_tools.executor.url_guard import check_outbound_url


def fake_dns(monkeypatch, *addrs: str) -> None:
    """让 getaddrinfo 返回指定的地址，可以多条。

    多条那种是**真实的攻击形状**：一个域名同时解析到公网和内网地址，只查第一条的
    实现就会被绕过（返回顺序不由我们决定）。
    """
    def fake(host, port, *a, **kw):
        out = []
        for s in addrs:
            fam = socket.AF_INET6 if ":" in s else socket.AF_INET
            sockaddr = (s, port or 443, 0, 0) if fam == socket.AF_INET6 else (s, port or 443)
            out.append((fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ── scheme ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://x/",
    "ftp://x/",
    # file:// 能读本地文件；其余 scheme httpx 直接抛，表现为一个看不懂的 500。
])
def test_non_http_schemes_are_rejected(url):
    reason = check_outbound_url(url, resolve=False)
    assert reason and "scheme" in reason


def test_https_is_allowed(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34")
    assert check_outbound_url("https://api.example.com/mcp") is None


def test_plain_http_is_allowed(monkeypatch):
    # 允许 http：内网的合法 MCP server 常常没证书。真正的判据是地址而不是协议。
    fake_dns(monkeypatch, "93.184.216.34")
    assert check_outbound_url("http://api.example.com/mcp") is None


# ── 字面 IP ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,what", [
    ("127.0.0.1", "回环"),
    ("127.1.2.3", "回环整个 /8，不只是 .0.1"),
    ("10.0.0.5", "10/8"),
    ("172.16.0.5", "172.16/12"),
    ("192.168.1.1", "192.168/16"),
    ("169.254.169.254", "云元数据 —— 最要紧的一条"),
    ("100.64.0.1", "CGNAT，容易被漏掉"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "多播"),
    ("[::1]", "v6 回环"),
    ("[fc00::1]", "v6 私有"),
    ("[fe80::1]", "v6 链路本地"),
])
def test_internal_literal_addresses_are_rejected(host, what):
    """写死内网 IP 的直接拦掉。不需要 DNS，所以 resolve 与否都该拦。"""
    assert check_outbound_url(f"http://{host}/mcp", resolve=False), f"漏了 {what}"
    assert check_outbound_url(f"http://{host}/mcp"), f"漏了 {what}（resolve=True）"


def test_v4_mapped_v6_is_rejected():
    """`::ffff:10.0.0.1` 是写成 v6 形式的 10.0.0.1，必须拦。"""
    assert check_outbound_url("http://[::ffff:10.0.0.1]/mcp", resolve=False)


def test_v4_mapped_rejection_names_the_v4_address():
    """错误信息里要出现 `10.0.0.1`，不是 `::ffff:a00:1`。

    ⚠️ 这条**不是**在验"拦不拦" —— 变异测试证明了那件事由 is_global 独立保证：
    `::ffff:10.0.0.1` 的 is_global 本身就是 False，把 ipv4_mapped 换算那两行删掉，
    上面那条照样绿。

    那两行今天的实际作用是可读性：不换算的话报出来是 `::ffff:a00:1`，谁也看不出它是
    10.0.0.1。留着它们还有第二个理由 —— 万一以后有人把判据换回逐条列举内网段，
    换算就重新变成正确性的一部分。
    """
    reason = check_outbound_url("http://[::ffff:10.0.0.1]/mcp", resolve=False)
    assert reason and "10.0.0.1" in reason, f"报的是 {reason!r}，看不出是哪个内网地址"


def test_public_literal_ip_is_allowed():
    assert check_outbound_url("https://93.184.216.34/mcp", resolve=False) is None


# ── localhost ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "foo.localhost"])
def test_localhost_is_rejected(host):
    """localhost 不一定在 DNS 里（可能只在 hosts 文件），所以单独挡一道。

    大小写和子域都要覆盖：Host 头不区分大小写，而 .localhost 是保留后缀。
    """
    assert check_outbound_url(f"http://{host}:6379/", resolve=False)


# ── DNS 解析 ──────────────────────────────────────────────────────────────

def test_domain_resolving_to_internal_is_rejected(monkeypatch):
    """**这是这道门存在的主要理由。**

    `foo.example.com` 看起来毫无问题，A 记录却指向 10.0.0.5。只看主机名字面的实现
    在这里全盘失效 —— 而那是最容易写出来的实现。
    """
    fake_dns(monkeypatch, "10.0.0.5")
    reason = check_outbound_url("https://innocent.example.com/mcp")
    assert reason and "10.0.0.5" in reason


def test_every_resolved_address_is_checked(monkeypatch):
    """公网 + 内网混在一起时必须拒绝。

    只查第一条的实现会被这个形状绕过：让域名同时解析到一个公网 IP 和一个内网 IP，
    返回顺序不由我们决定，所以"第一条是公网"是可以安排的。
    """
    fake_dns(monkeypatch, "93.184.216.34", "169.254.169.254")
    reason = check_outbound_url("https://mixed.example.com/mcp")
    assert reason and "169.254.169.254" in reason


def test_all_public_addresses_pass(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34", "8.8.8.8")
    assert check_outbound_url("https://api.example.com/mcp") is None


def test_resolution_failure_is_fail_closed(monkeypatch):
    """解析不了 → 拒绝，不是放行。

    可能是域名不存在（那这个 endpoint 本来也没用），也可能是 DNS 抖动 —— 两种情况下
    "先连了再说"都不对。
    """
    def boom(*a, **kw):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    reason = check_outbound_url("https://nx.example.com/mcp")
    assert reason and "解析失败" in reason


def test_empty_resolution_is_rejected(monkeypatch):
    fake_dns(monkeypatch)  # 一条都没有
    assert check_outbound_url("https://nx.example.com/mcp")


def test_resolve_false_skips_dns(monkeypatch):
    """resolve=False 时不碰 DNS —— 给单测和快速筛用。

    ⚠️ 它只跳过解析，字面内网 IP 和 localhost 仍然拦（上面那两组已经验过）。
    真要出网的路径上不该关掉它：那等于只挡住老实写 10.0.0.5 的人。
    """
    def boom(*a, **kw):
        raise AssertionError("resolve=False 时不该调 getaddrinfo")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert check_outbound_url("https://whatever.example.com/mcp", resolve=False) is None


# ── 畸形输入 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["", "not-a-url", "http://", "https:///path"])
def test_malformed_urls_are_rejected(url):
    """没有主机名的一律拒。别让它们走到 httpx 那儿变成一个看不懂的异常。"""
    assert check_outbound_url(url, resolve=False)


def test_port_does_not_change_the_verdict(monkeypatch):
    """端口不参与判据 —— 内网的 6379 和 443 一样该拦。

    写成"只挡数据库端口"那类端口黑名单是另一张记不全的清单，而且没意义：内网上任何
    端口都不该由用户指定去连。
    """
    assert check_outbound_url("http://127.0.0.1:443/", resolve=False)
    assert check_outbound_url("http://127.0.0.1:6379/", resolve=False)
