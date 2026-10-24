# Feature 1: Search and status filter

As a team member, I want to find a task by title or description and optionally limit results to one status.

Acceptance criteria:

- Add a case-insensitive search field and an All / To do / Doing / Done status filter above the board.
- Trim the query. Match a task when the query appears in its title or description. Empty query matches every task.
- Search and filter do not change or persist task data. Clearing both controls restores the full board.
- Column counts reflect the visible tasks. A clear empty-result message appears when there are no matches.
- Existing create, edit, move, delete, and reload behavior still work.
- Add deterministic model tests for title match, description match, case handling, combined status filter, and no match. Run `npm test` and syntax checks.
