"""A/B 测试框架 — Redis-backed 分布式指标收集。

用途：
  - prompt 版本对比
  - 检索策略对比（hybrid vs vector-only）
  - 模型版本对比

所有计数器用 Redis HINCRBY/HINCRBYFLOAT 原子累加，
多实例部署下指标自动汇总。
"""

from __future__ import annotations

import hashlib
import json
import time

import structlog

logger = structlog.get_logger(__name__)


class ABTestManager:
    """A/B 测试管理器 — Redis-backed。"""

    def __init__(self, redis_client):
        self._redis = redis_client

    def _exp_key(self, name: str) -> str:
        return f"lea:ab:{name}"

    def _variant_key(self, exp_name: str, variant_name: str) -> str:
        return f"lea:ab:{exp_name}:v:{variant_name}"

    def create_experiment(
        self,
        name: str,
        variants: list[dict],
        description: str = "",
    ) -> None:
        key = self._exp_key(name)
        self._redis.hset(key, mapping={
            "name": name,
            "description": description,
            "status": "active",
            "created_at": str(time.time()),
            "variants": json.dumps(
                [{"name": v["name"], "weight": v.get("weight", 0.5)} for v in variants],
                ensure_ascii=False,
            ),
        })
        for v in variants:
            vk = self._variant_key(name, v["name"])
            self._redis.hset(vk, mapping={
                "name": v["name"],
                "weight": str(v.get("weight", 0.5)),
                "config": json.dumps(v.get("config", {}), ensure_ascii=False),
                "request_count": "0",
                "total_score": "0",
                "total_latency_ms": "0",
                "error_count": "0",
            })
        logger.info("experiment_created", name=name, variants=[v["name"] for v in variants])

    def assign(self, experiment_name: str, user_id: str) -> dict | None:
        """确定性分桶 — 同一 user_id 始终分到同一 variant。"""
        exp = self._redis.hgetall(self._exp_key(experiment_name))
        if not exp:
            return None
        status = (exp.get(b"status") or exp.get("status", b"")).decode()
        if status != "active":
            return None

        variants_raw = (exp.get(b"variants") or exp.get("variants", b"[]")).decode()
        variants = json.loads(variants_raw)
        if not variants:
            return None

        h = int(hashlib.md5(f"{experiment_name}:{user_id}".encode()).hexdigest(), 16)
        total_weight = sum(v["weight"] for v in variants)
        point = (h % 10000) / 10000 * total_weight

        cumulative = 0.0
        for v in variants:
            cumulative += v["weight"]
            if point < cumulative:
                return v
        return variants[-1]

    def record(
        self,
        experiment_name: str,
        variant_name: str,
        score: float,
        latency_ms: float,
        error: bool = False,
    ) -> None:
        """原子累加指标 — 多实例安全。"""
        vk = self._variant_key(experiment_name, variant_name)
        pipe = self._redis.pipeline()
        pipe.hincrby(vk, "request_count", 1)
        pipe.hincrbyfloat(vk, "total_score", score)
        pipe.hincrbyfloat(vk, "total_latency_ms", latency_ms)
        if error:
            pipe.hincrby(vk, "error_count", 1)
        pipe.execute()

    def get_summary(self, experiment_name: str) -> dict | None:
        exp = self._redis.hgetall(self._exp_key(experiment_name))
        if not exp:
            return None

        name = (exp.get(b"name") or exp.get("name", b"")).decode()
        status = (exp.get(b"status") or exp.get("status", b"")).decode()
        variants_raw = (exp.get(b"variants") or exp.get("variants", b"[]")).decode()
        variants = json.loads(variants_raw)

        variant_stats = []
        for v in variants:
            vk = self._variant_key(experiment_name, v["name"])
            data = self._redis.hgetall(vk)
            if not data:
                continue
            reqs = int(data.get(b"request_count", data.get("request_count", 0)))
            total_score = float(data.get(b"total_score", data.get("total_score", 0)))
            total_lat = float(data.get(b"total_latency_ms", data.get("total_latency_ms", 0)))
            errs = int(data.get(b"error_count", data.get("error_count", 0)))
            variant_stats.append({
                "name": v["name"],
                "requests": reqs,
                "avg_score": round(total_score / reqs, 3) if reqs else 0.0,
                "avg_latency_ms": round(total_lat / reqs, 1) if reqs else 0.0,
                "error_rate": round(errs / max(reqs, 1), 4),
            })

        return {"name": name, "status": status, "variants": variant_stats}
