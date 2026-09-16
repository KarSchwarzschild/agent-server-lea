package session

import (
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

// rebuildFingerprint 把"挂载 + 激活 agent"两类重建输入压成一个可比较串。这些用例锁住三条
// 契约，任何一条破了都会让"切专家触发重建"或"默认会话不白重建"回归：
//
//  1. 激活 agent 进指纹 —— 同挂载、不同 agent 必须得到不同指纹（否则切专家不触发重建）。
//  2. 用 raw 值（0=默认）而不是解析后的 cfg.ID —— 默认态请求带 0、cfg.ID 是具体 super-agent
//     主键，若混用，默认会话第二条消息就会假变化、白重建一次。
//  3. 挂载部分复用 persistence.Fingerprint（已排序、稳定），agent 只是追加后缀。
func TestRebuildFingerprint(t *testing.T) {
	rows := []persistence.SessionAttachment{
		{Kind: "skill", RefID: 3},
		{Kind: "tool", RefID: 1},
	}

	// 契约 1：同挂载、不同 agent → 指纹必须不同。
	if fpA, fpB := rebuildFingerprint(rows, 7), rebuildFingerprint(rows, 8); fpA == fpB {
		t.Fatalf("同挂载不同 agent 指纹相同 (%q) —— 切专家将不触发重建", fpA)
	}

	// 契约 2：默认(0) 与任何具体 id 都不同；且 0 就是字面 0，不被归一成别的。
	if rebuildFingerprint(rows, 0) == rebuildFingerprint(rows, 1) {
		t.Fatal("默认(0) 与 agent 1 指纹相同 —— raw 值没进指纹")
	}
	if got := rebuildFingerprint(nil, 0); got != "|a:0" {
		t.Fatalf("空挂载 + 默认 应为 %q，得到 %q", "|a:0", got)
	}

	// 契约 3：agent 部分是追加在挂载指纹之后的稳定后缀。
	if got := rebuildFingerprint(rows, 7); !strings.HasSuffix(got, "|a:7") {
		t.Fatalf("指纹 %q 没有以 |a:7 结尾", got)
	}
	// 挂载部分与 persistence.Fingerprint 一致（顺序无关，已在其内部排序）。
	if got := rebuildFingerprint(rows, 7); !strings.HasPrefix(got, persistence.Fingerprint(rows)) {
		t.Fatalf("指纹 %q 的挂载前缀与 persistence.Fingerprint 不一致", got)
	}

	// 同一组输入两次必须相等（可比较性的地基）。
	if rebuildFingerprint(rows, 7) != rebuildFingerprint(rows, 7) {
		t.Fatal("相同输入产生了不同指纹")
	}
}
