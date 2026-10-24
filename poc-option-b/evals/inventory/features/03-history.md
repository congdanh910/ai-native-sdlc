# Feature 3: Stock history

As a store keeper, I want every stock adjustment recorded with a reason so that I can explain the current quantity.

Acceptance criteria:

- Every successful adjustment records the time, the change, the resulting quantity and a reason; the reason is required and trimmed.
- `adjust SKU DELTA --reason TEXT` records it; a failed adjustment records nothing.
- `history SKU` prints the movements of one item, oldest first.
- Inventory files saved before this feature load with empty histories.
