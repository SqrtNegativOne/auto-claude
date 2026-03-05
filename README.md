# Claude Code Auto-Task Runner

Automatically uses leftover Claude Code quota before it resets. When your usage is below 70% with 20 minutes left in a billing block, it launches Claude Code on the next task from your queue.

## Setup (one-time)

**Requirements:**
- Python with `pip install winotify`
- Node.js (for `npx`/`ccusage`)
- Claude Code CLI (`claude` on PATH)

**Run once to start the chain:**
```
remind_setup.bat
```

After that, Windows Task Scheduler takes over automatically. You never need to run it again unless the task gets deleted.

**To stop permanently:**
```
schtasks /delete /tn "ClaudeUsageNotifier" /f
```

## How it works

Every 5-hour Claude billing block, the script fires 20 minutes before reset:

| Usage | Has tasks? | What happens |
|---|---|---|
| < 70% | Yes | Toast notification → 20s grace period → Claude runs the task |
| < 70% | No | Notification: "no tasks in queue" |
| ≥ 70% | — | Warning notification only |

**Recovery from sleep/shutdown:**

| Scenario | Recovery mechanism |
|---|---|
| Slept through trigger (< 5 hrs) | `StartWhenAvailable` fires on wake |
| Slept through trigger (> 5 hrs) | Logon trigger fires on next login |
| Computer was fully shut down | Logon trigger fires on next login |
| Normal operation | Time trigger fires at exact moment |

## Cancelling a task

When Claude is about to launch, a toast notification appears with a **Cancel task** button. Clicking it deletes `.pending_task` (the sentinel file), aborting the launch. The task stays in the queue for next time.

## Adding tasks

Edit `tasks.txt`. Each task is a directory path followed by a prompt, separated by a blank line:

```
D:\my-project
Review error handling and write missing unit tests for core modules.

D:\another-project
Refactor the database layer to use connection pooling.
```

Lines starting with `#` are comments. Tasks are processed top-to-bottom and removed after completion.

## Reviewing what Claude did

Every completed task gets a session ID in the notification and the log file (`~/.claude/usage_notifier.log`). To review the full conversation:

```
claude --resume <session-id>
```

## Files

| File | Purpose |
|---|---|
| `remind.py` | Main script |
| `remind_setup.bat` | One-time launcher |
| `tasks.txt` | Your task queue |
| `cancel_task.bat` | Auto-generated; click from notification to cancel |
| `.pending_task` | Auto-generated sentinel file during grace period |
| `~/.claude/usage_notifier.log` | Log file |
