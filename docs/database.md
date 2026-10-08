# Database

[Project overview](../README.md) · [Architecture](architecture.md) · [API](api.md)

## raw_documents

Published migration `0001_raw_documents` creates the original document table and
remains unchanged:

| Column | PostgreSQL type | Constraints/default |
| --- | --- | --- |
| `id` | UUID | Primary key, required; application generates UUID v4 |
| `source_name` | VARCHAR(255) | Required |
| `source_url` | TEXT | Nullable |
| `document_type` | VARCHAR(255) | Required |
| `raw_text` | TEXT | Required |
| `content_hash` | CHAR(64) | Required, unique |
| `retrieved_at` | TIMESTAMP WITH TIME ZONE | Nullable |
| `created_at` | TIMESTAMP WITH TIME ZONE | Required, database `now()` default |

Alembic also maintains its `alembic_version` tracking table. Phase 1B adds the two
tables below. There are no extraction-run, taxonomy lookup, or vector tables.

PostgreSQL stores timezone-aware timestamps as instants; API input and output use
UTC. `retrieved_at` is caller-supplied provenance, while `created_at` records insertion
time using the database default. UUID generation happens in the application, not
through a PostgreSQL extension or database default.

Raw text is stored without rewriting, summarization, or Unicode normalization.
Findings reference `raw_documents.id` so original material remains recoverable.

## security_findings

Migration `0002_findings_taxonomy` adds:

| Column | PostgreSQL type | Constraints/default |
| --- | --- | --- |
| `id` | UUID | Primary key, required; application UUID v4 |
| `raw_document_id` | UUID | Required FK to `raw_documents.id`, `ON DELETE RESTRICT` |
| `source_finding_id` | VARCHAR(255) | Nullable; unique with `raw_document_id` when non-null |
| `source_title` | VARCHAR(500) | Nullable, original source text |
| `source_severity` | VARCHAR(255) | Nullable, original source text |
| `source_category` | VARCHAR(255) | Nullable, original source text |
| `title` | VARCHAR(500) | Required, database nonblank check |
| `severity` | VARCHAR(16) | Required, allowed-value CHECK |
| `canonical_category` | VARCHAR(40) | Required, allowed-value CHECK |
| `summary`, `root_cause`, `impact`, `recommendation` | TEXT | Nullable |
| `affected_contract`, `affected_function` | VARCHAR(255) | Nullable |
| `source_file` | VARCHAR(1024) | Nullable |
| `line_start`, `line_end` | INTEGER | Nullable; positive if present; ordered if both present |
| `protocol_name`, `language` | VARCHAR(255) | Nullable |
| `verification_status` | VARCHAR(16) | Required, allowed-value CHECK, `UNREVIEWED` default |
| `created_at`, `updated_at` | TIMESTAMP WITH TIME ZONE | Required, database `now()` defaults |

The creation service explicitly persists `UNREVIEWED` verification status; clients
cannot supply it. The database default is an additional integrity boundary, and
the CHECK constraint retains `VERIFIED` and `REJECTED` for a future explicit review
workflow. Direct database access remains a trusted administrative boundary.

Application enums are stored as VARCHAR with CHECK constraints, not PostgreSQL
ENUM types. The independent source fields are never replaced with normalized
values. Missing optional data remains null. `updated_at` starts at creation time;
SQLAlchemy sets it to `now()` on ORM updates. No update endpoint or database
timestamp trigger is introduced.

The unique constraint `uq_security_findings_document_source_id` permits multiple
null source IDs using ordinary PostgreSQL uniqueness semantics. IDs remain exact
and case-sensitive; titles are not unique. The composite index also supports
document-specific lookups. A duplicate ID within the same document becomes 409
after rollback, including races.

## finding_evidence

| Column | PostgreSQL type | Constraints/default |
| --- | --- | --- |
| `id` | UUID | Primary key, required; application UUID v4 |
| `finding_id` | UUID | Required FK to `security_findings.id`, `ON DELETE CASCADE`, indexed |
| `evidence_type` | VARCHAR(16) | Required, allowed-value CHECK, `SOURCE_TEXT` default |
| `field_name` | VARCHAR(32) | Nullable; allowed normalized field names checked |
| `source_excerpt` | TEXT | Required, exact source text |
| `start_offset` | INTEGER | Required, nonnegative CHECK |
| `end_offset` | INTEGER | Required, greater than start CHECK |
| `created_at` | TIMESTAMP WITH TIME ZONE | Required, database `now()` default |

Offsets are always resolved before persistence, even when omitted by the caller.
They count Python Unicode code points and define the exact half-open source slice.
Text containment and document-length checks belong to the deterministic service,
not a cross-table database CHECK.

Finding creation loads and locks the source document for shared access, validates
all evidence, and commits the finding/evidence together. A validation failure
persists neither. The lock prevents concurrent document modification or deletion
during creation; direct database writes after creation are outside this service
guarantee. The API has no document-text update endpoint.

A referenced raw document cannot be deleted. Deleting a finding removes its
evidence through the database FK without deleting the raw document. No deletion
API is added in this phase.

## Duplicate boundary

The named constraint `uq_raw_documents_content_hash` creates a unique PostgreSQL
index on `content_hash`. The primary key has its own unique index on `id`.
The server computes SHA-256 from the exact UTF-8 bytes of `raw_text`.

Creation uses an explicit transaction. PostgreSQL rejects duplicate hashes, and
the service rolls back before mapping a unique violation on that specific
constraint to HTTP 409. This works for concurrent submissions as well as sequential
ones. No pre-insert duplicate query is needed. Other integrity errors remain
database errors and receive the API's sanitized 500 response.

GET uses an explicit read transaction. The ORM uses parameterized operations.

Deduplication is global to raw content, independent of source metadata. A rejected
duplicate does not add another provenance record or update the existing row.
Multiple-source attribution is not modeled in Phase 1A.

## Migrations

Set `DATABASE_URL` through `.env` or the environment, then run:

```sh
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

The current revision is `0002_findings_taxonomy`, whose `down_revision` is
`0001_raw_documents`. A clean database upgrades through both revisions.
`alembic check` compares the migrated
schema with SQLAlchemy metadata for unexpected differences. The async Alembic
environment reads the same required configuration as the application.

Application startup never calls `Base.metadata.create_all()` or replaces migration
management. Tests apply the real migration inside isolated temporary schemas.
See [development checks](architecture.md#development-checks).

Phase 1B can be reversed on a disposable database:

```sh
uv run alembic downgrade 0001_raw_documents
uv run alembic upgrade head
```

**Downgrading Phase 1B deletes findings and evidence**, while preserving the raw
document table and its rows. Re-upgrade recreates empty finding/evidence tables.
Published migration `0001_raw_documents` is never edited or regenerated.

The initial migration supports downgrade. **Downgrading to `base` drops
`raw_documents` and permanently deletes its rows.** Exercise downgrade checks only
on a disposable database.

## Local persistence

Docker Compose keeps PostgreSQL data in the named `postgres_data` volume. Stopping
the container does not remove the volume. The application itself runs locally,
outside Compose; no worker containers or other infrastructure are required.
