"""Artifact 识别策略对比测试。

目的：在 skill 没指明输出目录 / 指错目录 / 产物形态跨类型的情况下，
     测哪种策略能挑出最终产物、不夹带中间垃圾。

跑法：
    python tests/test_artifact_identify.py              # 只跑 5 个规则策略（离线）
    python tests/test_artifact_identify.py --llm       # 加上 LLM judge 策略（要 API key）
    python tests/test_artifact_identify.py --scenario 3  # 只跑场景 3

输出：precision/recall/F1 表格 + 每场景每策略挑出/漏挑/多挑的文件列表。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Windows 终端默认 GBK，强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 让脚本能直接跑，不用先 pip install -e .
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "src"))

# 加载 .env（顶层 lea-platform/.env）
try:
    from dotenv import load_dotenv
    _root = _HERE.parent.parent.parent
    _env = _root / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

# ============================================================
# 场景数据 —— 6 个手工标注的场景
# ============================================================


@dataclass
class Scenario:
    """一个场景 = 一份文件列表 + 标注 + 可选的 skill manifest。"""

    id: int
    name: str
    files: list[str]  # /workspace 下的相对路径
    expected: set[str]  # 用户真正想要的产物（人工标注）
    manifest: dict | None = None  # skill manifest（可选）
    note: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        id=1,
        name="PPT 生成 · skill 放 output/",
        files=[
            "output/final.pptx",
            "output/preview.png",
            "tmp/chart1.png",
            "tmp/chart2.png",
            "tmp/data.csv",
            "cache.json",
        ],
        expected={"output/final.pptx", "output/preview.png"},
        manifest={"artifacts": {"output_dir": "output/"}},
        note="理想场景：skill 老实用 output/ + 有 manifest。所有策略都该轻松命中。",
    ),
    Scenario(
        id=2,
        name="PPT 生成 · skill 直接放根目录（无 output/）",
        files=[
            "final_report.pptx",
            "cover.png",
            "chart1.png",
            "chart2.png",
            "temp_data.csv",
            "run.log",
            "helper.py",
        ],
        expected={"final_report.pptx"},
        manifest=None,
        note=(
            "开源 skill 不知道我们的约定，就把 pptx 写根目录。"
            "扩展名策略会把 chart1/chart2/cover 也推上去（中间图误判）。"
            "关键考察点：能不能只挑 final_report.pptx？"
        ),
    ),
    Scenario(
        id=3,
        name="前端小项目 · 整个目录都是产物",
        files=[
            "dist/index.html",
            "dist/assets/index-abc123.js",
            "dist/assets/index-def456.css",
            "dist/assets/logo.png",
            "src/App.vue",
            "src/main.ts",
            "src/router.ts",
            "package.json",
            "vite.config.ts",
            "README.md",
            "node_modules/vue/package.json",  # 应该被剪枝
            ".vite/deps_temp/xxx.js",  # 中间缓存
        ],
        expected={
            "dist/index.html",
            "dist/assets/index-abc123.js",
            "dist/assets/index-def456.css",
            "dist/assets/logo.png",
            "src/App.vue",
            "src/main.ts",
            "src/router.ts",
            "package.json",
            "vite.config.ts",
            "README.md",
        },
        manifest=None,
        note=(
            "写小项目场景：用户要整个项目，包括 .vue/.ts/.json/.md。"
            "扩展名白名单会漏 .vue/.ts。目录白名单不知道整个 src/ 都算产物。"
            "关键考察点：能不能识别'这是一个完整项目'？"
        ),
    ),
    Scenario(
        id=4,
        name="数据分析 · 多个产物+多个中间物混在根目录",
        files=[
            "sales_report.xlsx",
            "revenue_chart.png",
            "region_map.png",
            "raw_data_cleaned.csv",
            "raw_data_backup.csv",  # 中间备份
            "processing.log",
            "step1_debug.png",  # 调试图
            "step2_debug.png",
        ],
        expected={
            "sales_report.xlsx",
            "revenue_chart.png",
            "region_map.png",
            "raw_data_cleaned.csv",
        },
        manifest=None,
        note=(
            "扩展名策略会把所有 png/csv 都推，包括 debug 图和 backup。"
            "关键考察点：LLM 能不能从名字看出 debug/backup 是中间物？"
        ),
    ),
    Scenario(
        id=5,
        name="文档改写 · 原文件+修改版共存",
        files=[
            "input/user_upload.docx",  # 用户上传的原件（不该再推）
            "user_upload_edited.docx",  # 改后的
            "diff_summary.md",
            "revision_notes.txt",  # 内部笔记
        ],
        expected={"user_upload_edited.docx", "diff_summary.md"},
        manifest=None,
        note=(
            "input/ 里的是用户自己传上去的，不该当产物再推给用户。"
            "revision_notes.txt 是中间笔记不是最终交付。"
            "关键考察点：input/ 的东西是不是能被识别为非产物？"
        ),
    ),
    Scenario(
        id=6,
        name="用户要一段脚本",
        files=[
            "solution.py",
            "test_solution.py",  # 用户可能也想要测试
            "output.log",
            "example_input.txt",
        ],
        expected={"solution.py", "test_solution.py"},
        manifest=None,
        note=(
            "现在的白名单没 .py，所以扩展名策略会 0 命中。"
            "关键考察点：LLM 能不能识别'这是要给用户的代码'？"
        ),
    ),
]


# ============================================================
# 策略实现
# ============================================================


_ALLOWED_EXTS = frozenset({
    ".pdf", ".pptx", ".ppt", ".xlsx", ".xls", ".docx", ".doc",
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".webm",
    ".zip", ".tar", ".gz", ".7z",
    ".html", ".md", ".txt",
})

_PRUNE_DIRS = frozenset({
    "__pycache__", ".cache", ".ipynb_checkpoints", ".git", ".venv",
    "venv", "node_modules", "site-packages", ".pytest_cache", ".mypy_cache",
    "skill",
})


def _prune(files: list[str]) -> list[str]:
    """先做剪枝目录过滤，所有策略共用（正式代码里 os.walk 层就剪了）。"""
    out = []
    for f in files:
        parts = f.split("/")
        if any(p in _PRUNE_DIRS for p in parts):
            continue
        out.append(f)
    return out


def strategy_extension(files: list[str], manifest: dict | None) -> set[str]:
    return {f for f in files if os.path.splitext(f)[1].lower() in _ALLOWED_EXTS}


def strategy_output_dir_only(files: list[str], manifest: dict | None) -> set[str]:
    return {f for f in files if f.startswith("output/")}


def strategy_manifest(files: list[str], manifest: dict | None) -> set[str]:
    if not manifest or "artifacts" not in manifest:
        return set()
    od = manifest["artifacts"].get("output_dir", "").rstrip("/")
    if not od:
        return set()
    return {f for f in files if f.startswith(od + "/")}


def strategy_layered(files: list[str], manifest: dict | None) -> set[str]:
    """三层兜底：manifest → output_dir → extension。命中即停。"""
    picked = strategy_manifest(files, manifest)
    if picked:
        return picked
    picked = strategy_output_dir_only(files, manifest)
    if picked:
        return picked
    return strategy_extension(files, manifest)


def strategy_all(files: list[str], manifest: dict | None) -> set[str]:
    """全推（不含剪枝目录）。用来看'如果我们什么都不过滤'会多离谱。"""
    return set(files)


# 下面这段是整段发给模型的提示词，换行位置属于内容而不是排版 —— 为了凑 120 列去
# 断行等于改提示词。E501 在 pyproject.toml 的 per-file-ignores 里对本文件整体豁免：
# 超长那行结束在三引号字符串内部，行尾没地方挂逐行豁免注释。
LLM_PROMPT = """You are an assistant that identifies which files in a sandbox workspace are FINAL DELIVERABLES for the end user versus intermediate/temporary files.

