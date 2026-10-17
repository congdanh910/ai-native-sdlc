# Feature 5: JSON backup import and export

As a team member, I want to back up my task board to a JSON file and restore it later.

Acceptance criteria:

- Export all saved tasks as a UTF-8 JSON file with a top-level format version and task array. Include a timestamp in the filename.
- Add an import file control. Validate the format version, task schema, and unique IDs before changing storage. A malformed or unsupported file must leave current tasks untouched and show an error.
- Show the number of tasks in the file and require a clear confirmation before replacing the current board. Cancel leaves the board untouched.
- A successful import replaces the board, persists it, and updates the visible columns. Export and import must work for an empty board.
- Do not use network requests or external packages. Add deterministic tests for roundtrip, duplicate IDs, unsupported version, malformed JSON, and no mutation on validation failure. Run `npm test` and syntax checks.
