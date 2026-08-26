-- Migration: mcp_servers 连接配置收口到 endpoint —— 「一张表两个模型」的第三例
-- 日期: 2026-08-25
--
-- ## 起因
--
-- 这张表被 lea-config 和 lea-tools **各定义一遍**，create_all() 只建表不改表，于是
-- 真实表是两份模型的并集，还带着两组表达同一件事的列：
--
--     lea-config 的模型**写**的        lea-tools 的执行侧**读**的
--     ───────────────────────        ──────────────────────────
--     config (JSON)                   endpoint / command / env_config
--     connection_status               auth_config / tool_filter
--     last_connected_at / last_error
--
-- 后果是端到端不通，而且不通得很安静：通过 lea-config 建出来的行，执行侧要读的那
-- 几列**永远是 NULL** —— 因为 MCPServerCreate 里根本没有这些字段，用户想填也填不了。
--
-- 现网实测（4 行全部如此）：
--
--     id=22 seed-acme   filesystem    endpoint=NULL  config={"command":"npx",...}
--     id=23 seed-acme   alice-notion  endpoint=NULL  config={"url":"https://..."}
--     id=24 seed-globex filesystem    endpoint=NULL  config={"command":"npx",...}
--     id=25 seed-globex alice-notion  endpoint=NULL  config={"url":"https://..."}
--
-- 于是 /sync 必然 400 "has no endpoint configured"（admin_mcp.py 读 endpoint），
-- 而 mcp_call 读的是同一列。**修一处等于没修。**
--
-- 这也解释了为什么 execution_type='mcp' 的工具行至今是 0：sync 从来没成功过一次。
--
-- ## 为什么收口到 endpoint 而不是 config
--
-- 判据跟 tools 表那次（migrations/006）保持一致：**运行时真正决定能不能调通的那一
-- 侧，它的命名是权威的**。那次的结论写在 lea_config/models/tool.py 头部，这次照搬，
-- 不新发明一套判据。
--
-- 另外两条支持它：
--
--   1. 只支持 streamable_http 之后（见下），一个连接**就是一个 URL**。一个 typed
--      的 VARCHAR 列比在 JSON 里挖一个键更容易加约束和索引。
--   2. config 那个 JSON 是「MCP 客户端 dict shape」，里面混着 command / args /
--      env —— 那些是 stdio 的东西，而 stdio 这次关死了。留着它等于留一个装着已废
--      配置的口袋。
--
-- 反方向的理由（config 对齐外部规范、拆成自己的列会丢信息如自定义 headers）是成立
-- 的，但自定义 header 该由 auth_config 表达，那一列本来就在。
--
-- ## 传输方式：只留 streamable_http
--
-- **stdio 关死**：语义是在网关进程里 fork 子进程（现网 id=22/24 就是 `npx`）。今天
-- 没实现所以是死值；一旦实现，「用户能加 MCP」就等于「用户能在网关进程里执行任意
-- 命令」。而它偏偏还是**默认值**。
--
-- **sse 也不要**：它是长连接，绑在某一个副本上。所有服务都是多副本，负载均衡把下
-- 一次工具调用发到别的副本，那边没有这条连接。要支持就得引入会话亲和或连接注册表
-- —— 为一个还没人要的传输方式付这个价不值。
--
-- streamable_http 是请求-响应，无状态，天然多副本安全。
--
-- ## 现网 4 行怎么办
--
-- 全是 seed 数据，没有真实用户。两行 stdio 的（22/24）搬不过来 —— 它们没有 URL，
-- 只有 `npx` 命令，而那条路关死了。**直接停用**，不删：留着它们能说明「这里曾经有
-- 两行 stdio」，而 enabled=0 保证它们不会被绑、不会被同步、不会被调用。
--
-- 两行 streamable_http 的（23/25）把 config->>'$.url' 搬进 endpoint。
--
-- 执行方式: python scripts/apply_migration.py migrations/016_mcp_servers_endpoint_convergence.sql
--
-- ⚠️ 不可回滚：DROP 掉了 config / command / env_config。执行前先备份：
--    python -c "import sys;sys.path.insert(0,'scripts');import _dbconn as d;
--               c=d.connect();cur=c.cursor();cur.execute('SELECT * FROM mcp_servers');
--               print(cur.fetchall())"

