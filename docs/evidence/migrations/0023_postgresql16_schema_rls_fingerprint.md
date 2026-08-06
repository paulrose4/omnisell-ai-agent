# OmniSell Migration Roundtrip & RLS Evidence

- Dialect: `postgresql`
- Revision: `0023`
- schema_sha256: `8063394e78a59e7774d441a5498595f4d1454ae662315979843b7ca52540ea92`
- Tables captured: **57**
- RLS authoritative: **True**
- Roundtrip steps executed:
  1. Drop and recreate `omnisell_0023_roundtrip` owned by `omnisell` admin role.
  2. `alembic upgrade head` to baseline `0023_frozen_baseline`.
  3. Capture schema fingerprint A.
  4. `alembic downgrade base` then `alembic upgrade head`.
  5. Capture schema fingerprint B.
  6. Assert `A == B` and run `alembic check`.
- Migration SHA256 fingerprints:
  - `alembic/versions/0023_frozen_baseline.py` -> `c5d1846062dbe51b11d4041a8e1fdbf1f2d14701207c125eae29dcef559d8223`

All schema columns, primary keys, unique constraints, indexes and tenant-scoped
RLS policies are reproduced byte-for-byte across the upgrade/downgrade cycle.
