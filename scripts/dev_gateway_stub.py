"""本机开发用的宿主平台替身 —— 让 lea-gateway 能在没有 passport 的机器上跑起来。

## 为什么需要它

gateway 的 auth 有两道硬依赖，都在宿主平台那边：

  1. RS256 token，签发方是 passport-ai.ouchn.edu.cn，公钥走 JWKS 拉（main.go:176）
  2. **每一个**被代理的请求都要调 TENANT_MEMBERSHIP_URL 解析用户在所选租户里的角色
     （main.go:agentRoute / trustedRoute），失败就 403

所以本机既签不出 token，也解析不出租户。绕开 gateway 直连 lea-agent 能让界面动起来，
但那会跳过 gateway 唯一真正独有的东西 —— **那张白名单路由表**。而它恰好是历史上真出过
两次生产 404 的地方（见 main.go:routes 的注释：项目、用户文件库都漏注册过，
开发环境直连 agent 时完全正常）。所以正确的做法是留着 gateway，把它的两个外部依赖
换成本机替身。

这个脚本就是那个替身，提供两个端点：

  GET /jwks.json                              自签 RSA 公钥
  GET /api/TenantUser/CurrUserTenants         固定返回一条租户成员关系

配合 infra/docker-compose.dev-auth.yml 把 gateway 的 OIDC_ISSUER / OIDC_JWKS_URL /
TENANT_MEMBERSHIP_URL 指过来。

⚠️ 只能本机用。它对**任何** Authorization 都返回同一条成员关系 —— 换句话说指向它的
gateway 等于没有鉴权。别把这套 override 带到任何共享环境。

用法：
    uv run --no-sync python scripts/dev_gateway_stub.py          # 起服务
    uv run --no-sync python scripts/dev_gateway_stub.py --mint   # 只打一个 token
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PORT = 9310
# gateway 容器里用 host.docker.internal 回连宿主；iss 必须跟 OIDC_ISSUER 逐字相等
# （jwt.WithIssuer 是精确比较），所以这里和 compose override 里的值得一起改。
ISSUER = f"http://host.docker.internal:{PORT}"
KID = "lea-dev-key-1"
USER_ID = "lab-dev-user"
TENANT_ID = "default"
ROLE = "user"

KEY_PATH = pathlib.Path(__file__).resolve().parent.parent / "infra" / ".dev-oidc-key.pem"


def _load_key() -> rsa.RSAPrivateKey:
    """密钥持久化到磁盘 —— 每次重启都换一把的话，浏览器里存着的 token 会突然失效，
    表现成"刚才还好好的现在全 401"，很难往密钥上想。"""
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    KEY_PATH.chmod(0o600)
    return key


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks(key: rsa.RSAPrivateKey) -> dict:
    numbers = key.public_key().public_numbers()
    return {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
        "n": _b64u(numbers.n), "e": _b64u(numbers.e),
    }]}


def mint(key: rsa.RSAPrivateKey, days: int = 7) -> str:
    import jwt

    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # UserId 是 gateway claims 结构体里的主字段，sub 只是兜底（main.go:parseClaims）。
    # 两个都给，免得将来哪个被改成必填。
    return jwt.encode(
        {"UserId": USER_ID, "sub": USER_ID, "tenant_id": TENANT_ID,
         "iss": ISSUER, "exp": int(time.time()) + days * 86400},
        pem, algorithm="RS256", headers={"kid": KID},
    )


def membership() -> dict:
    if ROLE == "platform_admin":
        return {"data": [{
            "tenantId": TENANT_ID, "tenantType": "super",
            "role": "Master", "userTenantRoleName": "管理者", "disabled": False,
        }]}
    if ROLE == "admin":
        return {"data": [{
            "tenantId": TENANT_ID, "tenantType": "normal",
            "role": "Master", "userTenantRoleName": "管理者", "disabled": False,
        }]}
    return {"data": [{
        "tenantId": TENANT_ID, "tenantType": "normal",
        "role": "Member", "userTenantRoleName": "成员", "disabled": False,
    }]}


class Handler(BaseHTTPRequestHandler):
    key: rsa.RSAPrivateKey

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/jwks.json":
            body = jwks(self.key)
        elif path == "/api/TenantUser/CurrUserTenants":
            body = membership()
        else:
            self.send_error(404)
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        print(f"  stub {fmt % args}", flush=True)


def main() -> None:
    global ROLE, USER_ID, TENANT_ID
    parser = argparse.ArgumentParser()
    parser.add_argument("--mint", action="store_true", help="只打印一个 token 就退出")
    parser.add_argument("--role", choices=("user", "admin", "platform_admin"), default="user")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--tenant-id", default=TENANT_ID)
    args = parser.parse_args()

    ROLE = args.role
    USER_ID = args.user_id
    TENANT_ID = args.tenant_id

    key = _load_key()
    if args.mint:
        print(mint(key))
        return

    Handler.key = key
    print(f"dev issuer  {ISSUER}")
    print(f"jwks        {ISSUER}/jwks.json")
    print(f"membership  {ISSUER}/api/TenantUser/CurrUserTenants")
    print(f"token       {mint(key)}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
