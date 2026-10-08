# Taxonomy direction — Phase 1B onward

[Project overview](../README.md) · [Roadmap](roadmap.md)

**Planning only.** Phase 1A has no finding extraction, classification, taxonomy
tables, canonical category list, or source-to-canonical mapping implementation.
The current `document_type` field describes a document; it is not a vulnerability
taxonomy.

## Preserve source terminology

Audit reports, competitions, incident databases, and security tools often use
different category names, severity scales, and root-cause descriptions. Future
structured records should retain those source labels verbatim alongside their
supporting evidence. Normalization must not silently rewrite or discard them.

## Canonical interpretation is a separate layer

The planned canonical taxonomy provides a way to compare related problems across
sources. A canonical assignment should remain distinguishable from the upstream
label and traceable to original evidence. Severity, vulnerability category, and
root cause should not be treated as interchangeable concepts.

No canonical categories or mapping rules are fixed by this document. Schema and
taxonomy choices belong to a separately scoped Phase 1B implementation.

## Evidence and uncertainty

Future mappings and normalized fields should:

- Reference the original raw document and field-level supporting evidence.
- Preserve the source label even when a canonical mapping is proposed.
- Allow unknown, ambiguous, or unmapped values instead of forcing a category.
- Record human verification state separately from generated suggestions.

AI-assisted classifications are planned for Phase 1C. Any generated interpretation
must remain reviewable and must never replace source material. Versioning and
evaluation details will be designed with that phase, rather than added as
placeholders now.
