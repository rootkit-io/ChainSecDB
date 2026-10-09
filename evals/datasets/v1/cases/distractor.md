# Threat glossary and actual results

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

Glossary: reentrancy, oracle attacks, signature replay, and overflow are classes of risk. Listing a term is not a reported defect.

## Findings

### DISTRACTOR-01: Liquidation in reviewed path
Severity: HIGH
The liquidation routine compares debt using the wrong decimals and seizes excess collateral.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
