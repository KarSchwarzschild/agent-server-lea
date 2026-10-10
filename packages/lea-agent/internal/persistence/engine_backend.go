package persistence

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"

	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
	"gitlab.ouc-online.com.cn/aibase/agentloop/sessions"
)

// maxLoadRows 是防病态会话的粗保护，**不是**策略。
//
// 真正的上限是构造时传进来的 token 预算（见 recentWithinBudget）。这一条只防止一个
// 行数离谱的会话在预算裁剪之前就被整个物化出来。取值远高于预算能放行的量，所以正常
// 情况下它不该生效 —— 而它一旦生效，loadHistoryRaw 会记一条日志，不会闷声返回一段
// 被截短的对话。
//
// ── 为什么这里不再有"20 轮"那个帽子 ──────────────────────────────────
//
// 原来这里是 maxLoadTurns = 20，判据是轮数。它有两个问题：
//
//  1. **轮数跟大小无关。**20 轮可能是 2 万 token，也可能是 40 万 —— 取决于这 20 轮
//     里有没有人调过 Read。
//  2. **它切在摘要器的上游。**LoadHistory 的结果会被 ReplaceHistory 整体替换成工作
//     历史，所以被这一刀砍掉的消息压根到不了引擎的 reduce/compact 面前 —— 那是净
//     删除，不是压缩。而引擎的 contextControl 每轮第一次模型调用之前就会跑，它才是
//     该负责"发多少给模型"的那一层。
//
// 后果是一个又长又轻的会话（比如 50 轮闲聊、总共 1.5 万 token）永远压不到压缩触发线，
// 于是永远走这条 raw 路径，于是**每一轮**都只看得见最近 20 轮。表现是"模型忘了前面
// 说过的话"，而且跟跨不跨 pod 无关。
const maxLoadRows = 5_000

// fallbackLoadBudget 是调用方传了非正数预算时的兜底。
//
// 刻意取小。往大了估的后果是历史加载到超过模型能接受的量，然后在 provider 那里整轮
// 失败 —— 用户能看到的错误。往小了估只是让引擎比必要更早地压缩一次，代价是一次摘要
// 调用。在"失败"和"多花钱"之间选多花钱。
const fallbackLoadBudget = 32_000

// EngineBackend stores a session's conversation as engine-native messages, so any
// process can pick a session up mid-conversation.
//
// It implements agentloop's sessions.Backend, which is the seam the engine already
// uses for exactly this: Build calls LoadHistory when Config.Resume is set, and
// writes through OpenStore when Config.PersistSession is on. Those two are
// independent switches, so a chain leg can read the conversation without writing a
// second copy of it under the leader's id.
//
// Implementing that interface instead of seeding by hand is the point. The
// hand-written version had to be remembered at every call site, and it was not:
// the star path got it, the chain path did not, and the failure was silent — the
// request succeeded, tokens were charged, the model just answered as though the
// conversation had not happened.
type EngineBackend struct {
	db       *sql.DB
	tenantID string
	// loadBudget 是 raw 路径最多载入多少 token 的历史。摘要路径不受它约束 ——
	// 那一段的长度已经由压缩决定了，再叠一道会把摘要之后的近期对话砍掉。
	loadBudget int
}

// 编译期钉住接口。少一个方法的后果不是编译失败而是 Build 时才报错 ——
// 那时已经是运行期，且表现为会话建不起来。
var (
	_ sessions.Backend       = (*EngineBackend)(nil)
	_ sessions.Store         = (*engineStore)(nil)
	_ sessions.SummaryStore  = (*engineStore)(nil)
	_ sessions.InflightStore = (*engineStore)(nil)
)

// NewEngineBackend returns a Backend scoped to one tenant.
//
// The tenant is fixed at construction because sessions.Backend's methods take only
// a session id. Every query filters on both: this table's rows are fed straight
// into a model's context, so a session id that leaked or collided must not be
// enough to read someone else's conversation.
//
// loadBudget 应当跟 engine.Config.MaxContextTokens 出自同一处（见 session 包的
// contextBudget）。两个数各自算的话迟早会漂开，而漂开的表现是：加载侧砍掉了一段，
// 引擎却以为自己还有余量，于是既丢了历史又不触发压缩。非正数走 fallbackLoadBudget。
func NewEngineBackend(db *sql.DB, tenantID string, loadBudget int) *EngineBackend {
	if loadBudget <= 0 {
		loadBudget = fallbackLoadBudget
	}
	return &EngineBackend{db: db, tenantID: tenantID, loadBudget: loadBudget}
}

