"""
Error Log Analyzer — scheduled by /loop.

Pipeline:
  1. Read new log entries since last run (using a cursor file).
  2. Group errors by error_id; count occurrences and collect examples.
  3. For each unique actionable error, read the relevant source file.
  4. Ask Claude to produce a minimal code fix + PR description.
  5. Create a branch + commit the fix + open a GitHub PR (via gh CLI).
  6. Advance the cursor.

Usage:
    uv run python analyze.py \
        --log ../logs/app.log \
        --repo-dir ../mock-app \
        --gh-repo ShayanShamsi/error-log-monitor-demo
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CURSOR_FILE = Path(__file__).parent / ".log_cursor"
STATE_FILE  = Path(__file__).parent / ".opened_prs.json"   # avoid duplicate PRs

MIN_COUNT_FOR_PR = 3       # only create PR if error occurred >= N times
MAX_ERRORS_PER_RUN = 5     # limit PRs opened per run to avoid spamming


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def read_new_entries(log_path: Path) -> list[dict]:
    """Return log entries written after the last cursor position."""
    cursor = int(CURSOR_FILE.read_text()) if CURSOR_FILE.exists() else 0
    entries = []
    with log_path.open() as f:
        f.seek(cursor)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        new_cursor = f.tell()
    CURSOR_FILE.write_text(str(new_cursor))
    return entries


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_errors(entries: list[dict]) -> dict[str, dict]:
    """
    Group entries by error_id. Returns:
      { error_id: { count, level, examples: [entry, ...], file, line } }
    Ignores INFO-level entries without a traceback.
    """
    groups: dict[str, dict] = {}
    for e in entries:
        eid = e.get("error_id", "UNKNOWN")
        level = e.get("level", "INFO")
        # Skip pure info noise
        if level == "INFO" and not e.get("traceback"):
            continue
        if eid not in groups:
            groups[eid] = {
                "error_id": eid,
                "level": level,
                "count": 0,
                "examples": [],
                "file": e.get("file"),
                "line": e.get("line"),
                "traceback": e.get("traceback"),
            }
        groups[eid]["count"] += 1
        if len(groups[eid]["examples"]) < 3:
            groups[eid]["examples"].append(e)
    return groups


# ---------------------------------------------------------------------------
# Source reading
# ---------------------------------------------------------------------------

def read_source(repo_dir: Path, rel_path: str) -> str | None:
    """Return source file contents, or None if not found."""
    full = repo_dir / rel_path
    if full.exists():
        return full.read_text()
    return None


# ---------------------------------------------------------------------------
# Test-mode canned analysis (bypasses Claude for pipeline smoke-testing)
# ---------------------------------------------------------------------------

_CANNED: dict[str, dict] = {
    "ERR_INDEX_PRICE_RANGE": {
        "actionable": True,
        "pr_title": "fix: guard parse_price_range against missing hyphen separator",
        "pr_body": (
            "## Problem\n`parse_price_range` crashes with `IndexError` when the caller "
            "omits the `-` separator (e.g. `?price_range=50`).\n\n"
            "## Root cause\n`parts = range_str.split('-')` produces a 1-element list; "
            "`parts[1]` raises `IndexError`.\n\n"
            "## Fix\nValidate that `parts` has exactly 2 elements before indexing, "
            "and raise a descriptive `ValueError` otherwise.\n\n"
            "## Test suggestions\n- `parse_price_range('10.00-99.99')` → `(10.0, 99.99)`\n"
            "- `parse_price_range('50')` → raises `ValueError`\n"
            "- `parse_price_range('')` → raises `ValueError`\n"
        ),
        "fixed_source": None,  # filled in by _canned_analysis
        "fix_description": "Add length check before indexing parts to prevent IndexError.",
    },
    "ERR_ZERO_DIV_DISCOUNT": {
        "actionable": True,
        "pr_title": "fix: raise ValueError when discount_pct==100 in calculate_discount",
        "pr_body": (
            "## Problem\n`calculate_discount(price, 100)` raises `ZeroDivisionError` "
            "because `100 / (100 - 100)` → division by zero.\n\n"
            "## Root cause\nNo guard against `discount_pct >= 100`.\n\n"
            "## Fix\nAdd an explicit check at the top of the function and raise "
            "`ValueError` with a clear message.\n\n"
            "## Test suggestions\n- `calculate_discount(100, 50)` → `50.0`\n"
            "- `calculate_discount(100, 100)` → raises `ValueError`\n"
            "- `calculate_discount(100, 0)` → `100.0`\n"
        ),
        "fixed_source": None,
        "fix_description": "Guard against discount_pct == 100 to avoid ZeroDivisionError.",
    },
    "ERR_NONE_USER_BALANCE": {
        "actionable": True,
        "pr_title": "fix: raise NotFoundError when user is None in update_user_balance",
        "pr_body": (
            "## Problem\n`update_user_balance` silently receives `None` from `get_user` "
            "for unknown user IDs, then crashes with `TypeError`.\n\n"
            "## Root cause\n`_USERS.get(user_id)` returns `None`; no guard before "
            "`user['balance'] += delta`.\n\n"
            "## Fix\nCheck for `None` after `get_user` and raise `KeyError` with the "
            "unknown `user_id`.\n\n"
            "## Test suggestions\n- `update_user_balance('u001', 10)` → no error\n"
            "- `update_user_balance('unknown', 10)` → raises `KeyError`\n"
        ),
        "fixed_source": None,
        "fix_description": "Check for None return value before mutating user balance.",
    },
    "ERR_RUNTIME_CACHE_INVALIDATE": {
        "actionable": True,
        "pr_title": "fix: iterate over list copy in invalidate_user to avoid RuntimeError",
        "pr_body": (
            "## Problem\n`invalidate_user` raises `RuntimeError: dictionary changed size "
            "during iteration` because it deletes keys while iterating `_cache`.\n\n"
            "## Root cause\nPython does not allow mutating a dict during iteration.\n\n"
            "## Fix\nCollect matching keys first (`list(_cache.keys())`), then delete "
            "them in a second pass.\n\n"
            "## Test suggestions\n- Populate cache with several `user:X:*` keys, call "
            "`invalidate_user('X')`, verify count and that keys are gone.\n"
        ),
        "fixed_source": None,
        "fix_description": "Collect keys to delete in a list first, then delete in a second pass.",
    },
    "ERR_SPLIT_ZERO_INSTALLMENTS": {
        "actionable": True,
        "pr_title": "fix: validate num_installments > 0 in split_payment",
        "pr_body": (
            "## Problem\n`split_payment(total, 0)` raises `ZeroDivisionError`.\n\n"
            "## Root cause\nNo validation that `num_installments` is positive before "
            "dividing.\n\n"
            "## Fix\nRaise `ValueError` at function entry when `num_installments <= 0`.\n\n"
            "## Test suggestions\n- `split_payment(100, 4)` → `[25.0, 25.0, 25.0, 25.0]`\n"
            "- `split_payment(100, 0)` → raises `ValueError`\n"
            "- `split_payment(100, -1)` → raises `ValueError`\n"
        ),
        "fixed_source": None,
        "fix_description": "Raise ValueError when num_installments <= 0.",
    },
}

# Minimal source patches applied in test mode
_SOURCE_PATCHES: dict[str, tuple[str, str]] = {
    "ERR_INDEX_PRICE_RANGE": (
        "    parts = range_str.split(\"-\")\n    # BUG: IndexError if range_str has no '-' (e.g. user passes just '50')\n    return float(parts[0]), float(parts[1])",
        "    parts = range_str.split(\"-\")\n    if len(parts) != 2:\n        raise ValueError(f\"Invalid price_range format: {range_str!r}. Expected 'min-max'.\")\n    return float(parts[0]), float(parts[1])",
    ),
    "ERR_ZERO_DIV_DISCOUNT": (
        "    # BUG: No guard against discount_pct == 100, causes ZeroDivisionError\n    # when someone applies a 100% coupon code\n    multiplier = 100 / (100 - discount_pct)",
        "    if discount_pct >= 100:\n        raise ValueError(f\"discount_pct must be < 100, got {discount_pct}\")\n    multiplier = 100 / (100 - discount_pct)",
    ),
    "ERR_NONE_USER_BALANCE": (
        "    user = _USERS.get(user_id)\n    # BUG: user could be None here, causing TypeError\n    user[\"balance\"] += delta",
        "    user = _USERS.get(user_id)\n    if user is None:\n        raise KeyError(f\"User not found: {user_id}\")\n    user[\"balance\"] += delta",
    ),
    "ERR_RUNTIME_CACHE_INVALIDATE": (
        "    # BUG: mutating dict while iterating — RuntimeError in Python 3\n    count = 0\n    for key in _cache:\n        if key.startswith(f\"user:{user_id}:\"):\n            del _cache[key]\n            count += 1",
        "    keys_to_delete = [k for k in _cache if k.startswith(f\"user:{user_id}:\")]\n    for key in keys_to_delete:\n        del _cache[key]\n    count = len(keys_to_delete)",
    ),
    "ERR_SPLIT_ZERO_INSTALLMENTS": (
        "    # BUG: ZeroDivisionError when num_installments=0 (allowed by frontend)\n    per_installment = total / num_installments",
        "    if num_installments <= 0:\n        raise ValueError(f\"num_installments must be > 0, got {num_installments}\")\n    per_installment = total / num_installments",
    ),
}


def _canned_analysis(group: dict, source: str | None) -> dict:
    eid = group["error_id"]
    base = _CANNED.get(eid)
    if base is None:
        return {"actionable": False, "reason": "no canned analysis for this error_id"}

    result = dict(base)

    # Apply patch to source
    if source and eid in _SOURCE_PATCHES:
        old, new = _SOURCE_PATCHES[eid]
        if old in source:
            result["fixed_source"] = source.replace(old, new, 1)
        else:
            result["fixed_source"] = source  # unchanged fallback

    return result


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_with_claude(group: dict, source: str | None) -> dict | None:
    """
    Ask Claude to produce:
      - a one-line PR title
      - a PR body (markdown)
      - the fixed source file contents (if source was provided)
      - the file path that was fixed
    Returns a dict or None if not actionable.
    Uses the `claude` CLI (already authenticated via Claude Code).
    """
    source_section = (
        f"\n\nSOURCE FILE ({group['file']}):\n```python\n{source}\n```"
        if source else ""
    )

    examples_str = "\n".join(
        f"  [{i+1}] {ex['message']}"
        for i, ex in enumerate(group["examples"])
    )

    traceback_section = (
        f"\n\nTRACEBACK:\n```\n{group['traceback']}\n```"
        if group.get("traceback") else ""
    )

    prompt = f"""You are a senior engineer triaging production errors to create targeted pull requests.

