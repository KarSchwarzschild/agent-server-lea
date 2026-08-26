-- 007 — 工具名改成**用户内唯一**（平台工具仍然每租户每名字一行）
--
-- ## 从"租户内唯一"改成"用户内唯一"
--
-- 原来是 UNIQUE(tenant_id, name)：一个租户里 alice 建了 weather，bob 就再也不能建。
-- 那比产品语义严 —— 工具是用户自己的东西（owner_scope=user + owner_user_id），
-- 两个人各有一个同名的私有工具是完全正常的状态。
--
-- ## 为什么是一个 COALESCE 函数索引，而不是两条普通索引
--
-- 直觉写法是 UNIQUE(tenant_id, owner_user_id, name)。它对用户工具是对的，但对平台
-- 工具**等于不生效** —— MySQL 里 NULL 不参与唯一性判断，而平台工具的 owner_user_id
-- 就是 NULL，于是可以插入任意多行同名的平台工具。
--
-- 那就再加一条管平台的？我第一版是
--
--     UNIQUE (tenant_id, name, (owner_user_id IS NOT NULL))     -- ❌ 错的
--
-- 这条把**所有**用户工具的第三列都算成 1，于是 alice 的 weather 和 bob 的 weather
-- 撞成同一个键 —— 正好禁掉了这次改动要放开的状态。实测第一条用例就红了。
--
-- 正确形式是把 NULL 折成一个哨兵，让两种归属落在同一个键空间里：
--
--     UNIQUE (tenant_id, name, (COALESCE(owner_user_id, '')))
--
--     平台工具  第三列 = ''      → (租户, 名字, '') 唯一 = 每租户每名字一行平台工具
--     用户工具  第三列 = user_id → (租户, 名字, alice) 与 (租户, 名字, bob) 不同键
--
-- 一条索引同时表达两件事，不需要第二条。函数索引要 MySQL 8.0.13+，本库 9.3.0。
--
-- ⚠️ 前提：owner_user_id 要么是 NULL 要么是真的 user_id，**不能是空字符串** ——
-- 空串会跟平台工具撞键。OwnerMixin 的默认值是 NULL，api/tools.py 也只写 None 或
-- actor.user_id，所以这个前提成立；哪天有人写了 '' 进来，症状是"建不了工具"而不是
-- 静默串行，那是个能查的失败。
--
-- ## 遮蔽（用户工具跟内置工具同名）不在这里管
--
-- 上面这条**管不了**遮蔽：alice 的 weather 和平台的 weather 是两行不同归属的数据，
-- SQL 层面完全合法（第三列一个 'alice' 一个 ''）。而"用户不能覆盖系统底层工具"是
-- 产品规则，而且跨租户 —— 内置工具住在 tenant_id='default'，用户工具在自己租户里，
-- 唯一索引根本碰不到一起。
--
-- 所以遮蔽在**应用层**挡（api/tools.py 的 create_tool 查一次内置名字）。分工写明白，
-- 不然下一个人会以为库里已经挡住了。

ALTER TABLE tools DROP INDEX uq_tools_tenant_name;

ALTER TABLE tools
    ADD CONSTRAINT uq_tools_owner_name
    UNIQUE (tenant_id, name, (COALESCE(owner_user_id, '')));
