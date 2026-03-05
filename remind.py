"""
Claude Code Usage Notifier + Auto-Task Runner
==============================================
Run once. It will:
  1. Read your current Claude Code billing block via ccusage
  2. If usage < 70% with <=20 min left: auto-launch Claude Code
     on the next task from tasks.txt, then notify you with the
     session ID so you can review via `claude --resume <id>`
  3. If usage >= 70%: just fire a Windows notification
  4. Schedule itself in Windows Task Scheduler to run again
     20 minutes before the NEXT block ends

First run:
    uv run remind.py

After that, it reschedules itself automatically every block.
To stop: schtasks /delete /tn "ClaudeUsageNotifier" /f
Task list: tasks.txt (same folder as this script)
"""

import json
import subprocess
import sys
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from loguru import logger

TASK_NAME        = "ClaudeUsageNotifier"
WARN_MINUTES     = 20
USAGE_THRESHOLD  = 70          # auto-launch Claude if usage is below this %
GRACE_SECONDS    = 20          # seconds to wait before launching (cancel window)
LOG_FILE         = Path.home() / ".claude" / "usage_notifier.log"
SCRIPT_PATH      = Path(__file__).resolve()
SCRIPT_DIR       = SCRIPT_PATH.parent
TASKS_FILE       = SCRIPT_DIR / "tasks.txt"
PENDING_FILE     = SCRIPT_DIR / ".pending_task"   # delete this to cancel
CANCEL_SCRIPT    = SCRIPT_DIR / "cancel_task.bat"
UV_EXE           = shutil.which("uv") or "uv"
AUTO_CREATIONS_DIR = Path.home() / "Documents" / "Claude auto-creations"

# CLAUDE.md written into every auto-created project directory.
# Instructs Claude to work freely inside the folder but stop before going outside it.
AUTO_PROJECT_CLAUDE_MD = """\
# Automated Claude Session

You are running in a **fully automated, non-interactive session** launched by an auto-task runner.

## Rules (non-negotiable)

- **Stay inside this folder.** Never read, write, move, delete, or execute anything outside
  your assigned project directory. If completing the task would require leaving this folder,
  stop immediately and explain why in your output instead of proceeding.
- **No approval needed inside this folder.** Create, edit, and delete files freely within
  this directory. Do not ask the user for permission, clarification, or plan approval before
  taking action here — just do the work.
- **Make reasonable decisions autonomously.** If something is ambiguous, choose the most
  sensible approach, document your reasoning in a comment or README, and continue.
- **No system-level changes.** Do not install system packages, modify environment variables,
  change registry entries, or touch anything outside this project directory.
"""

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add(LOG_FILE, level="INFO", encoding="utf-8", rotation="1 week")


# ── Notification ───────────────────────────────────────────────────────────────

def notify(title: str, body: str, actions=None):
    """
    Show a Windows toast notification.
    actions: optional list of (label, launch_path) tuples for clickable buttons.
    """
    from winotify import Notification, audio
    t = Notification(app_id="Claude Code", title=title, msg=body, duration="long")
    t.set_audio(audio.Default, loop=False)
    for label, launch in (actions or []):
        t.add_actions(label=label, launch=launch)
    t.show()
    logger.info("[notify] {} | {}", title, body)


# ── ccusage ────────────────────────────────────────────────────────────────────

def fetch_blocks():
    try:
        r = subprocess.run(
            "npx --yes ccusage@latest blocks --json",
            capture_output=True, text=True, timeout=45, shell=True,
        )
        if r.returncode != 0:
            logger.warning("ccusage error: {}", r.stderr.strip()[:300])
            return None
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.warning("ccusage failed: {}", e)
        return None

def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))

def find_active_block(data):
    blocks = data if isinstance(data, list) else (data or {}).get("blocks") or []
    now = datetime.now(timezone.utc)
    active = None
    for b in blocks:
        start = parse_dt(b.get("startTime") or b.get("start_time") or b.get("start"))
        end   = parse_dt(b.get("endTime")   or b.get("end_time")   or b.get("end"))
        if start is None or start > now:
            continue
        if end is None:
            end = start + timedelta(hours=5)
        if end > now:
            if active is None or start > active["_start"]:
                active = dict(b, _start=start, _end=end)
    return active

def usage_pct(block):
    for k in ("usagePercentage", "usage_percentage", "pct", "percentUsed", "percent_used"):
        v = block.get(k)
        if v is not None:
            return float(v)
    used  = block.get("totalTokens") or block.get("tokenCount")  or block.get("tokens_used")
    limit = block.get("tokenLimit")  or block.get("maxTokens")   or block.get("tokens_limit")
    if used and limit:
        return round(100.0 * float(used) / float(limit), 1)
    return None


