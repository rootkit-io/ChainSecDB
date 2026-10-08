import asyncio
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.taxonomy.categories import CanonicalCategory, EvidenceType, Severity, VerificationStatus


async def test_create_and_retrieve_finding(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    response = await client.post(f"/documents/{raw_document.id}/findings", json=finding_payload)
    assert response.status_code == 201
    finding = response.json()
    assert finding["raw_document_id"] == str(raw_document.id)
    assert finding["title"] == "Spot-price oracle manipulation"
    for field in ["source_finding_id", "source_title", "source_severity", "source_category"]:
        assert finding[field] == finding_payload[field]
    assert finding["severity"] == "HIGH"
    assert finding["canonical_category"] == "ORACLE_PRICE_MANIPULATION"
    assert finding["verification_status"] == "UNREVIEWED"
    assert finding["created_at"].endswith("Z")
    assert finding["updated_at"] == finding["created_at"]
    evidence = finding["evidence"][0]
    assert evidence["finding_id"] == finding["id"]
    assert evidence["field_name"] == "root_cause"
    assert (
        raw_document.raw_text[evidence["start_offset"] : evidence["end_offset"]]
        == evidence["source_excerpt"]
    )
    assert evidence["start_offset"] == raw_document.raw_text.index(evidence["source_excerpt"])
    retrieved = (await client.get(f"/findings/{finding['id']}")).json()
    assert retrieved == finding
    assert retrieved["verification_status"] == "UNREVIEWED"
    assert (await client.get(f"/documents/{raw_document.id}/findings")).json() == [finding]
    assert await session.scalar(select(SecurityFinding.raw_document_id)) == raw_document.id
    assert (
        await session.scalar(select(SecurityFinding.verification_status))
        == VerificationStatus.UNREVIEWED
    )
    assert (await client.get(f"/documents/{raw_document.id}")).json()[
        "raw_text"
    ] == raw_document.raw_text


async def test_minimum_finding_optional_values_null(
    client: AsyncClient,
    raw_document: RawDocument,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            "title": "Historical issue",
            "severity": "UNKNOWN",
            "canonical_category": "OTHER",
        },
    )
    assert response.status_code == 201
    finding = response.json()
    for field in [
        "source_finding_id",
        "source_title",
        "source_severity",
        "source_category",
        "summary",
        "root_cause",
        "impact",
        "recommendation",
        "affected_contract",
        "affected_function",
        "source_file",
        "line_start",
        "line_end",
        "protocol_name",
        "language",
    ]:
        assert finding[field] is None
    assert finding["evidence"] == []


@pytest.mark.parametrize("category", list(CanonicalCategory))
async def test_canonical_values_serialize(
    client: AsyncClient,
    raw_document: RawDocument,
    category: CanonicalCategory,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            "title": "Finding",
            "severity": "UNKNOWN",
            "canonical_category": category.value,
        },
    )
    assert response.status_code == 201
    assert response.json()["canonical_category"] == category.value


@pytest.mark.parametrize("severity", list(Severity))
async def test_severity_values_serialize(
    client: AsyncClient,
    raw_document: RawDocument,
    severity: Severity,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            "title": "Finding",
            "severity": severity.value,
            "canonical_category": "OTHER",
        },
    )
    assert response.status_code == 201
    assert response.json()["severity"] == severity.value


@pytest.mark.parametrize("status", list(VerificationStatus))
async def test_verification_status_forbidden_at_creation(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    status: VerificationStatus,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "verification_status": status.value,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "loc": ["body", "verification_status"],
            "type": "extra_forbidden",
            "msg": "Extra inputs are not permitted",
        }
    ]
    assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
    assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", " \t\n "),
        ("title", "bad\x00title"),
        ("severity", "Major"),
        ("severity", None),
        ("canonical_category", "oracle"),
        ("canonical_category", None),
        ("verification_status", "APPROVED"),
        ("source_category", "bad\x00label"),
        ("source_category", ""),
        ("line_start", 0),
        ("line_start", -1),
        ("line_end", 0),
        ("line_end", -1),
        ("line_end", 119),
        ("line_start", True),
        ("line_start", "120"),
    ],
)
async def test_finding_validation(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    field: str,
    value: object,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings", json={**finding_payload, field: value}
    )
    assert response.status_code == 422
    assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0


@pytest.mark.parametrize("field", ["title", "severity", "canonical_category"])
async def test_required_normalization_never_guessed(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    field: str,
) -> None:
    del finding_payload[field]
    assert (
        await client.post(f"/documents/{raw_document.id}/findings", json=finding_payload)
    ).status_code == 422


@pytest.mark.parametrize("line_start,line_end", [(120, 120), (None, 138), (120, None)])
async def test_valid_partial_or_equal_lines(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    line_start: int | None,
    line_end: int | None,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "line_start": line_start,
            "line_end": line_end,
        },
    )
    assert response.status_code == 201
    assert response.json()["line_start"] == line_start
    assert response.json()["line_end"] == line_end


