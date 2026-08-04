from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from domain.models import DataSource, SKUCostProfile
from infrastructure.repositories.profitability import DataImportJobRepository, ImportIdempotencyConflict, SKUCostProfileRepository


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
REQUIRED_COLUMNS = {
    "seller_sku", "marketplace_id", "currency", "product_cost", "domestic_shipping_cost", "international_freight_cost",
    "duty_cost", "prep_cost", "packaging_cost", "estimated_referral_fee_rate", "estimated_fulfillment_fee",
    "estimated_storage_cost", "estimated_return_loss_rate", "exchange_rate_buffer_rate", "target_margin_rate",
    "minimum_margin_rate", "effective_from", "source",
}


@dataclass(frozen=True)
class ImportRowError:
    row_number: int
    code: str
    field: str | None
    message: str


@dataclass(frozen=True)
class CostImportResult:
    job_id: str
    dry_run: bool
    total_rows: int
    valid_rows: int
    committed_rows: int
    content_sha256: str
    errors: tuple[ImportRowError, ...]
    repeated: bool = False


class CostCSVImporter:
    def __init__(self, repository: SKUCostProfileRepository) -> None:
        self.repository = repository
        self.jobs = DataImportJobRepository(repository.session_factory)

    def import_bytes(self, *, tenant_id: str, actor_id: str, content: bytes, idempotency_key: str, dry_run: bool) -> CostImportResult:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("cost CSV exceeds the configured 2 MiB limit")
        checksum = hashlib.sha256(content).hexdigest()
        persisted_idempotency_key = f"{idempotency_key}:dry-run" if dry_run else idempotency_key
        existing = self.jobs.get_by_idempotency(tenant_id=tenant_id, import_type="sku_cost", idempotency_key=persisted_idempotency_key)
        if existing is not None:
            if existing.content_sha256 != checksum:
                raise ImportIdempotencyConflict("idempotency key was already used with different content")
            return CostImportResult(
                job_id=existing.id, dry_run=existing.dry_run, total_rows=existing.total_rows,
                valid_rows=existing.valid_rows, committed_rows=existing.committed_rows,
                content_sha256=checksum, errors=tuple(ImportRowError(**item) for item in existing.errors), repeated=True,
            )
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("cost CSV must be UTF-8 encoded") from exc
        reader = csv.DictReader(io.StringIO(decoded, newline=""))
        headers = set(reader.fieldnames or [])
        missing_headers = sorted(REQUIRED_COLUMNS - headers)
        if missing_headers:
            raise ValueError("missing required CSV columns: " + ", ".join(missing_headers))
        profiles: list[SKUCostProfile] = []
        errors: list[ImportRowError] = []
        seen: set[tuple[str, str, datetime]] = set()
        total_rows = 0
        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            if total_rows > MAX_ROWS:
                raise ValueError(f"cost CSV exceeds the configured {MAX_ROWS} row limit")
            try:
                profile = self._parse_row(tenant_id, row_number, row)
                key = (profile.marketplace_id, profile.seller_sku, profile.effective_from)
                if key in seen:
                    raise RowValidationError("duplicate_row", None, "duplicate SKU/marketplace/effective_from in file")
                seen.add(key)
                profiles.append(profile)
            except RowValidationError as exc:
                errors.append(ImportRowError(row_number=row_number, code=exc.code, field=exc.field, message=str(exc)))
        job_id = self.jobs.create(
            tenant_id=tenant_id, actor_id=actor_id, import_type="sku_cost", idempotency_key=persisted_idempotency_key,
            content_sha256=checksum, dry_run=dry_run, total_rows=total_rows, valid_rows=len(profiles),
            errors=[asdict(error) for error in errors],
        )
        committed_rows = 0
        if not dry_run and not errors and profiles:
            self.repository.save_profiles(tenant_id=tenant_id, profiles=profiles, import_job_id=job_id)
            committed_rows = len(profiles)
            self.jobs.mark_committed(job_id=job_id, committed_rows=committed_rows, completed_at=datetime.now(UTC))
        return CostImportResult(
            job_id=job_id, dry_run=dry_run, total_rows=total_rows, valid_rows=len(profiles), committed_rows=committed_rows,
            content_sha256=checksum, errors=tuple(errors),
        )

    def _parse_row(self, tenant_id: str, row_number: int, row: dict[str, str | None]) -> SKUCostProfile:
        for field_name, raw in row.items():
            value = (raw or "").strip()
            if value.startswith(CSV_FORMULA_PREFIXES):
                raise RowValidationError("csv_injection", field_name, f"unsafe spreadsheet formula prefix in {field_name}")
        try:
            source = (row.get("source") or "").strip().lower()
            if source not in {item.value for item in DataSource}:
                raise RowValidationError("invalid_source", "source", "source is not an allowed data source")
            return SKUCostProfile(
                tenant_id=tenant_id, seller_sku=self._required(row, "seller_sku"), marketplace_id=self._required(row, "marketplace_id"),
                currency=self._required(row, "currency"), product_cost=self._decimal(row, "product_cost"),
                domestic_shipping_cost=self._decimal(row, "domestic_shipping_cost"), international_freight_cost=self._decimal(row, "international_freight_cost"),
                duty_cost=self._decimal(row, "duty_cost"), prep_cost=self._decimal(row, "prep_cost"), packaging_cost=self._decimal(row, "packaging_cost"),
                estimated_referral_fee_rate=self._decimal(row, "estimated_referral_fee_rate"), estimated_fulfillment_fee=self._decimal(row, "estimated_fulfillment_fee"),
                estimated_storage_cost=self._decimal(row, "estimated_storage_cost"), estimated_return_loss_rate=self._decimal(row, "estimated_return_loss_rate"),
                exchange_rate_buffer_rate=self._decimal(row, "exchange_rate_buffer_rate"), target_margin_rate=self._decimal(row, "target_margin_rate"),
                minimum_margin_rate=self._decimal(row, "minimum_margin_rate"), effective_from=self._datetime(row, "effective_from"),
                effective_to=self._datetime(row, "effective_to", required=False), source=source,
            )
        except RowValidationError:
            raise
        except (ValueError, InvalidOperation) as exc:
            raise RowValidationError("invalid_value", None, str(exc)) from exc

    @staticmethod
    def _required(row: dict[str, str | None], name: str) -> str:
        value = (row.get(name) or "").strip()
        if not value:
            raise RowValidationError("required", name, f"{name} is required")
        return value

    @staticmethod
    def _decimal(row: dict[str, str | None], name: str) -> Decimal | None:
        value = (row.get(name) or "").strip()
        return None if value == "" else Decimal(value)

    @staticmethod
    def _datetime(row: dict[str, str | None], name: str, required: bool = True) -> datetime | None:
        value = (row.get(name) or "").strip()
        if not value:
            if required:
                raise RowValidationError("required", name, f"{name} is required")
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise RowValidationError("timezone_required", name, f"{name} must include a timezone")
        return parsed


class RowValidationError(ValueError):
    def __init__(self, code: str, field: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
