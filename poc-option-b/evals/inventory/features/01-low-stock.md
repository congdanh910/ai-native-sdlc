# Feature 1: Low stock alerts

As a store keeper, I want to set a reorder threshold per item and list the items that need reordering.

Acceptance criteria:

- Each item has a reorder threshold, a whole number of at least 0; new items default to 0 and negative values are rejected.
- `add` accepts `--threshold`, and a new `threshold SKU N` command changes it.
- `list --low` shows only items whose quantity is at or below their threshold, sorted by SKU.
- Inventory files saved before this feature load with threshold 0.
