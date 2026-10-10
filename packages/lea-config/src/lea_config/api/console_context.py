"""Current administration-console identity and capabilities."""

from fastapi import APIRouter, Depends

from lea_authkit import Actor, require_actor
from lea_config.api._auth_helpers import require_admin

router = APIRouter(prefix="/console-context", tags=["console-context"])


@router.get("")
def get_console_context(actor: Actor = Depends(require_actor)):
    require_admin(actor)
    role = "platform_admin" if actor.is_platform_admin else "admin"
    capabilities = [
        "resources.manage",
        "publications.review",
        "publications.lifecycle",
        "checks.run",
        "operations.runs",
        "operations.document_jobs",
        "usage.read",
        "quotas.manage",
        "audit.read",
        "providers.manage",
    ]
    if actor.is_platform_admin:
        capabilities.extend(["tenants.cross_read", "publications.global"])
    return {
        "user_id": actor.user_id,
        "tenant_id": actor.tenant_id,
        "role": role,
        "capabilities": capabilities,
    }
