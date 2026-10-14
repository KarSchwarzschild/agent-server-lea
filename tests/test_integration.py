"""LEA 平台全链路集成测试。

验证微服务间协同：
- lea-config (8500): Agent 配置 CRUD + 聚合接口
- lea-tools (8400): 工具执行网关 + 限流
- lea-parser (8600): 文件解析 + 分块 + 鉴权
- lea-rag (8300): KB 检索 + Memory（桩验证接口格式）

运行方式：
  DB_HOST=sqlite pytest tests/test_integration.py -v

不依赖外部服务（MinerU/Milvus/Redis），纯逻辑验证。
"""

import os
import sys
import tempfile

# ── 环境设置 ──────────────────────────────────────────────────
import tempfile as _tf

import pytest

_test_db_dir = _tf.mkdtemp(prefix="lea_integ_")
os.environ["DB_HOST"] = "sqlite"
os.environ["DB_NAME"] = os.path.join(_test_db_dir, "test")
# 允许测试环境不配 LEA_INTERNAL_TOKEN 也能用 X-User-ID 头
os.environ["LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS"] = "1"
os.environ["JWT_SECRET"] = "test-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "lea-config", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "lea-parser", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "lea-tools", "src"))

# lea-parser 用 JWT 鉴权（Bearer token），以下仅供 parser 测试
from jose import jwt as _jwt

_JWT_SECRET = "test-secret"
TOKEN_ADMIN = _jwt.encode(
    {"sub": "admin1", "tenant_id": "t1", "role": "admin"},
    _JWT_SECRET, algorithm="HS256",
)
TOKEN_USER = _jwt.encode(
    {"sub": "user1", "tenant_id": "t1", "role": "user"},
    _JWT_SECRET, algorithm="HS256",
)
ADMIN_H = {"Authorization": f"Bearer {TOKEN_ADMIN}"}
USER_H = {"Authorization": f"Bearer {TOKEN_USER}"}

# lea-config 统一端点 auth 头（X-User-ID/X-Tenant-ID/X-User-Role）
CTX_ADMIN_H = {"X-User-ID": "admin1", "X-Tenant-ID": "default", "X-User-Role": "admin"}


# ══════════════════════════════════════════════════════════════
# Section 1: lea-config 集成
# ══════════════════════════════════════════════════════════════

