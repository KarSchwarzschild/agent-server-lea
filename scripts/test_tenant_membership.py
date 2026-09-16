"""用 access_token 测试用户租户关系接口，不输出 Token 内容。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_URL = "https://ai.ouchn.edu.cn/api/TenantUser/CurrUserTenants"


def decode_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Token 不是 JWT 格式")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("TENANT_MEMBERSHIP_URL", DEFAULT_URL))
    parser.add_argument("--token-file", help="包含 access_token 的本地文件")
    parser.add_argument("--tenant-id", help="只显示该租户是否在列表中")
    args = parser.parse_args()

    if args.token_file:
        token = open(args.token_file, encoding="utf-8").read().strip()
    else:
        token = os.getenv("ACCESS_TOKEN", "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        print("缺少 Token：请设置 ACCESS_TOKEN 或使用 --token-file", file=sys.stderr)
        return 2

    try:
        claims = decode_claims(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"JWT 解析失败: {exc}", file=sys.stderr)
        return 2

    print(f"issuer={claims.get('iss', '')}")
    print(f"subject={claims.get('sub', '')}")
    print(f"user_id={claims.get('UserId', '')}")
    print(f"audience={claims.get('aud', '')}")

    request = urllib.request.Request(
        args.url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP_STATUS={exc.code}")
        print("租户接口拒绝了请求，请检查 Token、环境和接口地址。")
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"请求失败: {exc}", file=sys.stderr)
        return 1

    tenants = body.get("data") or []
    print(f"HTTP_STATUS={status}")
    print(f"stateCode={body.get('stateCode')} state={body.get('state')} authorization={body.get('authorization')}")
    print(f"tenant_count={len(tenants)}")
    if args.tenant_id:
        matches = [
            item for item in tenants
            if item.get("tenantId") == args.tenant_id and not item.get("disabled", False)
        ]
        print(f"selected_tenant={args.tenant_id}")
        print(f"selected_tenant_valid={bool(matches)}")
        if matches:
            item = matches[0]
            print(f"tenant_type={item.get('tenantType', '')}")
            print(f"role={item.get('role', '')}")
            print(f"role_name={item.get('userTenantRoleName', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
