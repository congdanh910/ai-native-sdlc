# Review rules

## Passes
1. Bugs: logic errors, unhandled input, broken existing behaviour.
2. Conformance: the diff implements spec.md and plan.md, and nothing else.
3. Tests: every acceptance criterion has a test that would fail without the change.

## Severity
- Important: a bug, a missing acceptance criterion, a test that cannot fail, or a change outside the plan.
- Nit: naming, comments, formatting. Report at most three.

## Skip
- Style that the formatter handles.
