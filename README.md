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

The editable installation links the package to the source directory so edits are visible without reinstalling. Installing the build backend may require an internet connection. There are no runtime dependencies or runnable workflows yet. See [CHECKLIST.md](CHECKLIST.md) for progress and the next proposed task.

### Example settings

[settings.example.toml](settings.example.toml) illustrates future paths for read and queued papers, local processing data and an isolated export vault. The application does not read settings yet, and no path is created or accessed by this example. Once configuration support exists, copy it to `settings.local.toml` and replace the placeholders with your own paths. That local file is ignored by Git; keep originals outside the repository and use a separate test vault before exporting to your existing vault. Relative-path resolution and validation will be defined when settings are implemented.

## Personal data

The repository is public. Keep PDFs, CVs, private notes, credentials, databases, extracted text and logs outside version control. The ignore rules provide a safeguard, not a guarantee: inspect `git status` and staged changes before committing. Original papers and the existing vault remain in their current locations.
