# Backend architecture

[Project overview](../README.md) · [API](api.md) · [Database](database.md) · [Security](security.md)

## Current scope

Phase 1A stores original security documents with provenance and deterministic hashes.
Phase 1B stores manually supplied findings, source and canonical labels, review
status, and exact source evidence. Phase 1C.1 adds internal extraction-run lifecycle
and structured-output validation. Phase 1C.2 connects one internal OpenAI provider.
Existing HTTP operations do not call a model. Source connectors, public extraction
triggers, and extraction evaluation are not implemented.

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
  db/models/extraction_run.py
  db/models/finding_evidence.py
  schemas/documents.py     # Request and response validation
  schemas/findings.py      # Finding fields, source labels, evidence, and offsets
  services/documents.py    # Hashing, transactions, duplicate handling
  services/findings.py     # Atomic creation and exact evidence verification
  taxonomy/categories.py  # Stable application-level vocabularies
  extraction/schemas.py   # Provenance metadata and untrusted structured output
  extraction/transitions.py # Run states and permitted transitions
  extraction/service.py   # Locked lifecycle and atomic completion
  extraction/providers/base.py # Small provider protocol and sanitized errors
  extraction/providers/openai.py # Lazy config and async Responses adapter
  extraction/prompts.py   # Versioned extraction instructions
  extraction/orchestrator.py # Short transactions around one provider attempt
  main.py                  # Lifecycle and sanitized error handlers
alembic/                   # Migration environment and published revision chain
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

## Continuous integration

[CI](../.github/workflows/ci.yml) runs on pull requests and pushes to `main`.
One job uses Python 3.12, locked uv dependencies, and a health-checked PostgreSQL 16
service with development-only credentials. Separate steps check Ruff lint,
formatting, mypy, Alembic upgrade/current/schema drift, and the full PostgreSQL-backed
pytest suite. Outdated lockfiles fail instead of being regenerated.

The workflow has read-only repository permissions and cancels superseded runs
for the same PR or ref. It does not deploy, publish releases, or run on tag pushes.

## Internal extraction boundary

Phase 1C.1 exposes four internal functions in `app/extraction/service.py`:
`create_extraction_run`, `start_extraction_run`, `complete_extraction_run`, and
`fail_extraction_run`. Call them with a clean, dedicated async session; each owns
its transaction. Create/start commit separately from completion, preserving the
run even when a later result is rejected. Keep run UUIDs separately from ORM
instances: a rolled-back transaction expires loaded objects.

The lifecycle is `PENDING -> RUNNING -> SUCCEEDED | FAILED`. Terminal states cannot
be reset. A later retry must create a new run with a new UUID; identical provenance
configurations are allowed. There is no retry mechanism or lifecycle HTTP API.

Completion locks the run with `FOR UPDATE`, refreshing any cached instance, and
locks its source document for shared access. It revalidates the structured output
and nested models, checks every exact evidence slice, and uses a single savepoint
for all candidate findings/evidence. Success commits the entire batch with
`SUCCEEDED`; rejection discards the batch and commits `FAILED` before raising a
sanitized domain error. The run lock survives savepoint rollback, so another caller
cannot complete the same run concurrently.

Phase 1C.2 supplies provider output through this existing boundary. See
[database](database.md#extraction-runs) and [security](security.md#extraction-output)
for invariants and failure limits.

## Internal OpenAI extraction

`ExtractionProvider` describes identity/version metadata and an async `extract`
operation returning the existing `ExtractionOutput`. `OpenAIExtractionProvider`
uses the official SDK's `AsyncOpenAI.responses.parse`, passing that same model as
`text_format`. There is no parallel provider-specific finding schema.

Configuration is loaded only when constructing this provider, from environment or
`.env`. Application startup and manual endpoints require no OpenAI settings:

| Variable | Behavior |
| --- | --- |
| `OPENAI_API_KEY` | Required, nonblank secret; never persisted |
| `OPENAI_MODEL` | Required, nonblank bounded identifier; no default model |
| `OPENAI_TIMEOUT_SECONDS` | SDK request timeout; default 180, allowed 1–600 seconds |

The configured model is used for the request and stored as run provenance, alongside
provider `openai`, prompt `extract-findings-v1`, and schema `finding-output-v1`.
Semantic changes to instructions or the structured contract require a corresponding
version bump. Prompt text stays in versioned code, not PostgreSQL.

An internal caller uses the application's session factory (`expire_on_commit=False`):

```python
provider = OpenAIExtractionProvider()
run = await extract_document(session_factory, document_id, provider)
```

The orchestrator validates provenance, creates and starts the run, loads exact source
text, then closes the database session before awaiting the provider. No PostgreSQL
transaction, row lock, or checked-out connection spans that network request. A new
short session completes the run through Phase 1C.1 or records a sanitized provider
failure. Completion errors propagate without retry or another state transition.

Each attempt uses one SDK request with `max_retries=0`. Default clients are scoped
to that request and closed afterward; injected clients remain caller-owned.
Injected clients receive the same timeout/retry policy. Default clients use the
official OpenAI API URL; `OPENAI_BASE_URL` is not a supported gateway override.
Instructions use the separate Responses `instructions` field; the unmodified raw
document is `input`. No tools are supplied. `store=False` disables Responses storage
for later API retrieval; `truncation="disabled"` rejects oversized context instead
of silently dropping source text. These settings do not promise zero provider retention.

Refusals, absent parsed output, incomplete responses, SDK failures, and invalid
structured output become fixed domain failures. No prose salvage, offset repair,
chunking, automatic retry, or second model request occurs. Cancellation/crash can
leave `RUNNING` provenance; stale-run reconciliation is not implemented. Database
commit ambiguity remains the documented Phase 1C.1 limitation.

Normal tests use injected clients/in-memory HTTP transports and real PostgreSQL.
An autouse fixture removes provider credentials and blocks real HTTP transports;
CI requires no key or model and makes no provider requests. Live smoke testing is
optional and must use only a tiny synthetic document, never private source material.

Upstream references: [SDK helpers](https://github.com/openai/openai-python/blob/main/helpers.md),
[SDK configuration/errors](https://github.com/openai/openai-python#usage), and
[Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create).