# ── Task file ─────────────────────────────────────────────────────────────────

def parse_tasks():
    """Read tasks.txt and return a list of {"dir": str, "prompt": str}."""
    if not TASKS_FILE.exists():
        return []
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()

    tasks = []
    current_dir = None
    current_prompt_lines = []

    def flush():
        nonlocal current_dir, current_prompt_lines
        if current_dir and current_prompt_lines:
            tasks.append({
                "dir": current_dir,
                "prompt": "\n".join(current_prompt_lines).strip(),
            })
        current_dir = None
        current_prompt_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped == "":
            flush()
            continue
        if current_dir is None:
            current_dir = stripped
        else:
            current_prompt_lines.append(stripped)

    flush()
    return tasks


def remove_first_task():
    """Remove the first task from tasks.txt, preserving comments and structure."""
    if not TASKS_FILE.exists():
        return
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()

    result = []
    in_first_task = False
    first_task_removed = False

    for line in lines:
        stripped = line.strip()

        if first_task_removed:
            result.append(line)
            continue

        if stripped.startswith("#") or (stripped == "" and not in_first_task):
            result.append(line)
            continue

        if not in_first_task:
            in_first_task = True
            continue  # skip directory line

        if stripped == "":
            first_task_removed = True
            continue  # skip blank separator
        else:
            continue  # skip prompt lines

    TASKS_FILE.write_text("\n".join(result) + "\n", encoding="utf-8")


def ensure_cancel_script():
    """Write a tiny .bat that deletes the sentinel file (one-click cancel)."""
    CANCEL_SCRIPT.write_text(
        f'@del "{PENDING_FILE}" 2>nul\r\n',
        encoding="utf-8",
    )


def wait_for_grace_period():
    """
    Create a sentinel file, wait GRACE_SECONDS, then check if it still exists.
    Returns True if we should proceed (file still exists), False if cancelled.
    """
    PENDING_FILE.write_text("pending", encoding="utf-8")
    logger.info("Grace period: {} seconds to cancel", GRACE_SECONDS)
    time.sleep(GRACE_SECONDS)

    if PENDING_FILE.exists():
        PENDING_FILE.unlink(missing_ok=True)
        return True
    else:
        logger.info("Task cancelled by user")
        return False


def setup_new_project(project_dir: Path):
    """Write CLAUDE.md with automation rules into a newly created project directory."""
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(AUTO_PROJECT_CLAUDE_MD, encoding="utf-8")
        logger.info("Wrote CLAUDE.md to new project: {}", project_dir)


def resolve_task_dir(raw_dir: str) -> Path:
    """
    Resolve a task directory path:
    - Absolute path that already exists → use as-is.
    - Absolute path that doesn't exist → create it (it's a new project).
    - Relative path or bare name (no separators) → create under AUTO_CREATIONS_DIR.
    Writes CLAUDE.md into any directory that had to be created.
    """
    p = Path(raw_dir)
    if not p.is_absolute():
        # Bare name like "FlutterSaaS" or relative path → put it in auto-creations
        p = AUTO_CREATIONS_DIR / raw_dir

    if not p.exists():
        logger.info("Creating new project directory: {}", p)
        p.mkdir(parents=True, exist_ok=True)
        setup_new_project(p)
    return p


