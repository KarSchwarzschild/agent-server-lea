-- Migration: chat_sessions 记录当前会话选择的聊天模型
-- 日期: 2026-10-12
--
-- 模型选择属于会话状态，不属于 Agent 配置。用户在聊天页切换模型后，下一条
-- 消息自动写入这里；重新打开会话、跨设备访问和后台重试都恢复同一个模型。
-- NULL 表示这个会话还没有显式运行过，首次发送时使用 Agent 默认模型并固化。

ALTER TABLE chat_sessions
  ADD COLUMN model VARCHAR(200) NULL AFTER active_agent_id;
