# Simple access review

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

## Findings

### SINGLE-01: Access Control in reviewed path
Severity: HIGH
Anyone can call setFeeRecipient and redirect every future fee payment.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
