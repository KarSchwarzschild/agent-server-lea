package runtime

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// 工具网关的 HTTP 错误必须以**错误**的形式交给模型。
//
// 这一整个文件盯的是一条以前完全不存在的分支：ExecuteRemoteTool 曾经不看
// resp.StatusCode，只看 body 里的 status / error / result。而 lea-tools 的拒绝路径
// 抛的是 FastAPI 的 HTTPException，body 形如
//
//	{"detail": "tool 'my__stock_lookup' not found"}
//
// 那三个字段一个都不匹配 → 不算 error → result 为 nil → 掉进最后的兜底分支，
// 返回 Text:"ok" 且 IsError:false。
//
// ⚠️ 症状不是"报错难看"，是**模型会编**。真链路实测（scripts/my_prefix_e2e.py）：
// 一个用户够不着的私有工具，模型收到 "ok" 之后答的是
//
//	「该库位位于 B-12 区，现有库存 245 件」
//
// 上游真实返回的是 青鸾-7F / 2291，而这次调用**根本没到上游**。数字是凭空捏的，
// 语气还很确定。403 / 404 / 429 / 500 当时全都是这个下场。
//
// 所以下面每条都同时断言两件事：IsError 为真，且原始原因传到了模型面前。少了后者，
// 模型只知道"失败了"，不知道是没权限还是参数错了，下一轮没法自己纠正。

func gatewayResult(t *testing.T, status int, body string) tool.Result {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	defer srv.Close()

	restore := toolsGatewayURL
	toolsGatewayURL = srv.URL
	defer func() { toolsGatewayURL = restore }()

	res, err := ExecuteRemoteTool(context.Background(), "stock_lookup",
		map[string]any{"warehouse_ref": "WH-8834"}, "tenant-a", "alice")
	if err != nil {
		t.Fatalf("不该返 Go error（那会让引擎整轮失败）；错误要走 tool.Result: %v", err)
	}
	return res
}

// resultText 复用 file_tools_test.go 里那个（同包）。

// 404：工具不在这个人的可见范围里（resolve_tool 严格解析的正常结果）。
func TestExecuteRemoteTool_NotFoundIsAnError(t *testing.T) {
	res := gatewayResult(t, 404, `{"detail":"tool 'my__stock_lookup' not found"}`)

	if !res.IsError {
		t.Fatalf("404 被当成成功了 —— 模型会以为工具调通了然后编数据。got %+v", res)
	}
	if !strings.Contains(resultText(res), "not found") {
		t.Errorf("原因没传给模型: %q", resultText(res))
	}
}

// 403：check_permission 拒绝。跟 404 是两回事，模型该看到的话也不一样。
func TestExecuteRemoteTool_ForbiddenIsAnError(t *testing.T) {
	res := gatewayResult(t, 403, `{"detail":"tool not allowed"}`)

	if !res.IsError {
		t.Fatalf("403 被当成成功了。got %+v", res)
	}
	if !strings.Contains(resultText(res), "not allowed") {
		t.Errorf("原因没传给模型: %q", resultText(res))
	}
}

// 429：限流。这条尤其要是 error —— 静默成 ok 的话模型不会重试，而这是**可恢复**的。
func TestExecuteRemoteTool_RateLimitIsAnError(t *testing.T) {
	res := gatewayResult(t, 429, `{"detail":"rate limit exceeded"}`)

	if !res.IsError {
		t.Fatalf("429 被当成成功了 —— 可恢复的错误被吞了。got %+v", res)
	}
	if !strings.Contains(resultText(res), "rate limit") {
		t.Errorf("原因没传给模型: %q", resultText(res))
	}
}

// 422：pydantic 的请求体校验，detail 是**数组**不是字符串。
//
// 单独一条是因为它的形状跟上面三条不同，而且是模型自己能修的那种错 ——
// 摊平成 msg 之后它下一轮能照着改参数。
func TestExecuteRemoteTool_ValidationDetailListIsFlattened(t *testing.T) {
	res := gatewayResult(t, 422,
		`{"detail":[{"loc":["body","params","warehouse_ref"],"msg":"field required","type":"missing"}]}`)

	if !res.IsError {
		t.Fatalf("422 被当成成功了。got %+v", res)
	}
	if !strings.Contains(resultText(res), "field required") {
		t.Errorf("数组形状的 detail 没摊平: %q", resultText(res))
	}
}

// 500 且 body 不是我们认识的形状 —— 也不能变成 ok。
//
// 兜底要带上状态码和 body 开头：空错误信息比错误本身更难查。
func TestExecuteRemoteTool_UnknownErrorBodyStillErrors(t *testing.T) {
	res := gatewayResult(t, 500, `<html><body>502 Bad Gateway</body></html>`)

	if !res.IsError {
		t.Fatalf("500 被当成成功了。got %+v", res)
	}
	if txt := resultText(res); !strings.Contains(txt, "500") {
		t.Errorf("兜底信息里没有状态码，查起来没线索: %q", txt)
	}
}

// 正向那一格 —— 没有它，上面五条全靠"永远返 IsError"也能绿。
func TestExecuteRemoteTool_SuccessIsNotAnError(t *testing.T) {
	res := gatewayResult(t, 200, `{"status":"success","result":{"zone":"青鸾-7F","quantity":2291}}`)

	if res.IsError {
		t.Fatalf("正常返回被当成错误了。got %+v", res)
	}
	if len(res.Content) == 0 {
		t.Fatal("成功但没有内容")
	}
}

// 200 + 空 result 仍然是 "ok"，**不是**错误。
//
// 这一格是边界：修状态码那件事很容易顺手把"成功但无内容"也变成错误，而那是有些
// 工具的正常返回（写入类工具没有输出）。
func TestExecuteRemoteTool_EmptyResultOnTwoHundredStaysOK(t *testing.T) {
	res := gatewayResult(t, 200, `{"status":"success"}`)

	if res.IsError {
		t.Fatalf("200 且无内容被误判成错误了。got %+v", res)
	}
	if resultText(res) != "ok" {
		t.Errorf(`want "ok", got %q`, resultText(res))
	}
}
