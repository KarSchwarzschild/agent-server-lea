#!/usr/bin/env python3
"""给 xray 加一个只监听 docker0 网关的 inbound，让容器也能用宿主的代理。

## 为什么需要

容器里的 WebSearch/WebFetch 要走宿主代理才能出网（DuckDuckGo 直连是 i/o
timeout）。但 xray 原来只监听 127.0.0.1:10809，而 127.0.0.1 在容器里指的是
容器自己——填这个地址容器永远连不上。

## 为什么是"加一个"而不是"改原来那个"

原有的 127.0.0.1:10809 被这台机器上很多东西用着，最要紧的是 dockerd 自己的
systemd 代理配置（HTTPS_PROXY=http://127.0.0.1:10809，拉镜像靠它）。把 listen
改掉会让那些调用方**全部立刻失效**，而且报错离原因很远（"拉镜像超时"根本
看不出是代理监听地址变了）。加一个新 inbound 对现有调用方零影响。

## 为什么绑 172.17.0.1 而不是 0.0.0.0

0.0.0.0 会连公网网卡一起监听——这台机器有公网 IP，那等于对外开了一个匿名
代理，谁都能拿它当跳板。172.17.0.1 是 docker0 网关，只有本机容器网络到得了。
"""
from __future__ import annotations

import copy
import json
import sys

CONFIG = "/usr/local/etc/xray/config.json"
DOCKER_GW = "172.17.0.1"
DOCKER_TAG = "socks-docker"


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)

    inbounds = cfg.get("inbounds", [])
    if any(i.get("tag") == DOCKER_TAG for i in inbounds):
        print(f"already present: tag={DOCKER_TAG} — nothing to do")
        return 0

    # 照抄现有那个 mixed inbound 的全部设置（sniffing/settings 等都要跟着，
    # 否则新 inbound 的行为会跟老的不一致，排查时很难想到是这里不一样）。
    source = next((i for i in inbounds if i.get("protocol") == "mixed"), None)
    if source is None:
        print("no mixed inbound found to clone", file=sys.stderr)
        return 1

    clone = copy.deepcopy(source)
    clone["listen"] = DOCKER_GW
    clone["tag"] = DOCKER_TAG
    inbounds.append(clone)
    cfg["inbounds"] = inbounds

    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"added inbound tag={DOCKER_TAG} listen={DOCKER_GW}:{clone.get('port')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
