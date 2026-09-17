# Ty Work Hub — Project Agent Guide

This file is the project-specific working agreement for agents and developers changing the Schedule project. The application calls itself **Ty Work Hub** in the UI and README.

## September 2026 architecture update

The application chrome and primary workspaces were rewritten. Read [docs/architecture.md](docs/architecture.md) for the current presentation boundaries. `ui/shell.py` owns navigation and the command bar; `ui/inspector.py` owns the selected-ticket layout; Tasks and Review board use virtual Qt models. `ui/theme.py` holds the named palettes and `ui/design.py`, `ui/styles.py`, and `ui/typography.py` render them. These replace the older styling and table assumptions described below.

Run `python -m tests.rewrite_smoke` in addition to the existing UI and SVN suites for new model, persistence, calendar, standup, and threading behavior. `python tools/preview.py` renders isolated screenshots, and `python main.py --demo` opens sample data in a temporary workspace. Ruff configuration is now in `pyproject.toml`. Existing integration and storage boundaries remain in effect.

## Product purpose

Ty Work Hub is a Windows and Linux PyQt6 desktop command center for efficient day-to-day software work. Its primary job is to reduce the friction between a personal Git project or Backlog ticket, the local work session, notes, planning, and the next action.

The product should feel fast, calm, keyboard-friendly, and trustworthy. A good change makes the common work path shorter without hiding state or making risky actions ambiguous.

## Current project map

The application is a single Python desktop program with deliberately small local modules and a central UI coordinator:

| Area | Location | Responsibility |
| --- | --- | --- |
| Entry points | `main.py`, `TyWorkHub.pyw` | QApplication setup, icon, global exception logging, stylesheet, window launch |
| Configuration | `config.py` | Defaults, validation, JSON preferences under `~/.ty-work-app` |
| Backlog adapter | `modules/backlog_api.py` | HTTP client, issue normalization, statuses, comments, demo issues |
| Local domain helpers | `modules/ticket_utils.py` | Ticket keys, project names, status buckets, Backlog URLs |
| Local persistence | `modules/event_store.py`, `modules/workspace_store.py`, `modules/standup_store.py` | Atomic JSON-backed events, planning state, standup data |
| Obsidian integration | `modules/obsidian_sync.py`, `ui/obsidian_widget.py` | Ticket notes, daily links, AI note templates, history backups, Markdown browsing/editing |
| Background work | `services/sync_service.py`, `services/local_llm.py` | QThread workers for Backlog HTTP and the streaming llama.cpp client |
| Source control | `modules/svn_models.py`, `modules/svn_service.py`, `modules/patch_store.py`, `services/svn_workers.py`, `ui/source_control_widget.py` | Safe SVN commands, patch manifests/integrity, background workflows, and the ticket-aware Source Control workspace |
| Credentials/cache | `services/credentials.py`, `services/issue_cache.py` | Windows Credential Manager access and offline issue cache |
| Time tracking | `services/time_tracking.py`, `modules/work_schedule.py`, `modules/work_insights.py` | One active work session backed by the event store, the schedule that decides what counts, and the totals built from both |
| Main shell | `ui/main_window.py` | Navigation, signals, refresh lifecycle, selected-ticket orchestration, shortcuts |
| Workspaces | `ui/dashboard.py`, `ui/ticket_list.py`, `ui/review_board.py`, `ui/calendar_widget.py`, `ui/standup_widget.py`, `ui/ticket_detail.py` | User-facing work surfaces |
| Dialogs and styling | `ui/*_dialog.py`, `ui/command_palette.py`, `ui/theme.py`, `ui/styles.py` | Settings, confirmation/input flows, keyboard command launcher, named-theme visual system |
| Regression coverage | `tests/ui_smoke.py` | Offscreen end-to-end smoke coverage with temporary local data and mocked network behavior |

The repository uses Ruff and Windows/Ubuntu CI. `modules/git_service.py` owns Git commands, `services/git_worker.py` owns background execution, and `ui/git_widget.py` is the personal-project workspace. `ui/git_settings.py` and `modules/git_install.py` own explicit dependency installation. Keep Git separate from the ticket-aware SVN patch shelf. Include `python -m tests.git_integration` in source-control changes; all repositories in that suite are temporary.

## Runtime and data boundaries

The application currently uses:

- PyQt6 for the desktop UI.
- `requests` for Backlog HTTP calls and the llama.cpp server's OpenAI-compatible API.
- `keyring` for the Backlog API key and environment passwords.
- JSON files under `~/.ty-work-app` for non-secret local state.
- An optional Obsidian vault selected in Settings.
- A local llama.cpp server for ticket briefs, note templates, and comment drafts.
- Backlog demo data when no API key is configured.
- A last-successful issue cache when a Backlog refresh fails.
- Local `svn.exe` commands and optional TortoiseSVN dialogs for source-control workflows.

