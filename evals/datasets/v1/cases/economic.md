# Liquidity incentives assessment

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

## Findings

### ECONOMIC-01: Economic Attack in reviewed path
Severity: MEDIUM
The subsidy exceeds trading fees, so a trader can wash trade indefinitely for net profit.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### ECONOMIC-02: Other in reviewed path
Severity: LOW
An unsupported native-asset withdrawal path always reverts and strands those balances.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