ERROR SUMMARY
=============
error_id  : {group['error_id']}
level     : {group['level']}
occurrences (this window): {group['count']}
file      : {group.get('file', 'unknown')}
line      : {group.get('line', 'unknown')}

EXAMPLE LOG MESSAGES:
{examples_str}
{traceback_section}
{source_section}

TASK
====
1. Determine if this error is actionable (i.e. a code bug that can be fixed).
   - If NOT actionable (e.g. user behaviour, external service, already noise) respond with:
     {{"actionable": false, "reason": "..."}}
   - If actionable, respond with a JSON object:
     {{
       "actionable": true,
       "pr_title": "<concise title, max 72 chars>",
       "pr_body": "<full PR description in GitHub markdown — include: problem, root cause, fix summary, test suggestions>",
       "fixed_source": "<complete corrected file content as a string, or null if no source was provided>",
       "fix_description": "<one sentence describing the change>"
     }}

Respond ONLY with the JSON object, no other text.
"""

    # Use the claude CLI (authenticated via Claude Code) to avoid needing ANTHROPIC_API_KEY
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text", prompt],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"[WARN] claude CLI failed for {group['error_id']}: {result.stderr[:200]}", file=sys.stderr)
        return None

    text = result.stdout.strip()
    # Strip potential markdown code fence
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"[WARN] Claude returned non-JSON for {group['error_id']}: {text[:200]}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# GitHub PR creation
# ---------------------------------------------------------------------------

def load_opened_prs() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_opened_prs(opened: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(opened)))


def create_pr(
    repo_dir: Path,
    gh_repo: str,
    error_id: str,
    pr_title: str,
    pr_body: str,
    file_path: str,
    fixed_source: str,
) -> str | None:
    """
    1. Create a branch fix/<error_id_lower>.
    2. Write the fixed source.
    3. Commit + push.
    4. Open PR via gh CLI.
    Returns the PR URL or None on failure.
    """
    branch = f"fix/{error_id.lower().replace('_', '-')}"

    # Ensure we're on main / have a clean base
    run = lambda cmd, **kw: subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, **kw)

    # Make sure remote is set
    remotes = run(["git", "remote"]).stdout.strip()
    if not remotes:
        run(["git", "remote", "add", "origin", f"https://github.com/{gh_repo}.git"])

    # Checkout or reset branch
    run(["git", "fetch", "origin", "main", "--quiet"])
    checkout = run(["git", "checkout", "-B", branch, "origin/main"])
    if checkout.returncode != 0:
        # If origin/main doesn't exist yet, just branch from HEAD
        run(["git", "checkout", "-B", branch])

    # Write fix
    full_path = repo_dir / file_path
    full_path.write_text(fixed_source)

    # Commit
    run(["git", "add", str(full_path)])
    commit = run(["git", "commit", "-m", f"fix: {pr_title}"])
    if commit.returncode != 0:
        print(f"[WARN] Nothing to commit for {error_id}", file=sys.stderr)
        return None

    # Push
    push = run(["git", "push", "-u", "origin", branch, "--force"])
    if push.returncode != 0:
        print(f"[ERROR] Push failed for {branch}:\n{push.stderr}", file=sys.stderr)
        return None

    # Open PR via gh
    pr_result = subprocess.run(
        ["gh", "pr", "create",
         "--repo", gh_repo,
         "--head", branch,
         "--base", "main",
         "--title", pr_title,
         "--body", pr_body],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if pr_result.returncode != 0:
        # PR may already exist
        if "already exists" in pr_result.stderr:
            print(f"[INFO] PR already exists for {branch}", file=sys.stderr)
            # Return existing PR URL
            url_result = subprocess.run(
                ["gh", "pr", "view", "--repo", gh_repo, branch, "--json", "url", "--jq", ".url"],
                capture_output=True, text=True,
            )
            return url_result.stdout.strip() or None
        print(f"[ERROR] gh pr create failed:\n{pr_result.stderr}", file=sys.stderr)
        return None

    return pr_result.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze error logs and open PRs")
    parser.add_argument("--log",      default="../logs/app.log",         help="Path to JSONL log file")
    parser.add_argument("--repo-dir", default="../mock-app",             help="Path to mock-app git repo")
    parser.add_argument("--gh-repo",  required=True,                     help="GitHub repo slug (owner/name)")
    parser.add_argument("--dry-run",  action="store_true",               help="Analyze but don't open PRs")
    parser.add_argument("--test-mode", action="store_true",              help="Skip Claude call; use canned analysis to test PR pipeline")
    parser.add_argument("--reset-cursor", action="store_true",           help="Re-read entire log from start")
    args = parser.parse_args()

    log_path  = Path(args.log).resolve()
    repo_dir  = Path(args.repo_dir).resolve()

    if args.reset_cursor and CURSOR_FILE.exists():
        CURSOR_FILE.unlink()
        print("[INFO] Cursor reset — will re-read entire log", file=sys.stderr)

    if not log_path.exists():
        print(f"[ERROR] Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    # ── 1. Read new entries ──────────────────────────────────────────────
    entries = read_new_entries(log_path)
    print(f"[INFO] Read {len(entries)} new log entries", file=sys.stderr)
    if not entries:
        print("[INFO] Nothing new. Exiting.", file=sys.stderr)
        return

    # ── 2. Group errors ──────────────────────────────────────────────────
    groups = group_errors(entries)
    actionable_groups = [
        g for g in groups.values()
        if g["level"] in ("ERROR", "CRITICAL") and g["count"] >= MIN_COUNT_FOR_PR and g.get("file")
    ]
    print(f"[INFO] {len(groups)} unique error types; {len(actionable_groups)} meet threshold (>={MIN_COUNT_FOR_PR} occurrences, have source file)", file=sys.stderr)

    if not actionable_groups:
        print("[INFO] No actionable errors this window.", file=sys.stderr)
        return

    # ── 3. Initialize state ──────────────────────────────────────────────
    opened_prs = load_opened_prs()
    prs_this_run = 0

    report_lines = [
        f"# Error Log Monitor Report",
        f"**Run at:** {datetime.now(timezone.utc).isoformat()}",
        f"**Log entries scanned:** {len(entries)}",
        f"**Unique error types:** {len(groups)}",
        f"**Actionable:** {len(actionable_groups)}",
        "",
    ]

    for group in sorted(actionable_groups, key=lambda g: -g["count"]):
        if prs_this_run >= MAX_ERRORS_PER_RUN:
            print(f"[INFO] Reached PR limit ({MAX_ERRORS_PER_RUN}/run). Stopping.", file=sys.stderr)
            break

        eid = group["error_id"]
        print(f"\n[INFO] Analyzing {eid} ({group['count']} occurrences) ...", file=sys.stderr)

        # Skip if we already opened a PR for this error
        if eid in opened_prs:
            print(f"[INFO]   Already opened PR for {eid}, skipping.", file=sys.stderr)
            report_lines.append(f"- **{eid}** ({group['count']}x) — PR already open, skipped.")
            continue

        # ── 4. Read source ───────────────────────────────────────────────
        source = read_source(repo_dir, group["file"])

        # ── 5. Claude analysis ───────────────────────────────────────────
        if args.test_mode:
            analysis = _canned_analysis(group, source)
        else:
            analysis = analyze_with_claude(group, source)
        if analysis is None:
            report_lines.append(f"- **{eid}** ({group['count']}x) — Claude analysis failed.")
            continue

        if not analysis.get("actionable"):
            reason = analysis.get("reason", "no reason given")
            print(f"[INFO]   Not actionable: {reason}", file=sys.stderr)
            report_lines.append(f"- **{eid}** ({group['count']}x) — Not actionable: {reason}")
            continue

        pr_title   = analysis["pr_title"]
        pr_body    = analysis["pr_body"]
        fixed_src  = analysis.get("fixed_source")
        fix_desc   = analysis.get("fix_description", "")

        print(f"[INFO]   Actionable! PR title: {pr_title}", file=sys.stderr)
        print(f"[INFO]   Fix: {fix_desc}", file=sys.stderr)

        if args.dry_run or not fixed_src:
            print(f"[DRY-RUN] Would open PR: {pr_title}", file=sys.stderr)
            report_lines.append(f"- **{eid}** ({group['count']}x) — [dry-run] Would open PR: _{pr_title}_")
            continue

        # ── 6. Open PR ───────────────────────────────────────────────────
        pr_url = create_pr(
            repo_dir=repo_dir,
            gh_repo=args.gh_repo,
            error_id=eid,
            pr_title=pr_title,
            pr_body=pr_body,
            file_path=group["file"],
            fixed_source=fixed_src,
        )

        if pr_url:
            print(f"[INFO]   PR opened: {pr_url}", file=sys.stderr)
            opened_prs.add(eid)
            save_opened_prs(opened_prs)
            prs_this_run += 1
            report_lines.append(f"- **{eid}** ({group['count']}x) — PR opened: [{pr_title}]({pr_url})")
        else:
            report_lines.append(f"- **{eid}** ({group['count']}x) — Failed to open PR.")

    # ── Print report ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("\n".join(report_lines))
    print("=" * 60)


if __name__ == "__main__":
    main()
