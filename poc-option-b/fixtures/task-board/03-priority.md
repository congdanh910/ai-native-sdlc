# Feature 3: Priority and sorting

As a team member, I want to assign Low, Medium, or High priority and see the most important work first within each column.

Acceptance criteria:

- Add a priority selector to create and edit forms and a visible priority label on each card.
- New tasks default to Medium. Existing stored tasks without priority load as Medium. Reject unknown priority values.
- Sort cards within each status column High, Medium, Low. Within a priority, preserve creation order, then use task ID to break ties.
- Changing priority updates storage and immediately reorders the board. Moving a task to a different status retains its priority.
- Add deterministic tests for defaults, migration of old saved tasks, invalid values, sort order, and stable ties. Run `npm test` and syntax checks.
