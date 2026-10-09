# 🔐 Revisión de café — 東京 é

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

## Findings

### UNICODE-01: Token Integration in reviewed path
Severity: HIGH
The vault credits the requested token amount rather than the smaller amount actually received.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### UNICODE-02: Accounting in reviewed path
Severity: MEDIUM
Reward debt is updated after the reward amount is calculated twice, duplicating earned rewards.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
