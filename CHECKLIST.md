# Project checklist

## Completed

- Inspected the GitHub repository and the top level of the paper library; the repository initially contained a README and Python ignore template.
- Established a local checkout on the Desktop and documented the intended architecture and privacy boundaries.
- Added project-specific ignore rules. Verify with the `git check-ignore` examples below and inspect `git status --short`.
- Added an installable, empty Python package and documented virtual-environment setup. An offline editable-install smoke check used the locally available build backend; Python 3.14.6 imported the package successfully.
- Added placeholder settings for future local paths; the application does not yet read or validate them.
- Added an in-memory validated paper record with UUID identity, independent reading status and analysis scope. Run `python -m unittest discover -s tests -v` in the installed environment to verify its core behavior.
- Added in-memory topic, evidence and relationship records with typed endpoints and proposed review status; structural checks are covered by the same unit-test command.
- Added SQLite save and retrieval for paper records. Tests cover reopening a database, updates and rollback on a failed source insert.
- Added SQLite storage for topics, relationships and evidence, with reference checks, review status and a version 1 to 2 migration. Tests cover reopening, failed updates and migration.
- Added manual PDF fingerprint registration and exact-byte duplicate detection; tests use synthetic files and check renamed copies and changed paths.
- Implemented page extraction, SQLite extraction cache and a manual import command. On four local PDFs, three extracted and reused cache; one raised a pypdf warning about skipped form content and is now rejected instead of cached. Text presence does not verify formulas, figures or scientific accuracy.
- Added a versioned analyst instruction and structured, source-checked one-call OpenAI integration. Mock tests cover valid output, invented quotes, incomplete responses and input bounds; live model use and scientific evaluation remain outstanding.
- Added SQLite analysis runs and a cache tied to the paper, extraction, model and instruction inputs. Tests cover reuse, invalidation, failed calls and mismatched source text.
- Added an explicit `analyze-pdf` command; it uses a previously imported file, reports text-only limitations and prints the proposed analysis. Real API execution remains unverified.

## Decisions

- Build one local Python application; SQLite is the structured source of truth.
- Keep analysis and topic mapping separate, with human review before accepting proposed connections.
- Export to an isolated Obsidian vault first; preserve personal writing and original files.
- Work one small task at a time; Vaultcraft is not part of the implementation.
- Topic links should be specific and evidence-backed; unrelated marine biology and engineering papers should remain in separate clusters absent a supported, reviewed connection. Avoid generic links that create misleading clusters.

## Next proposed task

- Configure the optional SDK and a model, then evaluate analyst accuracy and cost on a small set of papers; investigate the PDF with skipped content separately.

## Reproduce the privacy check

From this repository in PowerShell:

```powershell
git check-ignore -v sample.pdf .env data/library.sqlite3 data/extracted/paper.txt private/reflection.md logs/run.log settings.local.toml
git status --short
```

These examples test ignore patterns, not every possible sensitive file. Always inspect staged content before committing.
