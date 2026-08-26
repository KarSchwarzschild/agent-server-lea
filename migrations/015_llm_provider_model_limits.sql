-- Migration: llm_providers 增加 model_limits —— 上下文窗口从代码挪到配置
-- 日期: 2026-08-24
--
-- ## 起因
--
-- 「这个模型能吃多少 token」原来是 lea-agent 里一个按模型名前缀匹配的 switch
-- （session/agent_session.go 的 contextWindowFor）。两个毛病：
--
--   1. 换模型要改代码、发版
--   2. 前缀表在模型名多一个后缀时**静默**落进缺省分支（"qwen3-max" → "qwen3-max-2026"），
--      表现只是「压缩时机不对」，没有任何报错
--
-- 而且那个缺省是 128_000，同时在当「典型值」和「兜底」用 —— 于是 qwen3-max（真实
-- 258048）和 glm-5.1（真实 ~200000）拿到同一个数，两个都偏小一半以上，白花摘要钱。
--
-- 也问不到 provider：实测 GET https://tenantapi-ai.ouchn.edu.cn/v1/models 返
-- {"StateCode":1001,"Message":"Forbidden"}，那个网关只开了 chat completions。
-- 所以来源只能是配置。
--
-- ## 为什么加列而不是新建表
--
-- 现在只需要「模型 → 窗口」这一个映射，而 llm_providers 已经有 models 那个逗号串在
-- 描述「这个 provider 有哪些模型」，全仓只有一处解析它（internal.py 的 split(",")）。
-- 加一个 JSON 列是纯附加，不动那个串、不动任何现有查询。
--
-- 以后要长（计费档位、per-model 参数、输出上限）再升成独立表 —— 那时 models 和
-- model_limits 一起搬，是一次干净的 expand-migrate-contract。
--
-- ## 填的是「最大输入长度」，不是「总窗口」
--
-- 一次请求 = [系统提示 + 历史 + 本轮输入] + [生成的回复]，窗口管的是两段之和，
-- 而这个数只用来约束前一段（引擎的 MaxContextTokens 只看历史侧）。
--
--     qwen3-max   258048   总窗口 262144，输出上限 65536
--     glm-5.1     200000   公开资料 ~203K，取整往小
--
-- 拿不准往**小**填：小了只是压缩早跑一次（一次摘要调用），大了会在 provider 那里
-- 整轮失败，那是用户能看到的错误。
--
-- ## 没填会怎样
--
-- 不拒服。internal.py 查不到就下发 0，lea-agent 走保守兜底
-- （session.fallbackContextWindow = 32_000）。兜底刻意取小，理由同上。
--
-- 执行方式: python scripts/apply_migration.py migrations/015_llm_provider_model_limits.sql
--
-- 幂等：加列用 IF NOT EXISTS 表达不了（MySQL 8 不支持 ADD COLUMN IF NOT EXISTS），
-- 但 apply_migration.py 把 "Duplicate column name" 当成「这条跑过了」跳过。

ALTER TABLE llm_providers
    ADD COLUMN model_limits JSON NULL
    COMMENT '每个模型的最大输入长度，如 {"qwen3-max": 258048}；没填走 lea-agent 的保守兜底';

-- 把现网这个 provider 的两个模型填上。
-- 用 JSON_MERGE_PATCH 而不是直接赋值：重复执行不会把后来手工加的模型冲掉。
UPDATE llm_providers
   SET model_limits = JSON_MERGE_PATCH(
           COALESCE(model_limits, JSON_OBJECT()),
           JSON_OBJECT('qwen3-max', 258048, 'glm-5.1', 200000)
       )
 WHERE models LIKE '%qwen3-max%' OR models LIKE '%glm-5%';
