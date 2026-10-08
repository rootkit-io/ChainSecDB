# Security and trust boundaries

[Project overview](../README.md) · [API](api.md) · [Database](database.md)

## Scope

ChainSecDB currently stores research evidence. It does not audit contracts, execute
exploit PoCs, evaluate smart-contract code, or make security assessments. It
has no authentication or authorization; use it within a trusted development
environment. It is not a production security service.

## Untrusted input

Document text, finding fields, evidence, source metadata, timestamps, and identifiers
are untrusted input.
Pydantic validates them before persistence.

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
- Finding labels, details, and evidence reject NUL and invalid UTF-8. Normalized
  title is trimmed; source labels and excerpts remain exact. Blank supplied text
  is rejected; unknown optional fields should be null.
- Category, severity, status, evidence type, and evidence field names use closed
  vocabularies. Contract line numbers are positive and ordered when both are supplied.
- At most 100 evidence items may be supplied with a finding. Every excerpt must
  occur in the associated raw document, with exact code-point offsets.

Stored text is never executed, evaluated, or rendered as code by this application.
The backend does not fetch supplied URLs or follow links inside documents.

## Database integrity

SQLAlchemy uses parameterized database operations; document text is not interpolated
into SQL. PostgreSQL enforces the primary key, required columns, and unique content
hash constraint. Hashes are calculated by deterministic code on the server.

Failed transactions roll back. Concurrent duplicates are handled through the same
database constraint as sequential duplicates. See [database](database.md) for the
precise boundary.

Findings cannot exist without their source document. Deletion of a referenced
document is restricted; deletion of a finding cascades its evidence. These database
policies do not expose deletion endpoints.

Findings are created with `UNREVIEWED` verification status. Clients cannot set
verification state during creation. `VERIFIED` and `REJECTED` are reserved for a
future explicit review workflow. Creation rejects any supplied verification status
as a forbidden field, and the service explicitly persists `UNREVIEWED`. No review
workflow or status-update endpoint is implemented.

Evidence validation proves exact text containment at creation, not that the excerpt
semantically supports the normalized claim. Location fields are source claims,
not checked against a fetched
contract or executed code. Direct database writes can bypass textual validation;
the API exposes no document-text update operation.

A content hash identifies bytes; it does not establish that a report is authentic,
accurate, complete, or authored by its claimed source. Future derived interpretations
must retain evidence references and their own verification state.

## Extraction output

Phase 1C.1 has no provider implementation and makes no LLM calls. Its internal
service accepts untrusted structured objects, not provider responses or arbitrary
code. It does not fetch URLs, evaluate text, or execute Solidity. There is no
HTTP endpoint for starting extraction or manipulating run states.

The extraction schema reuses Phase 1B finding fields, enums, and text validation.
Unknown fields are forbidden, including IDs, lifecycle timestamps, verification
state, and model-response/reasoning fields. Nested model instances are revalidated
to catch mutations. Every extracted finding requires 1–100 evidence items with
explicit strict integer offsets; output contains at most 100 findings. An empty
finding list is valid and can succeed. Existing per-field text limits apply.

Each excerpt must equal `raw_text[start_offset:end_offset]`, using Unicode code
points and an exclusive end. Offsets must be nonnegative, ordered, and within the
document. No inference, relocation, case folding, whitespace rewriting, fuzzy
matching, or Unicode normalization occurs. Exact containment does not prove the
truth of a finding or semantic support for every populated field.

All persisted findings explicitly begin `UNREVIEWED`, with a run UUID supplied by
the service. Manual findings keep null run IDs and optional evidence. There is no
review workflow, automatic verification, or model-controlled status.

Failure recording accepts only six operational codes: `PROVIDER_ERROR`, `TIMEOUT`,
`INVALID_OUTPUT`, `EVIDENCE_MISMATCH`, `DUPLICATE_SOURCE_FINDING`, and
`PERSISTENCE_ERROR`. Messages are fixed, bounded strings. Arbitrary exception text,
provider messages, headers, credentials, environment values, and stack traces are
not accepted or persisted. Raw model output, hidden reasoning, prompts, and request
payloads have no storage fields. Provider/model/version strings are caller-declared
provenance, not independently verified identities or a schema-version dispatcher.

The service does not log document bodies, evidence excerpts, or structured output.
See [transaction failure limits](database.md#extraction-runs) for commit ambiguity
and runs that may remain `RUNNING` after an outer database failure.

## Errors and logs

Validation responses retain error location/type/message while omitting submitted
values and exception context. Database failures return a fixed generic message.
API clients do not receive SQL, credentials, connection strings, or stack traces.

Application logs record database exception classes without full exception dumps.
SQLAlchemy parameter values are hidden. Full raw documents are not logged by
default. POST responses contain metadata only; GET deliberately returns original
text so the evidence remains recoverable.

Finding responses include source excerpts intentionally. Full finding or evidence
payloads are not logged by default. Evidence rejection messages describe the
failed rule without echoing the excerpt.

## Configuration

`DATABASE_URL` is required and represented as a secret in settings. Missing or
invalid configuration fails clearly without printing credentials. Startup checks
database connectivity. There are no committed production secrets or fallback
production credentials.

Compose binds PostgreSQL to localhost and uses development example credentials.
Supply real deployment configuration through the environment. The current phase
does not provide access control, a public deployment setup, or an independent
security certification.
