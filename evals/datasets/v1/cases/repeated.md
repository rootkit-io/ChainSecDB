# Repeated statements review

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

Documentation example (not a finding): The call can fail.

## Actual assessment
The external call ignores its return value and loses the recorded payout on failure.
## Findings

### REPEATED-01: External Call in reviewed path
Severity: MEDIUM
The call can fail.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
