package runtime

import (
	"net/http"
	"os"
)

// 服务间鉴权的调用方一侧：给所有发往内网服务的请求自动带上 X-Internal-Token。
//
// ── 为什么用 Transport 而不是在每个调用点加一行 ──────────────────────────
//
// 这个包里往 lea-tools / lea-rag / lea-config 发请求的地方有八处以上，散在
// gateway.go、file_tools.go、code_interpreter.go、platform_tools.go、
// skill_provision.go、memory_middleware.go、config_loader.go 里。
//
// 逐个加头的问题不是麻烦，是**漏掉的那个只在开启鉴权之后才炸**，而且炸的是
// 某一条具体功能（比如只有技能投递失败），排查时很难联想到鉴权。而 Transport
// 是所有出站请求的必经之路，现在和将来新增的调用点都自动覆盖。
//
// ── 没配 token 时不带头 ──────────────────────────────────────────────────
//
// 跟被调方（lea_tools/internal_auth.py）对称：token 没配就当这套机制不存在。
// 这让上线顺序无关紧要 —— 滚动更新期间两边版本共存，任一侧先出去都不会断链。
// 两边都发布之后再配上密钥，最后把服务端的 LEA_REQUIRE_INTERNAL_TOKEN 打开。
const InternalTokenHeader = "X-Internal-Token"

type internalTokenTransport struct {
	token string
	base  http.RoundTripper
}

func (t *internalTokenTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	if t.token == "" {
		return base.RoundTrip(req)
	}
	// RoundTripper 的契约要求不得修改传入的 Request —— 调用方可能还要复用它
	// （重试、日志）。所以浅拷一份再改 Header。Header 本身也要拷，不然改的还是
	// 同一个 map。
	clone := req.Clone(req.Context())
	clone.Header.Set(InternalTokenHeader, t.token)
	return base.RoundTrip(clone)
}

// init 把本包所有共享 client 的 Transport 换掉。
//
// 包级 var 在 init 之前就初始化好了，所以这里拿到的五个 client 都是非 nil 的。
// 它们各自的 Timeout 不动 —— 那些值是按各条链路的真实耗时调过的
// （沙箱冷启动 180s、工具执行 120s、读配置 10s），换 Transport 不影响。
//
// 注意：router 包里 /api/user/agents 那个反向代理不走这里 —— 它用的是
// httputil.ReverseProxy，不是这几个 client。lea-config 已经开了鉴权，所以那处
// 在 router/proxy.go 的 Director 里单独设了同一个头（用的是上面导出的常量）。
func init() {
	token := os.Getenv("LEA_INTERNAL_TOKEN")
	if token == "" {
		return
	}
	for _, c := range []*http.Client{
		httpClient,
		sandboxCreateClient,
		gatewayClient,
		fileToolClient,
		skillProvisionClient,
	} {
		c.Transport = &internalTokenTransport{token: token, base: c.Transport}
	}
}
