#!/usr/bin/env python3
"""Publish a readable failure report for a broken Buildozer CI run.

A red GitHub Actions "X" normally tells you nothing: the real error is buried
tens of thousands of lines deep in buildozer's log, and the raw log endpoint is
not always reachable for everybody. This script therefore

  1. finds the interesting lines in the build logs,
  2. publishes them as workflow annotations (shown inline on the run page and
     readable through the checks API),
  3. writes them to the job step summary,
  4. posts them as a comment on the open pull request (if there is one).

It must never break the build itself, so every path exits 0.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

MAX_ANNOTATIONS = 12
ANNOTATION_LEN = 800
MAX_EXCERPT_CHARS = 18000
CONTEXT_LINES = 3          # lines of context kept before an error line
CONTEXT_AFTER = 1          # lines of context kept after an error line
GAP_TOLERANCE = 4          # nearby hits are merged into one readable block

ERROR_PATTERNS = re.compile(
    r"(?:\berror\b|\bERROR\b|Error:|fatal:|Traceback|Critical error|"
    r"Buildozer failed|Error running command|Could NOT find|No such file or directory|"
    r"not found|No recipe named|Couldn't find|undefined reference|undeclared identifier|"
    r"command failed|exit code|cannot execute|Permission denied|"
    r"ERROR: please|RequirementNotFound|BuildInterruptingException)",
    re.IGNORECASE,
)

IGNORE_PATTERNS = re.compile(
    r"(?:^\s*(?:[0-9]+\s+)?(?:warning|note):|deprecated|-Wno-error|"
    r"Downloading|Progress \[|Receiving objects|^remote:)",
    re.IGNORECASE,
)


def candidate_logs():
    """Every log file that could contain the error, biggest (most complete) first."""
    found, seen = [], set()

    def add(path):
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0 and path not in seen:
                seen.add(path)
                found.append(path)
        except OSError:
            pass

    add("build.log")  # what the `| tee build.log` in the build step captured
    add("buildozer.log")
    add(os.path.expanduser("~/.buildozer/log/buildozer.log"))
    for root, _dirs, files in os.walk(".buildozer"):
        for name in files:
            if name.endswith(".log"):
                add(os.path.join(root, name))
    add(os.path.expanduser("~/.buildozer/android/platform/build-arm64-v8a/build.log"))
    found.sort(key=lambda p: -os.path.getsize(p))
    return found


def read_lines(path, limit_bytes=40 * 1024 * 1024):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit_bytes))
        chunk = handle.read().decode("utf-8", "replace")
    return chunk.splitlines()


def excerpt_from(lines):
    """Error-looking lines plus their context, merged into contiguous blocks.

    Blocks are collected from the end of the log backwards (the tail holds the
    command that actually failed) until the character budget is used up.
    """
    keep = set()
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or len(text) > 1000:
            continue
        if not ERROR_PATTERNS.search(text) or IGNORE_PATTERNS.search(text):
            continue
        keep.update(
            range(
                max(0, index - CONTEXT_LINES),
                min(len(lines), index + 1 + CONTEXT_AFTER),
            )
        )
    if not keep:
        return []

    ordered = sorted(keep)
    ranges, start, previous = [], ordered[0], ordered[0]
    for index in ordered[1:]:
        if index - previous > GAP_TOLERANCE:
            ranges.append((start, previous))
            start = index
        previous = index
    ranges.append((start, previous))

    blocks, total = [], 0
    for first, last in reversed(ranges):
        block = [lines[i].rstrip() for i in range(first, last + 1)]
        blocks.append(block)
        total += sum(len(line) + 1 for line in block)
        if total > MAX_EXCERPT_CHARS:
            break
    blocks.reverse()
    return [line for block in blocks for line in block]


def error_lines(block):
    out, seen = [], set()
    for line in block:
        text = " ".join(line.split())
        if not text or text in seen:
            continue
        if ERROR_PATTERNS.search(text) and not IGNORE_PATTERNS.search(text):
            seen.add(text)
            out.append(text[:ANNOTATION_LEN])
    return out or [block[-1].strip()[:ANNOTATION_LEN]]


def emit_annotations(block):
    """`::error::` lines -> GitHub annotations on the run page (and via the API)."""
    for line in error_lines(block)[:MAX_ANNOTATIONS]:
        safe = line.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{safe}")


def write_step_summary(log_path, block, extra):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    body = [
        "## Build log excerpt",
        "",
        f"Source: `{log_path}` ({extra})",
        "",
        "```",
        *block[-400:],
        "```",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(body) + "\n")


def post_pr_comment(text):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    pr = os.environ.get("PR_NUMBER", "")
    if not (token and repo and pr):
        return "skipped (no GITHUB_TOKEN/GITHUB_REPOSITORY/PR_NUMBER)"
    url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
    request = urllib.request.Request(
        url,
        data=json.dumps({"body": text}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ci-failure-report",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"posted ({response.status})"
    except urllib.error.HTTPError as exc:
        return f"failed ({exc.code}: {exc.read()[:200].decode('utf-8', 'replace')})"
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break CI
        return f"failed ({exc})"


def main():
    logs = candidate_logs()
    if not logs:
        print("::error::No build log found (.buildozer/**, build.log, buildozer.log)")
        print("::error::Build log missing: nothing to excerpt")
        post_pr_comment(
            "### Build failed with no readable log\n\n"
            "No `build.log` / `buildozer.log` was produced, so the build probably "
            "died before Buildozer started. Open the run page and read the "
            "`Build debug APK with Buildozer` step directly."
        )
        return 0

    tail = []
    chosen = logs[0]
    for log in logs[:3]:
        try:
            tail = excerpt_from(read_lines(log))
        except Exception as exc:  # noqa: BLE001
            print(f"::error::Could not read {log}: {exc}")
            continue
        if tail:
            chosen = log
            break

    print(f"=== excerpt from {chosen} ({os.path.getsize(chosen)} bytes) ===")
    for line in tail:
        print(line)
    print(f"=== end of excerpt ({len(tail)} lines) ===")
    if not tail:
        marker = f"::error::Log {chosen} had no recognizable error lines; see the step output"
        print(marker)
        tail = [f"(no error pattern matched in {chosen})"]

    emit_annotations(tail)
    write_step_summary(chosen, tail, f"{os.path.getsize(chosen)} bytes")

    fenced = "\n".join(tail[-300:])
    note = post_pr_comment(
        "**Build Android APK failed.** Excerpt from `{}`:\n\n```\n{}\n```"
        "\n\n_Full output: Actions → the failing run → `Build debug APK with "
        "Buildozer`._".format(chosen, fenced[:MAX_EXCERPT_CHARS])
    )
    print(f"PR comment: {note}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::ci_failure_report.py crashed: {exc}")
        sys.exit(0)
