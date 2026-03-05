# Claude Auto-Task Runner

This project auto-launches Claude Code on queued tasks when usage is low before a billing block resets.

## Key files

- `remind.py` — main script; run by Windows Task Scheduler
- `tasks.txt` — task queue (directory + prompt blocks, separated by blank lines)
- `remind_setup.bat` — one-time launcher to register the scheduled task
- `pyproject.toml` / `uv.lock` — uv project; dependencies: `loguru`, `winotify`

## Architecture

**Self-scheduling chain**: `remind.py` always ends by calling `schedule_next()`, which registers itself in Windows Task Scheduler for the next billing block. The task has two triggers: a `TimeTrigger` (20 min before block end) and a `LogonTrigger` (self-heal on boot/login). `StartWhenAvailable=true` with a 5-hour window handles sleep recovery.

**Flow at trigger time**: fetch block data via `ccusage` → if ≤20 min left and usage <70% and tasks exist → show grace-period notification → wait 20s → check sentinel file → launch `claude -p` → notify with session ID.

**Cancellation**: grace period uses a sentinel file (`.pending_task`). The notification's Cancel button runs `cancel_task.bat` which deletes the sentinel. Script checks existence after sleep.

## Constants to tune (top of remind.py)

- `WARN_MINUTES = 20` — how early before reset to trigger
- `USAGE_THRESHOLD = 70` — only auto-launch if usage is below this %
- `GRACE_SECONDS = 20` — cancel window before Claude launches

## Task queue format

```
# comment
D:\path\to\project
Prompt text here, can span
multiple lines.

D:\another\project
Another prompt.
```

First non-comment line in a block = working directory. Completed tasks are removed from the top.

## Reviewing Claude's work

`claude --resume <session-id>` — session IDs are logged to `~/.claude/usage_notifier.log` and shown in the completion notification.
