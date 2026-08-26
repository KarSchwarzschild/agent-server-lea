"""lea_authkit 的服务间鉴权 —— 行为测试。

这几条都属于"改错了功能完全正常、只是不设防了"，所以必须有测试盯着：探针免鉴权、
/docs 不免、缺头/错头拒绝、没配密钥时放行、required 模式拒绝启动。

install_internal_auth 在**调用时**读环境变量，所以每条用例都要先设 env 再新建 app
（不能复用一个 app fixture）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lea_authkit import HEADER, install_internal_auth, internal_headers

TOKEN = "tok-abc-123"


def build_app() -> FastAPI:
    """一个最小的被保护服务：一条探针 + 一条业务接口。"""
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/kb/search")
    def search():
        return {"hits": ["机密"]}

    install_internal_auth(app, service="test-svc")
    return app


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("LEA_REQUIRE_INTERNAL_TOKEN", raising=False)
    return TestClient(build_app())


# ── 挡住的 ────────────────────────────────────────────────────────────────

def test_missing_header_is_rejected(guarded):
    r = guarded.get("/kb/search")
    assert r.status_code == 401
    assert r.json() == {"error": "invalid or missing internal token"}


def test_wrong_token_is_rejected(guarded):
    r = guarded.get("/kb/search", headers={HEADER: "wrong"})
    assert r.status_code == 401


def test_empty_token_is_rejected(guarded):
    """空串要走"缺头"那条，不能进 compare_digest —— 空串比空串是相等的。"""
    r = guarded.get("/kb/search", headers={HEADER: ""})
    assert r.status_code == 401


def test_prefix_of_real_token_is_rejected(guarded):
    r = guarded.get("/kb/search", headers={HEADER: TOKEN[:-1]})
    assert r.status_code == 401


# /docs 和 /openapi.json 故意不在豁免名单里：它们会把整个接口面（含 admin 那几组）
# 的完整签名交给任何能连上端口的东西。这条测试是防止有人"为了方便调试"把它们加进去。
@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_are_not_exempt(guarded, path):
    assert guarded.get(path).status_code == 401


# ── 放过的 ────────────────────────────────────────────────────────────────

def test_health_is_exempt(guarded):
    """k8s 的 readiness/liveness 不会带自定义头。这条挂了 pod 会一直 NotReady。"""
    r = guarded.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_correct_token_passes(guarded):
    r = guarded.get("/kb/search", headers={HEADER: TOKEN})
    assert r.status_code == 200
    assert r.json() == {"hits": ["机密"]}


# ── 没配密钥时的降级 ──────────────────────────────────────────────────────

def test_no_token_configured_lets_everything_through(monkeypatch):
    """滚动更新期间调用方还没带头，被调方先上线也不能断链。见模块顶部的说明。"""
    monkeypatch.delenv("LEA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("LEA_REQUIRE_INTERNAL_TOKEN", raising=False)
    c = TestClient(build_app())
    assert c.get("/kb/search").status_code == 200


def test_required_without_token_refuses_to_start(monkeypatch):
    """生产必须开这个开关 —— 起不来会被立刻发现，静默不设防不会。"""
    monkeypatch.delenv("LEA_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("LEA_REQUIRE_INTERNAL_TOKEN", "1")
    with pytest.raises(RuntimeError, match="LEA_REQUIRE_INTERNAL_TOKEN"):
        build_app()


def test_required_with_token_starts_fine(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("LEA_REQUIRE_INTERNAL_TOKEN", "true")
    c = TestClient(build_app())
    assert c.get("/kb/search", headers={HEADER: TOKEN}).status_code == 200


# ── 调用方一侧 ────────────────────────────────────────────────────────────

def test_internal_headers_empty_when_unset(monkeypatch):
    """返回空 dict，而不是一个值为空串的头 —— 后者到对面是一次失败的鉴权尝试。"""
    monkeypatch.delenv("LEA_INTERNAL_TOKEN", raising=False)
    assert internal_headers() == {}


def test_internal_headers_carries_token(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", TOKEN)
    assert internal_headers() == {HEADER: TOKEN}


def test_internal_headers_strips_whitespace(monkeypatch):
    """env 里带尾随换行是常见的（kubectl create secret --from-file 就会）。

    两侧都用 .strip() 才对得上：只 strip 一侧的话，密钥"看起来配对了"但永远 401。
    """
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", f"  {TOKEN}\n")
    assert internal_headers() == {HEADER: TOKEN}
    c = TestClient(build_app())
    assert c.get("/kb/search", headers={HEADER: TOKEN}).status_code == 200
