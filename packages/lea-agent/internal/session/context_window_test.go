package session

import "testing"

// contextBudget 把"模型窗口"换算成引擎的 MaxContextTokens。
//
// 这个文件盯三件事，都属于"错了不会报错，只会表现为别的症状"那一类：
//
//	永不为零    0 会让历史回收整个不跑，而且是静默的
//	兜底取小    不知道模型时猜大 = 在 provider 处整轮失败
//	换算对齐    MaxContextTokens 不是触发线，它 × 0.8 才是

// 0 的后果：repl/session.go 里 `if s.maxContextTokens <= 0 { return working, false }`
// 直接返回，于是 Reduce 和 Compact 两道全不跑，五个文件工具声明的 ResultRetention
// 一次都不会被调用。不编译失败、不报错，只表现为"上下文莫名其妙涨得快"。
//
// 注意 1 和 100 这两格：它们乘上 0.875 之后向下取整就是 0，也就是**一个手滑填小的
// 配置不会让压缩变激进，会让它彻底消失**。这条测试写出来的第一版就是被这一格抓到的。
func TestContextBudgetNeverZero(t *testing.T) {
	for _, window := range []int{0, -1, 1, 100, 4_095, 32_000, 258_048, 1_000_000} {
		if got := contextBudget(window, costCeilingTokens); got <= 0 {
			t.Errorf("contextBudget(%d, %d) = %d，必须为正 —— 0 会让历史回收整个不跑",
				window, costCeilingTokens, got)
		}
	}
}

// 小得离谱的窗口和"没配"必须走同一条兜底路径。
//
// 分开处理的话这个函数在边界上是反直觉的：填 0 得到保守兜底，填 1 得到压缩关闭 ——
// 后者比前者危险得多，却看起来像个"更具体"的配置。
func TestImplausiblySmallWindowIsTreatedAsUnconfigured(t *testing.T) {
	unconfigured := contextBudget(0, 0)
	for _, window := range []int{1, 100, minPlausibleWindow - 1} {
		if got := contextBudget(window, 0); got != unconfigured {
			t.Errorf("窗口 %d = %d，期望跟未配置一样走兜底 %d", window, got, unconfigured)
		}
	}
}

// 配置没给窗口时走兜底，而且兜底必须**小于**一个真实大模型的窗口。
//
// 方向很要紧：兜底生效 = "我们不知道这是什么模型"。那时猜大的代价是历史加载到超过
// 模型能吃的量、在 provider 处整轮失败（用户可见）；猜小的代价只是早压一次。
func TestUnknownModelFallsBackConservatively(t *testing.T) {
	unknown := contextBudget(0, 0)
	big := contextBudget(258_048, 0)

	if unknown >= big {
		t.Errorf("未知模型拿到 %d，不应大于等于已知大模型的 %d", unknown, big)
	}
	if want := int(float64(fallbackContextWindow) * engineBudgetRatio); unknown != want {
		t.Errorf("未知模型 = %d，期望走 fallbackContextWindow → %d", unknown, want)
	}
}

// 压缩必须落在真实窗口的 ~70%。
//
// 这条是整个换算存在的理由，也是最容易被下一个人"顺手改简单"改坏的地方：
// MaxContextTokens **不是**触发线。引擎在 MaxContextTokens × 0.8 处才压缩
// （compactionThresholdRatio，引擎的包级常量，我们改不了），所以要让触发线落在 70%，
// 传给引擎的数必须是窗口 × 0.7/0.8 = 0.875。
//
// 把 engineBudgetRatio 直接写成 0.7 的话，实际触发线会掉到 56% —— 压得过早，
// 白花摘要钱，而且没有任何报错。
func TestCompactionLandsAtSeventyPercentOfWindow(t *testing.T) {
	const engineCompactionRatio = 0.8 // repl/session.go 的 compactionThresholdRatio

	for _, window := range []int{32_000, 200_000, 258_048, 1_000_000} {
		budget := contextBudget(window, 0)
		trigger := float64(budget) * engineCompactionRatio
		pct := trigger / float64(window)

		if pct < 0.68 || pct > 0.72 {
			t.Errorf("窗口 %d：预算 %d → 触发线 %.0f = 窗口的 %.1f%%，期望 ~70%%",
				window, budget, trigger, pct*100)
		}
	}
}

// 成本闸比窗口小时，按闸来。
func TestCostCeilingCapsTheBudget(t *testing.T) {
	const ceiling = 128_000
	capped := contextBudget(1_000_000, ceiling)
	want := int(float64(ceiling) * engineBudgetRatio)

	if capped != want {
		t.Errorf("窗口 1M + 闸 %d = %d，期望 %d", ceiling, capped, want)
	}
}

// 成本闸比窗口大，或者为 0（不设闸），都不该影响结果。
//
// 0 那一格是现在的生产配置：闸位留着但空着，所以预算完全跟着模型窗口走。
func TestCostCeilingIsIgnoredWhenUnsetOrLarger(t *testing.T) {
	byWindow := contextBudget(258_048, 0)

	if got := contextBudget(258_048, 1_000_000); got != byWindow {
		t.Errorf("闸比窗口大时 = %d，期望不影响结果 %d", got, byWindow)
	}
	if got := contextBudget(258_048, -1); got != byWindow {
		t.Errorf("闸为负时 = %d，期望不影响结果 %d", got, byWindow)
	}
}

// 大窗口模型必须真的拿到更大的预算 —— 这是"窗口从配置来"这件事的意义所在。
//
// 换掉的那版是按模型名前缀查表的，qwen3-max 和 glm-5.1 都落进同一个 128_000 缺省，
// 于是一个 258k 窗口的模型和一个 200k 窗口的模型拿到一样的预算，而且换模型要改代码。
func TestBiggerWindowGetsBiggerBudget(t *testing.T) {
	small := contextBudget(32_000, 0)
	mid := contextBudget(200_000, 0)
	big := contextBudget(1_000_000, 0)

	if !(small < mid && mid < big) {
		t.Errorf("预算没有随窗口单调增：32k→%d, 200k→%d, 1M→%d", small, mid, big)
	}
}
