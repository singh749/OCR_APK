#!/usr/bin/env python3
"""Publish a readable failure report for a broken Buildozer CI run.

A red GitHub Actions "X" normally tells you nothing: the real error is buried
tens of thousands of lines deep in the Buildozer log. This script

  1. finds the lines that actually explain the failure (the log region around
     Buildozer's own "the last command failed" marker, falling back to the
     tail of the log),
  2. publishes them where they can be read:
       * a dedicated check run ("Build log excerpt") whose output.summary is
         plain API-readable text -- check runs are the only channel that both
         humans and the REST API expose in full,
       * `::error::` annotations (a runner only keeps ~10 per job, so this is
         deliberately limited to the most decisive lines),
       * the job step summary,
       * a comment on the open pull request, if there is one.

It must never break the build itself, so every path exits 0.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Buildozer prints its own banner when a recipe's build command dies; the lines
# right after it are the failing command and its output.
FAILURE_MARKERS = (
    "Buildozer failed to execute the last command",
    "Error running command",
    "ERROR: Should have a quantum",
    "Build Interrupting Exception",
    "BuildInterruptingException",
    "ERROR: Please",
    "No recipe named",
    "Couldn't find the",
    "ERROR: ",
)

ERROR_RE = re.compile(
    r"(\berror\b|Error:|fatal:|Traceback|Critical error|Could NOT find|"
    r"No such file or directory|undefined reference|undeclared identifier|"
    r"command failed|exit code|cannot execute|Permission denied|"
    r"RequirementNotFound|BuildInterruptingException)",
    re.IGNORECASE,
)

# Compiler warnings are noise here: they flood the log and they also steal the
# runner's ~10 annotation slots (they contain the word "error").
NOISE_RE = re.compile(
    r"(warning:|\bnote:|to allow an error code|^\s*\d+\.\s|deprecated|"
    r"^remote:|Resolving |^Receiving |^Counting |^Compressing |Unpacking objects)",
    re.IGNORECASE,
)

MAX_SUMMARY_CHARS = 60000
MAX_ANNOTATION_CHARS = 1000
ANNOTATION_LINES = 6
TAIL_FALLBACK_LINES = 60


def log_candidates():
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
    found.sort(key=lambda p: -os.path.getsize(p))
    return found


def read_lines(path, limit_bytes=48 * 1024 * 1024):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit_bytes))
        chunk = handle.read().decode("utf-8", "replace")
    return [ANSI_RE.sub("", line).rstrip() for line in chunk.splitlines()]


def clean(text):
    return " ".join(text.split())


def is_interesting(line):
    text = line.strip()
    if not text or len(text) > 800 or NOISE_RE.search(text):
        return False
    return bool(ERROR_RE.search(text))


def excerpt_from(lines):
    """The block of log that explains the failure.

    Priority: the region after Buildozer's own failure banner -> the last
    error-looking lines -> the plain tail of the log.
    """
    anchor = None
    for index in range(len(lines) - 1, -1, -1):
        if any(marker in lines[index] for marker in FAILURE_MARKERS):
            anchor = index
            break

    if anchor is not None:
        start = max(0, anchor - 45)
        block = [line for line in lines[start:anchor + 40] if line.strip()]
        block = [line for line in block if not NOISE_RE.search(line)]
        if block:
            return block[-90:]

    hits = [i for i, line in enumerate(lines) if is_interesting(line)]
    if hits:
        keep, ranges, start, prev = set(), [], hits[0], hits[0]
        keep.update(range(max(0, hits[0] - 3), min(len(lines), hits[0] + 1)))
        for index in hits[1:]:
            if index - prev > 4:
                ranges.append((start, prev))
                start = index
            keep.update(range(max(0, index - 3), min(len(lines), index + 1)))
            prev = index
        ranges.append((start, prev))
        blocks, total = [], 0
        for first, last in reversed(ranges):
            block = [lines[i] for i in range(first, last + 1) if lines[i].strip()]
            blocks.append(block)
            total += sum(len(line) + 1 for line in block)
            if total > MAX_SUMMARY_CHARS // 3:
                break
        blocks.reverse()
        flat = [line for block in blocks for line in block]
        if flat:
            return flat[-90:]

    return [line for line in lines[-TAIL_FALLBACK_LINES:] if line.strip()]


def api(path, payload, token):
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "buildozer-ci-failure-report",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"ok ({response.status})"
    except urllib.error.HTTPError as exc:
        return f"failed ({exc.code}: {exc.read()[:160].decode('utf-8', 'replace')})"
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break CI
        return f"failed ({exc})"


def publish_check_run(title, summary):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("HEAD_SHA") or os.environ.get("GITHUB_SHA")
    if not (token and repo and sha):
        return "skipped (missing GITHUB_TOKEN/GITHUB_REPOSITORY/HEAD_SHA)"
    return api(
        f"/repos/{repo}/check-runs",
        {
            "name": "Build log excerpt",
            "head_sha": sha,
            "status": "completed",
            "conclusion": "failure",
            "output": {
                "title": title[:255],
                "summary": summary[:MAX_SUMMARY_CHARS],
                "text": summary[:MAX_SUMMARY_CHARS],
            },
        },
        token,
    )


def publish_pr_comment(summary):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    pr = os.environ.get("PR_NUMBER", "")
    if not (token and repo and pr):
        return "skipped (not a pull_request event, or no token)"
    return api(
        f"/repos/{repo}/issues/{pr}/comments",
        {"body": f"### Build Android APK failed\n\n{summary[:MAX_SUMMARY_CHARS]}"},
        token,
    )


def publish_annotations(block):
    """The decisive lines only -- the runner keeps ~10 annotations per job."""
    picked = []
    for line in reversed(block):
        text = clean(line)
        if text and text not in picked:
            picked.append(text)
        if len(picked) >= ANNOTATION_LINES:
            break
    packed = " | ".join(picked)[:MAX_ANNOTATION_CHARS]
    if packed:
        safe = packed.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{safe}")


def main():
    logs = log_candidates()
    if not logs:
        print("::error::No build log found (build.log / buildozer.log / .buildozer/**/*.log)")
        publish_pr_comment(
            "The build died before Buildozer produced a log. Open the run page and "
            "read the `Build debug APK with Buildozer` step directly."
        )
        return 0

    block, chosen = [], logs[0]
    for log in logs[:3]:
        try:
            block = excerpt_from(read_lines(log))
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Could not read {log}: {exc}")
            continue
        if block:
            chosen = log
            break

    if not block:
        block = [f"(no readable content in {chosen})"]

    title = f"Buildozer failed - excerpt from {chosen} ({os.path.getsize(chosen)} bytes)"
    fenced = "```\n" + "\n".join(block) + "\n```"
    summary = (
        f"## {title}\n\n{fenced}\n\n"
        "_Complete output: Actions → the failing run → "
        "`Build debug APK with Buildozer`, or the `build-log` artifact._"
    )

    print(f"=== excerpt from {chosen} ===")
    print("\n".join(block))
    print("=== end of excerpt ===")

    publish_annotations(block)

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")

    results = [
        f"check run: {publish_check_run(title, summary)}",
        f"PR comment: {publish_pr_comment(summary)}",
    ]
    for line in results:
        print(line)
        print(f"::notice::{clean(line)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::ci_failure_report.py crashed: {exc}")
        sys.exit(0)