// LoadHistory returns the session's working history: [summary + recent messages]
// when a compaction summary was persisted, or as much raw history as loadBudget
// admits otherwise.
//
// The summary path reconstructs what the compacting pod had in memory — the whole
// point of persisting summaries — so a pod switch does not re-compact (which wastes
// an LLM call and may produce a different summary). The fallback path is for
// sessions that never compacted or whose summary table does not exist yet.
//
// ── 两条路合起来为什么不丢东西 ────────────────────────────────────────
//
// loadBudget 高于引擎的压缩触发线（MaxContextTokens × compactionThresholdRatio）：
//
//	没压缩过的会话   按定义整段在触发线以下 → 预算装得下 → 全量载入
//	压缩过的会话     走摘要路径，不受预算约束
//
// 所以正常情况下这里**不做取舍**，取舍全部交给引擎的 contextControl（它在每轮第一次
// 模型调用之前就跑）。预算只在压缩因故没跑成、历史堆到异常大时才咬。
//
// 这一点很要紧：这个函数的返回值会被 ReplaceHistory **整体替换**成工作历史，不是
// "发给模型之前修剪一份副本"。在这里砍掉的东西没有第二次机会。
//
// A session with nothing stored returns (nil, nil), per the interface.
func (b *EngineBackend) LoadHistory(ctx context.Context, sessionID string) ([]llm.Message, error) {
	if b.db == nil {
		return nil, nil
	}

	// Try the summary path first.
	if msgs, err := b.loadHistoryWithSummary(ctx, sessionID); err == nil && msgs != nil {
		return msgs, nil
	}

	// Fallback: load raw messages and trim to recent turns.
	return b.loadHistoryRaw(ctx, sessionID)
}

// loadHistoryWithSummary returns [summary + messages after covered_id] when a
// persisted summary exists, or (nil, nil) when it does not.
func (b *EngineBackend) loadHistoryWithSummary(ctx context.Context, sessionID string) ([]llm.Message, error) {
	var rawSummary []byte
	var coveredID int64
	err := b.db.QueryRowContext(ctx,
		`SELECT summary, covered_id FROM session_summaries
		  WHERE tenant_id = ? AND session_id = ?`,
		b.tenantID, sessionID,
	).Scan(&rawSummary, &coveredID)
	if err != nil {
		// No summary (table missing or no row) — caller falls back to raw load.
		return nil, nil
	}

	var summary llm.Message
	if err := json.Unmarshal(rawSummary, &summary); err != nil {
		// Corrupt summary — treat as if it does not exist.
		return nil, nil
	}

	rows, err := b.db.QueryContext(ctx,
		`SELECT message FROM agent_messages
		  WHERE tenant_id = ? AND session_id = ? AND id > ?
		  ORDER BY id ASC`,
		b.tenantID, sessionID, coveredID,
	)
	if err != nil {
		return nil, fmt.Errorf("load history after summary: %w", err)
	}
	defer rows.Close()

	var out []llm.Message
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, fmt.Errorf("scan message: %w", err)
		}
		var msg llm.Message
		if err := json.Unmarshal(raw, &msg); err != nil {
			break
		}
		out = append(out, msg)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate messages: %w", err)
	}

	return append([]llm.Message{summary}, out...), nil
}

// loadHistoryRaw is the pre-summary LoadHistory: load raw messages up to the budget.
func (b *EngineBackend) loadHistoryRaw(ctx context.Context, sessionID string) ([]llm.Message, error) {
	// Newest-first so the cap keeps the *recent* end. Oldest-first + LIMIT would
	// keep the opening of a long conversation and drop what was just said.
	//
	// The row LIMIT is a coarse guard, not the cap: it only stops an enormous session
	// from being materialized before recentWithinBudget can trim it. That judgment
	// belongs in recentWithinBudget, where it is tested.
	rows, err := b.db.QueryContext(ctx,
		`SELECT message FROM agent_messages
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY id DESC
		  LIMIT ?`,
		b.tenantID, sessionID, maxLoadRows,
	)
	if err != nil {
		return nil, fmt.Errorf("load history: %w", err)
	}
	defer rows.Close()

	var newestFirst []llm.Message
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, fmt.Errorf("scan message: %w", err)
		}
		var msg llm.Message
		if err := json.Unmarshal(raw, &msg); err != nil {
			// One unreadable row must not cost the whole conversation. Stop here and
			// keep what is already collected: since rows arrive newest-first that
			// yields the most recent contiguous stretch — the part that matters — and
			// never a history with a hole in the middle.
			break
		}
		newestFirst = append(newestFirst, msg)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate messages: %w", err)
	}
	// 粗保护咬到了。它不该咬 —— 咬了说明这个会话的行数离谱（或者预算配得过大），
	// 而后果是历史被悄悄截短。说出来，别让它看起来像"这段对话本来就这么长"。
	if len(newestFirst) >= maxLoadRows {
		log.Printf("history_row_guard_hit session=%s rows=%d —— 触到 %d 行的粗保护，"+
			"更早的历史本轮读不到", sessionID, len(newestFirst), maxLoadRows)
	}
	return recentWithinBudget(newestFirst, b.loadBudget), nil
}

