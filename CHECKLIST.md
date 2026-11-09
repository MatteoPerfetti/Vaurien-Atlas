# Project checklist

## Completed

- Inspected the GitHub repository and the top level of the paper library; the repository initially contained a README and Python ignore template.
- Established a local checkout on the Desktop and documented the intended architecture and privacy boundaries.
- Added project-specific ignore rules. Verify with the `git check-ignore` examples below and inspect `git status --short`.
- Added an installable, empty Python package and documented virtual-environment setup. An offline editable-install smoke check used the locally available build backend; Python 3.14.6 imported the package successfully.
- Added placeholder settings for future local paths; the application does not yet read or validate them.
- Added an in-memory validated paper record with UUID identity, independent reading status and analysis scope. Run `python -m unittest discover -s tests -v` in the installed environment to verify its core behavior.

## Decisions

- Build one local Python application; SQLite is the structured source of truth.
- Keep analysis and topic mapping separate, with human review before accepting proposed connections.
- Export to an isolated Obsidian vault first; preserve personal writing and original files.
- Work one small task at a time; Vaultcraft is not part of the implementation.

## Next proposed task

- Define topic and evidenced relationship records before implementing SQLite persistence. No ingestion workflow is implemented yet.

## Reproduce the privacy check

From this repository in PowerShell:

```powershell
git check-ignore -v sample.pdf .env data/library.sqlite3 data/extracted/paper.txt private/reflection.md logs/run.log settings.local.toml
git status --short
```

These examples test ignore patterns, not every possible sensitive file. Always inspect staged content before committing.
