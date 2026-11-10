# Vaurien Atlas

A personal research and learning application for organizing papers, exploring topics and recording learning. It will support both curiosity-driven reading and professional development. The import, analysis and review workflows are experimental; the full application is not complete.

## Intended architecture

One local Python application will start with a command-line interface and a workflow controller. SQLite will hold the authoritative structured records and processing state. Deterministic Python functions will handle predictable operations; bounded model calls will handle paper analysis and topic mapping separately.

The initial workflow is: manual PDF import → identity and duplicate checks → text extraction → paper analysis → topic mapping → deterministic validation → selective semantic review → proposals for human review → accepted knowledge → Obsidian export. Source documents are untrusted data, never instructions to the application.

The paper analyst will extract source-backed questions, contributions, methods, limitations and findings, distinguishing paper claims from interpretation. The topic mapper will reuse established topics and propose evidenced links; co-occurrence alone does not establish a prerequisite. Proposed relationships will remain separate from accepted ones.

For now, day-to-day conversation and literature discovery are intended to happen in Codex/ChatGPT when the session has access to the local project and suitable search tools. They should use tested Python retrieval operations and keep suggested papers separate from the reading queue. This depends on the session's access and plan limits; it is not an autonomous background service. Paper analysis and topic mapping may use paid API calls. A later bounded literature scout could use an Undermind integration if its standalone availability, access and costs are verified first; the chat connector alone does not establish that Python can call it. A folder watcher will reuse the manual ingestion workflow once it is reliable. Learning planning begins as an on-demand workflow.

Papers, topics, evidenced relationships, reading sessions, skill evidence, projects and goals, and processing runs will have explicit records. Reading status, demonstrated understanding, processing state and review status are distinct: reading a paper does not prove mastery.

SQLite will remain the source of truth. A deterministic, one-way exporter will eventually generate linked paper and topic notes, navigation and a dashboard for Obsidian. Export will first be tested in an isolated vault; generated content and personal writing must have clear ownership, and repeat exports must preserve reflections. No unrestricted two-way sync is planned.

Extraction and analysis should be cached with source fingerprints and model/instruction versions. Validation precedes selective review; claims require supporting passages, and unavailable cost or usage measurements stay unknown. Model configurations will be evaluated on representative papers, including unsupported claims, relationships, correction effort and cost.

## Setup and use

