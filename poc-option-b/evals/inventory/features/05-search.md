# Feature 5: Search

As a store keeper, I want to find items by part of their SKU or name.

Acceptance criteria:

- `list --search TEXT` shows items whose SKU or name contains the text, ignoring case and surrounding spaces.
- An empty search shows all items.
- Results stay sorted by SKU.
- A search without matches prints the header and a "no items match" line and exits with code 0.
