# Security and trust boundaries

[Project overview](../README.md) · [API](api.md) · [Database](database.md)

## Scope

ChainSecDB currently stores research evidence. It does not audit contracts, execute
exploit PoCs, evaluate smart-contract code, or make security assessments. Phase 1A
has no authentication or authorization; use it within a trusted development
environment. It is not a production security service.

## Untrusted input

Document text, source metadata, timestamps, and identifiers are untrusted input.
Pydantic validates them before the document service persists anything.

- Required metadata is trimmed and checked for blank/oversized values.
- Raw text is checked for blank content, valid UTF-8, PostgreSQL-incompatible NUL
  characters, and the 1 MiB encoded-size limit; the original text is not mutated.
- HTTP request bodies are capped at 8 MiB before JSON parsing, including streamed
  bodies without `Content-Length`.
- Source URLs must be HTTP/HTTPS URLs, but they remain metadata. Validation does
  not establish source authenticity or trustworthiness.
- Supplied retrieval timestamps must be timezone-aware. They are caller claims,
  not independently verified retrieval events.
- Unknown request fields are rejected. The client cannot set hashes, UUIDs, or
  creation timestamps.

Stored text is never executed, evaluated, or rendered as code by this application.
The backend does not fetch supplied URLs or follow links inside documents.

## Database integrity

SQLAlchemy uses parameterized database operations; document text is not interpolated
into SQL. PostgreSQL enforces the primary key, required columns, and unique content
hash constraint. Hashes are calculated by deterministic code on the server.

Failed transactions roll back. Concurrent duplicates are handled through the same
database constraint as sequential duplicates. See [database](database.md) for the
precise boundary.

A content hash identifies bytes; it does not establish that a report is authentic,
accurate, complete, or authored by its claimed source. Future derived interpretations
must retain evidence references and their own verification state.

## Errors and logs

Validation responses retain error location/type/message while omitting submitted
values and exception context. Database failures return a fixed generic message.
API clients do not receive SQL, credentials, connection strings, or stack traces.

Application logs record database exception classes without full exception dumps.
SQLAlchemy parameter values are hidden. Full raw documents are not logged by
default. POST responses contain metadata only; GET deliberately returns original
text so the evidence remains recoverable.

## Configuration

`DATABASE_URL` is required and represented as a secret in settings. Missing or
invalid configuration fails clearly without printing credentials. Startup checks
database connectivity. There are no committed production secrets or fallback
production credentials.

Compose binds PostgreSQL to localhost and uses development example credentials.
Supply real deployment configuration through the environment. The current phase
does not provide access control, a public deployment setup, or an independent
security certification.
