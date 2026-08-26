"""模板渲染 — 支持 {{variable}} 占位符。"""

from __future__ import annotations

from typing import Any

from jinja2 import BaseLoader, Environment

_env = Environment(
    loader=BaseLoader(),
    variable_start_string="{{",
    variable_end_string="}}",
)


def render_template(template_obj: dict | str, context: dict[str, Any]) -> Any:
    if isinstance(template_obj, str):
        tpl = _env.from_string(template_obj)
        return tpl.render(**context)
    if isinstance(template_obj, dict):
        return {k: render_template(v, context) for k, v in template_obj.items()}
    if isinstance(template_obj, list):
        return [render_template(v, context) for v in template_obj]
    return template_obj
