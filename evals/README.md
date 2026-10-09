# Extraction evaluation

**Evaluation infrastructure complete; real-world baseline not yet established.**

This tooling measures the existing security-document extraction pipeline. It does
not discover vulnerabilities from contract code, tune prompts, or certify software.
The current corpus contains **12 original synthetic documents, 21 gold findings,
three negative documents, and zero human-reviewed real documents**. Fixture scores
verify evaluator arithmetic and persistence, not OpenAI quality.

## Frozen target and versions

- Dataset: `chainsec-eval-v1`.
- Metrics: `chainsec-metrics-v1`.
- Prompt: `extract-findings-v1`, unchanged.
- Structured output: `finding-output-v1`, unchanged.
- Existing OpenAI provider, configured externally through `OPENAI_MODEL`.
- Existing exact Unicode evidence rules and server-owned `UNREVIEWED` initial state.

Once an official baseline uses this dataset, treat its documents, gold, matching,
and metric definitions as immutable. A correction requires a documented new
dataset version, such as `chainsec-eval-v1.1`; matching/metric changes also require
a metric version bump. The current validator deliberately accepts only v1.
Do not inspect failures, tune the prompt, rerun this benchmark, and claim an
uncontaminated improvement. Future tuning uses `dev`; `holdout` stays untouched.

## Files and corpus

`datasets/v1/manifest.json` identifies each document and separate gold JSON file.
`fixtures/predictions.json` is a deterministic **OFFLINE** fixture, not a model
response. `fixtures/predictions-with-errors.json` exercises misses, duplicates,
classification disagreements, a negative false positive, and a simulated timeout.
`candidates.json` contains safe metadata for three local Solodit candidates;
they are not benchmark cases or gold. Generated outputs belong in ignored `runs/`.

| Case | Split | Gold | Purpose |
| --- | --- | ---: | --- |
| single | dev | 1 | Obvious access-control finding |
| multiple | dev | 4 | Independent findings and mixed labels |
| mixed | holdout | 3 | Bridge/signature/time risks and critical severity |
| unicode | dev | 2 | Emoji, non-Latin text, combining character before evidence |
| repeated | holdout | 1 | Identical excerpt in a harmless earlier occurrence |
| injection | dev | 1 | Embedded instructions must not affect findings |
| distractor | holdout | 1 | Glossary terms alongside an actual finding |
| long | holdout | 6 | Extended report with background and finding sections |
| economic | dev | 2 | Economic and otherwise uncategorized risks |
| negative-glossary | dev | 0 | Security terminology without findings |
| negative-status | holdout | 0 | Assessment status without findings |
| negative-injection | holdout | 0 | Instructions to invent findings |

These scenarios were independently authored for this repository under Apache-2.0.
Their gold is deterministic fixture data, explicitly `human_reviewed=false`.
Passing a fixture does not prove a model resists injection or handles real reports.
Solodit's pinned report organization informed the choice of headings and density;
no upstream report prose, findings, or taxonomy descriptions were copied.

## Manifest and gold contracts

Every case requires `case_id`, `split` (`dev`/`holdout`), `kind`
(`positive`/`negative`), `document_path`, `gold_path`, SHA-256 hashes for **both**
files, source name/URL/type, redistribution status, explicit boolean
`human_reviewed`, license/terms note, retrieval timestamp, and notes.
The document hash covers exact UTF-8 bytes, including original line endings.
Gold hashes prevent unnoticed annotation drift. The manifest itself is hashed in
every prediction artifact and report.

Gold files contain `{"findings": [...]}`. Each finding requires `gold_id`, `title`,
canonical category, normalized severity, and at least one evidence span:

```json
{
  "gold_id": "g01",
  "title": "Missing access control",
  "severity": "HIGH",
  "canonical_category": "ACCESS_CONTROL",
  "evidence": [{
    "source_excerpt": "Anyone can call setFeeRecipient and redirect every future fee payment.",
    "start_offset": 239,
    "end_offset": 309
  }]
}
```

Offsets must be verified against the particular source; this illustrative object
is not a standalone case. Indices count Python Unicode characters, not bytes or
UTF-16 units. End is exclusive:
`document[start_offset:end_offset] == source_excerpt`. Never normalize whitespace,
line endings, case, or Unicode. Optional source labels and descriptive fields use
the existing finding-field contract; `notes` is also allowed on gold. Gold is
independent of SDK objects. Unknown fields and invalid enum values are rejected.

The validator checks the **entire corpus before selection or provider creation**:
version, schemas, unique case IDs, unique gold IDs within each case, paths, both
hashes, production document limits, positive/negative counts, real-case provenance,
and exact evidence ranges/slices. Oversized reports must be explicitly excluded;
there is no chunking or automatic repair.

## Real sources, licensing, and human review

Public access does not establish redistribution permission. Record the source,
URL, pinned provenance, hash, retrieval time, and actual license/terms assessment.
Do not infer a report license from its host or from the license of unrelated code.

