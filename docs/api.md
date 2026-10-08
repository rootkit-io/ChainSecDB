# API contract

[Project overview](../README.md) · [Architecture](architecture.md) · [Database](database.md)

The current API stores and retrieves original documents. Source URLs are metadata
only. There is no authentication or automatic source retrieval in Phase 1A.
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

## Status codes and errors

| Status | Meaning | Response |
| --- | --- | --- |
| 201 | Document created | Metadata object |
| 200 | Document retrieved | Metadata plus `raw_text` |
| 404 | Document absent | `{"detail":"Document not found"}` |
| 409 | Duplicate content | `{"detail":"Document content already exists"}` |
| 413 | HTTP body over 8 MiB | `{"detail":"Request body exceeds 8 MiB"}` |
| 422 | Invalid request or UUID | `{"detail":[{"loc":[...],"type":"...","msg":"..."}]}` |
| 500 | Database operation failed | `{"detail":"Database operation failed"}` |

Validation errors include location, type, and message; they omit submitted values
and exception context. Database responses do not expose SQL, connection strings,
credentials, or stack traces. See [security](security.md) for trust boundaries.
