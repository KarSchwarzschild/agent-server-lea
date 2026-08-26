-- Migration: 新增 agent_messages —— lea-agent 的会话历史真源
-- 日期: 2026-08-12
-- 说明:
--   一行一条**引擎原生消息**（agentloop 的 llm.Message），而不是一行一个前端气泡。
--   这是 OpenAI Agents SDK / Google ADK 的表形状：存 item，UI 视图从 item 投影出来。
--
--   为什么不复用 chat_messages：那张表有两个生产者 —— lea-agent 和 lea-server 的
--   chat.py（当前默认落地页 SuperAgent 的后端）。后者没有引擎，写不出引擎消息，
--   所以那张表容不下这个形状。等 SuperAgent 收口到 lea-agent、chat.py 下线之后，
--   chat_messages 整张删掉即可，本表不受影响。
--
--   与 chat_messages 的分工（过渡期）：
--     agent_messages —— 给模型看的，全保真（含 tool_use / tool_result 块）
--     chat_messages  —— 给前端看的，渲染后的正文 + extra 可视化字段
--   收口后前端改为从 agent_messages 投影，chat_messages 退场。
--
-- 执行方式: mysql -u root -p <db> < migrations/003_agent_messages.sql
--
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 会话归属。tenant_id 冗余在这里是为了让 LoadHistory 能同时按
    -- (tenant_id, session_id) 过滤 —— 只按 session_id 查的话，一个猜到/泄漏的
    -- session_id 就能读到别人的对话，而这张表的内容会被直接灌进模型上下文。
    session_id VARCHAR(64) NOT NULL,
    tenant_id  VARCHAR(64) NOT NULL DEFAULT '',

    -- 消息角色。真值在 message JSON 里，这一列是冗余的投影键：
    -- 前端视图和排查都按角色筛，没必要每次解 JSON。
    role VARCHAR(20) NOT NULL,

    -- 引擎原生 llm.Message 的完整 JSON。含 content block 数组，所以
    -- tool_use / tool_result / thinking 块都在，跨节点恢复不丢工具调用。
    message JSON NOT NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- 顺序即 id 升序，不另设 seq 列：seq 需要 read-modify-write 才能算出来，
    -- 多副本并发写同一会话时会打架，而 AUTO_INCREMENT 天生单调。
    -- 一次 Append 是一个事务（一整轮），所以同轮消息的 id 连续。
    KEY idx_session (tenant_id, session_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
