"""凭证加解密 + 选取。"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from lea_tools.config import get_encryption_settings
from lea_tools.db.models import ToolCredential


def _get_cipher() -> Fernet:
    key = get_encryption_settings().key
    if not key:
        key = Fernet.generate_key().decode()
    if len(key) != 44:
        key = base64.urlsafe_b64encode(key.ljust(32)[:32].encode()).decode()
    return Fernet(key.encode())


def encrypt_key(plain: str) -> str:
    return _get_cipher().encrypt(plain.encode()).decode()


def decrypt_key(encrypted: str) -> str:
    return _get_cipher().decrypt(encrypted.encode()).decode()


def get_credential_for_tool(
    db: Session, tenant_id: str, tool_id: int
) -> dict | None:
    cred = (
        db.query(ToolCredential)
        .filter(
            ToolCredential.tool_id == tool_id,
            ToolCredential.tenant_id == tenant_id,
        )
        .first()
    )
    if not cred:
        cred = (
            db.query(ToolCredential)
            .filter(
                ToolCredential.tool_id == tool_id,
                ToolCredential.is_platform_default.is_(True),
            )
            .first()
        )
    if not cred:
        return None

    return {
        "api_key": decrypt_key(cred.api_key_enc),
        "api_base": cred.api_base,
        "extra": cred.extra_config,
    }