- `SYNTHETIC`: original fictional fixture; never real-world quality evidence.
- `REDISTRIBUTABLE`: redistribution clearly permitted, with the basis recorded.
- `LOCAL_ONLY`: rights unclear/restrictive; document **and gold excerpts** must use
  absolute paths outside this repository. Do not commit either file. Local manifest
  variants may live in ignored `evals/local/`; outputs stay in ignored `evals/runs/`.

The inspected Solodit tree at commit
`0be75be262b17472d7a2a857c6343b892eec86f7` contains no license/terms file. Three
candidate blobs remain in the external reference Git repository. Their hashes and
structural heading counts are recorded without report bodies or excerpts. Allowed
local evaluation use and redistribution still require confirmation. Reference
findings and headings are leads, never automatically accepted gold.

Before admitting a real case to headline metrics, a human must:

1. Establish provenance and confirm permitted local evaluation use.
2. Record exact document hash and redistribution basis.
3. Verify each finding exists and assign canonical category/normalized severity.
4. Verify exact excerpts and Unicode offsets against original text.
5. Save gold, compute its hash, explicitly set `human_reviewed=true`, and lock the case.

Do not change gold after seeing predictions to improve scores. Genuine corrections
need documented versioned history. Only reviewed real cases enter `real_world`.
Unreviewed real cases appear separately, excluded from `combined` quality metrics.

## Commands

From repository root, without credentials or network:

```sh
uv run python -m app.evaluation.cli validate --dataset evals/datasets/v1
uv run python -m app.evaluation.cli score \
  --dataset evals/datasets/v1 --predictions evals/fixtures/predictions.json
```

`score` validates the corpus and normalized prediction artifact, then writes
`report.json` and `report.md` under a fresh timestamped `evals/runs/` directory.
`--output evals/runs/my-run` chooses a directory that must not already exist.
JSON is authoritative. No command silently overwrites previous artifacts.

### Explicit live evaluation

**Live calls send selected documents to OpenAI and can incur charges.** Confirm
source-use permission and data-sharing suitability first. One invocation makes
one extraction attempt per selected case; failed cases are never rerun automatically.
No live benchmark has been used to establish a real-world baseline yet.

```sh
EVAL_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/chainsec_eval \
OPENAI_API_KEY=your-local-key \
OPENAI_MODEL=your-explicit-model-id \
uv run python -m app.evaluation.cli run --dataset evals/datasets/v1 --live
```

Supply credentials securely in your local environment; never commit keys or put
real secrets in shared command transcripts. OpenAI settings retain their existing
lazy `.env` behavior. `EVAL_DATABASE_URL` is **environment-only**, explicitly required,
and must use `postgresql+psycopg`. It never falls back to `DATABASE_URL`.
Use a dedicated evaluation database/role with schema-creation permission.

The runner creates a random `chainsec_eval_*` schema, sets a search path containing
only that schema, runs the published Alembic chain in a subprocess, verifies the
active schema, and uses existing ingestion/orchestration/completion services.
It drops only its owned schema on normal completion or handled exceptions. Normal
application tables and environment configuration are untouched. A process kill or
unavailable database during cleanup can leave an orphaned schema; inspect and remove
only that evaluation schema manually. No evaluation tables or migrations are added.

Before requests, the CLI prints case count, provider, exact model, prompt, and
dataset version. Neither `run` without `--live` nor validation/scoring/reporting
constructs a provider or sends network requests. Normal pytest blocks real HTTP
transports, including the OpenAI SDK transport, and CI needs no provider credentials.

Selection options for live runs:

```sh
# Add these to the run command with --live and configuration above:
--split dev
--split holdout
--case-id single --case-id unicode
```

Default selects all cases. A requested ID conflicting with the split is rejected.
Reports always list executed IDs, per-split metrics, and `partial_selection`.

Live `predictions.json` contains normalized **persisted** findings/evidence,
run status, fixed failure code, and configuration provenance. It stores no API keys,
headers, raw exceptions, SDK payloads, or hidden reasoning. Prediction indices sort
persisted findings by evidence start, title, category, severity, and source ID;
random ORM UUIDs are not the ordering key. Cancellation/ambiguous persistence may
leave a nonterminal run, reported as `INCOMPLETE` when observable, without retries.
Connection loss can abort evaluation; no quality score should be inferred from
an unfinished invocation. Reports contain source-derived content and must remain
local, especially for `LOCAL_ONLY` cases.

## Deterministic matching

Span IoU = intersection length / union length in Unicode character coordinates.
Finding overlap is the **maximum** IoU over all prediction/gold evidence-span pairs.
Eligible pairs have **IoU ≥ 0.50**, inclusive. Sort by descending IoU, ascending
`gold_id`, then ascending prediction index. Greedily consume unused prediction/gold
pairs, enforcing one-to-one matching. Title, category, and severity do not determine
a match. Repeated text at different offsets does not become the same evidence.

