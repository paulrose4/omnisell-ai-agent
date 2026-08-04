from __future__ import annotations

from dataclasses import dataclass


class AmazonWriteBlocked(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AmazonWriteGate:
    global_enabled: bool = False
    tenant_enabled: bool = False
    pilot_read_only: bool = True

    def require_write(self, *, action: str, tenant_id: str, resource_id: str | None = None) -> None:
        if self.pilot_read_only:
            raise AmazonWriteBlocked("blocked_read_only_pilot", "Amazon writes are disabled during the read-only production pilot.")
        if not self.global_enabled:
            raise AmazonWriteBlocked("blocked_global_write_disabled", "Amazon writes are disabled globally.")
        if not self.tenant_enabled:
            raise AmazonWriteBlocked("blocked_tenant_write_disabled", f"Amazon writes are disabled for tenant {tenant_id}.")