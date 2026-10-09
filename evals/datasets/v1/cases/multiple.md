# Vault security review

This is an original fictional evaluation fixture, not an audit of a real protocol.

## Scope
Manual review of the specified behavior only.

## Findings

### MULTIPLE-01: Reentrancy in reviewed path
Severity: HIGH
withdraw transfers ether before reducing the balance, allowing the recipient to withdraw twice by reentering.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### MULTIPLE-02: Oracle Price Manipulation in reviewed path
Severity: HIGH
borrow values collateral with the instantaneous pool price, which a flash loan can inflate.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### MULTIPLE-03: Arithmetic Precision in reviewed path
Severity: LOW
The fee calculation rounds down on each deposit, allowing many small deposits to avoid fees.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

### MULTIPLE-04: Upgradeability Initialization in reviewed path
Severity: MEDIUM
The admin upgrade skips storage-layout checks and can overwrite user balances.
Impact: The behavior above violates the stated protocol guarantees.
Recommendation: Enforce the missing invariant before changing state.

## Limitations
This fictional report does not certify real software.
