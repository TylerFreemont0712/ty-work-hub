# Rewrite architecture

Git lives beside SVN under a shared Source control tab container. `MainWindow.source_control` remains the SVN workspace for compatibility, while `MainWindow.git` owns the standalone Git surface. `source_control_provider` chooses the generic command/shortcut target; ticket patch actions still target the SVN shelf. Git subprocesses use literal pathspecs, a validated repository root, native credential helpers, bounded waits, and a cancellable Qt worker. Staged commits require preview confirmation and a matching patch digest.

The app remains a native Python/PyQt6 desktop application. The redesign replaces the application chrome and the primary task, review, dashboard, and standup presentation, adds reusable rendering primitives, and improves selected persistence and background-work boundaries. The established Backlog, SVN, patch, Obsidian, and local AI adapters are retained to preserve their detailed workflows.

## Presentation boundaries

| Module | Responsibility |
| --- | --- |
| `ui/shell.py` | Sidebar, workspace labels, command bar, theme picker, responsive chrome |
| `ui/theme.py` | The named palettes. One frozen `Theme` per appearance; no other module hard-codes a colour |
| `ui/design.py`, `ui/styles.py`, `ui/typography.py` | Cached vector icons, native painting primitives, palette-driven stylesheet, offscreen font registration |
| `ui/patch_dialog.py` | Label, group, and note for one patch, used when creating and when editing |
| `modules/work_schedule.py` | The working day. Decides which tracked seconds count; no Qt, no I/O |
| `modules/work_insights.py` | Counted totals per day, ticket, and project, built on the schedule |
| `ui/timer_chip.py`, `ui/charts.py`, `ui/insights_widget.py` | The always-visible timer, painted charts, and the Time insights workspace |
| `ui/note_preview_dialog.py` | Review of a generated ticket note before it is written to the vault |
| `ui/main_window.py` | Application orchestration, selected ticket, service and workspace signal routing |
| `ui/inspector.py` | Scrollable selected-ticket controls, separate from orchestration |
| `ui/ticket_model.py` | Virtual task model, natural sorting, and painted action/status cells |
| `ui/ticket_list.py` | Search/filter state, bulk actions, stable selection, responsive table presentation |
| `ui/dashboard.py`, `ui/queue_delegate.py` | Today overview, focus session, priority previews, schedule, and work totals |
| `ui/daily_tools.py` | Local capture inbox, recurring checklist, and debounced scratchpad |
| `ui/review_board.py` | Virtual watchlist and selection-scoped actions |
| `ui/standup_widget.py` | Standup preparation, daily history, rollover-safe editor saves, and roster |
| `ui/calendar_delegate.py` | Calendar event blocks; the existing calendar controller handles editing and deadline actions |

## Efficiency changes

- `QTableView` and `QAbstractTableModel` replace per-cell items and nested buttons in Tasks. Seen/Note actions use painted cells with real click and keyboard/context-menu paths. No child widgets are allocated per row.
- Searchable ticket text is indexed when data arrives. Typing uses a 120 ms debounce. Local planning state is applied as one snapshot, producing one model reset instead of four separate table rebuilds.
- Sorting uses a proxy model, natural ticket keys, and semantic priority order. Selection is restored by stable ticket key; initial loads skip selection scans when no selection exists.
- The Review board also uses a virtual table. Commands search all indexed entries while rendering at most 60 matches.
- Active work is indexed in `EventStore`. Timer ticks no longer copy and scan all historical events.
- Seven-day work totals are recalculated when the store revision or date changes. Work spans are clipped to local day boundaries.
- Calendar rendering parses events once per render and reuses those timestamps across visible days. Hourly views show every affected hour and offer a chooser for overlaps.
- Local AI connection/model discovery uses `services/background.py`, with task lifetimes independent of a settings dialog. Existing streaming generation and SVN workers retain their specialized boundaries.
- Obsidian tree selection uses an owned, coalescing timer, avoiding stale callbacks after widget destruction.

## Persistence and compatibility

The names and formats of existing configuration, events, workspace, standup, and patch files are retained. Normal startup still uses `~/.ty-work-app`; `--demo` selects a temporary workspace before importing configuration-dependent modules.

Calendar commits restore the previous in-memory collection if atomic persistence fails. Workspace commits similarly restore the last successfully committed snapshot and skip identical writes. The scratchpad and standup keep unsaved editor content available after failure and block closing until a save succeeds.

SVN mutation previews/confirmations, patch manifests and integrity checks, credentials in the OS keychain, explicit application of generated notes, and ticket-scoped Backlog comment drafts remain intact.

## Verification scope

`tests.ui_smoke` exercises the inherited integration workflows, adapted to the new model API. `tests.svn_smoke` checks deterministic SVN/patch behavior. `tests.svn_integration` creates and removes an isolated real local SVN repository. `tests.rewrite_smoke` covers new behavior, failure recovery, threading, and task-volume measurements. `tools/preview.py` renders all workspaces and narrow layouts using isolated sample data.

Live Backlog and local AI server behavior require their configured services. The rewrite does not claim a live remote validation from the deterministic tests.
