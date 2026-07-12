# Repository Guidelines

## Project Structure & Module Organization

This repository contains a small Python conversion tool centered on `scripts/pdf2markdown`, a bash wrapper that provisions and runs the Python engine. Core logic lives in `scripts/pdf2markdown.py`; image classification and replacement logic is in `scripts/image_postprocess.py`; vendor HTML datasheet support is in `scripts/datasheet_sources.py`. Runtime dependencies are listed in `scripts/requirements.txt`, while test-only dependencies are in `scripts/requirements-dev.txt`.

Tests live under `tests/` and use fixtures from `tests/fixtures/`. Design notes and implementation plans are kept in `docs/superpowers/`.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate`: create and enter a local development environment.
- `pip install -r scripts/requirements.txt -r scripts/requirements-dev.txt`: install runtime and test dependencies.
- `pytest`: run the full test suite.
- `pytest -m "not slow"`: skip slower conversion tests.
- `scripts/pdf2markdown [-f] [--no-ocr] [--no-postprocess] <input.pdf | URL> [output_dir]`: run the converter through the wrapper. The wrapper maintains its own cache venv outside the repo unless `PDF2MARKDOWN_VENV` is set.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, descriptive `snake_case` names for functions and variables, and `UPPER_CASE` for module-level constants. Keep scripts importable where practical; tests rely on `pytest.ini` setting `pythonpath = scripts`. Prefer `pathlib.Path` for filesystem work and structured parsing libraries, such as BeautifulSoup, for HTML. Keep comments short and focused on non-obvious behavior.

## Testing Guidelines

Tests use `pytest`. Name test files `test_*.py` and test functions `test_*`. Add focused tests for URL routing, vendor source behavior, image deduplication, and conversion command construction when changing those areas. Mark slow or end-to-end cases with the existing `slow` or `e2e` markers. Network-dependent coverage should remain opt-in and isolated from the default fast suite.

Tests marked `known_defect` reproduce conversion defects observed on real datasheets. They assert the **correct** behavior, so they fail until the converter is fixed; the default suite auto-skips them. Run the red list with `pytest --known-defects -m "known_defect and not slow"` (fast) or `pytest --known-defects -m known_defect` (including e2e). When a defect is fixed, its test turns green: remove the `known_defect` marker so it becomes a permanent regression guard. Each test's docstring describes the symptom and where it was observed.

## Commit & Pull Request Guidelines

Use commit messages that help reviewers understand the observable effect of the change without inventing unsupported context.

### Subject

Write a concise, imperative subject line that is understandable in `git log --oneline`.

A prefix is optional. Add one, formatted as `prefix: subject`, only when at least one of these applies:

- A ticket ID is available from the user request, branch name, issue, or surrounding commits (for example `SEI-2196:`).
- The commit mostly concerns one project or component (for example `shopup6:` or `RestDbCore:`).
- A topic clearly describes the nature of the change (for example `CI`, `Docs`, `Cleanup`, `Typo`, `Correction`).

Multiple prefixes are permitted when more than one applies; chain them with colons, for example `SEI-2196: CI: ...`. Omit the prefix when none of these reasons applies. Do not invent ticket IDs, scopes, or prefixes.

Examples:

```text
Fix login redirect after session expiry
```

```text
ABC-123: Update device tree overlay generation
```

```text
CI: Fix MSI copy exit code
```

```text
SEI-2196: CI: Fix MSI copy exit code
```

### Body decision

After writing the subject, decide whether the subject alone is sufficient to understand the observable effect of the commit.

Use a subject-only commit when the subject is enough.

Add a body after a blank line when the subject would leave important context unclear, such as what behavior changed, what limitation was addressed, or what notable files, flows, or interfaces were affected.

### Body contents

When a body is needed, summarize the relevant context and important changes. Focus on information that helps a reviewer understand the commit.

Prefer explaining the user-visible, reviewer-relevant, or operational effect of the change over restating low-level implementation details that are obvious from the diff.

### Rationale

Include rationale only when it is directly supported by explicit evidence, such as the user request, issue text, failing test, error message, design note, code comment, or reviewed source material.

If the rationale is not clear from the available context, do not infer it.

### Tests, docs, and verification

Mention tests, documentation updates, setup commands, screenshots, manual checks, or other verification only when they were actually performed, reviewed, or explicitly provided.

Do not invent test results, claim verification that was not done, or imply that documentation was updated when it was not.

Do not append "Claude Session:" lines, since other people besides me are reading the commits as well and they might not be interested in links they don't get to open.

Pull requests should describe the user-visible change, list tests run, and call out network, OCR, or docling behavior changes. Include sample input URLs or files when changing vendor datasheet handling or output formatting.

## Agent-Specific Notes

Do not overwrite generated output unless `--force` behavior is under test. Avoid committing local virtualenvs, downloaded models, converted PDFs, or generated `<stem>.images/` directories.