-- ── 1. 把还能救的 URL 搬进 endpoint ──────────────────────────────────────
--
-- 只搬 config 里有 url 的行。JSON_UNQUOTE 是必须的 —— ->> 已经 unquote，但显式写
-- 出来免得有人改成 -> 之后引号跟着进列里，那种脏数据在 httpx 那边表现为
-- "Invalid URL '\"https://...\"'"，看着像网络问题。

UPDATE mcp_servers
   SET endpoint = JSON_UNQUOTE(JSON_EXTRACT(config, '$.url'))
 WHERE endpoint IS NULL
   AND config IS NOT NULL
   AND JSON_EXTRACT(config, '$.url') IS NOT NULL;

-- ── 2. 停用搬不过来的 ─────────────────────────────────────────────────────
--
-- 走到这一步还没有 endpoint 的，要么是 stdio（只有 command），要么 config 里压根
-- 没有 url。两种都不该再被当成可用的 server。
--
-- 用 enabled=0 而不是 DELETE：删掉的话「为什么少了两行」以后没人答得上来，而停用
-- 是个能自解释的状态。mcp_call 和 sync 都会拒绝 disabled 的 server。

UPDATE mcp_servers
   SET enabled = 0,
       last_error = '传输方式已停止支持（stdio），或缺少 endpoint —— 见 migrations/016'
 WHERE endpoint IS NULL OR endpoint = '';

-- ── 3. 传输方式收窄 ──────────────────────────────────────────────────────
--
-- 先把存量行的值改掉再改 ENUM 定义，否则 ALTER 会因为「现有值不在新定义里」失败。
-- 上一步已经把它们停用了，这里只是让 transport 列的值合法。

UPDATE mcp_servers SET transport = 'streamable_http' WHERE transport <> 'streamable_http';

ALTER TABLE mcp_servers
    MODIFY COLUMN transport ENUM('streamable_http') NOT NULL DEFAULT 'streamable_http'
    COMMENT '只支持 streamable_http：stdio = 网关进程里执行任意命令；sse 长连接在多副本下会飘';

-- ── 4. endpoint 收紧 ─────────────────────────────────────────────────────
--
-- 到这里每一行要么有 endpoint，要么已经停用但仍然 NULL —— 所以**不能**加 NOT NULL，
-- 那会让第 2 步保留的那些行插不回去。留 NULL 可空，由接口层保证新建时必填
-- （MCPServerCreate.endpoint 是必填字段）。
--
-- 加注释说清它是唯一真源，免得下一个人看到 auth_config 是 JSON 就想把 url 也塞进去。

ALTER TABLE mcp_servers
    MODIFY COLUMN endpoint VARCHAR(500) NULL
    COMMENT 'MCP server 的 URL —— 连接配置的唯一真源（见 migrations/016）';

-- ── 5. 删掉死列 ──────────────────────────────────────────────────────────
--
-- config    lea-config 以前写的，执行侧从来没读过
-- command   stdio 专用，那条路关死了
-- env_config 同上

ALTER TABLE mcp_servers DROP COLUMN config;
ALTER TABLE mcp_servers DROP COLUMN command;
ALTER TABLE mcp_servers DROP COLUMN env_config;

-- ── 6. 自检 ──────────────────────────────────────────────────────────────
--
-- 启用中的 server 必须都有 endpoint。不为 0 就是上面哪一步漏了，别往下走。

SELECT
    COUNT(*) AS enabled_without_endpoint
FROM mcp_servers
WHERE enabled = 1 AND (endpoint IS NULL OR endpoint = '');
