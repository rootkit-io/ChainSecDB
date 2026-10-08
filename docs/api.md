# API contract

[Project overview](../README.md) · [Architecture](architecture.md) · [Database](database.md)

The current API stores original documents and manually supplied findings with
source evidence. Source URLs are metadata only. There is no authentication or
automatic source retrieval in Phases 1A/1B.
Interactive OpenAPI documentation is available at <http://127.0.0.1:8000/docs>.

## POST /documents

Send a JSON object:

```json
{
  "source_name": "manual",
  "source_url": "https://example.com/security-report",
  "document_type": "audit_report",
  "raw_text": "Security report contents...",
  "retrieved_at": null
}
```

### Validation

| Field | Required | Rules |
| --- | --- | --- |
| `source_name` | Yes | String, trimmed, nonblank, at most 255 characters |
| `source_url` | No | Null or valid HTTP/HTTPS URL; trimmed; at most 2,083 characters |
| `document_type` | Yes | String, trimmed, nonblank, at most 255 characters |
| `raw_text` | Yes | String, nonblank, at most 1 MiB encoded as UTF-8; content unchanged |
| `retrieved_at` | No | Null or timezone-aware timestamp, normalized to UTC |

`document_type` is a descriptive string, not a vulnerability category or enum.
`source_url` spelling is preserved after trimming; the URL is never fetched.
For `retrieved_at`, use ISO 8601 with a timezone, for example
`2026-10-08T14:00:00+02:00`, serialized back as `2026-10-08T12:00:00Z`.

Blank raw text is rejected using a whitespace check, but the check never trims
stored content. Newlines, whitespace, and Unicode normalization remain unchanged.
NUL characters and invalid Unicode surrogates are rejected because PostgreSQL
UTF-8 text cannot preserve them. Unknown request fields are forbidden, including
`content_hash`, `id`, and `created_at`.

HTTP bodies are capped at 8 MiB before JSON parsing, including streamed requests
without `Content-Length`. This allows room for JSON escaping while bounding the
request. The 1 MiB raw-text limit is a separate validation boundary.

### Success response

Returns **201 Created** with metadata only. Example UUID and timestamp below are
illustrative; the hash matches the example raw text above:

```json
{
  "id": "c8a4e540-6e8b-451d-ae21-1973ab815191",
  "source_name": "manual",
  "source_url": "https://example.com/security-report",
  "document_type": "audit_report",
  "content_hash": "5833857862e5a1d045b96afa9f8715e594283dd64ea6aa0e4b0e410aeb9e5561",
  "retrieved_at": null,
  "created_at": "2026-10-08T15:00:00Z"
}
```

### Hashing and duplicates

The server computes:

```python
hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
```

Identical text always produces the same hash and cannot be inserted twice, even
with different source metadata. Differences in whitespace or Unicode normalization
produce distinct inputs. There is no fuzzy or semantic deduplication.

Duplicate content returns **409 Conflict**:

```json
{"detail": "Document content already exists"}
```

