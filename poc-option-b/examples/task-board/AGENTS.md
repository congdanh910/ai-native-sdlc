# Task Board

Browser task board without a build step. Keep this file short; add a line whenever an AI role repeats a mistake.

## Commands
- Test: `npm test`
- Syntax check: `node --check src/app.js`

## Structure
- `src/tasks.js`: pure task model (no DOM). All rules about tasks live here.
- `src/app.js`: DOM wiring only; calls functions from `src/tasks.js`.
- `test/`: Node test runner tests for `src/tasks.js`.

## Conventions
- ES modules, no dependencies.
- Validate input at the model boundary; never silently convert invalid input.

## Never
- Modify `.github/`, `package-lock.json` or anything outside `src/` and `test/`.
