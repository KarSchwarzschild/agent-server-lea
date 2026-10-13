-- Migration: 清掉 model_limits 里已下架的 qwen3-max
-- 日期: 2026-10-13
--
-- 034 换掉 qwen3-max 时**故意**把 model_limits 里的 qwen3-max: 258048 留着了，
-- 因为当时分不清两种情况：模型在中转上下架了，还是这个 API key 掉了对它的权限。
-- /v1/models 返回 403，从外面看不出区别，而两者的处置不同 —— 掉权限的话窗口值
-- 以后还要用。
--
-- 现在确认了是**下架**，所以这条是死配置，清掉。
--
-- 清理前的状态（provider id=1, tenant=default, name=tenant_qwen）：
--   models:       qwen-plus,glm-4-plus,glm-4-air,glm-4-flash,glm-5.1   （已无 qwen3-max）
--   model_limits: {"glm-5.1": 200000, "glm-4-air": 128000, "qwen-plus": 128000,
--                  "qwen3-max": 258048, "glm-4-plus": 128000, "glm-4-flash": 128000}
--   agents 里 model='qwen3-max' 的行数：0
--   chat_sessions 里 model='qwen3-max' 的行数：0
--
-- 为什么值得清而不是放着：这条残留本身是惰性的（qwen3-max 已不在 models 列表里，
-- 选择器不会给出它，provider 匹配也落不到它）。但前端的模型选择器开着 tag、允许
-- 手输任意模型名，而会话级模型选择会把手输的名字直接发给后端。于是手输
-- "qwen3-max" 会让 window_of 解出一个 258048 的窗口 —— 一个不存在的模型配着一个
-- 看起来很正常的上下文预算，随后上游返回假成功（HTTP 200 + finish_reason: stop +
-- content "Model not exist." + usage 全零）。留着它等于在库里留一个会把人引向
-- 错误结论的数据点。
--
-- 幂等：JSON_REMOVE 对不存在的路径是无操作，重复执行安全。

UPDATE llm_providers
   SET model_limits = JSON_REMOVE(model_limits, '$."qwen3-max"')
 WHERE JSON_EXTRACT(model_limits, '$."qwen3-max"') IS NOT NULL;
