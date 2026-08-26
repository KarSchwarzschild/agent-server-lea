package session

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

// ErrPlanModeUnsupported 表示这是个 handoff 团，而计划模式在接力链上跑不起来。
// 调用方应当回 400 —— 请求本身不成立，不是服务端出错，重试也不会变好。
var ErrPlanModeUnsupported = errors.New("plan mode is not supported for handoff teams")

// planSupported 判断这个会话能不能跑计划模式。Send 在做任何事之前先问它。
//
// 只挡 handoff，不挡别的 —— **计划模式是所有 agent 都支持的能力**，不是配置出来的。
// 要不要进规划态由每次请求带来（SendRequest.plan_mode），跟 agent 怎么配没关系。
//
// 也不挡星型。星型团的团长有常驻 engine，它就是那个"主 agent"，
// ask_expert 相当于派活给子代理 —— 主 agent 规划、批准后再派活，这个形状是成立的。
// handoff 不成立，三条各自都足够：
//
//  1. s.engine 是 nil（每一棒都是 engine.RunChain 现建的），而 SetPlanMode 会解
//     引用它 —— 实测 panic，且 Send 的 goroutine 里没有 recover，整个进程跟着走。
//  2. 引擎的 Config **没有** PlanMode 字段（它是 repl 会话上的运行时原子量），而每条
//     leg 由 deriveExpertBase 从 Config 派生，所以计划模式传不下去。
//  3. save_plan 走 Options.ExtraTools，而 deriveExpertBase 里有一句
//     `opts.ExtraTools = nil` —— 链上没有任何一棒拿得到它。
//
// 概念上也重复：接力链的拓扑本身就是一份计划，配置时就定好了。
//
// 星型的专家**会**跟着团长进规划态，但走的不是 2 那条路：我们自己注入了
// engine.Options.Permissions（一个会话一个 *permissions.Service），而
// deriveExpertBase 按值复制 Options 时把这个指针原样带下去，所以团长
// SetPlanMode 之后专家用的是同一个服务。见 runtime.NewPermissionService。
//
// 还没跟下去的是 prompt 那半（3 仍然成立）：专家看不到"你在规划态"那段文字，
// 只会在真去写东西时撞上拒绝。行为是对的，只是它得多绕一轮才知道。
//
// 抽成方法是为了能单独测：从 Send 那一头测，"放行"这一格会让它继续往下起 goroutine，
// 而一个手搓的假会话在那里会因为别的 nil 字段炸掉 —— 那种失败跟被测的规则无关。
func (s *AgentSession) planSupported(plan PlanControl) error {
	if plan.Mode && s.isHandoff {
		return ErrPlanModeUnsupported
	}
	return nil
}

// planHint 渲染每轮拼在用户消息前面的那一行计划摘要。没有已批准的计划时返回空串。
//
// 它替代了以前那种"把计划全文每轮拼一遍"的做法。设计上要满足两件事：
//
//   - **短**。它会跟着每一轮的 user 消息落库，所以在历史里是 O(轮数) 份。三十来个
//     token 堆二十份无所谓，一千五百个就不行了。
//   - **足以让模型知道该去拉**。纯粹的"有一份计划"不够，模型不会主动去查；带上进度
//     和当前这步，它才有理由在需要细节时调 get_plan。
//
// 没有结构化步骤的计划（老数据，或模型提交时没给 steps）走降级分支：只给指针，
// 不谎报一个 0/0 的进度。
func planHint(p persistence.Plan) string {
	if p.Approved == "" {
		return ""
	}
	done, total := p.Progress()
	if total == 0 {
		return "[执行计划] 本会话有一份已批准的执行计划。需要具体内容时调 get_plan。"
	}

	hint := fmt.Sprintf("[执行计划] 已批准，共 %d 步，已完成 %d 步", total, done)
	if cur := p.CurrentStep(); cur != "" {
		hint += fmt.Sprintf("，当前：%s", cur)
	}
	hint += "。\n全文用 get_plan 查看；每步开始/完成时用 update_plan_step 标记。" +
		"计划需要改动时用 save_plan 提交修订版，再用 AskUser 请用户重新审批。"
	return hint
}

// applyPlanAction 把请求里的 plan_action 翻译成计划表上的状态流转。
//
// 这是"用户批准了"这个信号**唯一**的入口。之所以要显式字段，是因为它以前是推断
// 出来的：老实现在 plan_mode=false 时无条件 ActivatePlan，也就是把"用户发了一条
// 普通消息"当成"用户批准了计划"。这两件事没有关系 —— 用户回一句"算了不用了"同样
// 是 plan_mode=false，于是那份被否掉的计划反而被激活，并注入到之后的每一轮。
//
// 三个取值各自独立，不是一条流水线：
//
//	""        没表态。绝大多数消息都是它，什么都不做。待批准的留着（不注入），
//	          已批准的也留着（继续注入）—— 执行阶段每轮都是这一支。
//	approve   批准。draft → approved，下一轮起注入。没有待批准的计划时是 no-op
//	          （用户重复点、或请求重发，都不该报错）。
//	reject    否决这一版修订，已批准的那份原样留着继续执行。
//
// plan.Mode 在这里**故意不参与判断**。批准和"这一轮跑在哪个态"是两个维度：用户点
// "同意"时前端发 Mode=false+approve，而用户在执行中途切回规划是 Mode=true+""，
// 那时旧计划应当原样留着。把两者绑起来就是又造一次刚拆掉的那种耦合。
//
// 返回错误只用于记日志 —— 计划流转失败不该让这一轮对话失败。
func applyPlanAction(ctx context.Context, db *sql.DB, tenantID, sessionID string, plan PlanControl) error {
	switch plan.Action {
	case PlanActionApprove:
		if err := persistence.ApprovePlan(ctx, db, tenantID, sessionID); err != nil {
			return err
		}
		// 回读一次再记日志。ApprovePlan 是 UPDATE ... WHERE draft_content IS NOT NULL，
		// 没有待批准的计划时它成功且什么都没干 —— 无条件打 "plan_approved" 就是在日志
		// 里声明一件没发生的事，而这一支恰好是排查"用户点了同意但计划没生效"时第一个
		// 会看的地方。多一次 SELECT 不心疼：approve 一份计划只发生一次。
		//
		// 区分不了"刚转正"和"本来就有"，但排查时有用的是"现在到底有没有"。
		switch p, err := persistence.LoadPlan(ctx, db, tenantID, sessionID); {
		case err != nil:
			log.Printf("plan_approved session=%s (回读失败，无法确认: %v)", sessionID, err)
		case p.Approved == "":
			log.Printf("plan_approve_noop session=%s —— 没有待批准的计划", sessionID)
		default:
			_, total := p.Progress()
			log.Printf("plan_approved session=%s bytes=%d steps=%d", sessionID, len(p.Approved), total)
		}
		return nil

	case PlanActionReject:
		// 只丢掉待批准的那份，已批准的原样留着 —— 用户的意思是"这版修订我不要，
		// 按原计划继续"，不是"整个计划都别要了"。后者是 ClearPlan，走会话删除
		// 或者以后真出现"放弃计划"这个动作时再接。
		if err := persistence.RejectPlan(ctx, db, tenantID, sessionID); err != nil {
			return err
		}
		log.Printf("plan_rejected session=%s", sessionID)
		return nil

	default:
		// 包含 PlanActionNone 和任何无法识别的值。无法识别时按"没表态"处理而不是
		// 报错：这个字段来自客户端，一个拼错的值不该把用户的消息卡住，而"什么都
		// 不做"在这里恰好是安全的那一边（不会激活也不会删）。
		return nil
	}
}
