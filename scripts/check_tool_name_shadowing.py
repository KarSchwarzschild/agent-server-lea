"""体检：找出库里非法的同名工具（遮蔽）。

## 为什么需要一个脚本，而不是一条约束

"用户不能占用系统内置工具名"这条规则**表达不成唯一索引**：内置工具住在
`tenant_id='default'`，用户工具在自己的租户里，唯一索引根本碰不到一起。而且规则
本身是跨行的（"这个名字在别的租户里是不是内置的"），MySQL 里没有这种约束。

所以分工是：

    接口层    lea-config 的 _reject_name_conflict —— 挡住**新**的遮蔽
    运行时    lea-tools 的 resolve_tool —— 已有的遮蔽收敛时打 warning
    这个脚本  一次性把**存量**捞出来

前两道管的是"以后不会再有"，存量只能查。规则是在遮蔽已经存在之后才加的，所以
存量一定要看一眼再决定怎么清。

## 用法

    PYTHONPATH="packages/lea-config/src;packages/lea-authkit/src" \
        python scripts/check_tool_name_shadowing.py

只读，不改任何东西。有发现时退出码 1，方便挂进 CI。
"""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["packages/lea-config/src", "packages/lea-authkit/src"]

from lea_config.database import engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

GLOBAL_TENANT = "default"


def main() -> int:
    # Windows 上重定向到文件时 stdout 会用 locale 编码（cp936），中文输出存成 GBK ——
    # 再用 UTF-8 打开就是一片乱码，看起来像脚本坏了。显式钉成 UTF-8。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, tenant_id, name, owner_scope, owner_user_id, is_builtin, enabled "
            "FROM tools ORDER BY tenant_id, name, id"
        )).mappings().all()

    builtin_names = {
        r["name"] for r in rows
        if r["tenant_id"] == GLOBAL_TENANT and r["is_builtin"]
    }

    findings: list[str] = []

    # ① 占用了内置工具名的非内置行
    for r in rows:
        if r["name"] in builtin_names and not r["is_builtin"]:
            findings.append(
                f"  [占用内置名] id={r['id']:<5} tenant={r['tenant_id']:<12} "
                f"name={r['name']:<20} owner={r['owner_scope']}/{r['owner_user_id']}"
            )

    # ② 同一个人（或同一个租户的共享工具）在自己的可见集合里有两行同名。
    #    按 (租户, 名字) 分组，组内只要同时存在"平台行"和"用户行"，那个用户就会
    #    同时看见两行 —— 也就是执行时需要"谁赢"的那个状态。
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        grouped[(r["tenant_id"], r["name"])].append(r)

    for (tenant, name), group in grouped.items():
        if tenant == GLOBAL_TENANT or len(group) < 2:
            continue
        has_platform = any(g["owner_scope"] == "platform" for g in group)
        users = {g["owner_user_id"] for g in group if g["owner_scope"] == "user"}
        if has_platform and users:
            findings.append(
                f"  [遮蔽共享工具] tenant={tenant:<12} name={name:<20} "
                f"平台行 + 用户 {sorted(users)}"
            )

    print(f"扫描 {len(rows)} 行工具，内置工具名 {len(builtin_names)} 个：{sorted(builtin_names)}\n")
    if not findings:
        print("没有发现非法同名。")
        return 0

    print(f"发现 {len(findings)} 处非法同名：\n")
    print("\n".join(findings))
    print(
        "\n处理建议：这些行**现在解析不到**（排序是内置 > 平台 > 用户），"
        "\n所以它们的配置已经不生效了。确认无人依赖后删掉，或改名。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
