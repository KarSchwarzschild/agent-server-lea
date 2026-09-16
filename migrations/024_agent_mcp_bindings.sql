-- Migration: agent ↔ MCP server 的持久绑定
-- 日期: 2026-09-04
-- 说明: 让"专家"（agent）能以**整个 MCP server 为单位**持久绑定，与会话级挂载
--       （session_attachments kind=mcp_server）粒度对齐。
--
-- 背景：此前 agent 只能逐个绑 MCP 的 tool 行（agent_tool_bindings），既累又不跟随
-- server 增删工具；而会话级早就能挂整个 server。这张表补上专家级的整-server 绑定。
-- 组装 agent-config 时（internal.py）把绑定的 server 展开成它同步出的全部 tool 行，
-- 与逐个 tool 绑的结果按 tool id 去重后合并。
--
-- 不加 mcp_server 的外键级联删除以外的约束；owner/可见性在 bind 端点和分享闸里判。

CREATE TABLE IF NOT EXISTS agent_mcp_bindings (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    agent_id      INT NOT NULL,
    mcp_server_id INT NOT NULL,
    enabled       TINYINT(1) NOT NULL DEFAULT 1,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_agent_mcp (agent_id, mcp_server_id),
    KEY idx_agent_mcp_agent (agent_id),
    CONSTRAINT fk_agent_mcp_agent FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_mcp_server FOREIGN KEY (mcp_server_id) REFERENCES mcp_servers (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
