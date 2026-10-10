package persistence

import (
	"context"
	"database/sql"
	"sort"
	"strconv"
	"strings"
)

// 会话级挂载 —— 用户在**这一个会话里**临时挂上的工具/知识库（migrations/017）。
//
// ⚠️ 不要跟同包的 attachments.go 搞混：那个是**聊天附件**（用户上传的文件，走
// chat_attachments 表），跟这里毫无关系。两者英文都叫 attachment，所以文件名和类型名
// 都带 SessionAttachment 前缀区分。
//
// 跟 agent 的绑定也是两回事：
//
//	agent_tool_bindings   agent 定义的一部分，所有使用者相同，改它 = 改这个 agent
//	session_attachments   只属于一个会话，别人用同一个 agent 完全不受影响
//
// ## 为什么要落库而不是只留在内存
//
// lea-agent 会回收最久未活跃的闲置会话（session/manager.go 的 ensureCapacity）。回收
// 不丢对话（历史在 agent_messages），但只存在于内存对象里的挂载会没：
//
//	10:00 挂上 → 12:30 会话闲置被回收 → 13:00 回来发消息 → 「我没有查库存的工具」
//
// 用户什么都没做错，上午还在用，而且没有任何提示。

// SessionAttachment 是 session_attachments 的一行。
//
// mcp_server 那种解析出来是**一批 tool** —— 挂一个 MCP server 等于挂它同步出来的全部
// 工具行（粒度跟主流做法一致：MCP 的心智模型是"接一个服务"）。所以 resolvedAttachments
// 里没有 mcp 这一类，它们混在 Tools 里回来。
type SessionAttachment struct {
	Kind  string // tool | kb | skill | mcp_server
	RefID int
}

// Fingerprint 把一组挂载压成一个可比较的字符串。
//
// 用途：判断"内存里这份会话是不是按当前的挂载集合建的"。会话建立时记下指纹，下次
// GetOrCreate 命中缓存时重算一遍，不一样就说明有人改过挂载，得重建。
//
// ## 为什么需要它 —— 多副本
//
// 一开始只有 Manager.Drop：挂载接口落库之后丢掉内存对象，下条消息重建。**那在多副本
// 下是错的**，而且是真链路才暴露的（scripts/session_attach_e2e.py）：
//
//	06:16:32  POST /attachments  → 副本 A     A 内存里没这个会话，Drop 直接返回成功
//	06:16:51  POST /chat/stream  → 副本 B     B 手里那个旧引擎对象还在，工具集是旧的
//
// nginx 轮询，两条请求本来就会落到不同副本。Drop 只能丢**本副本**的。
//
// ## 为什么不走 control 总线
//
// internal/control 正是为这类跨副本动作建的（停 turn 就走它）。但它基于 Redis
// pub/sub，而那个包自己的注释写明了：**没有投递保证**，目标 pod 的订阅连接刚好在重连
// 窗口里，消息就没了。
//
// 对"停止"来说可以接受 —— 用户看得见没停下来，会再点一次。挂载**没有这个反馈回路**：
// 消息丢了的表现是工具永远不出现，用户不知道发生了什么，也不知道要重试。
//
// 所以这里用拉取式：每次 GetOrCreate 自己比一次指纹。代价是一个走
// idx_session 索引的小查询，而 ReloadHistory 本来每轮就要读一次库，多这一下可以忽略。
// 换来的是**自愈**：任何原因错过一次（副本重启、Drop 失败、消息丢失），下一条消息
// 照样会发现并修正。
func Fingerprint(rows []SessionAttachment) string {
	if len(rows) == 0 {
		return ""
	}
	parts := make([]string, 0, len(rows))
	for _, r := range rows {
		parts = append(parts, r.Kind+":"+strconv.Itoa(r.RefID))
	}
	// 排序，否则同一组挂载因为 SELECT 顺序不同就算"变了"，每条消息都白重建一次。
	// LoadSessionAttachments 有 ORDER BY id，但依赖它等于让指纹的稳定性取决于另一个
	// 函数的实现细节。
	sort.Strings(parts)
	return strings.Join(parts, ",")
}

//
// 表上那列是 VARCHAR 而不是 ENUM（见 migrations/017），所以库里可能出现这一版还不认识
// 的值 —— 那是故意留的：加一种新资源不该要求先改所有读端再排序部署。读到不认识的照常
// 传给 lea-config，由它决定丢弃，**这里不过滤**：过滤了就等于把"未知 kind"变成一个
// 静默的黑洞，而 lea-config 那边至少会在 dropped 里报出来。
var SessionAttachmentKinds = map[string]bool{
	"tool": true, "kb": true, "skill": true, "mcp_server": true,
}

