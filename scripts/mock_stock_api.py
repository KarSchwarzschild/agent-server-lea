"""给 my__ 前缀验收当上游的假 HTTP 服务。

    python scripts/mock_stock_api.py [port]      默认 9911

一个 GET /stock?warehouse_ref=XXX，返回一段**编不出来的**文本。

## 为什么要自己起一个而不是打真 API

判据得是"模型说得出这个值 ⇔ 工具真的调通了"。用 open-meteo 之类的公共 API
做不到这一点：温度是可以蒙的，而且 get_weather 的参数名太好猜 —— 上一个验收
（p0_tool_schema_e2e.py）就吃过这个亏，坏的那版照样答对了，只能靠看参数**类型**
才分得出来。

所以这里两样都做绝：

    参数名   warehouse_ref  —— 不是 id / query / warehouse 这种能猜到的
    返回值   青鸾-7F / 2291 件 —— 训练数据里不存在，模型编不出来

于是断言可以很硬：回复里出现 "青鸾-7F"，就等于这条链真的走通了。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# 这两个值是断言的锚点。改它们要同步改 my_prefix_e2e.py。
MAGIC_ZONE = "青鸾-7F"
MAGIC_QTY = 2291

CALLS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/stock":
            self._send(404, {"error": "not found"})
            return

        q = parse_qs(u.query)
        ref = (q.get("warehouse_ref") or [""])[0]
        CALLS.append({"warehouse_ref": ref})
        print(f"  [mock] GET /stock warehouse_ref={ref!r}", flush=True)

        # 空参数单独回一个**看得出是空**的结果。
        #
        # 不回 400：那样模型只会说"查询失败"，跟"工具没到它面前"长得一样，
        # 分不开是 schema 丢了还是别的。回一段明确的文本，日志里一眼能定位。
        if not ref:
            self._send(200, {
                "warehouse_ref": "",
                "note": "未收到 warehouse_ref —— schema 可能没下发到模型",
            })
            return

        self._send(200, {
            "warehouse_ref": ref,
            "zone": MAGIC_ZONE,
            "quantity": MAGIC_QTY,
            "unit": "件",
        })

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args) -> None:
        pass  # 默认那行 access log 会盖住上面自己打的，静音


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9911
    srv = HTTPServer(("0.0.0.0", port), Handler)
    print(f"mock stock api → http://0.0.0.0:{port}/stock?warehouse_ref=XXX")
    print(f"  容器里按 host.docker.internal:{port} 访问")
    print(f"  锚点 {MAGIC_ZONE} / {MAGIC_QTY}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(f"\n收到 {len(CALLS)} 次调用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
