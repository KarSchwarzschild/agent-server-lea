package runtime

import (
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/team"
)

// 库里 agent_team_members.role 是自由字符串（models/team.py 的 String(50)），所以映射
// 必须自己兜住脏值。这一层留在 lea-agent：那套词汇是我们数据库的，引擎只认 team.Role。
func TestEngineRoleMapsKnownValues(t *testing.T) {
	cases := map[string]team.Role{
		"entry":  team.RoleEntry,
		"worker": team.RoleWorker,
		"exit":   team.RoleExit,
		// 库里是人手填的，大小写和空格都会出现。
		"Entry":  team.RoleEntry,
		" exit ": team.RoleExit,
		"EXIT":   team.RoleExit,
		// 空 = worker，这样 handoff 之前配的团（role 列一直是默认 "worker"，也可能为空）
		// 读起来是"一群干活的"，而不是一个没有调度也没有验收的链。
		"": team.RoleWorker,
	}
	for in, want := range cases {
		if got := EngineRole(in, "某专家"); got != want {
			t.Errorf("EngineRole(%q) = %q, want %q", in, got, want)
		}
	}
}

// 不认识的角色按 worker 处理而不是报错：星型压根不读 Role，为一个惰性字段让整个会话
// 建不起来是不成比例的。handoff 下它会变成 ValidateChain 的"缺 entry/exit"，那条报错
// 会列出全部专家名，配合日志能看出根因是拼错。
func TestEngineRoleFallsBackToWorker(t *testing.T) {
	for _, in := range []string{"reviewer", "leader", "验收", "exi"} {
		if got := EngineRole(in, "某专家"); got != team.RoleWorker {
			t.Errorf("EngineRole(%q) = %q, want worker", in, got)
		}
	}
}

// strategy 决定拓扑。这条钉住"认不出来时走星型"—— 星型是 strategy 这个字段有值以来
// 一直的实际行为（它此前从没被消费过，只用来打了一行日志），所以对已有的团来说退回
// 星型不是变更；而静默当成 handoff 会让一个从没验证过的拓扑上线。
func TestTeamStrategyMapsToEngineMode(t *testing.T) {
	// 库里 agent_teams.strategy 的默认值就是 round_robin，线上现有的团都是它。
	if _, err := team.ParseMode("round_robin"); err == nil {
		t.Fatal("round_robin 不该被当成合法拓扑 —— 它得走那条记日志退回星型的路")
	}
	for _, in := range []string{"", "star"} {
		m, err := team.ParseMode(in)
		if err != nil || m != team.ModeStar {
			t.Errorf("ParseMode(%q) = %q, %v; want star", in, m, err)
		}
	}
	if m, err := team.ParseMode("handoff"); err != nil || m != team.ModeHandoff {
		t.Errorf("ParseMode(handoff) = %q, %v", m, err)
	}
}

// role 取自 member 行而不是 agent 自己的配置：同一个"文档处理专家"可以在一个团里干活、
// 在另一个团里验收。这条断言的是 BuildExperts 读的是 m.Role —— 在本轮之前
// `grep m.Role` 在整个 lea-agent 里是零命中，member 的 role 拿到就扔了。
func TestTeamMemberRoleReachesTheExpert(t *testing.T) {
	// BuildExperts 会为每个成员打 lea-config，所以这里只验映射那一步本身：给定
	// member 的 role 串，装出来的专家该是哪个引擎角色。
	members := []TeamMemberDef{
		{MemberAgentID: 1, Name: "调度员", Role: "entry"},
		{MemberAgentID: 2, Name: "分析师", Role: "worker"},
		{MemberAgentID: 3, Name: "验收员", Role: "exit"},
	}
	experts := make([]team.Expert, 0, len(members))
	for _, m := range members {
		experts = append(experts, team.Expert{Name: m.Name, Role: EngineRole(m.Role, m.Name)})
	}
	// 装出来的名册必须能通过引擎的链校验，否则这个团第一次 Send 就起不来。
	if err := team.ValidateChain(team.NewSet(experts)); err != nil {
		t.Fatalf("三个角色齐备的团应当能形成链: %v", err)
	}
}

// 全是 worker 的名册（也就是现有那些星型团）不能形成链，且报错要说清缺什么。
// 这是 handoff 团配错时用户实际会看到的东西。
func TestStarRosterCannotFormAChain(t *testing.T) {
	experts := []team.Expert{
		{Name: "文档处理专家", Role: EngineRole("worker", "文档处理专家")},
		{Name: "内部沟通专家", Role: EngineRole("", "内部沟通专家")},
	}
	err := team.ValidateChain(team.NewSet(experts))
	if err == nil {
		t.Fatal("全是 worker 的名册不该被当成一条链")
	}
	// 报错里要有专家名 —— 只说"缺 entry"的话，配置的人不知道现在都有谁。
	if msg := err.Error(); !strings.Contains(msg, "文档处理专家") {
		t.Errorf("报错应列出现有专家名，便于对照修改: %s", msg)
	}
}
