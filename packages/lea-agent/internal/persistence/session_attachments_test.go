package persistence

import "testing"

// 挂载指纹 —— 多副本正确性靠它。
//
// ## 它修的那个 bug
//
// 一开始只有 Manager.Drop：挂载接口落库后丢掉内存对象，下条消息重建。**多副本下那是
// 错的**，真链路才暴露（scripts/session_attach_e2e.py，nginx 日志实录）：
//
//	06:16:32  POST /attachments  → 副本 A     A 内存里没这个会话，Drop 直接返回成功
//	06:16:51  POST /chat/stream  → 副本 B     B 手里那个旧引擎对象还在，工具集是旧的
//
// 结果：挂载"成功"了，工具却不出现。而 nginx 轮询让这件事一半概率发生 —— 所以第一次
// 跑 e2e 时判据 ① 失败、判据 ③ 通过，同一个机制一次成一次不成。
//
// 指纹让每个副本各自发现变化：GetOrCreate 缓存命中时重算一遍，跟建会话时记下的比，
// 不一样就重建。不依赖任何跨副本消息投递。

func rows(pairs ...any) []SessionAttachment {
	out := make([]SessionAttachment, 0, len(pairs)/2)
	for i := 0; i < len(pairs); i += 2 {
		out = append(out, SessionAttachment{Kind: pairs[i].(string), RefID: pairs[i+1].(int)})
	}
	return out
}

func TestFingerprint_EmptyIsStable(t *testing.T) {
	// 绝大多数会话什么都没挂 —— 这一格必须稳定，否则每条消息都重建一次。
	if Fingerprint(nil) != Fingerprint([]SessionAttachment{}) {
		t.Error("nil 和空切片的指纹该一样")
	}
	if Fingerprint(nil) != "" {
		t.Errorf("空挂载的指纹该是空串，得到 %q", Fingerprint(nil))
	}
}

func TestFingerprint_DetectsAddition(t *testing.T) {
	before := Fingerprint(rows("tool", 1))
	after := Fingerprint(rows("tool", 1, "tool", 2))
	if before == after {
		t.Error("加了一个挂载，指纹必须变 —— 不变就是那个多副本 bug")
	}
}

func TestFingerprint_DetectsRemoval(t *testing.T) {
	before := Fingerprint(rows("tool", 1, "tool", 2))
	after := Fingerprint(rows("tool", 1))
	if before == after {
		t.Error("摘掉一个挂载，指纹必须变")
	}
}

func TestFingerprint_DetectsSwap(t *testing.T) {
	// 数量没变但内容变了 —— 用计数当指纹就会漏掉这种。
	a := Fingerprint(rows("tool", 1))
	b := Fingerprint(rows("tool", 2))
	if a == b {
		t.Error("换成另一个工具，指纹必须变（别用数量当指纹）")
	}
}

func TestFingerprint_DistinguishesKind(t *testing.T) {
	// tool:5 和 kb:5 是两个完全不同的东西，ref_id 撞号很正常。
	if Fingerprint(rows("tool", 5)) == Fingerprint(rows("kb", 5)) {
		t.Error("kind 必须参与指纹，否则 tool:5 换成 kb:5 检测不到")
	}
}

func TestFingerprint_IsOrderIndependent(t *testing.T) {
	// **这条是防误报的**：同一组挂载因为 SELECT 返回顺序不同就算"变了"的话，
	// 每条消息都会白重建一次 —— 而重建要打一次 lea-config 的 HTTP 加一次引擎构造。
	//
	// LoadSessionAttachments 确实有 ORDER BY id，但依赖它等于让指纹的稳定性取决于
	// 另一个函数的实现细节；哪天有人加个 WHERE 或换个索引，这里就静默退化成"每轮重建"。
	a := Fingerprint(rows("tool", 2, "kb", 7, "tool", 1))
	b := Fingerprint(rows("kb", 7, "tool", 1, "tool", 2))
	if a != b {
		t.Errorf("顺序不该影响指纹:\n  %q\n  %q", a, b)
	}
}

func TestFingerprint_SameSetIsStable(t *testing.T) {
	// 同一组算两次要一样 —— 听着显然，但如果实现里掺了时间戳或者 map 迭代，就不成立。
	set := rows("tool", 3, "kb", 9)
	if Fingerprint(set) != Fingerprint(set) {
		t.Error("同一组挂载两次算出不同指纹")
	}
}

func TestFingerprint_NoCollisionOnAdjacentIDs(t *testing.T) {
	// 拼接不当会撞：{tool:1, tool:12} 和 {tool:11, tool:2} 如果直接连成 "112"
	// 就分不开了。分隔符必须真的分隔。
	a := Fingerprint(rows("tool", 1, "tool", 12))
	b := Fingerprint(rows("tool", 11, "tool", 2))
	if a == b {
		t.Errorf("相邻 id 组合撞了: %q", a)
	}
}

func TestFingerprint_DistinguishesSkillFromTool(t *testing.T) {
	// skill:5 和 tool:5 是两个完全不同的东西。挂载 kind 加一种就要在这儿钉一次 ——
	// 漏了的话"把挂的工具换成同 id 的 skill"检测不到，会话不重建。
	if Fingerprint(rows("skill", 5)) == Fingerprint(rows("tool", 5)) {
		t.Error("skill:5 和 tool:5 的指纹不该相同")
	}
}

// ── 认识的 kind ────────────────────────────────────────────────────────────

func TestKnownAttachmentKinds_MatchesTheMap(t *testing.T) {
	// 这个列表只给 400 的错误信息用，但它必须跟真正的判据同源。手写那份在加 skill 时
	// 就过时了：提示里只有 tool/kb，实际已经收 skill —— 用户照着提示做反而是错的。
	got := KnownAttachmentKinds()
	if len(got) != len(SessionAttachmentKinds) {
		t.Fatalf("数量不一致: %v vs map 里 %d 个", got, len(SessionAttachmentKinds))
	}
	for _, k := range got {
		if !SessionAttachmentKinds[k] {
			t.Errorf("%q 不在 map 里", k)
		}
	}
}

func TestKnownAttachmentKinds_IsSorted(t *testing.T) {
	// map 迭代顺序随机，不排序的话同一个 400 每次措辞不一样 —— 而错误信息进日志、
	// 进前端提示，飘动的文案没法被任何东西断言。
	got := KnownAttachmentKinds()
	for i := 1; i < len(got); i++ {
		if got[i-1] > got[i] {
			t.Errorf("没排序: %v", got)
			break
		}
	}
}

func TestKnownAttachmentKinds_IncludesSkill(t *testing.T) {
	for _, k := range KnownAttachmentKinds() {
		if k == "skill" {
			return
		}
	}
	t.Error("skill 不在认识的 kind 里 —— 挂载接口会 400")
}