// KnownAttachmentKinds 返回认识的 kind，排序后返回。
//
// 只给错误信息用。手写那份列表在加 skill 时就过时了（提示里只有 tool/kb，实际已经收
// skill），而 map 迭代顺序随机，不排序的话同一个 400 每次措辞不一样。
func KnownAttachmentKinds() []string {
	out := make([]string, 0, len(SessionAttachmentKinds))
	for k := range SessionAttachmentKinds {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// LoadSessionAttachments 读一个会话挂了什么。按 (tenant, session) 查 —— 索引就是这个形状。
//
// db 为 nil 时返回 nil（单元测试和没配库的本地开发都会走到），不是错误。
func LoadSessionAttachments(
	ctx context.Context, db *sql.DB, tenantID, sessionID string,
) ([]SessionAttachment, error) {
	if db == nil || sessionID == "" {
		return nil, nil
	}
	rows, err := db.QueryContext(ctx,
		`SELECT kind, ref_id FROM session_attachments
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY id`,
		tenantID, sessionID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []SessionAttachment
	for rows.Next() {
		var a SessionAttachment
		if err := rows.Scan(&a.Kind, &a.RefID); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// AddSessionAttachment 挂上一个。重复挂载是**幂等**的，不是错误。
//
// 用 INSERT ... ON DUPLICATE KEY UPDATE 而不是先查再插：并发两个请求挂同一个工具时，
// 先查再插会有一个撞唯一索引 500。而"重复点了一下挂载按钮"是个必然会发生的操作，
// 不该让用户看到错误。
//
// UPDATE 里只碰 user_id，因为主键三元组是 (session_id, kind, ref_id) —— 同一个会话里
// 同一个工具被"重新挂"时唯一可能变的就是操作者。created_at 保留首次的时间。
func AddSessionAttachment(
	ctx context.Context, db *sql.DB, tenantID, sessionID, userID, kind string, refID int,
) error {
	if db == nil {
		return nil
	}
	_, err := db.ExecContext(ctx,
		`INSERT INTO session_attachments (session_id, tenant_id, user_id, kind, ref_id)
		 VALUES (?, ?, ?, ?, ?)
		 ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)`,
		sessionID, tenantID, userID, kind, refID,
	)
	return err
}

// RemoveSessionAttachment 摘掉一个。返回是否真的删了行。
//
// 删不存在的行返回 (false, nil) 而不是错误 —— 调用方据此返 404 或者干脆当成功。
// 摘一个已经不在的东西，结果状态跟用户想要的完全一致。
func RemoveSessionAttachment(
	ctx context.Context, db *sql.DB, tenantID, sessionID, kind string, refID int,
) (bool, error) {
	if db == nil {
		return false, nil
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM session_attachments
		  WHERE tenant_id = ? AND session_id = ? AND kind = ? AND ref_id = ?`,
		tenantID, sessionID, kind, refID,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// ReplaceSessionAttachments 把这个会话的挂载集合整体替换成 desired。
//
// 跟上面 AddSessionAttachment/RemoveSessionAttachment 那条"逐条增删"的路径是
// 两种语义，服务两种不同的调用场景：
//
//	逐条增删   "挂载"/"摘除"两个独立按钮，一次只动一样，别的不受影响
//	整体替换   发消息时带的 attachments 字段——调用方认为的就是这条会话现在
//	          该有的完整清单，凡是不在这份清单里的旧挂载都要摘掉（见
//	          router.SendRequest.Attachments 的说明）
//
// 用事务做"先清空、再按 desired 重新插"，不是因为原子性有多重要（这张表没有
// 外键，中间态最多是短暂的"少几行"，见 017_session_attachments.sql），是为了
// 避免在替换过程中另一个并发请求看到一份不完整的中间结果。
//
// 未知 kind 静默丢弃，跟 LoadSessionAttachments 那边"读到不认识的照常往下传"
// 的宽容策略是同一条原则的两个方向：写入端拦，读取端放，理由见文件里
// SessionAttachmentKinds 那段说明。
func ReplaceSessionAttachments(
	ctx context.Context, db *sql.DB, tenantID, sessionID, userID string, desired []SessionAttachment,
) error {
	if db == nil {
		return nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx,
		`DELETE FROM session_attachments WHERE tenant_id = ? AND session_id = ?`,
		tenantID, sessionID,
	); err != nil {
		return err
	}

	// 去重：desired 里如果有重复的 (kind, ref_id)，唯一索引会让第二条 INSERT 报错——
	// 前端传重了不该变成一次 500。
	seen := make(map[string]bool, len(desired))
	for _, item := range desired {
		if !SessionAttachmentKinds[item.Kind] {
			continue
		}
		key := item.Kind + ":" + strconv.Itoa(item.RefID)
		if seen[key] {
			continue
		}
		seen[key] = true
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO session_attachments (session_id, tenant_id, user_id, kind, ref_id)
			 VALUES (?, ?, ?, ?, ?)`,
			sessionID, tenantID, userID, item.Kind, item.RefID,
		); err != nil {
			return err
		}
	}
	return tx.Commit()
}
