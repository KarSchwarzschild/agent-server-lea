-- Migration: 把 qwen3-max 换成中转上还活着的模型
-- 日期: 2026-10-12
--
-- ## 起因
--
-- 中转 API（tenantapi-ai.ouchn.edu.cn）上 qwen3-max 已经不存在了。而它**不报错**：
--
--     POST /v1/chat/completions  {"model":"qwen3-max", ...}
--     → HTTP 200
--       {"choices":[{"finish_reason":"stop",
--                    "message":{"content":"Model not exist.","role":"assistant"}}],
--        "usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}
--
-- 返回码是 200、finish_reason 是 stop、消息体是合法的 assistant 回复。所以在我们
-- 看来这次调用**成功了**：agent_runs 记 completed、error_message 为空，那六个字被
-- 当成模型的回答存进 chat_messages 显示给用户。用户看到的不是报错弹窗，是 AI
-- "说"了一句 Model not exist.
--
-- LEA_MODEL_FALLBACK=glm-5.1 没有接住，两层原因：
--   1. 它是死配置 —— 全仓库除了 .env.example / README / 一份文档，没有任何代码读它
--   2. 就算接上了也不会触发：上游返 200，没有错误可捕获
--
-- ## 换成什么
--
-- /v1/models 对我们是 403（见 015 的说明），所以按模型名逐个试 —— 走生产同一条
-- /v1/chat/completions、同一个标准 Bearer 头，只换 model 字段。判定依据就是上面
-- 那个 bug 本身暴露的规律：不存在的模型 total_tokens 恒为 0，所以 >0 才算真活着。
--
-- 42 个候选名里活着 7 个，其中 5 个能当 agent 主模型（必须回 tool_calls）：
--
--     qwen-plus      ✅ 工具调用正常，输出干净           ← 选它
--     glm-4-plus     ✅ 工具调用正常，输出干净
--     glm-4-air      ✅ 工具调用正常，输出干净
--     glm-4-flash    ✅ 工具调用正常，输出干净
--     glm-4v         ✅ 但是视觉模型，不进聊天列表
--     glm-5.1        ⚠️ 工具调用正常，但把 <think> 推理过程写进 content
--     qwen3-8b       ❌ 不回 tool_calls，agent 干不了活
--
-- 死掉的 35 个：qwen-turbo / qwen-long / qwen-flash / qwen2.5 全家 / qwen3-32b /
-- qwen3-14b / qwen3-coder-plus / qwen-vl 系 / qwen-max / qwen-max-latest /
-- qwen3-max-preview / qwen3-plus / glm-4.5 / glm-4.6 / glm-z1-air / deepseek 全家 /
-- moonshot / kimi-k2 / doubao / ernie / hunyuan / step / minimax / baichuan4 /
-- yi-large / spark-max / gpt 系 / claude 系。
--
-- 主模型选 qwen-plus：跟 qwen3-max 同系（提示词不用重调），工具调用正常，输出
-- 干净。glm-5.1 **不选**为主模型 —— 它漏 <think>，而当前链路（lea-agent 侧和
-- frontend-lab 侧）都没有剥这个标签的地方（只有不在链路上的 lea-graph-legacy 有），
-- 用户会直接看到一整段推理过程。但它留在 models 列表里：它确实能用，而且原本就是
-- 配好的，删掉属于无关改动。
--
-- ## 上下文窗口填多少
--
-- 规则同 015：填**最大输入长度**不是总窗口，拿不准往小填。小了只是压缩早跑一次
-- （一次摘要调用），大了会在 provider 那里整轮失败 —— 那是用户能看到的错误。
--
--     qwen-plus      128000
--     glm-4-plus     128000
--     glm-4-air      128000
--     glm-4-flash    128000
--     glm-5.1        200000   （015 填的，不动）
--
-- 这几个数按公开资料取整往小。没填不会拒服：internal.py 下发 0，lea-agent 走
-- session.fallbackContextWindow = 32000。
--
-- ## qwen3-max 的历史值保留在这里
--
--     llm_providers.models        'qwen3-max,glm-5.1'
--     llm_providers.model_limits  {"glm-5.1": 200000, "qwen3-max": 258048}
--     agents.model                19 行是 'qwen3-max'，7 行为空（继承）
--     .env  LEA_MODEL_CHAT        qwen3-max
--
-- 为什么要留：**还不确定 qwen3-max 是被下架了，还是这把 key 丢了它的权限。**
-- 两者现象一样，但修法不同 —— 如果是权限问题，真正该修的在上游，上游恢复后会想
-- 换回去。查不出来是因为 /v1/models 对我们 403，列不出上游到底有什么。这件事得
-- 找中转那边确认。
--
-- model_limits 里**故意不删** qwen3-max 那一条：留着它，上游恢复后只需改 models
-- 和 agents.model，窗口不用重新查。多一个用不到的键没有代价。
--
-- 执行方式: uv run python scripts/apply_migration.py migrations/034_swap_dead_chat_model.sql
--
-- 幂等：三条都是 UPDATE，判据写成"只改还指着 qwen3-max 的行"，重复执行是 no-op。

-- 1) provider 的可选模型列表。
--    这一列同时是 /llm-models 给前端选择器的数据源和 internal.py 的匹配表
--    （两边共用 models/llm_provider.py 的 models_of），所以改它一处就够。
--    qwen3-max 从列表里去掉 —— 留着它等于让用户能在界面上选一个死模型。
UPDATE llm_providers
   SET models = 'qwen-plus,glm-4-plus,glm-4-air,glm-4-flash,glm-5.1'
 WHERE models LIKE '%qwen3-max%';

-- 2) 新模型的上下文窗口。
--    JSON_MERGE_PATCH 而不是直接赋值：保住 015 填的 glm-5.1，也保住 qwen3-max
--    那一条（见上面"历史值保留"）。重复执行不会把手工加的模型冲掉。
UPDATE llm_providers
   SET model_limits = JSON_MERGE_PATCH(
           COALESCE(model_limits, JSON_OBJECT()),
           JSON_OBJECT(
               'qwen-plus',   128000,
               'glm-4-plus',  128000,
               'glm-4-air',   128000,
               'glm-4-flash', 128000
           )
       )
 WHERE models LIKE '%qwen-plus%';

-- 3) 还指着死模型的 agent。
--    只动 model='qwen3-max' 的那些；model 为空的 7 行不碰 —— 空是"继承默认"，
--    它们跟着 .env 的 LEA_MODEL_CHAT 走（那个要在 .env 里单独改，迁移管不着）。
UPDATE agents
   SET model = 'qwen-plus'
 WHERE model = 'qwen3-max';
