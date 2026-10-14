# Feature 2: Categories

As a store keeper, I want to group items by category so that I can list one group at a time.

Acceptance criteria:

- An item may have a category; categories are trimmed and stored in lower case, and an empty category means none.
- `add` accepts `--category`, and `list --category NAME` shows only that category (case-insensitive).
- `list` shows a category column.
- Inventory files saved before this feature load with no category.