// recentWithinBudget turns newest-first rows into an oldest-first history costing
// at most budget estimated tokens. Returns nil when nothing usable remains.
//
// Separate from the query so the judgment it encodes is testable without a
// database: which end the cap keeps, and what a cap landing mid-turn leaves behind.
//
// 用 llm.EstimateBlockTokens 而不是自己数字节：那正是引擎 contextTokens 的底层估算
// （一个与模型无关的字节启发式），所以加载侧和发送侧量的是同一把尺。引擎在它之上还
// 乘一个用 provider 回报的真实 input token 数做 EMA 校准的比值，那个比值在 session
// 里，backend 拿不到 —— 差别只让这里偏保守，方向是安全的。
func recentWithinBudget(newestFirst []llm.Message, budget int) []llm.Message {
	if budget <= 0 {
		budget = fallbackLoadBudget
	}
	kept := make([]llm.Message, 0, len(newestFirst))
	spent := 0
	for _, msg := range newestFirst {
		cost := 0
		for _, b := range msg.Content {
			cost += llm.EstimateBlockTokens(b)
		}
		// 至少收一条。预算比单条消息还小时宁可超一点也不能返回空历史 —— 那会让模型
		// 面对一个没有任何上下文的追问，比超预算糟得多。
		if len(kept) > 0 && spent+cost > budget {
			break
		}
		spent += cost
		kept = append(kept, msg)
	}

	out := make([]llm.Message, len(kept))
	for i, m := range kept {
		out[len(kept)-1-i] = m
	}
	// A history opening on a non-user message is a turn the cut landed inside of —
	// its prompt is gone. Some providers reject that shape outright, and a model that
	// accepts it reads the orphan as its own unprompted speech, so drop the head.
	//
	// 换成 token 预算之后这一步更要紧了：轮数判据是在 user 消息边界上停的，切点天然
	// 对齐；预算判据可以停在任何一条消息上，所以半个轮次是常态而不是例外。
	// LangChain 的 trim_messages 为同一件事加了 start_on='human' / end_on=('human','tool')。
	for len(out) > 0 && out[0].Role != llm.RoleUser {
		out = out[1:]
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// OpenStore returns the writer for a session.
func (b *EngineBackend) OpenStore(_ context.Context, sessionID string) (sessions.Store, error) {
	return &engineStore{db: b.db, tenantID: b.tenantID, sessionID: sessionID}, nil
}

// The remaining Backend methods exist for the desktop's session picker (list past
// sessions, resume the latest, remember the model you switched to). This host has
// none of that: sessions are listed from chat_sessions with owner filtering, and
// runtime state comes from the agent's config on every request. They are stubbed
// rather than half-implemented so a future caller fails loudly instead of quietly
// getting an empty answer that looks like "no sessions".
var errNotSupported = fmt.Errorf("not supported by the lea-agent session backend")

func (b *EngineBackend) List(context.Context) ([]sessions.Info, error) {
	return nil, errNotSupported
}

func (b *EngineBackend) Describe(context.Context, string) (sessions.Info, error) {
	return sessions.Info{}, errNotSupported
}

func (b *EngineBackend) Latest(context.Context) (string, error) {
	return "", errNotSupported
}

// SaveMeta/LoadMeta are no-ops rather than errors: the engine calls them on its
// own schedule, and a session's runtime state here is whatever the agent config
// says on this request, not something that should survive from the last one.
func (b *EngineBackend) SaveMeta(context.Context, string, sessions.SessionMeta) error {
	return nil
}

func (b *EngineBackend) LoadMeta(context.Context, string) (sessions.SessionMeta, error) {
	return sessions.SessionMeta{}, nil
}

func (b *EngineBackend) Close(context.Context) error { return nil }

// engineStore appends one session's messages.
type engineStore struct {
	db        *sql.DB
	tenantID  string
	sessionID string
}

// Append commits a whole turn atomically, as the Store contract requires: a turn
// half-written is worse than one not written, because the next LoadHistory would
// hand the model a user message whose reply is missing.
func (s *engineStore) Append(ctx context.Context, messages []llm.Message) error {
	return s.append(ctx, messages, nil)
}

func (s *engineStore) AppendCheckpoint(ctx context.Context, messages []llm.Message, checkpoint sessions.TurnCheckpoint) error {
	return s.append(ctx, messages, &checkpoint)
}

func (s *engineStore) append(ctx context.Context, messages []llm.Message, checkpoint *sessions.TurnCheckpoint) error {
	if s.db == nil || (len(messages) == 0 && checkpoint == nil) {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin append: %w", err)
	}
	defer tx.Rollback() //nolint:errcheck // no-op once Commit succeeded
	run, tracked := agentRunAttemptFromContext(ctx)
	if tracked {
		if err := lockAgentRunAttempt(ctx, tx, run); err != nil {
			log.Printf("agent_history_fence_rejected session=%s run=%s attempt=%d err=%v",
				s.sessionID, run.RunID, run.Attempt, err)
			return err
		}
	}
	if checkpoint != nil && !tracked {
		checkpoint = nil
	}

	if err := s.appendMessages(ctx, tx, messages, run, tracked); err != nil {
		return err
	}
	if checkpoint != nil {
		raw, err := json.Marshal(checkpoint)
		if err != nil {
			return fmt.Errorf("marshal turn checkpoint: %w", err)
		}
		_, err = tx.ExecContext(ctx,
			`INSERT INTO agent_run_checkpoints
			    (run_id, attempt, phase, iteration, checkpoint_json)
			 VALUES (?, ?, ?, ?, ?)
			 ON DUPLICATE KEY UPDATE
			    attempt = VALUES(attempt), phase = VALUES(phase),
			    iteration = VALUES(iteration), checkpoint_json = VALUES(checkpoint_json),
			    updated_at = CURRENT_TIMESTAMP(6)`,
			run.RunID, run.Attempt, checkpoint.Phase, checkpoint.Iteration, raw,
		)
		if err != nil {
			return fmt.Errorf("upsert turn checkpoint: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit append: %w", err)
	}
	return nil
}

func (s *engineStore) appendMessages(ctx context.Context, tx *sql.Tx, messages []llm.Message, run agentRunAttempt, tracked bool) error {
	if len(messages) == 0 {
		return nil
	}
	query := `INSERT INTO agent_messages (session_id, tenant_id, role, message) VALUES (?, ?, ?, ?)`
	if tracked {
		query = `INSERT INTO agent_messages
		          (session_id, tenant_id, role, message, run_id, run_attempt)
		         VALUES (?, ?, ?, ?, ?, ?)`
	}
	stmt, err := tx.PrepareContext(ctx, query)
	if err != nil {
		return fmt.Errorf("prepare append: %w", err)
	}
	defer stmt.Close()
	for _, message := range messages {
		raw, err := json.Marshal(message)
		if err != nil {
			return fmt.Errorf("marshal message: %w", err)
		}
		args := []any{s.sessionID, s.tenantID, string(message.Role), raw}
		if tracked {
			args = append(args, run.RunID, run.Attempt)
		}
		if _, err := stmt.ExecContext(ctx, args...); err != nil {
			return fmt.Errorf("insert message: %w", err)
		}
	}
	return nil
}

// WriteInflight mirrors the working history to Redis after every engine step, so a
// replacement worker resumes from what this one had rather than from the last
// CheckpointStore boundary.
//
// Best-effort by contract (sessions.InflightStore): the engine logs a failure and
// keeps going. Losing the snapshot costs the finer granularity — the retry falls
// back to re-running the tail of the current batch — not the turn.
//
// The run id and attempt come from the context, set by WithAgentRunAttempt at the
// top of a run. Outside a tracked run (an interactive HTTP turn with no Temporal
// activity behind it) there is nothing to key on, so this is a no-op: those turns
// have no retry to recover.
func (s *engineStore) WriteInflight(ctx context.Context, messages []llm.Message) error {
	run, tracked := agentRunAttemptFromContext(ctx)
	if !tracked {
		return nil
	}
	return WriteInflightSnapshot(ctx, inflightStore(), s.tenantID, s.sessionID, run.RunID, run.Attempt, messages)
}

// ClearInflight drops the snapshot and the in-process append anchor once the
// turn reached a durable end. Dropping the anchor ensures a subsequent write
// for the same key (same run re-queued) starts with a full re-anchor rather
// than appending onto a list that no longer exists.
func (s *engineStore) ClearInflight(ctx context.Context) error {
	run, tracked := agentRunAttemptFromContext(ctx)
	if !tracked {
		return nil
	}
	return ClearInflightSnapshot(ctx, inflightStore(), s.tenantID, s.sessionID, run.RunID, run.Attempt)
}

// SaveSummary persists a compaction summary and how many recent messages follow it.
// It is called by the engine after every compact, so it must be idempotent (upsert).
//
// recentCount is the number of messages in the working history after the summary.
// Since maxHistoryMessages is 0 (no trimming) and Reduce does not change message
// counts, the working-history length matches the DB row count, so covered_id is
// the id of the row at position (totalRows - recentCount) from the start.
//
// ── 为什么这里自己记日志 ──────────────────────────────────────────────
//
// 因为**返回的错误没人看**。引擎那边是：
//
//	_ = ss.SaveSummary(ctx, final[0], len(final)-1)   // repl/session.go:809
//
// 加上 LoadHistory 那侧把「表不存在」和「这个会话没压缩过」当成同一件事回落，两边
// 合起来的效果是：摘要一次都没落库，功能表现却完全正常 —— 跨 pod 每轮白算一次摘要
// （一次真实的模型调用），而且没有任何一条日志说过这件事。
//
// 这不是假设，是实际发生过的：migrations/013 建表的 SQL 写好了没执行，代码全在、
// 测试全绿、线上一切正常，直到有人专门去查 session_summaries 才发现是空的。
//
// 所以错误必须在**能看见错误的这一层**说出来。返回值照常返回（接口契约），但不指望
// 调用方处理它。日志前缀统一用 summary_save_failed，方便告警按前缀抓。
func (s *engineStore) SaveSummary(ctx context.Context, summary llm.Message, recentCount int) error {
	if s.db == nil {
		return nil
	}

	fail := func(err error) error {
		log.Printf("summary_save_failed session=%s tenant=%s recent=%d err=%v"+
			" —— 摘要没落库，跨 pod 接管时会重算（一次模型调用）",
			s.sessionID, s.tenantID, recentCount, err)
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fail(fmt.Errorf("begin summary save: %w", err))
	}
	defer func() { _ = tx.Rollback() }()
	if run, tracked := agentRunAttemptFromContext(ctx); tracked {
		if err := lockAgentRunAttempt(ctx, tx, run); err != nil {
			return fail(err)
		}
	}

	// Find the id of the last covered message: skip recentCount rows from the end,
	// take the next one's id. This is the boundary LoadHistory uses (WHERE id > covered_id).
	var coveredID int64
	err = tx.QueryRowContext(ctx,
		`SELECT id FROM agent_messages
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY id DESC
		  LIMIT 1 OFFSET ?`,
		s.tenantID, s.sessionID, recentCount,
	).Scan(&coveredID)
	if err != nil {
		// sql.ErrNoRows 单独说：它不是故障，是「摘要覆盖了全部消息、后面一条不剩」，
		// 那种情况下没有边界可存。混进通用错误里会让人去查连接问题。
		if errors.Is(err, sql.ErrNoRows) {
			log.Printf("summary_save_skipped session=%s recent=%d"+
				" —— 摘要之前没有可覆盖的消息，无边界可存", s.sessionID, recentCount)
			return nil
		}
		return fail(fmt.Errorf("compute covered_id for summary: %w", err))
	}

	raw, err := json.Marshal(summary)
	if err != nil {
		return fail(fmt.Errorf("marshal summary: %w", err))
	}

	_, err = tx.ExecContext(ctx,
		`INSERT INTO session_summaries (session_id, tenant_id, summary, covered_id)
		  VALUES (?, ?, ?, ?)
		  ON DUPLICATE KEY UPDATE
		    summary = VALUES(summary),
		    covered_id = VALUES(covered_id),
		    created_at = CURRENT_TIMESTAMP`,
		s.sessionID, s.tenantID, raw, coveredID,
	)
	if err != nil {
		return fail(fmt.Errorf("upsert summary: %w", err))
	}
	if err := tx.Commit(); err != nil {
		return fail(fmt.Errorf("commit summary: %w", err))
	}
	return nil
}

func lockAgentRunAttempt(ctx context.Context, tx *sql.Tx, run agentRunAttempt) error {
	var attempt int
	var status RunStatus
	if err := tx.QueryRowContext(ctx,
		`SELECT attempt, status FROM agent_runs WHERE id = ? FOR UPDATE`, run.RunID,
	).Scan(&attempt, &status); err != nil {
		return fmt.Errorf("lock agent run attempt: %w", err)
	}
	if attempt != run.Attempt || (status != RunRunning && status != RunCancelling) {
		return fmt.Errorf("agent run %s attempt %d is stale (current=%d status=%s)",
			run.RunID, run.Attempt, attempt, status)
	}
	return nil
}

func (s *engineStore) Close(context.Context) error { return nil }