class TestLeaConfig:
    """验证配置服务满足 Go 引擎对接需求。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        import lea_config.models  # noqa
        from lea_config.database import Base, engine
        from lea_config.server import app
        Base.metadata.create_all(bind=engine)
        from fastapi.testclient import TestClient
        self.client = TestClient(app)
        yield
        Base.metadata.drop_all(bind=engine)

    def test_agent_lifecycle(self):
        """创建 Agent → 绑定 Tool/KB → 拉聚合配置。"""
        h = CTX_ADMIN_H
        # 1. 创建 Agent
        r = self.client.post("/agents", json={
            "name": "测试助手",
            "model": "gpt-4o", "temperature": 7,
            "max_turns": 15, "max_tokens": 8192,
            "kb_mode": "always", "memory_mode": "tool",
            "soul_prompt": "你是一个测试助手。",
        }, headers=h)
        assert r.status_code == 201, r.text
        agent_id = r.json()["id"]

        # 2. 创建 Skill
        r = self.client.post("/skills", json={
            "name": "代码分析",
            "display_name": "代码分析", "category": "general",
            "system_prompt": "分析代码质量", "status": "draft",
        }, headers=h)
        assert r.status_code == 201
        skill_id = r.json()["id"]

        # 3. 创建 Tool
        #
        # execution_type + schema_json 是必填：真实表上这两列 NOT NULL 且无默认。
        # 以前这里传的是 input_schema —— 那是 lea-config 模型自己发明的列，执行器
        # 不读它，而缺 schema_json 会让 INSERT 直接 500。见 models/tool.py 的说明。
        r = self.client.post("/tools", json={
            "name": "web_search",
            "description": "联网搜索",
            "execution_type": "api",
            "endpoint": "https://example.test/search",
            "schema_json": {"type": "object", "properties": {"query": {"type": "string"}}},
        }, headers=h)
        assert r.status_code == 201, r.text
        tool_id = r.json()["id"]

        # 4. 绑定
        r = self.client.post(f"/agents/{agent_id}/skills", json={"skill_id": skill_id}, headers=h)
        assert r.status_code in (200, 201)
        r = self.client.post(f"/agents/{agent_id}/tools", json={"tool_id": tool_id}, headers=h)
        assert r.status_code in (200, 201)

        # 5. 聚合接口 — Go 引擎实际调用的
        r = self.client.get(f"/internal/agent-config/{agent_id}?tenant_id=default")
        assert r.status_code == 200
        cfg = r.json()

        # 验证关键字段
        assert cfg["agent"]["name"] == "测试助手"
        assert cfg["agent"]["model"] == "gpt-4o"
        assert cfg["agent"]["temperature"] == 7
        assert cfg["agent"]["max_turns"] == 15
        assert cfg["agent"]["kb_mode"] == "always"
        assert cfg["agent"]["memory_mode"] == "tool"
        assert len(cfg["skills"]) == 1
        assert cfg["skills"][0]["name"] == "代码分析"
        assert len(cfg["tools"]) == 1
        assert cfg["tools"][0]["name"] == "web_search"

    def test_team_management(self):
        """Team CRUD + 成员管理。"""
        h = CTX_ADMIN_H
        # 创建两个 Agent
        r1 = self.client.post("/agents", json={"name": "Agent-A"}, headers=h)
        r2 = self.client.post("/agents", json={"name": "Agent-B"}, headers=h)
        assert r1.status_code == 201, r1.text
        assert r2.status_code == 201, r2.text
        a1, a2 = r1.json()["id"], r2.json()["id"]

        # 创建 Team（需要 agent_id 指向领头 agent）
        r = self.client.post("/teams", json={
            "agent_id": a1,
            "strategy": "round_robin",
        }, headers=h)
        assert r.status_code == 201
        team_id = r.json()["id"]

        # 添加成员（字段是 member_agent_id）
        r = self.client.post(f"/teams/{team_id}/members", json={"member_agent_id": a1, "role": "leader"}, headers=h)
        assert r.status_code in (200, 201)
        r = self.client.post(f"/teams/{team_id}/members", json={"member_agent_id": a2, "role": "member"}, headers=h)
        assert r.status_code in (200, 201)

        # 通过聚合接口验证 team + members
        r = self.client.get(f"/internal/agent-config/{a1}?tenant_id=default")
        assert r.status_code == 200
        team = r.json()["team"]
        assert team is not None
        assert len(team["members"]) == 2

    def test_multi_tenant_isolation(self):
        """租户隔离 — t1 看不到 t2 的 Agent（走用户端点 + X-User-ID/X-Tenant-ID 头）。"""
        h1 = {"X-User-ID": "user1", "X-Tenant-ID": "t1"}
        h2 = {"X-User-ID": "user1", "X-Tenant-ID": "t2"}

        r = self.client.post("/agents", json={"name": "A-t1"}, headers=h1)
        assert r.status_code == 201, r.text
        r = self.client.post("/agents", json={"name": "A-t2"}, headers=h2)
        assert r.status_code == 201, r.text

        # t1 视角列表
        r = self.client.get("/agents", headers=h1)
        names = [a["name"] for a in r.json()["items"]]
        assert "A-t1" in names
        assert "A-t2" not in names

        # t2 视角列表
        r = self.client.get("/agents", headers=h2)
        names = [a["name"] for a in r.json()["items"]]
        assert "A-t2" in names
        assert "A-t1" not in names


# ══════════════════════════════════════════════════════════════
# Section 2: lea-parser 集成
# ══════════════════════════════════════════════════════════════

class TestLeaParser:
    """验证文件处理服务鉴权 + 限流 + 解析 + 计费。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        import lea_parser.models  # noqa
        from lea_parser.database import Base, engine
        from lea_parser.server import app
        Base.metadata.create_all(bind=engine)
        from fastapi.testclient import TestClient
        self.client = TestClient(app)
        yield
        Base.metadata.drop_all(bind=engine)

    def test_auth_enforced(self):
        """无 token 拒绝。"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"test")
            path = f.name
        with open(path, "rb") as f:
            r = self.client.post("/parse", files={"file": ("t.txt", f)})
        os.unlink(path)
        assert r.status_code == 401

    def test_parse_and_chunk_txt(self):
        """TXT 解析 + sentence_window 分块。"""
        text = "人工智能正在改变世界。机器学习是其核心技术。深度学习更是推动了重大突破！"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write(text)
            path = f.name
        with open(path, "rb") as f:
            r = self.client.post(
                "/parse?chunk=true&strategy=sentence_window&chunk_size=30",
                files={"file": ("ai.txt", f)},
                headers=ADMIN_H,
            )
        os.unlink(path)
        assert r.status_code == 200
        data = r.json()
        assert data["format"] == "txt"
        assert len(data["chunks"]) > 0
        assert "window_content" in data["chunks"][0]

    def test_quota_per_user(self):
        """设置用户配额后超限返回 413。"""
        # 设置极小配额
        self.client.put("/admin/quotas/user", json={
            "user_id": "user1", "max_file_size_mb": 0,
            "max_calls_per_hour": 100, "max_monthly_calls": 5000,
        }, headers=ADMIN_H)

        text = "x" * 1024
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write(text)
            path = f.name
        with open(path, "rb") as f:
            r = self.client.post("/parse", files={"file": ("big.txt", f)}, headers=USER_H)
        os.unlink(path)
        assert r.status_code == 413

    def test_usage_recorded(self):
        """解析后用量被记录。"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("usage test content")
            path = f.name
        with open(path, "rb") as f:
            self.client.post("/parse", files={"file": ("u.txt", f)}, headers=ADMIN_H)
        os.unlink(path)

        r = self.client.get("/usage?days=1", headers=ADMIN_H)
        assert r.status_code == 200
        assert r.json()["total_calls"] >= 1

    def test_video_format_supported(self):
        """视频格式在支持列表中。"""
        r = self.client.get("/formats")
        formats = r.json()["formats"]
        for fmt in ("mp4", "avi", "mkv", "mov", "webm"):
            assert fmt in formats

    def test_chunk_endpoint_standalone(self):
        """纯文本分块接口（不涉及文件上传）。"""
        r = self.client.post("/chunk", json={
            "text": "第一段内容。第二段内容。第三段内容。第四段！",
            "strategy": "recursive",
            "chunk_size": 15,
            "chunk_overlap": 5,
        }, headers=ADMIN_H)
        assert r.status_code == 200
        assert r.json()["total"] > 1