The user asked: "{task}"

After the code ran, these files exist in /workspace:
{file_list}

Return ONLY the files that the end user actually wants to download. Exclude:
- Intermediate files (temp charts embedded elsewhere, debug outputs, log files)
- Backups/copies of input files
- Cache files

But INCLUDE all files that form a complete deliverable, even if there are many
(e.g., a frontend project has many .js/.css/.vue files that all belong together).
When uncertain, prefer INCLUDING a file — missing a real deliverable is worse than including an extra file.

Respond with a JSON object in this exact format, nothing else:
{{"artifacts": ["path1", "path2"]}}

Example:
{{"artifacts": ["report.pdf", "chart.png"]}}"""


_TASK_HINT = {
    1: "Generate a PPT report",
    2: "Generate a PPT report",
    3: "Build a small Vue frontend project",
    4: "Do sales data analysis and produce charts",
    5: "Edit the uploaded document",
    6: "Write a Python script solution",
}


# 本地模型清单 —— 命中就走 LEA_LOCAL_API_BASE，否则走 LEA_TENANT_API_BASE。
# 目前所有测试模型都走中转，此集合留空。要测本地部署时把模型名塞进来即可。
_LOCAL_MODELS: frozenset[str] = frozenset()


def _resolve_endpoint(model: str) -> tuple[str, str]:
    """按模型名决定走本地还是中转 API。返回 (base_url, api_key)。"""
    if model in _LOCAL_MODELS:
        return (
            os.environ.get("LEA_LOCAL_API_BASE", "").rstrip("/"),
            os.environ.get("LEA_LOCAL_API_KEY", "sk-local"),
        )
    return (
        os.environ.get("LEA_TENANT_API_BASE", "").rstrip("/"),
        os.environ.get("LEA_TENANT_API_KEY", ""),
    )


def strategy_llm_judge(files: list[str], manifest: dict | None,
                       scenario_id: int = 0, model: str = "qwen3-max") -> tuple[set[str], float]:
    """用 LLM 挑。真调 API。返回 (挑选集合, 耗时秒数)。失败返 (空集, 耗时)。"""
    import time

    import httpx

    api_base, api_key = _resolve_endpoint(model)
    if not api_base or not api_key:
        print(f"  [{model}] 跳过：没配 endpoint")
        return set(), 0.0

    task = _TASK_HINT.get(scenario_id, "Unknown task")
    file_list = "\n".join(f"- {f}" for f in files)
    prompt = LLM_PROMPT.format(task=task, file_list=file_list)

    t0 = time.time()
    try:
        # 本地 Ollama 路径不同（/v1/chat/completions 或 /api/chat），走 OpenAI 兼容 endpoint
        url = f"{api_base}/v1/chat/completions" if "/v1" not in api_base else f"{api_base}/chat/completions"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                # 强制 JSON object 输出；不支持的模型会忽略此参数（不会报错）
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        elapsed = time.time() - t0
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        arr = _extract_artifacts(content)
        if arr is None:
            print(f"  [{model}] 解析失败 (原文前 300 字符): {content[:300]}")
            return set(), elapsed
        return set(arr) & set(files), elapsed  # 只信在文件列表里的
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [{model}] 调用失败 ({elapsed:.1f}s): {e}")
        return set(), elapsed


def _extract_artifacts(content: str) -> list | None:
    """从模型返回文本里抽 artifacts 列表。

    优先按 JSON object 解析（response_format=json_object 情况下），
    失败再退回到抓 JSON 数组（旧 prompt 格式或不支持该参数的模型）。

    支持：
    - 纯 JSON object: `{"artifacts": ["a", "b"]}`
    - 纯 JSON array: `["a", "b"]`
    - Markdown fence 包裹的以上两种
    - 前后有解释文字的以上两种
    """
    import re

    # 去 markdown fence
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()

    # 优先尝试 object
    obj = _find_balanced(content, "{", "}")
    if obj is not None:
        try:
            data = json.loads(obj)
            if isinstance(data, dict) and "artifacts" in data and isinstance(data["artifacts"], list):
                return data["artifacts"]
        except json.JSONDecodeError:
            pass

    # 回退到 array
    arr = _find_balanced(content, "[", "]")
    if arr is not None:
        try:
            data = json.loads(arr)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _find_balanced(content: str, open_c: str, close_c: str) -> str | None:
    """在 content 里找第一个 open_c，返回到配对 close_c 之间的子串（含首尾）。

    正确处理 JSON 字符串里的转义引号和括号。
    """
    start = content.find(open_c)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(content)):
        c = content[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


STRATEGIES: dict[str, Callable] = {
    "all_files":       strategy_all,           # baseline：不过滤
    "extension":       strategy_extension,     # 当前方案 B
    "output_dir_only": strategy_output_dir_only,
    "manifest":        strategy_manifest,
    "layered":         strategy_layered,       # 三层兜底
}


# ============================================================
# 评估
# ============================================================


@dataclass
class Metrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    picked: set[str] = field(default_factory=set)
    missed: set[str] = field(default_factory=set)  # 该挑没挑
    extra: set[str] = field(default_factory=set)   # 不该挑却挑了

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def evaluate(picked: set[str], expected: set[str]) -> Metrics:
    tp = len(picked & expected)
    fp = len(picked - expected)
    fn = len(expected - picked)
    return Metrics(
        tp=tp, fp=fp, fn=fn,
        picked=picked,
        missed=expected - picked,
        extra=picked - expected,
    )


# ============================================================
# 主流程
# ============================================================


def run(scenarios: list[Scenario], llm_models: list[str]) -> None:
    # 规则策略先跑
    strategies_rule = dict(STRATEGIES)
    per_strategy_totals: dict[str, list[Metrics]] = {k: [] for k in strategies_rule}
    per_model_totals: dict[str, list[Metrics]] = {m: [] for m in llm_models}
    per_model_times: dict[str, list[float]] = {m: [] for m in llm_models}

    for sc in scenarios:
        print("\n" + "=" * 72)
        print(f"场景 {sc.id} · {sc.name}")
        print(f"  {sc.note}")
        pruned = _prune(sc.files)
        print(f"  文件数（剪枝后）: {len(pruned)} / 原始 {len(sc.files)}")
        print(f"  期望产物: {sorted(sc.expected)}")

        # 规则策略
        for name, fn in strategies_rule.items():
            picked = fn(pruned, sc.manifest)
            m = evaluate(picked, sc.expected)
            per_strategy_totals[name].append(m)
            print(f"\n  [{name}]  P={m.precision:.2f}  R={m.recall:.2f}  F1={m.f1:.2f}")
            if m.extra:
                print(f"      多挑了: {sorted(m.extra)}")
            if m.missed:
                print(f"      漏挑了: {sorted(m.missed)}")

        # LLM 策略：每个模型跑一次
        for model in llm_models:
            picked, elapsed = strategy_llm_judge(pruned, sc.manifest, sc.id, model)
            m = evaluate(picked, sc.expected)
            per_model_totals[model].append(m)
            per_model_times[model].append(elapsed)
            print(f"\n  [llm:{model}]  P={m.precision:.2f}  R={m.recall:.2f}  F1={m.f1:.2f}  ({elapsed:.1f}s)")
            if m.extra:
                print(f"      多挑了: {sorted(m.extra)}")
            if m.missed:
                print(f"      漏挑了: {sorted(m.missed)}")

    print("\n" + "=" * 72)
    print("总体（各场景 macro 平均）")
    print("=" * 72)
    print(f"{'策略':<28} {'Precision':>10} {'Recall':>10} {'F1':>10} {'AvgTime':>10}")
    for name, ms in per_strategy_totals.items():
        if not ms:
            continue
        p = sum(x.precision for x in ms) / len(ms)
        r = sum(x.recall for x in ms) / len(ms)
        f1 = sum(x.f1 for x in ms) / len(ms)
        print(f"{name:<28} {p:>10.2f} {r:>10.2f} {f1:>10.2f} {'--':>10}")
    for model, ms in per_model_totals.items():
        if not ms:
            continue
        p = sum(x.precision for x in ms) / len(ms)
        r = sum(x.recall for x in ms) / len(ms)
        f1 = sum(x.f1 for x in ms) / len(ms)
        times = per_model_times[model]
        avg_time = sum(times) / len(times) if times else 0
        print(f"{('llm:' + model):<28} {p:>10.2f} {r:>10.2f} {f1:>10.2f} {avg_time:>8.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--models",
        default="",
        help="逗号分隔的模型名列表，如 qwen3-max,qwen3-32b,qwen3-8b。空 = 不跑 LLM",
    )
    ap.add_argument("--scenario", type=int, default=0, help="只跑指定场景（0=全跑）")
    args = ap.parse_args()

    scenarios = SCENARIOS if args.scenario == 0 else [s for s in SCENARIOS if s.id == args.scenario]
    if not scenarios:
        print(f"找不到场景 {args.scenario}")
        sys.exit(1)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    run(scenarios, llm_models=models)


if __name__ == "__main__":
    main()