def launch_claude(task):
    """
    Run `claude -p` with the task prompt in the task directory.
    Returns (session_id, result_text) or (None, error_msg).
    """
    claude_exe = shutil.which("claude")
    if not claude_exe:
        logger.error("claude CLI not found on PATH")
        return None, "claude CLI not found"

    task_dir = resolve_task_dir(task["dir"])
    if not task_dir.is_dir():
        logger.error("Task directory could not be created: {}", task_dir)
        return None, f"Directory not found or could not be created: {task_dir}"

    cmd = [claude_exe, "-p", task["prompt"], "--output-format", "json",
           "--dangerously-skip-permissions"]
    logger.info("Launching Claude in {}: {}", task["dir"], task["prompt"][:120])

    try:
        r = subprocess.run(
            cmd,
            cwd=str(task_dir),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Claude timed out after 10 minutes")
        return None, "Claude timed out"

    if r.returncode != 0:
        logger.warning("Claude exited with code {}: {}", r.returncode, r.stderr[:300])
        return None, f"Exit code {r.returncode}"

    try:
        data = json.loads(r.stdout)
        session_id = data.get("session_id", "unknown")
        result = data.get("result", r.stdout[:500])
        logger.info("Claude finished. Session: {}", session_id)
        return session_id, result
    except json.JSONDecodeError:
        logger.warning("Could not parse Claude JSON output")
        return None, r.stdout[:500]


# ── Task Scheduler ─────────────────────────────────────────────────────────────

def schedule_next(run_at: datetime):
    """
    Register the Task Scheduler job via XML with two triggers:
      - TimeTrigger: fires at the calculated time (20 min before next block end)
      - LogonTrigger: fires on every login, self-heals after shutdown/long sleep
    StartWhenAvailable=true with a 5-hour window catches wakes from short sleeps.
    """
    trigger_time = run_at.astimezone().strftime("%Y-%m-%dT%H:%M:%S")

    ps = f"""
$action   = New-ScheduledTaskAction -Execute '{UV_EXE}' -Argument 'run "{SCRIPT_PATH}"'
$time     = New-ScheduledTaskTrigger -Once -At '{trigger_time}' -RandomDelay 00:00:00
$time.ExecutionTimeLimit = 'PT5H'
$time.StartWhenAvailable = $true
$logon    = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -MultipleInstances IgnoreNew `
    -DisallowStartIfOnBatteries:$false `
    -StopIfGoingOnBatteries:$false
Register-ScheduledTask `
    -TaskName '{TASK_NAME}' `
    -Action $action `
    -Trigger $time,$logon `
    -Settings $settings `
    -Force | Out-Null
Write-Output "OK"
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True,
    )

    if r.returncode == 0 and "OK" in r.stdout:
        logger.info("Scheduled next run at {} (StartWhenAvailable=true)", trigger_time)
    else:
        logger.error("Failed to schedule task: {}", (r.stderr or r.stdout).strip())


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Clean up any stray 'nul' files — Windows artifact when cancel_task.bat's
    # "2>nul" redirect is executed from a bash context instead of cmd.exe.
    stray_nul = SCRIPT_DIR / "nul"
    if stray_nul.exists() and stray_nul.stat().st_size == 0:
        stray_nul.unlink(missing_ok=True)
        logger.info("Cleaned up stray 'nul' file in script directory")

    data  = fetch_blocks()
    block = find_active_block(data) if data else None
    now   = datetime.now(timezone.utc)

    if block is None:
        logger.info("No active billing block.")
        notify(
            "Claude Code -- No active block",
            "No active billing block found. The notifier will check again on next login.",
        )
        # Still register the task so the logon trigger keeps us alive
        schedule_next(now + timedelta(hours=1))
        return

    end          = block["_end"]
    mins_left    = (end - now).total_seconds() / 60
    pct          = usage_pct(block)
    pct_str      = f"{pct:.0f}%" if pct is not None else "unknown"
    reset_at_str = end.astimezone().strftime("%I:%M %p")

    logger.info("Block ends {} | {:.1f} min remaining | {} used", reset_at_str, mins_left, pct_str)

    # ── Notify / auto-launch ──────────────────────────────────────────────────
    if mins_left <= WARN_MINUTES:
        under_threshold = pct is not None and pct < USAGE_THRESHOLD

        if under_threshold:
            tasks = parse_tasks()
            if tasks:
                task = tasks[0]
                short_prompt = task["prompt"][:80].replace("\n", " ")

                ensure_cancel_script()
                notify(
                    f"Running task in {GRACE_SECONDS}s ({pct_str} used)",
                    f'"{short_prompt}" -- Click Cancel to abort.',
                    actions=[("Cancel task", str(CANCEL_SCRIPT))],
                )

                if not wait_for_grace_period():
                    notify("Task cancelled", "Auto-task was cancelled.")
                else:
                    session_id, result = launch_claude(task)
                    remove_first_task()
                    if session_id:
                        notify(
                            "Claude finished a task",
                            f"Session: {session_id}\nResume: claude --resume {session_id}",
                        )
                    else:
                        notify("Claude task failed", f"Error: {result[:120]}")
            else:
                notify(
                    f"Claude resets in {int(mins_left)} min!",
                    f"Only {pct_str} used but no tasks in tasks.txt.",
                )
        else:
            notify(
                f"Claude resets in {int(mins_left)} min!",
                f"Usage: {pct_str} consumed. Resets at {reset_at_str}.",
            )
    else:
        logger.info("Outside warning window ({:.1f} min left). No notification sent.", mins_left)

    # ── Schedule next run ─────────────────────────────────────────────────────
    next_block_end = end + timedelta(hours=5)
    next_run_at    = next_block_end - timedelta(minutes=WARN_MINUTES)

    if next_run_at <= now:
        next_run_at += timedelta(hours=5)

    schedule_next(next_run_at)


if __name__ == "__main__":
    main()
