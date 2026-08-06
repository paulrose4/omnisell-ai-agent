# Agent Payload Compatibility Scan

## Evidence boundary

The versioned JSON report in this directory is deterministic fixture evidence. It proves the scanner's classification, redaction and report contract; it is not evidence that a real deployment database has been scanned.

No persistent local OmniSell database was present in the workspace on 2026-08-05. Real historical compatibility therefore remains an operator gate. Do not mark it complete from unit tests or synthetic records.

## Read-only scanner

The scanner requires an explicit tenant allowlist and never discovers or scans all tenants implicitly:

```powershell
.venv\Scripts\python.exe scripts\scan_agent_payload_compatibility.py `
  --tenant-id <approved-tenant-id> `
  --output artifacts\agent-payload-compatibility-report.json
```

Repeat `--tenant-id` for each tenant approved by the database owner. `--database-url` is optional; when omitted, the configured application database is used without printing its URL.

For PostgreSQL, each tenant query runs in a read-only transaction and binds `app.current_tenant_id`. The report contains counts, hashed tenant references, stable rejection reason codes and record IDs. It never includes the persisted analysis payload.

## Required operator evidence

Before the interview candidate release, archive the ignored raw report in the approved evidence store and version only a reviewed, sanitized summary containing:

- database environment and revision;
- explicitly approved tenant count;
- records scanned, empty, v1-adapted, v2-validated and rejected;
- rejection reason distribution;
- manual-review record IDs and disposition;
- database owner and reviewer sign-off;
- confirmation that row counts and payload hashes were unchanged.

Any rejected record remains quarantined by the read path as `legacy_incompatible`. The scanner must not rewrite, delete or silently normalize historical rows.
