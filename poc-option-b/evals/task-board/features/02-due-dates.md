# Feature 2: Due dates

As a team member, I want to assign an optional due date and see which unfinished tasks are overdue.

Acceptance criteria:

- Add an optional date input to create and edit forms; store dates as `YYYY-MM-DD` strings or `null`.
- Existing stored tasks without a due date continue to load and receive a `null` value in the model.
- Show the due date on each card. Mark a task overdue only when its due date is before today's local calendar date and its status is not Done. A task due today is not overdue.
- Reject malformed calendar dates; display a useful validation error without losing saved tasks.
- Add deterministic tests with an injected current date for overdue, due today, done task, invalid date, and old stored task. Run `npm test` and syntax checks.
