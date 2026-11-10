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
- Added topic review states and a version 6 migration; existing topics remain proposed until explicitly reviewed.
- Added bounded literal lookup over accepted topic names and aliases, plus deterministic checks that proposed links cite an exact passage on a stored paper page. These checks do not establish semantic support.
- Added a separate, bounded topic mapper that proposes existing or new paper-topic links from a completed analysis and stages evidence-backed proposals atomically. Tests use a simulated model; live model quality, mapper usage logging, aliases and topic-to-topic proposals remain outstanding.
- Added commands to list, accept or reject proposals; a topic must be accepted before its link. Editing and reversing decisions are not yet available.
- Prepared an isolated, non-persistent ChatGPT plan sign-in smoke test with a simulated-flow test. The user reported live authentication and basic inference succeeded; this does not yet integrate the analyst or mapper.
- User reported the basic ChatGPT plan sign-in smoke test succeeded. Added a separate structured analyst-schema test with synthetic text.
- User reported the structured analyst-schema smoke test succeeded. Added an experimental one-session ChatGPT plan provider for the paper analyst, with provider-specific cache keys and simulated streaming/CLI tests. A live full-paper analyst run remains unverified; the topic mapper still uses the API-key provider.
- A first live full-paper analyst run failed with a generic ValueError; its cause was not recorded. Added controlled, non-sensitive diagnostics for future attempts, tested with simulated failures.
- A second live run failed because a quote did not match its stated page. Added v2 instructions requesting short exact quotations, whitespace-only matching against source text, and safe section/page diagnostics; real-paper retry remains outstanding.
- A later live retry again failed on a research-question quote. Added at most one bounded model call to seek exact replacement quotations on the same pages; all replacements are revalidated. If repair fails, source-matched claims are saved in a clearly partial draft and unverified claims are kept separately for review. Simulated tests cover this behavior; the live flow remains unverified.
- Separated matched and pending evidence per claim, including older partial runs. Added local commands to inspect, replace or reject citations and preserve review history; all replacement quotes are checked against the selected extracted page.
- Connected the topic mapper to the ChatGPT plan provider and made `gpt-6-luna` the default for analyst and mapper. The mapper integration is tested with a simulated client, not yet with a live model or unrelated papers.
- Prepared a second, unrelated real PDF in the isolated test database: 21 pages extracted without a reported reader warning. No model analysis was run for this paper yet.

## Decisions

- Build one local Python application; SQLite is the structured source of truth.
- Keep analysis and topic mapping separate, with human review before accepting proposed connections.
- Export to an isolated Obsidian vault first; preserve personal writing and original files.
- Work one small task at a time; Vaultcraft is not part of the implementation.
- Topic links should be specific and evidence-backed; unrelated marine biology and engineering papers should remain in separate clusters absent a supported, reviewed connection. Avoid generic links that create misleading clusters.
- For now, use API calls for paper analysis and topic mapping; use an available Codex/ChatGPT session for daily conversation and literature discovery instead of calling the API from the application for those interactions.
- Consider an automated literature scout with Undermind later if actual costs are acceptable and a supported standalone integration is verified. Chat tool availability alone does not prove standalone access.

## Next proposed task

- Review the existing partial SWA analysis and perform a small live Luna mapper evaluation across distinct topics. Add secure reusable sign-in and mapper run tracking. Investigate the PDF with skipped content separately.

## Reproduce the privacy check

From this repository in PowerShell:

```powershell
git check-ignore -v sample.pdf .env data/library.sqlite3 data/extracted/paper.txt private/reflection.md logs/run.log settings.local.toml
git status --short
```

These examples test ignore patterns, not every possible sensitive file. Always inspect staged content before committing.