This is greedy assignment, not globally optimal semantic matching. All eligible
pairs competing for a prediction or gold are exposed in `ambiguous_pairs` for review.
A good source slice can still support the wrong finding. Containment and correct
gold evidence are reported separately. Broad overlapping evidence and multiple
findings in one span are known limitations of this initial rule.

## Metrics and interpretation

Within each group/split, aggregate counts before division (micro averaging):

| Metric | Definition |
| --- | --- |
| Precision | Matched predictions / all predictions |
| Recall | Matched gold / all gold |
| F1 | 2 × precision × recall / (precision + recall) |
| Unsupported prediction rate | Unmatched predictions / all predictions |
| Miss rate | Unmatched gold / all gold |
| Category accuracy | Exact enum matches / matched pairs |
| Severity accuracy | Exact enum matches / matched pairs |
| Exact evidence match rate | Matched pairs with any identical excerpt/start/end span / matched pairs |
| Mean best evidence IoU | Sum of matched pair IoUs / matched pairs |
| Containment validity | Exact source-slice-valid predicted spans / all predicted spans |
| Run failure rate | (`FAILED` + `INCOMPLETE`) cases / executed cases |
| Negative-case FP rate | Successful negative cases returning any finding / successful negative cases |

Zero denominators yield JSON `null`; F1 is null if precision/recall is undefined,
and zero when both are defined and their sum is zero. An empty negative case is
credited as correctly empty only after a successful run. Failed negatives do not
improve the negative FP denominator. Failure-code frequencies and separate
SUCCEEDED/FAILED/INCOMPLETE counts expose reliability.

Failed positives count as misses in **end-to-end recall**, but provider outages
produce no predictions and therefore no hallucinated false positives. Inspect run
codes alongside quality; this baseline does not infer whether a refusal or timeout
was a model, infrastructure, or source issue.

Reports separate `synthetic`, reviewed `real_world`, `combined` (synthetic + reviewed
real), and `real_world_unreviewed`; each has `all`, `dev`, and `holdout` sections.
Category and severity tables show counts/recall; severity also shows classification
accuracy among matched findings. Groups with 1–2 gold examples are `low_sample`.
Failure details identify missed gold, unmatched predictions, classification
disagreements, and offsets without an LLM explanation.

Every report records dataset and metric versions, manifest hash, scoring Git commit
and dirty-tree flag, prediction Git provenance, provider, exact model, prompt/schema
versions, UTC timestamps, case/gold counts, selected IDs, and adjudication state.
A dirty-tree result is not a committed reproducible release baseline.

No magic composite score or production pass threshold exists. Offline results and
results without reviewed real cases are `INCONCLUSIVE`. Reviewed live baselines
require human interpretation; tiny subsets cannot justify strong product claims.
The committed perfect fixture should yield 21/21 matches, precision/recall/F1 and
classification/evidence metrics = 1, zero negative FPs, and zero failures. These
values are evaluator checks, **not measured OpenAI performance**.

The error fixture selects three cases: 5 gold, 6 predictions, 4 matches; precision
2/3, recall 4/5, F1 8/11, category/severity accuracy 3/4, exact evidence and mean IoU
1, negative-case FP rate 1, and run failure rate 1/3. The report explicitly marks
this partial offline fixture. Run `score` with its path to inspect failure details.

## Optional human adjudication

Store review separately from original predictions and bind it to the prediction
file's SHA-256. A human-authored review requires reviewer identity, timezone-aware
`reviewed_at`, `human_reviewed=true`, and explicit decisions:

```json
{
  "predictions_sha256": "replace-with-the-64-character-file-sha256",
  "reviewer": "Human reviewer name",
  "reviewed_at": "2026-10-09T12:00:00Z",
  "human_reviewed": true,
  "decisions": [
    {"case_id": "single", "action": "MATCH", "prediction_index": 0, "gold_id": "g01"}
  ]
}
```

Other actions: `VALID_NEW_FINDING` or `UNSUPPORTED_FALSE_POSITIVE` target only a
prediction index; `MISSED` targets only a gold ID. Reject invalid, duplicate, or
unexecuted targets and reviews of unsuccessful cases. Explicit MATCH decisions
reserve a pair even below IoU threshold; other decisions reserve targets away
from automatic matching. Remaining candidates use the unchanged greedy rule.

```sh
uv run python -m app.evaluation.cli score --dataset evals/datasets/v1 \
  --predictions evals/runs/my-run/predictions.json \
  --adjudication evals/local/human-review.json
```

`automated_metrics` always remains unchanged. `adjudicated_metrics` is separate;
`human_overrides_applied` and attributed decisions are recorded. Valid new findings
remain unmatched to this fixed gold set and do not secretly inflate primary
precision/recall; their annotation is visible for corpus review. This preserves
the explicit matched-gold formulas. Adjudication never edits raw predictions,
gold, production verification status, or the frozen extraction target.