The normal launch commands are:

```powershell
python main.py
```

The offscreen regression check is:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m tests.ui_smoke
```

When testing persistence, use a temporary directory and inject a `Path` into the store. Never use the user's real `~/.ty-work-app` or Obsidian vault in a test.

## Non-negotiable engineering principles

### 1. Keep boundaries clean

- Keep widgets responsible for presentation, signals, local validation, and user interaction.
- Keep domain rules and pure transformations outside Qt widgets wherever practical.
- Keep HTTP, filesystem-heavy work, subprocesses, and long-running operations outside the GUI thread.
- Treat `MainWindow` as an orchestration layer. Do not grow it into the implementation of every new feature. New substantial capabilities should have a focused module/service and a focused widget or dialog.
- Reuse `issue_key`, `project_name`, `status_bucket`, configuration normalization, and existing store patterns instead of duplicating them.
- Prefer explicit small data structures and typed return values over loose dictionaries passed through many layers. If a dictionary is necessary for a Qt signal or Backlog payload, document its keys at the boundary.
- Avoid unrelated formatting churn or broad refactors while implementing a feature.

### 2. Preserve responsiveness and recoverability

- Never perform network access, SVN/TortoiseSVN work, large directory scans, or blocking process waits in a slot running on the GUI thread.
- Every background operation needs a success path, an error path, and a finished/cleanup path. Keep worker references alive until Qt has finished with them.
- Surface progress, completion, cancellation, and failure in the UI. A status bar message alone is acceptable for small work; longer work needs a visible busy state or result panel.
- Preserve usable local state when an external service is unavailable. Follow the existing demo/cache/queued-action approach where it fits.
- Do not silently swallow exceptions around a new operation. If an error is intentionally non-fatal, explain why in code and provide a visible fallback or log entry.

### 3. Treat files, credentials, and commands as safety boundaries

- Never put API keys, passwords, tokens, or generated secrets in JSON config, source, crash logs, patch files, or user-visible diagnostics.
- Use atomic writes (`.tmp` followed by replace) for local state. Validate and tolerate malformed old records so one bad record cannot prevent startup.
- Resolve and validate user-selected paths before reading, writing, reverting, or launching anything. Reject empty, ambiguous, and out-of-scope paths.
- Do not copy `modules/launcher.py`'s `shell=True` pattern into new integrations. For SVN, use an argument list or `QProcess`; never interpolate ticket text, paths, or user input into a shell command.
- Destructive or potentially destructive actions—revert, patch apply, update, merge, cleanup, delete, and commit—must show the scope and require an intentional confirmation. Prefer preview, dry-run, or preflight output first.
- Preserve user-authored content. The Obsidian sync pattern—managed sections, backups before replacement, and explicit apply—is the right model for other generated or mutating workflows.

### 4. Make the common path obvious and fast

- Design for keyboard-first use: command palette, sensible shortcuts, row selection, Enter/Escape behavior, and visible tooltips.
- Add important actions to every relevant surface: toolbar or command palette, selected-ticket inspector, and context menu when appropriate. Keep labels consistent across all three.
- Optimize for a few seconds from “I found the ticket” to “I performed the next action.” Preserve the selected ticket when navigating between workspaces.
- Prefer safe defaults, remembered preferences, clear empty states, and inline feedback over repeated setup dialogs.
- Keep actions reversible where possible and make the next recovery action obvious when something fails.

### 5. Maintain the visual language

- Take every colour from the active `ui.theme.Theme`. Never hard-code a hex value in a widget, delegate, or paint handler; if a new colour is genuinely needed, add a token to `Theme` and give every registered theme a value for it.
- Use the existing `ui/styles.py` stylesheet, object names, properties, and spacing. A change has to hold up in all registered themes, both compact and comfortable density, and both light and dark palettes.
- Use the semantic tokens as intended: `accent` for identity/focus, `primary` for the confirming action, `muted` for secondary context, and `danger`/`warning` only for urgency or risk.
- Keep layouts usable at the tested widths, especially around the 900 px minimum window. Make rails collapse or hide intentionally rather than allowing clipping or horizontal overflow.
- Do not add decorative UI that competes with the selected ticket, current work state, or next action. Visual polish should improve scanability.
- Every new control needs a meaningful label, tooltip where the action is not self-evident, disabled/busy behavior, and an accessible keyboard path.

## Feature implementation workflow

1. Inspect the relevant modules, UI signal flow, configuration defaults, persistence format, and tests before editing.
2. Write down the user-visible flow and the failure/rollback behavior. For a mutating operation, define the preflight and confirmation text before coding.
3. Put pure logic in a testable module first. Add a worker/service boundary for external, blocking, or process-based work. Connect it to the UI with narrow signals.
4. Add or update configuration validation if the feature introduces a setting. Keep older config files valid and avoid storing secrets in them.
5. Add tests for the pure behavior and at least one UI smoke path. Cover success, empty input, malformed data, unavailable dependency, cancellation/timeout where relevant, and the Windows path shape when relevant.
6. Run the focused checks, then run the full offscreen smoke test. Inspect the changed files and the rendered behavior at narrow and normal window sizes.
7. Update `README.md` when a user-visible feature, setup step, shortcut, or persisted-data behavior changes.
8. Only after the feature or fix is fully implemented and verified, update `roadmap.md`: mark the completed item, record the meaningful implementation outcome, and add genuinely useful next goals or ideas that emerged. Do not mark a design, partial implementation, failed attempt, or unverified workaround as complete.

If a task is explicitly planning-only, update the roadmap only as planning material; do not move the item into Completed.

## Conventions for this codebase

- Use `from __future__ import annotations` in new Python modules.
- Prefer `Path` for filesystem work and `datetime` values with timezone information.
- Normalize external Backlog data at the adapter boundary. UI code should not need to understand every raw Backlog field variant.
- Use stable ticket keys for local state, not mutable titles.
- Record work sessions with their real start and end. Never trim a session to the working day at write time; ask `modules.work_schedule.WorkSchedule` how much of it counts at read time, so a schedule change re-reads history correctly and nothing is silently lost.
- Keep local-only workflow state separate from Backlog state and label it that way in the UI.
- Use signals for widget-to-window actions; avoid hidden parent calls from reusable widgets.
- When a mutation succeeds, refresh the affected view or re-render it explicitly so the screen cannot lie about state.
- When a mutation fails after local optimistic state changed, restore or refresh the state and explain what happened.
- Keep demo/offline behavior deterministic enough for the smoke test.
- Use clear, readable Python even where older files contain compact one-line methods. New code should favor maintainability over matching compressed formatting.

## SVN/TortoiseSVN feature guardrails

SVN integration is implemented as a patch-based ticket shelf. Preserve this product model when extending it:

1. Select a ticket and its working-copy folder.
2. Inspect status and diff.
3. Create a patch artifact in that ticket's patch folder. One ticket holds many patches, so each carries its own label, auto-assigned position, optional group, and optional note; the ticket title never names a patch on its own.
4. Verify the patch was created, then revert exactly the files represented by that patch after confirmation.
5. Later, preview and apply the patch back to the working copy when resuming or preparing to commit.

Classic SVN has patch creation/application but no universal native “shelve” concept. In this product, “shelve” and “unshelve” should be clearly labeled as the patch-backed workflow above, with a manifest and base information so the user can see what will be restored.

For maintenance and future expansion:

- Keep command construction and result parsing in a dedicated SVN service, not in `MainWindow`.
- Prefer `svn.exe` for deterministic status/diff/patch/revert operations and TortoiseSVN for optional visual dialogs or repository GUI actions. Detect and configure both explicitly.
- Run commands off the GUI thread, capture stdout/stderr/exit code, support cancellation, and retain the exact command intent in a redacted activity record.
- Validate that the selected working copy, patch destination, and patch application target are inside configured allowed roots. Prevent path traversal from ticket keys or patch metadata.
- Default to read-only inspection. Show the file list and diff before revert or apply. Never auto-commit, auto-update, or auto-delete a patch.
- Treat patch application conflicts as a first-class result with clear next actions: inspect, resolve externally, retry, or abandon.
- Patch metadata (label, group, note, position) is editable after the fact; patch and manifest file names are not. Renaming files would let one half of an artifact pair drift from the other.
- Add command-palette entries, selected-ticket context actions, keyboard shortcuts, and visible results together so the feature is efficient without being hidden.

The completed release notes, acceptance criteria, and grounded follow-up goals live in `roadmap.md`.

## Definition of done

A change is done when:

- The requested behavior works through the intended UI path and command-palette/shortcut path where applicable.
- The UI remains responsive and gives clear feedback for success, failure, and cancellation.
- Existing local data, offline/demo mode, themes, density settings, and narrow layouts remain usable.
- Secrets and user-authored files are protected.
- Focused tests and `tests.ui_smoke` pass, or the limitation is documented with a concrete reason.
- User-facing documentation is current.
- `roadmap.md` is updated only after the implementation is genuinely complete, with follow-on ideas added only when they are useful and grounded in the work.
