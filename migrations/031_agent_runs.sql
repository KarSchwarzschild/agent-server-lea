-- 031 · lea-agent 持久化 Run 状态机。
--
-- HTTP 进程只负责创建 run；Temporal 负责持久化调度，独立 worker 执行 turn。
-- 请求正文存在 request_json，便于审计和故障排查；流式 token 仍在 Redis Stream，
-- 不写 MySQL，也不写进 Temporal history。

CREATE TABLE IF NOT EXISTS `agent_runs` (
  `id`                  VARCHAR(64)  NOT NULL,
  `session_id`          VARCHAR(64)  NOT NULL,
  `tenant_id`           VARCHAR(64)  NOT NULL,
  `user_id`             VARCHAR(64)  NOT NULL,
  `agent_id`            INT          NOT NULL DEFAULT 0,
  `project_id`          INT          NULL,
  `status`              VARCHAR(24)  NOT NULL,
  `request_json`        JSON         NOT NULL,
  `worker_id`           VARCHAR(128) NOT NULL DEFAULT '',
  `attempt`             INT          NOT NULL DEFAULT 0,
  `dispatched_at`       DATETIME(6)  NULL,
  `dispatch_attempt`    INT          NOT NULL DEFAULT 0,
  `dispatch_error`      TEXT         NULL,
  `cancel_requested_at` DATETIME(6)  NULL,
  `started_at`          DATETIME(6)  NULL,
  `finished_at`         DATETIME(6)  NULL,
  `error_message`       TEXT         NULL,
  `created_at`          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at`          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                      ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (`id`),
  KEY `idx_agent_runs_session` (`tenant_id`, `session_id`, `created_at`),
  KEY `idx_agent_runs_status` (`status`, `created_at`),
  KEY `idx_agent_runs_dispatch` (`status`, `dispatched_at`, `created_at`),
  KEY `idx_agent_runs_user` (`tenant_id`, `user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='lea-agent 每轮执行的持久化状态；流式事件不在此表';

CREATE TABLE IF NOT EXISTS `agent_run_checkpoints` (
  `run_id`          VARCHAR(64) NOT NULL,
  `attempt`         INT         NOT NULL,
  `phase`           VARCHAR(32) NOT NULL,
  `iteration`       INT         NOT NULL DEFAULT 0,
  `checkpoint_json` JSON        NOT NULL,
  `updated_at`      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                  ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (`run_id`),
  CONSTRAINT `fk_agent_run_checkpoint_run`
    FOREIGN KEY (`run_id`) REFERENCES `agent_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Agent turn 最近一个原子 checkpoint；消息正文仍在 agent_messages';

-- 引擎会按已完成步骤增量写 agent_messages。记录来源 attempt 后，Worker 故障转移可以
-- 从最近 checkpoint 接着执行，且存储层能拒绝已经失去执行权的旧 Worker 继续追加。
ALTER TABLE `agent_messages`
  ADD COLUMN `run_id` VARCHAR(64) NULL AFTER `message`,
  ADD COLUMN `run_attempt` INT NULL AFTER `run_id`,
  ADD KEY `idx_agent_messages_run` (`run_id`, `run_attempt`, `id`);

-- 前端投影也必须按 run 幂等。否则 Worker 在 SaveMessages 成功、FinishAgentRun 之前
-- 退出时，新 attempt 会把同一轮 user/assistant 再插一遍。
ALTER TABLE `chat_messages`
  ADD COLUMN `run_id` VARCHAR(64) NULL AFTER `extra`,
  ADD COLUMN `run_attempt` INT NULL AFTER `run_id`,
  ADD UNIQUE KEY `uk_chat_messages_run_role` (`run_id`, `role`);