@pytest.mark.parametrize(
    "evidence",
    [
        {"source_excerpt": "not in this document"},
        {"source_excerpt": "Repeat."},
        {"source_excerpt": "Report.", "start_offset": 0, "end_offset": 7},
        {"source_excerpt": "Report.", "start_offset": 2, "end_offset": 9999},
        {"source_excerpt": "Report.", "start_offset": -1, "end_offset": 7},
        {"source_excerpt": "Report.", "start_offset": 2, "end_offset": 2},
        {"source_excerpt": "Report.", "start_offset": 7, "end_offset": 2},
        {"source_excerpt": "Report.", "start_offset": 2},
        {"source_excerpt": "Report.", "end_offset": 9},
        {"source_excerpt": "Report.", "start_offset": True, "end_offset": 9},
        {"source_excerpt": "Report.", "evidence_type": "URL"},
        {"source_excerpt": "Report.", "field_name": "imaginary_field"},
        {"source_excerpt": ""},
        {"source_excerpt": " \n "},
        {"source_excerpt": "bad\x00excerpt"},
    ],
)
async def test_invalid_evidence_atomic_rollback(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    evidence: dict[str, object],
) -> None:
    valid = {"source_excerpt": "The protocol uses the current pool price."}
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "evidence": [valid, evidence],
        },
    )
    assert response.status_code == 422
    assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
    assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0


@pytest.mark.parametrize("evidence_type", list(EvidenceType))
async def test_explicit_repeated_excerpt_offsets(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    evidence_type: EvidenceType,
) -> None:
    start = raw_document.raw_text.rindex("Repeat.")
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "evidence": [
                {
                    "evidence_type": evidence_type.value,
                    "source_excerpt": "Repeat.",
                    "start_offset": start,
                    "end_offset": start + len("Repeat."),
                }
            ],
        },
    )
    assert response.status_code == 201
    evidence = response.json()["evidence"][0]
    assert evidence["start_offset"] == start
    assert evidence["end_offset"] == start + len("Repeat.")
    assert evidence["evidence_type"] == evidence_type.value
    assert evidence["field_name"] is None


async def test_unicode_offsets_count_codepoints_and_excerpt_preserved(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "evidence": [
                {
                    "source_excerpt": "🔐 Report.\n",
                    "start_offset": 0,
                    "end_offset": 10,
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["evidence"][0]["source_excerpt"] == "🔐 Report.\n"


async def test_duplicate_source_id_and_document_scope(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    route = f"/documents/{raw_document.id}/findings"
    assert (await client.post(route, json=finding_payload)).status_code == 201
    response = await client.post(route, json=finding_payload)
    assert response.status_code == 409
    assert response.json() == {"detail": "Source finding ID already exists for document"}
    assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 1
    other = await client.post(
        "/documents",
        json={
            "source_name": "another source",
            "document_type": "report",
            "raw_text": raw_document.raw_text + "another source",
        },
    )
    assert (
        await client.post(f"/documents/{other.json()['id']}/findings", json=finding_payload)
    ).status_code == 201
    assert len((await client.get(route)).json()) == 1


async def test_concurrent_duplicate_source_id(
    client: AsyncClient,
    session: AsyncSession,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    route = f"/documents/{raw_document.id}/findings"
    responses = await asyncio.gather(*(client.post(route, json=finding_payload) for _ in range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 1
    assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 1


async def test_duplicate_titles_and_null_source_ids_allowed(
    client: AsyncClient,
    raw_document: RawDocument,
) -> None:
    payload = {"title": "Same title", "severity": "UNKNOWN", "canonical_category": "OTHER"}
    route = f"/documents/{raw_document.id}/findings"
    responses = [await client.post(route, json=payload) for _ in range(2)]
    assert all(response.status_code == 201 for response in responses)
    findings = (await client.get(route)).json()
    assert len(findings) == 2
    assert findings == sorted(findings, key=lambda item: (item["created_at"], item["id"]))


async def test_multiple_evidence_retrieval_order_stable(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            "evidence": [
                {"source_excerpt": "Report."},
                {"source_excerpt": "A loss may occur."},
            ],
        },
    )
    assert response.status_code == 201
    finding = response.json()
    assert (await client.get(f"/findings/{finding['id']}")).json() == finding


async def test_missing_records_and_empty_listing(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
) -> None:
    missing = uuid4()
    assert (
        await client.post(f"/documents/{missing}/findings", json=finding_payload)
    ).status_code == 404
    assert (await client.get(f"/documents/{missing}/findings")).status_code == 404
    assert (await client.get(f"/findings/{missing}")).json() == {"detail": "Finding not found"}
    assert (await client.get(f"/findings/{missing}")).status_code == 404
    assert (await client.get(f"/documents/{raw_document.id}/findings")).json() == []


@pytest.mark.parametrize(
    "method,route",
    [
        ("post", "/documents/bad-id/findings"),
        ("get", "/documents/bad-id/findings"),
        ("get", "/findings/bad-id"),
    ],
)
async def test_malformed_identifiers(
    client: AsyncClient,
    finding_payload: dict[str, object],
    method: str,
    route: str,
) -> None:
    response = await client.request(
        method, route, json=finding_payload if method == "post" else None
    )
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["id", "raw_document_id", "created_at", "updated_at"])
async def test_finding_server_fields_forbidden(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    field: str,
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            **finding_payload,
            field: "override",
        },
    )
    assert response.status_code == 422


async def test_invalid_unicode_and_validation_privacy(
    client: AsyncClient,
    raw_document: RawDocument,
    finding_payload: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = "private finding content"
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        content=json.dumps(
            {
                **finding_payload,
                "source_category": "\ud800",
                "summary": private,
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert private not in response.text
    assert private not in caplog.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
