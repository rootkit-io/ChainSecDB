# Backend architecture

[Project overview](../README.md) · [API](api.md) · [Database](database.md) · [Security](security.md)

## Current scope

Phase 1A stores original security documents with provenance and deterministic hashes.
Phase 1B stores manually supplied findings, source and canonical labels, review
status, and exact source evidence. There is no AI functionality, source connector,
automated extraction, or automated taxonomy mapping.

```mermaid
flowchart TD
    Request[HTTP request] --> Limit[8 MiB body limit]
    Limit --> API[FastAPI route and Pydantic validation]
    API --> Service[Document service]
    Service --> Evidence[Exact evidence validation for findings]
    Evidence --> Session[Async SQLAlchemy session / psycopg]
    Session --> DB[(PostgreSQL)]
    DB --> Response[Typed response / sanitized error]
```

The backend uses Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, psycopg, and
Alembic. There is no repository layer, event bus, queue, worker, or frontend.
EVMbench was reference material only; this project has no dependency on it.

## Layout

```text
app/
  api/documents.py          # POST /documents and GET /documents/{id}
  api/findings.py           # Finding creation, retrieval, and document listing
  core/config.py           # Required environment configuration
  core/body_limit.py       # Bound HTTP bodies before JSON parsing
  db/base.py               # SQLAlchemy declarative base
  db/session.py            # Per-request async session
  db/models/raw_document.py
  db/models/security_finding.py
  db/models/finding_evidence.py
  schemas/documents.py     # Request and response validation
  schemas/findings.py      # Finding fields, source labels, evidence, and offsets
  services/documents.py    # Hashing, transactions, duplicate handling
  services/findings.py     # Atomic creation and exact evidence verification
  taxonomy/categories.py  # Stable application-level vocabularies
  main.py                  # Lifecycle and sanitized error handlers
alembic/                   # Migration environment and initial migration
tests/                     # Unit and real PostgreSQL integration coverage
docs/                      # Contracts, architecture, and planned direction
```

## Lifecycle and transactions

Startup loads configuration, creates the async database engine, and checks
connectivity with `SELECT 1`. Missing/invalid configuration or an unavailable
database fails startup with a clear, sanitized error. Shutdown disposes the engine.

Startup does not create tables or run migrations. Apply Alembic migrations first.
A successful connectivity check alone does not establish that the schema exists.

Each request receives its own async session. The document service owns explicit
transactions; successful creation commits, and failed creation rolls back. Finding
creation holds a shared lock on its document while validating and persisting
evidence, preventing concurrent source updates or deletion during that transaction.
All evidence must pass before anything is committed. GET uses an explicit read
transaction and eagerly loads evidence without async lazy-loading surprises.
Duplicates map to 409; SQLAlchemy errors receive a generic 500 response. See [database](database.md) and
[security](security.md) for the integrity and disclosure boundaries.

## Configuration and local PostgreSQL

Copy [`.env.example`](../.env.example) to `.env`. Environment variables override
the file. `DATABASE_URL` is required, must name a database, and must use the
`postgresql+psycopg` driver. There is no fallback database or production credential.

Local development example:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/security_research
```

The [Compose file](../docker-compose.yml) starts PostgreSQL 16 only, binds port
5432 to localhost, checks readiness with `pg_isready`, and persists data in the
`postgres_data` named volume. The example credentials are for development.
For another database, update `DATABASE_URL` and skip Compose. Supply production
configuration through its environment; never commit real secrets.

Follow the [quick start](../README.md#quick-start) to install and run the backend.

## Development checks

Run from the repository root:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/security_research \
  uv run pytest -q
```

`TEST_DATABASE_URL` must be supplied explicitly. Pytest does not silently skip
integration coverage or substitute SQLite. The shell assignment above is
independent of `.env`; set it to the database you intend to use. The test role
needs schema creation permission. A separate test database is recommended.

Tests create a random temporary schema, apply the actual Alembic migration there,
clear only that schema's rows between tests, and drop only that schema during
cleanup. Existing application tables are untouched. Persistence tests use real
PostgreSQL without database mocks.

The Phase 1A verification run passed **49 tests**; these remain in the suite.
Run the commands above for current results. Coverage includes creation/retrieval, exact
UTF-8 preservation, hash determinism, schema constraints, concurrent duplicates,
rollback/session reuse, input validation, UTC conversion, request/text size
boundaries, streamed bodies, client hash rejection, startup failures, and real
PostgreSQL error sanitization. Phase 1B adds source-label preservation, all canonical
and severity values, evidence matching and ambiguity, atomic rollback, source-ID
conflicts, database checks/foreign keys, and provenance deletion rules.

Migration checks are documented in [database](database.md#migrations).