Python 3.10 or newer is required. From the repository in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -c "import vaurien_atlas; print(vaurien_atlas.__file__)"
```

The editable installation links the package to the source directory so edits are visible without reinstalling. Installation requires the `pypdf` dependency and may require an internet connection. Run checks with `.\.venv\Scripts\python -m unittest discover -s tests -v`. See [CHECKLIST.md](CHECKLIST.md) for progress and the next proposed task.

### Manual import

After installing dependencies, create a local data directory and use a path to a PDF you want to register:

```powershell
New-Item -ItemType Directory -Force data
.\.venv\Scripts\python -m vaurien_atlas.cli import-pdf "C:\path\to\paper.pdf" --db data\atlas.sqlite3 --status queued
```

The command registers the file and extracts page text to the local database, keyed by file fingerprint and extractor version. A repeat import reuses cached extraction. Empty and very short pages are flagged for inspection; this heuristic does not prove extraction quality and there is no OCR. Encrypted, malformed or unreadable files are reported; a failed extraction is not cached, while an already registered paper remains in the database. Extraction reads the complete PDF into memory. The original file is not modified. The example settings file is not read by the command.

The reader rejects and does not cache results when `pypdf` reports warnings; a prior extraction cache version is not reused. A small real-PDF check produced text and cache hits for three of four files; one was rejected because `pypdf` reported skipped form content. This is not a scientific accuracy or visual-layout evaluation.

### Paper analyst (experimental Python API)

An isolated ChatGPT plan sign-in smoke test is available separately from the API-key integration. Install its optional dependencies with `.\.venv\Scripts\python -m pip install -e '.[signin-test]'`, then run `.\.venv\Scripts\python tools\test_chatgpt_signin.py` in a network-enabled terminal. It opens a browser for explicit sign-in and consent, makes one short streamed request using a model returned for the account, and does not save credentials. The user reported a successful live smoke test; it does not connect the analyst or mapper to the ChatGPT plan. Model availability and plan usage limits apply.

For the next isolated test, add `--analyst-schema` to that command. This sends a short synthetic page, requests the existing analyst JSON schema and verifies at least one exact quotation against that page. It tests response compatibility, not scientific accuracy or a real paper. It still requires a new browser sign-in because the smoke test does not save credentials.

Install the optional integration with `.\.venv\Scripts\python -m pip install -e '.[analyst]'`. The function `vaurien_atlas.analyst.analyze_paper` accepts an initialized OpenAI client, an explicitly chosen model, numbered page texts as a tuple, scope and extraction limitations. It sends the supplied text to the model provider; only call it when you intend that transmission. It makes one analysis request without silent truncation, plus at most one targeted repair request if quotations fail source validation. Its schema separates paper claims, background and interpretations and requires supporting quotations. Local validation checks each quote appears on the stated page. When only whitespace differs, it records the corresponding exact text from that page; different words, punctuation or page numbers are never silently repaired. The repair call may propose replacement quotations from the same pages, and every replacement is rechecked. If support is still missing, the result is labeled `partial`: the affected claims are kept separately in `unverified_claims`, while the remaining analysis contains only source-matched quotations. The mapper requires explicit `--allow-partial` to use that subset. Neither a source match nor a partial result proves scientific accuracy; inspect the original paper. Instructions are versioned in `src/vaurien_atlas/instructions/`.

The analyst has deterministic mocked tests and one live full-paper run that produced a partial result. A scientific evaluation across representative papers has not been completed. Empty sections and unknowns are allowed. Treat outputs as proposals for review.

The paper analyst also supports a first experimental ChatGPT plan integration. After installing `.[signin-test]`, use `--provider chatgpt-plan` with a model shown by the sign-in smoke test:

```powershell
.\.venv\Scripts\python -m vaurien_atlas.cli analyze-pdf "C:\path\to\paper.pdf" --db data\atlas.sqlite3 --model MODEL_FROM_TEST --provider chatgpt-plan
```

The analyst and mapper default to `gpt-6-luna` and `chatgpt-plan`; neither falls back to an API key. On a cache miss the analyst opens the browser for authorization, sends the extracted text to OpenAI and processes a streamed structured response. The mapper also requires browser authorization for each call. Access and refresh tokens stay in memory; a new process needs a new sign-in. A stable opaque host ID, not a credential, is stored in `%LOCALAPPDATA%\VaurienAtlas\chatgpt_host_id`. An analyst cache hit requires no sign-in. Provider is part of the analyst cache key and saved result. The plan route does not support `max_output_tokens`; the application instead stops reading an excessively large response locally. This cannot guarantee a server-side output-token limit. The plan mapper has simulated tests but has not yet been evaluated live on papers from different domains.

To use the separate paid API-key provider explicitly after installing the optional dependency and configuring `OPENAI_API_KEY`, run:

```powershell
.\.venv\Scripts\python -m vaurien_atlas.cli analyze-pdf "C:\path\to\paper.pdf" --db data\atlas.sqlite3 --model YOUR_MODEL --provider openai-api
```

Choose a model supported by your API account; this command sends extracted text to OpenAI and may incur charges. It prints the analysis, including source quotations, to the terminal. Do not put credentials in the repository. No visual reading or OCR is performed, and the declared scope refers to extracted text, not verified complete coverage.

The database records the model, instruction version, input fingerprint, run status, elapsed time, available token counts and proposed result. Cost remains unknown. Identical successful inputs, including partial drafts, are reused without a model call; pass `--retry-partial` to request a fresh analysis of a cached partial draft. Changes to the text, model name, instructions, schema, scope, limits or extraction version invalidate the cache. A model alias can change behind the same name, so use a fixed model version when reproducibility matters. Failed calls are recorded without their error messages and are not cached. A run interrupted by process termination can remain `running`; it is not automatically retried or marked complete.

### Review analysis evidence

Use the run ID and extractor version reported by `analyze-pdf`. These operations use only local SQLite and never call a model:

```powershell
.\.venv\Scripts\python -m vaurien_atlas.cli analysis-show --db data\atlas.sqlite3 --run RUN_UUID --extractor-version EXTRACTOR_VERSION
.\.venv\Scripts\python -m vaurien_atlas.cli evidence-list --db data\atlas.sqlite3 --run RUN_UUID --extractor-version EXTRACTOR_VERSION
.\.venv\Scripts\python -m vaurien_atlas.cli page-show --db data\atlas.sqlite3 --run RUN_UUID --extractor-version EXTRACTOR_VERSION --page 3
.\.venv\Scripts\python -m vaurien_atlas.cli evidence-decide --db data\atlas.sqlite3 --run RUN_UUID --extractor-version EXTRACTOR_VERSION --section findings --claim 1 --evidence 2 --action replace --page 3 --quote "exact text from the extracted page"
```

`evidence-list` separates source-matched evidence from pending quotations; its claim and evidence indices refer to the original unverified claims. Use `--action reject` without `--page` or `--quote` to discard an unsupported citation. `evidence-revise` can replace or reject a source-matched citation when it does not support the claim; its indices are 1-based positions in the current `analysis-show` output, and can change after an edit. If the last matched citation is removed, the claim leaves the source-matched subset. Each decision is stored locally with an audit history; it does not assert scientific accuracy. Existing partial runs can be inspected without a new model call; older runs did not record their extraction version, so use the version originally displayed for that run. There is no graphical interface or automatic semantic approval.

### First paper record

`vaurien_atlas.paper.Paper` assigns a UUID when created and keeps it when a record is copied with changed metadata or paths. Reading status is explicit, while analysis scope defaults to `not_analyzed`; neither implies mastery. Title, DOI, arXiv ID and source paths can be absent. Paths are references only and identifiers are not checked against external sources. The record does not detect duplicate papers or verify extraction coverage.

`vaurien_atlas.storage` can initialize an empty SQLite database and save or retrieve paper records by UUID. The caller chooses the database path, creates its parent directory and closes the connection. Saving an existing ID replaces that complete record and its paths in one transaction; use a dedicated connection without unrelated pending changes. Initialization upgrades versions 1–5 to version 6. Existing topics are migrated as proposed because their review status was not recorded. No database is created until explicitly requested and the example settings are not yet read.

`vaurien_atlas.ingestion.import_pdf` registers a PDF path and SHA-256 fingerprint, without modifying or copying the original. Identical bytes reuse a paper ID even under a different name; this preserves its existing reading status and retains old paths, including paths that may have moved. A previously registered path with changed contents is rejected for manual reconciliation. The header check is not full PDF validation; different versions of the same paper are not recognized as duplicates.

### Topics and relationships

`vaurien_atlas.knowledge` defines topics with a name, definition and aliases, and typed relationships: a paper covers a topic, or one topic is a prerequisite of or related to another. A prerequisite points from the prerequisite to the dependent topic. Each relationship needs a source passage, location, paper ID and provenance, and defaults to proposed for review. SQLite storage checks that referenced papers and topics exist. Validation does not establish whether a passage supports a claim or whether a location is accurate. A saved review status is a record, not yet an interactive review workflow.

Topics also have proposed, accepted or rejected review status, defaulting to proposed. `storage.search_topics` returns at most 50 accepted candidates matching a literal substring of the name or an alias; it is a basic lexical lookup, with ASCII case-insensitive matching and case-sensitive matching for non-ASCII characters, not semantic retrieval. `topic_review.validate_proposal` checks a proposed relationship against existing records and an exact passage on a numbered page of a stored extraction. These checks do not prove semantic support or prevent overly broad conceptual links.

### Propose and review topics

After completing an analysis, note its run ID and extractor version printed by `analyze-pdf`. The following command sends the analysis and a bounded set of accepted topic candidates to the model, then stages proposals for review. `--query` can be repeated up to five times; without it the candidate set is empty. Select meaningful search terms because this first version does not automatically retrieve semantically similar topics.

```powershell
.\.venv\Scripts\python -m vaurien_atlas.cli map-topics --db data\atlas.sqlite3 --run RUN_UUID --extractor-version EXTRACTOR_VERSION --model YOUR_MODEL --query feedback
.\.venv\Scripts\python -m vaurien_atlas.cli review-list --db data\atlas.sqlite3
.\.venv\Scripts\python -m vaurien_atlas.cli review-decide --db data\atlas.sqlite3 --kind topic --id TOPIC_UUID --decision accepted
.\.venv\Scripts\python -m vaurien_atlas.cli review-decide --db data\atlas.sqlite3 --kind relationship --id RELATIONSHIP_UUID --decision accepted
```

Use `rejected` to reject a proposal. A topic must be accepted before a relationship to it can be accepted. A decision cannot yet be changed or edited through the CLI; inspect definitions and cited passages before deciding. For a partial analysis, mapping requires an explicit `--allow-partial` flag and uses only the source-matched subset; unverified claims are excluded. The mapper only proposes paper–topic links, not topic prerequisites or aliases, and its output is not automatically accepted. Quotes must occur in the saved analysis and on the corresponding extracted page, but human review is needed to judge meaning and topic specificity. Mapper calls are bounded and use no automatic retries; their usage and failure records are not yet persisted or cached. The API integration and biological-versus-engineering cluster quality have not been evaluated with a real model.

### Example settings

[settings.example.toml](settings.example.toml) illustrates future paths for read and queued papers, local processing data and an isolated export vault. The application does not read settings yet, and no path is created or accessed by this example. Once configuration support exists, copy it to `settings.local.toml` and replace the placeholders with your own paths. That local file is ignored by Git; keep originals outside the repository and use a separate test vault before exporting to your existing vault. Relative-path resolution and validation will be defined when settings are implemented.

## Personal data

The repository is public. Keep PDFs, CVs, private notes, credentials, databases, extracted text and logs outside version control. The ignore rules provide a safeguard, not a guarantee: inspect `git status` and staged changes before committing. Original papers and the existing vault remain in their current locations.

## test to run on VS Code per the mapper
python -m vaurien_atlas.cli map-topics --db data\vaurien-first-test.sqlite3 --run a629276a-4061-4a63-a08b-058899b2771c --extractor-version pypdf:6.21.4:v2 --allow-partial
