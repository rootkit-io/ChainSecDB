# Security Research DB — Phase 1A

A small backend for storing original security documents with provenance. It accepts
text, validates metadata, computes SHA-256 on the server, stores the document in
PostgreSQL, and retrieves it by UUID. There is no AI functionality.

## Local setup

Requires Python 3.12+, `uv`, and Docker Compose (or an existing PostgreSQL database).
Run these commands from this repository:

```sh
uv sync --python 3.12
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Compose starts PostgreSQL 16 only, binds it to localhost, checks readiness with
`pg_isready`, and persists data in the `postgres_data` named volume. Its credentials
are development examples. If PostgreSQL is already available, set `DATABASE_URL`
to that database instead. The driver must be `postgresql+psycopg`.

Environment variables override `.env`. `DATABASE_URL` is required; there is no
fallback database or production credential. Missing/invalid configuration fails
startup with a clear, sanitized error. Startup also checks database connectivity.
Schema creation is explicit through Alembic, never through application startup.
Run migrations before starting the application. Do not use development credentials
in production; supply production configuration through its environment.

OpenAPI documentation is available at <http://127.0.0.1:8000/docs>.

## API

### `POST /documents`

```sh
curl -i http://127.0.0.1:8000/documents \
  -H 'Content-Type: application/json' \
  -d '{"source_name":"manual","source_url":"https://example.com/security-report","document_type":"audit_report","raw_text":"Security report contents...","retrieved_at":null}'
```

Returns **201** with `id`, `source_name`, `source_url`, `document_type`,
`content_hash`, `retrieved_at`, and `created_at`. The response omits `raw_text`.

- `source_name`, `document_type`, and `raw_text` are required strings.
- Source name and document type are trimmed, nonblank, and at most 255 characters.
- `source_url` is optional HTTP/HTTPS metadata, trimmed and validated, at most
  2,083 characters. Its spelling is otherwise preserved. No URL is fetched.
- `retrieved_at` is optional and must include a timezone when provided; ISO 8601
  timestamps such as `2026-10-08T14:00:00+02:00` are accepted and normalized to UTC.
- Raw text must be nonblank and no larger than 1 MiB encoded as UTF-8. Whitespace,
  newlines, Unicode normalization, and all other content remain unchanged.
- NUL characters and invalid Unicode surrogates are rejected because PostgreSQL
  UTF-8 text cannot preserve them.
- Unknown request fields are rejected, including client-supplied `content_hash`.
- Request bodies are capped at 8 MiB before JSON parsing, including streamed
  requests without `Content-Length`. This leaves room for JSON escaping.

Hash formula: `hashlib.sha256(raw_text.encode("utf-8")).hexdigest()`.
Duplicate protection applies across all source metadata: identical text produces
the same hash and cannot be inserted twice. Text differing by whitespace or Unicode
normalization remains distinct.

### `GET /documents/{id}`

```sh
curl -i http://127.0.0.1:8000/documents/REPLACE_WITH_RETURNED_UUID
```

Returns **200** with the same metadata plus exact original `raw_text`.
Timestamps are serialized in UTC. A valid UUID with no matching row returns **404**;
a malformed UUID returns **422**.

### Errors

| Status | Meaning | Response |
| --- | --- | --- |
| 404 | Document absent | `{"detail":"Document not found"}` |
| 409 | Duplicate content | `{"detail":"Document content already exists"}` |
| 413 | Request body over 8 MiB | `{"detail":"Request body exceeds 8 MiB"}` |
| 422 | Invalid input | `{"detail":[{"loc":[...],"type":"...","msg":"..."}]}` |
| 500 | Database failure | `{"detail":"Database operation failed"}` |

Validation responses omit submitted values and exception context. Database errors
never expose SQL, connection strings, credentials, or stack traces. Application
logs report the database exception class, not SQL parameters or document contents.
The ORM uses parameterized statements. Raw text is never executed or rendered as
code, and links are never followed. No authentication is implemented in this phase.

## Database and transactions

Migration `0001_raw_documents` creates:

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

PostgreSQL stores timezone-aware timestamps as instants; API input and output use
UTC. The named unique constraint `uq_raw_documents_content_hash` is the final
duplicate boundary. Creation commits within an explicit transaction. PostgreSQL's
unique violation on that specific constraint becomes a conflict after rollback,
including concurrent insert races. Other integrity errors remain database errors.
GET uses an explicit read transaction. No pre-insert duplicate query is needed.

Original source material remains the source of truth. Future findings can reference
`raw_documents.id`; no finding tables or placeholders exist yet.

## Checks and tests

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/security_research uv run pytest -q
uv run alembic current
uv run alembic check
```

`TEST_DATABASE_URL` must be supplied explicitly; pytest does not silently skip
integration coverage or substitute SQLite. The PostgreSQL test role needs schema
creation permission. Tests create a random temporary schema, apply the actual
Alembic migration there, clear only that schema's rows between tests, and drop only
that schema during cleanup. Existing application tables are untouched. A separate
test database is still recommended. The shell assignment above is independent of
`.env`; set it to the database you intend to use.

Coverage includes creation/retrieval, exact UTF-8 preservation, hash determinism,
database constraints, concurrent duplicate submissions, rollback/session reuse,
input validation, UTC conversion, body-size boundaries and streamed uploads,
client hash rejection, configuration/startup failures, and real PostgreSQL error
sanitization. Database behavior is exercised without database mocks.

## Layout

```text
app/
  api/documents.py          # HTTP endpoints
  core/config.py           # Required environment configuration
  core/body_limit.py       # Bounded request bodies before parsing
  db/base.py               # SQLAlchemy declarative base
  db/session.py            # Per-request async session
  db/models/raw_document.py
  schemas/documents.py     # Input and output validation
  services/documents.py    # Hashing, transactions, duplicate handling
  main.py                  # App lifecycle and sanitized error handlers
alembic/                   # Initial migration and migration environment
tests/                     # Unit and real PostgreSQL integration coverage
```

## Phase boundary

Phase 1A only. No AI/LLM APIs, extraction, taxonomy, embeddings, vector search,
scraping, external-source integrations, agents, queues, workers, authentication,
or frontend. EVMbench is reference material only; this project has no dependency
on it. Phase 1B requires a new instruction.
