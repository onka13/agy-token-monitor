# AGY Token Monitor

Displays per-turn and total session token usage for Antigravity Desktop sessions in a compact, always-on-top corner HUD window, and saves all statistics to a local SQLite database (`data/history.db`).

## Running

```zsh
./run.sh
```

## Features
- **Corner HUD:** Opens in the bottom-right corner, draggable, can be pinned always-on-top (`📌`), or collapsed (`▾`).
- **Session Tracking:** Automatically follows the most recently updated session when `Auto` is enabled; switch to any Conversation ID via the dropdown (prefixed with the project name) or `Enter ID`, and rename sessions with `✎`.
- **Last Turn & Session Total:** Shows `input_tokens`, `cache_read_tokens`, `output_tokens`, step/tool counts, duration, and session totals.
- **Persistent History & Statistics:** Stores all fields (tool calls, thinking/content character lengths, error counts, timestamps) in `data/history.db` (`sessions`, `turns`, and `steps` tables) and supports CSV export.
