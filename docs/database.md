# Database

[Project overview](../README.md) · [Architecture](architecture.md) · [API](api.md)

## raw_documents

Migration `0001_raw_documents` creates the only application table:

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

Alembic also maintains its `alembic_version` tracking table. There are no finding,
evidence, extraction-run, taxonomy, or vector tables in this phase.

PostgreSQL stores timezone-aware timestamps as instants; API input and output use
UTC. `retrieved_at` is caller-supplied provenance, while `created_at` records insertion
time using the database default. UUID generation happens in the application, not
through a PostgreSQL extension or database default.

Raw text is stored without rewriting, summarization, or Unicode normalization.
Future derived records should reference `raw_documents.id` so original material
remains recoverable.

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

The current revision is `0001_raw_documents`. `alembic check` compares the migrated
schema with SQLAlchemy metadata for unexpected differences. The async Alembic
environment reads the same required configuration as the application.

Application startup never calls `Base.metadata.create_all()` or replaces migration
management. Tests apply the real migration inside isolated temporary schemas.
See [development checks](architecture.md#development-checks).

The initial migration supports downgrade. **Downgrading to `base` drops
`raw_documents` and permanently deletes its rows.** Exercise downgrade checks only
on a disposable database.

## Local persistence

Docker Compose keeps PostgreSQL data in the named `postgres_data` volume. Stopping
the container does not remove the volume. The application itself runs locally,
outside Compose; no worker containers or other infrastructure are required.
