# Bridge security review

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

## Findings

### MIXED-01: Cross Chain Bridge in reviewed path
Severity: CRITICAL
The bridge accepts messages without checking the origin chain, so an attacker can release locked assets.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### MIXED-02: Auth Signature in reviewed path
Severity: HIGH
Signatures omit the contract address, allowing a valid withdrawal signature to be replayed on another deployment.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### MIXED-03: Randomness Time in reviewed path
Severity: MEDIUM
The randomness seed uses the current timestamp, which block producers can bias.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
