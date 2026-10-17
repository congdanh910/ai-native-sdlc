# Feature 4: CSV export

As a store keeper, I want to export the inventory to CSV so that I can open it in a spreadsheet.

Acceptance criteria:

- `export PATH` writes a CSV file with the header sku,name,quantity,updated_at and one row per item sorted by SKU.
- Names containing commas, quotes or line breaks are quoted correctly.
- The command refuses to overwrite an existing file unless `--force` is given.
- An empty inventory exports only the header.
