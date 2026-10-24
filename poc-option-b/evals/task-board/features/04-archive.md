# Feature 4: Archive completed tasks

As a team member, I want to clear completed work from the active board without deleting its history.

Acceptance criteria:

- A Done task can be archived. Archived tasks disappear from the three active columns and appear in a separate Archive view.
- Archived tasks remain in local storage and can be restored to Done. Delete remains a separate, explicit action.
- Tasks without an archive field in existing storage are treated as active. Reject invalid archive values.
- Do not offer Archive for a To do or Doing task. Restored tasks retain their original title, description, creation time, and ID.
- Counts on the active board exclude archived tasks. Add deterministic tests for archive eligibility, persistence compatibility, restore, and counts. Run `npm test` and syntax checks.