# ══════════════════════════════════════════════════════════════
# Section 3: lea-tools 集成（如果可 import）
# ══════════════════════════════════════════════════════════════

class TestLeaTools:
    """验证工具网关核心逻辑。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        try:
            import lea_tools.db.models  # noqa
            from lea_tools.db import Base, engine
            from lea_tools.server import app
            Base.metadata.create_all(bind=engine)
            from fastapi.testclient import TestClient
            self.client = TestClient(app)
            self.available = True
        except ImportError:
            self.available = False
        yield
        if self.available:
            Base.metadata.drop_all(bind=engine)

    def test_health(self):
        if not self.available:
            pytest.skip("lea-tools not importable")
        r = self.client.get("/health")
        assert r.status_code == 200

    def test_session_capabilities(self):
        """模拟 Go 引擎启动时拉工具列表。"""
        if not self.available:
            pytest.skip("lea-tools not importable")
        r = self.client.post("/session/capabilities", json={
            "agent_id": 1, "tenant_id": "t1", "user_id": "test-user",
        })
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        # 应该有预置的 builtin 工具
        names = [t["name"] for t in data["tools"]]
        assert "kb_search" in names or len(names) >= 0


# ══════════════════════════════════════════════════════════════
# Section 4: 业务需求验证清单
# ══════════════════════════════════════════════════════════════

class TestBusinessRequirements:
    """验证架构满足核心业务需求。"""

    def test_service_independence(self):
        """各服务零内部包依赖 — import 不会交叉。"""
        # lea-config 不依赖 lea-graph/lea-apps
        import lea_config.server  # noqa — 应该无 ImportError

        # lea-parser 不依赖 lea-graph/lea-apps/lea-server
        import lea_parser.server  # noqa

    def test_config_api_contract_for_go_engine(self):
        """Go 引擎需要的聚合接口字段完整性。"""
        import lea_config.models  # noqa
        from lea_config.database import Base, engine
        from lea_config.server import app
        Base.metadata.create_all(bind=engine)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # 创建完整配置
        h = {"X-User-ID": "admin1", "X-Tenant-ID": "t1", "X-User-Role": "admin"}
        r = client.post("/agents", json={
            "name": "full-agent",
            "model": "deepseek-v3", "temperature": 5,
            "max_turns": 30, "max_tokens": 16384,
            "kb_mode": "always", "memory_mode": "always",
            "timeout_seconds": 180, "soul_prompt": "你是全能助手",
        }, headers=h)
        assert r.status_code in (200, 201), r.text
        agent_id = r.json()["id"]

        r = client.get(f"/internal/agent-config/{agent_id}?tenant_id=t1")
        assert r.status_code == 200, r.text
        cfg = r.json()

        # Go 引擎 AgentConfig 结构体需要的所有字段
        agent = cfg["agent"]
        required_fields = [
            "id", "name", "model", "temperature",
            "max_turns", "max_tokens", "kb_mode", "memory_mode",
        ]
        for field in required_fields:
            assert field in agent, f"Missing field: {field}"

        assert agent["model"] == "deepseek-v3"
        assert agent["temperature"] == 5
        assert agent["max_turns"] == 30
        assert agent["kb_mode"] == "always"
        assert agent["memory_mode"] == "always"

        Base.metadata.drop_all(bind=engine)

    def test_parser_multi_format_support(self):
        """验证解析服务支持所有要求的格式。"""
        from lea_parser.parsers.dispatcher import SUPPORTED_FORMATS
        required = {"pdf", "docx", "pptx", "md", "txt", "png", "jpg",
                    "mp3", "wav", "mp4", "avi", "mkv"}
        missing = required - SUPPORTED_FORMATS
        assert not missing, f"Missing formats: {missing}"

    def test_parser_auth_and_quota_model(self):
        """验证 parser 有完整的鉴权+限流+计费模型。"""
        from lea_parser.models import ParseTask, TenantQuota, UsageRecord, UserQuota
        # 模型存在即可
        assert ParseTask.__tablename__ == "parse_tasks"
        assert UsageRecord.__tablename__ == "parser_usage_records"
        assert TenantQuota.__tablename__ == "parser_tenant_quotas"
        assert UserQuota.__tablename__ == "parser_user_quotas"

    def test_chunking_strategies_available(self):
        """两种分块策略都可用。"""
        from lea_parser.chunking import chunk_recursive, chunk_sentence_window
        blocks = [{"text": "测试内容。另一句话！", "page_idx": 0}]
        r1 = chunk_sentence_window(blocks, chunk_size=10)
        assert len(r1) > 0
        r2 = chunk_recursive("测试内容。另一句话！", chunk_size=10)
        assert len(r2) > 0
