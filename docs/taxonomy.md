# Canonical taxonomy and source normalization

[Project overview](../README.md) · [API](api.md#findings) · [Roadmap](roadmap.md)

Phase 1B defines 20 internal categories for manually supplied findings. The caller
must explicitly select a canonical category and normalized severity; the backend
does not classify findings, infer aliases, or guess from a source tag.

## References considered

The design review used these repository snapshots, without importing their code
or datasets into ChainSecDB:

| Reference | Material reviewed | Design implication |
| --- | --- | --- |
| [EVMbench result structure](https://github.com/paradigmxyz/evmbench/blob/fe75a2d68df89a30f12218e419fc33dc7b5a92e5/backend/resultsvc/routers/v1.py) | Vulnerability title, severity, summary, file/line descriptions, impact, remediation, validation | Useful finding fields; its severity normalization is not adopted because unknown source values must remain explicit. |
| [Solodit report tags](https://github.com/solodit/solodit_content/blob/0be75be262b17472d7a2a857c6343b892eec86f7/report_tags.md) and [protocol categories](https://github.com/solodit/solodit_content/blob/0be75be262b17472d7a2a857c6343b892eec86f7/protocol_categories.md) | Tag names and their distinction from protocol categories | Tags cover mechanisms, components, and context; protocol tags are not automatically vulnerability categories. |
| [SmartBugs curated README](https://github.com/smartbugs/smartbugs-curated/blob/230e649123477eff332742a59a1c7cc6dc286cab/README.md) and [annotation index](https://github.com/smartbugs/smartbugs-curated/blob/230e649123477eff332742a59a1c7cc6dc286cab/vulnerabilities.json) | DASP-style categories, source attribution, and category/line annotations | Classic Solidity groups are useful, but do not cover all financial, governance, and cross-chain mechanisms needed here. |

The SmartBugs review covers its representation of DASP-style categories, not a
separate review of the DASP website. AuditVault/EVM Hack Registry was not reviewed;
no compatibility claim or crosswalk is made for it. Solodit ingestion and SmartBugs
imports are not implemented.

No single external taxonomy is adopted verbatim. Their purposes and granularity
differ, and a broad tag collection is not equivalent to a root-cause taxonomy.
The definitions below are independently written for ChainSecDB. Reference names
are factual attribution, not a runtime dependency or a redistribution of source
descriptions, annotations, contracts, or reports.

## Canonical categories

Values are stable machine-readable identifiers in application enums and database
VARCHAR columns with CHECK constraints. No PostgreSQL ENUM type or taxonomy service
is introduced.

| Identifier | Intended meaning |
| --- | --- |
| `ACCESS_CONTROL` | An operation or resource is available to an actor who should lack permission. |
| `AUTH_SIGNATURE` | Authentication or signature validation accepts an invalid, replayed, or improperly scoped authorization. |
| `REENTRANCY` | A callback or nested invocation observes or changes state at an unsafe point in an operation. |
| `ORACLE_PRICE_MANIPULATION` | A financial decision relies on price data that an attacker can manipulate or improperly influence. |
| `ACCOUNTING` | Balances, shares, liabilities, or accrued value fail to preserve the intended financial invariant. |
| `ARITHMETIC_PRECISION` | Numeric overflow, underflow, rounding, scaling, or precision loss changes the intended result. |
| `BUSINESS_LOGIC` | An application rule permits unintended behavior beyond a more specific mechanism below. |
| `STATE_TRANSITION` | Invalid lifecycle ordering or state movement breaks an operation's required preconditions. |
| `EXTERNAL_CALL` | Unsafe call targets, call context, failure handling, or returned data compromise an operation. |
| `TOKEN_INTEGRATION` | Assumptions about token behavior, transfers, approvals, or token interfaces break integration safety. |
| `FRONTRUNNING_MEV` | Transaction ordering or adversarial inclusion enables harmful value capture or interference. |
| `DENIAL_OF_SERVICE` | An actor or reachable condition prevents legitimate operations from making progress. |
| `UPGRADEABILITY_INITIALIZATION` | Initialization, proxy upgrades, or upgrade-related storage handling compromises state or control. |
| `GOVERNANCE` | Voting, proposals, execution, or governance safeguards permit an unintended decision or takeover. |
| `CROSS_CHAIN_BRIDGE` | Cross-chain message validation, settlement, or asset transfer violates the intended trust boundary. |
| `LIQUIDATION` | Liquidation eligibility, execution, incentives, or settlement produces an unsafe outcome. |
| `ECONOMIC_ATTACK` | An adversarial market or incentive strategy extracts unintended value beyond a more specific mechanism. |
| `RANDOMNESS_TIME` | Predictable randomness or unsafe timing assumptions affect a security-sensitive outcome. |
| `INPUT_VALIDATION` | Missing or incorrect validation permits malformed or out-of-domain inputs to reach an operation. |
| `OTHER` | Available evidence does not justify one of the defined categories, or the issue falls outside this set. |

Categories can overlap. The caller selects one primary mechanism justified by the
source evidence, preferring a specific mechanism over a broad residual category.
For example, an incorrect share invariant is accounting; a rounding operation
causing that error may justify arithmetic precision. The API does not decide
between them or support multiple canonical assignments in Phase 1B.

## Source fact and canonical interpretation

`source_title`, `source_category`, and `source_severity` preserve the caller's source
text exactly, including case and surrounding whitespace. They are never replaced
with canonical values. Unknown optional information remains null; supplied blank
labels are rejected. The original document preserves all source material even
when only one source-category string is recorded on a finding.

Example of explicitly supplied, independent values:

```json
{
  "source_category": "Oracle Manipulation",
  "canonical_category": "ORACLE_PRICE_MANIPULATION",
  "source_severity": "Major",
  "severity": "HIGH"
}
```

This is a caller assessment, not an automatic mapping. Names such as Solodit's
`Precision Loss` or SmartBugs' `unchecked_low_level_calls` may suggest relevant
internal categories, but do not establish universal mappings. An unmapped source
label is preserved and still requires an explicit valid canonical value.

No alias mapping module is needed for the current contract. `OTHER` is a category
fallback chosen explicitly; it is not an automatic classification.

## Severity and verification

Normalized severity values are `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`,
`INFORMATIONAL`, and `UNKNOWN`. Source scales are independent. The backend does
not translate severity labels or calculate severity from impact; use `UNKNOWN`
when a normalized severity assessment is unavailable.

Findings are created with `UNREVIEWED` verification status. Clients cannot set
verification state during creation. `VERIFIED` and `REJECTED` are reserved for a
future explicit review workflow. Status remains readable in responses; no review
workflow or status-update endpoint is implemented.

Evidence may support `title`, `severity`, `canonical_category`, `summary`,
`root_cause`, `impact`, `recommendation`, `affected_contract`, `affected_function`,
`source_file`, `line_start`, `line_end`, `protocol_name`, or `language`.
A null evidence `field_name` associates an excerpt with the finding as a whole.
Exact containment is mechanically checked; semantic support still requires review.

## Evolution policy and open questions

Keep published identifiers stable. Add categories through an application enum
change and a new migration extending the database CHECK constraint; never edit a
published migration. Preserve existing values when deprecating a category. A
materially different meaning needs a new identifier and an explicit, reviewed
record migration rather than a silent relabeling.

Future work may refine overlapping categories, multi-category representation,
source tag lists, explicit crosswalks, and review policy. These choices are not
implemented now. Phase 1C.1 records versioned extraction-run provenance, and Phase
1C.2 adds one internal provider. Extraction evaluation remains later work.
Original evidence remains
the source of truth.
