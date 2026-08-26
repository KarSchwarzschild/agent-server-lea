"""lea-parser E2E 测试 — 验证解析/分块/鉴权/限流/用量。"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("DB_HOST", "sqlite")
os.environ.setdefault("DB_NAME", "test_parser")
os.environ.setdefault("JWT_SECRET", "test-secret")

from jose import jwt
from fastapi.testclient import TestClient

from lea_parser.server import app
from lea_parser.database import Base, engine

TOKEN = jwt.encode(
    {"sub": "user1", "tenant_id": "t1", "role": "admin"},
    "test-secret", algorithm="HS256",
)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

USER_TOKEN = jwt.encode(
    {"sub": "user2", "tenant_id": "t1", "role": "user"},
    "test-secret", algorithm="HS256",
)
USER_HEADERS = {"Authorization": f"Bearer {USER_TOKEN}"}


@pytest.fixture(autouse=True)
def setup_db():
    import lea_parser.models  # noqa
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "lea-parser"


def test_formats(client):
    r = client.get("/formats")
    assert r.status_code == 200
    formats = r.json()["formats"]
    assert "pdf" in formats
    assert "mp4" in formats
    assert "docx" in formats


def test_auth_required(client):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello world")
        f.flush()
        f.seek(0)
        r = client.post("/parse", files={"file": ("test.txt", f, "text/plain")})
    os.unlink(f.name)
    assert r.status_code == 401


def test_parse_text(client):
    content = "这是一段测试文本。用来验证分块效果。另外一句话！"
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w",
                                     encoding="utf-8") as f:
        f.write(content)
        path = f.name

    with open(path, "rb") as f:
        r = client.post(
            "/parse?chunk=true&strategy=sentence_window&chunk_size=50",
            files={"file": ("test.txt", f, "text/plain")},
            headers=HEADERS,
        )
    os.unlink(path)
    assert r.status_code == 200
    data = r.json()
    assert data["format"] == "txt"
    assert data["task_id"]
    assert len(data["chunks"]) > 0
    assert data["duration_ms"] >= 0


def test_parse_md_recursive(client):
    md = "# Title\n\nParagraph one.\n\nParagraph two.\n\n## Section\n\nMore text."
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w",
                                     encoding="utf-8") as f:
        f.write(md)
        path = f.name

    with open(path, "rb") as f:
        r = client.post(
            "/parse?chunk=true&strategy=recursive",
            files={"file": ("doc.md", f, "text/markdown")},
            headers=HEADERS,
        )
    os.unlink(path)
    assert r.status_code == 200
    assert len(r.json()["chunks"]) > 0


def test_chunk_endpoint(client):
    r = client.post("/chunk", json={
        "text": "第一句话。第二句话。第三句话。",
        "strategy": "sentence_window",
        "chunk_size": 10,
    }, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["total"] > 0


def test_quota_admin(client):
    r = client.get("/admin/quotas/tenant", headers=HEADERS)
    assert r.status_code == 200
    assert "max_calls_per_hour" in r.json()

    r = client.put("/admin/quotas/tenant", json={
        "max_calls_per_hour": 1000,
        "max_file_size_mb": 300,
        "max_monthly_calls": 100000,
        "max_monthly_size_mb": 20000,
    }, headers=HEADERS)
    assert r.status_code == 200

    r = client.put("/admin/quotas/user", json={
        "user_id": "user2",
        "max_calls_per_hour": 50,
        "max_file_size_mb": 25,
        "max_monthly_calls": 2000,
    }, headers=HEADERS)
    assert r.status_code == 200

    r = client.get("/admin/quotas/user/user2", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["max_calls_per_hour"] == 50


def test_quota_non_admin_forbidden(client):
    r = client.get("/admin/quotas/tenant", headers=USER_HEADERS)
    assert r.status_code == 403


def test_usage(client):
    content = "测试用量记录。"
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w",
                                     encoding="utf-8") as f:
        f.write(content)
        path = f.name
    with open(path, "rb") as f:
        client.post("/parse", files={"file": ("u.txt", f, "text/plain")},
                    headers=HEADERS)
    os.unlink(path)

    r = client.get("/usage?days=1", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["total_calls"] >= 1


def test_unsupported_format(client):
    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        f.write(b"binary data")
        path = f.name
    with open(path, "rb") as f:
        r = client.post("/parse", files={"file": ("x.xyz", f, "application/octet-stream")},
                        headers=HEADERS)
    os.unlink(path)
    assert r.status_code == 400