The [PostgreSQL uniqueness constraint](database.md#duplicate-boundary) is the final
guard, including concurrent submissions.

## GET /documents/{id}

Use the UUID returned by POST:

```sh
DOCUMENT_ID=replace-with-returned-uuid
curl -i "http://127.0.0.1:8000/documents/$DOCUMENT_ID"
```

Returns **200 OK** with all POST metadata plus original `raw_text`:

```json
{
  "id": "c8a4e540-6e8b-451d-ae21-1973ab815191",
  "source_name": "manual",
  "source_url": "https://example.com/security-report",
  "document_type": "audit_report",
  "content_hash": "5833857862e5a1d045b96afa9f8715e594283dd64ea6aa0e4b0e410aeb9e5561",
  "retrieved_at": null,
  "created_at": "2026-10-08T15:00:00Z",
  "raw_text": "Security report contents..."
}
```

Both timestamps are serialized in UTC. A valid UUID with no matching row returns
404; a malformed identifier returns 422.

## Findings

### POST /documents/{document_id}/findings

Creates one manually supplied finding and up to 100 optional evidence items in
one transaction. Create the parent document first; this example requires a document
containing the exact sentence `The protocol uses the current pool price.`.

```json
{
  "source_finding_id": "H-01",
  "source_title": "Spot price can be manipulated",
  "source_severity": "Major",
  "source_category": "Oracle Manipulation",
  "title": "Spot-price oracle manipulation",
  "severity": "HIGH",
  "canonical_category": "ORACLE_PRICE_MANIPULATION",
  "summary": "The protocol trusts a manipulable spot price.",
  "root_cause": "The price can change within the same transaction.",
  "impact": "An attacker may borrow against inflated collateral.",
  "recommendation": "Use a manipulation-resistant oracle.",
  "affected_contract": "Vault",
  "affected_function": "borrow",
  "source_file": "src/Vault.sol",
  "line_start": 120,
  "line_end": 138,
  "protocol_name": "ExampleProtocol",
  "language": "Solidity",
  "evidence": [
    {
      "evidence_type": "SOURCE_TEXT",
      "field_name": "root_cause",
      "source_excerpt": "The protocol uses the current pool price."
    }
  ]
}
```

Required finding fields are `title`, `severity`, and `canonical_category`.
`raw_document_id` comes exclusively from the path and must identify an existing
document. All source labels, details, locations, and protocol/language metadata
are nullable. Evidence defaults to an empty list.

The title is trimmed, nonblank, and at most 500 characters. Source ID, severity,
and category labels, contract/function names, protocol name, and language are at
most 255 characters; source title is at most 500 and source file at most 1,024.
Summary, root cause, impact, and recommendation are each at most 1,048,576
characters, within the shared 8 MiB HTTP body limit. All supplied text must be
nonblank, valid UTF-8, and free of NUL characters. Source fields and descriptive
text are not silently trimmed or normalized; absent information remains null.

Normalized category/severity and status must use the exact [documented enum values](taxonomy.md).
No automatic source-label mapping occurs. `line_start` and `line_end`, when supplied,
are strict positive integers up to 2,147,483,647. Either may be unknown; when both
exist, `line_end >= line_start`. Contract locations are metadata, not verified
against fetched files.

Unknown fields are rejected, including client-supplied IDs, `raw_document_id`,
creation/update timestamps, and `verification_status`.

Findings are created with `UNREVIEWED` verification status. Clients cannot set
verification state during creation. Supplying `verification_status` with any
value, including `UNREVIEWED`, returns **422 Unprocessable Entity** as a forbidden
extra field and persists no finding or evidence. `VERIFIED` and `REJECTED` are
reserved for a future explicit review workflow; no review or status-update
endpoint is implemented. Verification status is readable in finding responses.

Returns **201 Created** with all finding fields, generated UUID, `raw_document_id`,
UTC `created_at`/`updated_at`, verification status, and evidence records. The parent
document body is omitted. Its metadata and raw text remain available through
`GET /documents/{id}`. Each evidence record contains:

```json
{
  "id": "9205983e-1260-496b-a0ac-cc5d47608380",
  "finding_id": "6147c8bd-afc2-457f-b96a-ad1f2be414f8",
  "evidence_type": "SOURCE_TEXT",
  "field_name": "root_cause",
  "source_excerpt": "The protocol uses the current pool price.",
  "start_offset": 0,
  "end_offset": 41,
  "created_at": "2026-10-08T15:00:00Z"
}
```

UUIDs/timestamp above are illustrative; the offsets apply if that sentence begins
the document. Evidence records are returned in UUID order for stable retrieval.

### Evidence integrity

`evidence_type` defaults to `SOURCE_TEXT`; `CODE_REFERENCE` and `OTHER` are also
accepted. All types require an exact excerpt from the same parent document.
`field_name` may be null for finding-wide evidence, or one of the normalized
finding fields listed in [taxonomy](taxonomy.md#severity-and-verification).

Offsets count **Python Unicode code points**, not UTF-8 bytes or UTF-16 code units.
`start_offset` is inclusive; `end_offset` is exclusive. Both must be supplied
together or both omitted, and must be strict integers:

- `0 <= start_offset < end_offset <= len(raw_text)`.
- `raw_text[start_offset:end_offset] == source_excerpt` exactly.
- Without offsets, a unique exact occurrence yields derived offsets.
- Repeated occurrences, including overlaps, require explicit offsets.
- An absent excerpt, mismatched slice, or out-of-range offset is rejected.

No fuzzy matching, rewriting, or Unicode normalization occurs. Invalid evidence
rejects the whole transaction: neither finding nor evidence is persisted.
Containment proves exact source correspondence, not semantic correctness of a claim.

### Source-ID duplicates

The database enforces uniqueness of `(raw_document_id, source_finding_id)` when
the source ID is non-null. IDs are exact, case-sensitive source strings. The same
ID on another document is allowed; null source IDs and duplicate titles are allowed.
No semantic deduplication occurs.

A duplicate returns **409 Conflict** after rollback:

```json
{"detail": "Source finding ID already exists for document"}
```

### GET /findings/{id}

Returns **200 OK** with the same full finding/evidence representation as creation.
Missing findings return **404** with `{"detail":"Finding not found"}`; malformed
UUIDs return **422**.

### GET /documents/{document_id}/findings

Returns **200 OK** with an array of full findings, ordered by `created_at`, then
UUID. Existing documents with no findings return `[]`. Missing documents return
**404** with `{"detail":"Document not found"}`; malformed UUIDs return **422**.
This phase returns the complete document-specific list without pagination or a
generic query language.

## Status codes and errors

| Status | Meaning | Response |
| --- | --- | --- |
| 201 | Document or finding created | Document metadata or full finding/evidence object |
| 200 | Document, finding, or findings retrieved | Object or document-specific findings array |
| 404 | Document absent | `{"detail":"Document not found"}` |
| 404 | Finding absent | `{"detail":"Finding not found"}` |
| 409 | Duplicate content | `{"detail":"Document content already exists"}` |
| 409 | Duplicate source ID within a document | `{"detail":"Source finding ID already exists for document"}` |
| 413 | HTTP body over 8 MiB | `{"detail":"Request body exceeds 8 MiB"}` |
| 422 | Invalid request or UUID | `{"detail":[{"loc":[...],"type":"...","msg":"..."}]}` |
| 500 | Database operation failed | `{"detail":"Database operation failed"}` |

Validation errors include location, type, and message; they omit submitted values
and exception context. Database responses do not expose SQL, connection strings,
credentials, or stack traces. See [security](security.md) for trust boundaries.

Evidence service failures also return 422, with a fixed rule-specific `detail`
string: `Evidence excerpt does not occur in the source document`,
`Repeated evidence excerpt requires explicit offsets`, or
`Evidence offsets do not match the source excerpt`. Submitted excerpts are never
echoed in those errors.
