# Vaurien Atlas

A personal research and learning application for organizing papers, exploring topics and recording learning. It will support both curiosity-driven reading and professional development. This repository currently contains the project foundation only; the application is not implemented yet.

## Intended architecture

One local Python application will start with a command-line interface and a workflow controller. SQLite will hold the authoritative structured records and processing state. Deterministic Python functions will handle predictable operations; bounded model calls will handle paper analysis and topic mapping separately.

The initial workflow is: manual PDF import → identity and duplicate checks → text extraction → paper analysis → topic mapping → deterministic validation → selective semantic review → proposals for human review → accepted knowledge → Obsidian export. Source documents are untrusted data, never instructions to the application.

The paper analyst will extract source-backed questions, contributions, methods, limitations and findings, distinguishing paper claims from interpretation. The topic mapper will reuse established topics and propose evidenced links; co-occurrence alone does not establish a prerequisite. Proposed relationships will remain separate from accepted ones.

A later conversational manager will retrieve relevant records and call the same tested operations. A bounded literature scout will search for an explicit question and suggest a small shortlist separately from the reading queue. A folder watcher will reuse the manual ingestion workflow once it is reliable. Learning planning begins as an on-demand workflow.

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

Install the optional integration with `.\.venv\Scripts\python -m pip install -e '.[analyst]'`. The function `vaurien_atlas.analyst.analyze_paper` accepts an initialized OpenAI client, an explicitly chosen model, numbered page texts as a tuple, scope and extraction limitations. It sends the supplied text to the model provider; only call it when you intend that transmission. It makes one bounded request, without silent truncation or retries. Its schema separates paper claims, background and interpretations and requires supporting quotations. Local validation checks each quote appears on the stated page, but cannot prove the claim follows from the quotation. Instructions are versioned in `src/vaurien_atlas/instructions/`.

The analyst has deterministic mocked tests only: no real model call or scientific evaluation has been performed. Empty sections and unknowns are allowed. Treat outputs as proposals for review.

To analyze a previously imported PDF after installing the optional dependency and configuring `OPENAI_API_KEY`, run:

```powershell
.\.venv\Scripts\python -m vaurien_atlas.cli analyze-pdf "C:\path\to\paper.pdf" --db data\atlas.sqlite3 --model YOUR_MODEL
```

Choose a model supported by your API account; this command sends extracted text to OpenAI and may incur charges. It prints the analysis, including source quotations, to the terminal. Do not put credentials in the repository. No visual reading or OCR is performed, and the declared scope refers to extracted text, not verified complete coverage.

The database records the model, instruction version, input fingerprint, run status, elapsed time, available token counts and proposed result. Cost remains unknown. Identical successful inputs are reused without a model call; changes to the text, model name, instructions, schema, scope, limits or extraction version invalidate the cache. A model alias can change behind the same name, so use a fixed model version when reproducibility matters. Failed calls are recorded without their error messages and are not cached. A run interrupted by process termination can remain `running`; it is not automatically retried or marked complete. The command does not yet offer an interactive review of results.

### First paper record

`vaurien_atlas.paper.Paper` assigns a UUID when created and keeps it when a record is copied with changed metadata or paths. Reading status is explicit, while analysis scope defaults to `not_analyzed`; neither implies mastery. Title, DOI, arXiv ID and source paths can be absent. Paths are references only and identifiers are not checked against external sources. The record does not detect duplicate papers or verify extraction coverage.

`vaurien_atlas.storage` can initialize an empty SQLite database and save or retrieve paper records by UUID. The caller chooses the database path, creates its parent directory and closes the connection. Saving an existing ID replaces that complete record and its paths in one transaction; use a dedicated connection without unrelated pending changes. Initialization upgrades versions 1–4 to version 5. No database is created until explicitly requested and the example settings are not yet read.

`vaurien_atlas.ingestion.import_pdf` registers a PDF path and SHA-256 fingerprint, without modifying or copying the original. Identical bytes reuse a paper ID even under a different name; this preserves its existing reading status and retains old paths, including paths that may have moved. A previously registered path with changed contents is rejected for manual reconciliation. The header check is not full PDF validation; different versions of the same paper are not recognized as duplicates.

### Topics and relationships

`vaurien_atlas.knowledge` defines topics with a name, definition and aliases, and typed relationships: a paper covers a topic, or one topic is a prerequisite of or related to another. A prerequisite points from the prerequisite to the dependent topic. Each relationship needs a source passage, location, paper ID and provenance, and defaults to proposed for review. SQLite storage checks that referenced papers and topics exist. Validation does not establish whether a passage supports a claim or whether a location is accurate. A saved review status is a record, not yet an interactive review workflow.

### Example settings

[settings.example.toml](settings.example.toml) illustrates future paths for read and queued papers, local processing data and an isolated export vault. The application does not read settings yet, and no path is created or accessed by this example. Once configuration support exists, copy it to `settings.local.toml` and replace the placeholders with your own paths. That local file is ignored by Git; keep originals outside the repository and use a separate test vault before exporting to your existing vault. Relative-path resolution and validation will be defined when settings are implemented.

## Personal data

The repository is public. Keep PDFs, CVs, private notes, credentials, databases, extracted text and logs outside version control. The ignore rules provide a safeguard, not a guarantee: inspect `git status` and staged changes before committing. Original papers and the existing vault remain in their current locations.
