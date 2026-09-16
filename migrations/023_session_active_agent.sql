-- Migration: chat_sessions 增加"激活 agent"列
-- 日期: 2026-09-04
-- 说明: 会话级"专家/专家团"的第 1 层——记录这个会话此刻以哪个 agent 在跑。
--
-- 背景：在此之前 agent_id 只是每条消息带的请求字段，缓存进内存会话对象后首建即锁死，
-- 从没落过库（见 sessions.go 里 ListSessions 的 `NULL AS agent_id`）。要支持"对话中途
-- 切专家 / 取消回默认"，就得有一个持久的、跨副本一致的"激活 agent"。
--
-- 0 = 默认 super-agent，与现有 LoadAgentConfig(…, 0, …) 的语义一致。
-- 不加外键：agent 被删/取消共享时，会话应回退默认而不是被级联删除（见 manager.go 的失效兜底）。

ALTER TABLE chat_sessions
  ADD COLUMN active_agent_id INT NOT NULL DEFAULT 0 AFTER project_id;
