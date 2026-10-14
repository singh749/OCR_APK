#!/usr/bin/env python3
"""Publish a readable failure report for a broken Buildozer CI run.

A red GitHub Actions "X" normally tells you nothing: the real error is buried
tens of thousands of lines deep in the Buildozer log. This script

  1. picks out the log lines that actually explain the failure (the last
     error-looking lines, each with a little context, while skipping compiler
     warnings and Buildozer's environment dump),
  2. publishes them where they can be read:
       * a dedicated check run ("Build log excerpt") -- check-run output is
         shown in the UI *and* fully readable through the REST API,
       * one packed `::error::` annotation (a job only keeps ~10 annotations),
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

# Lines that explain a failure. Deliberately broad: recipe errors, compiler
# diagnostics, missing files, p4a's own exceptions.
SIGNAL_RE = re.compile(
    r"(error:|\bERROR\b|\bError\b|\berror\b|fatal:|Traceback \(most recent call last\)"
    r"|Could NOT find|No such file or directory|undefined reference|undeclared identifier"
    r"|No recipe named|Couldn't find|RequirementNotFound|BuildInterruptingException"
    r"|Error running command|Command failed|exit code \d+|cannot execute"
    r"|Permission denied|\*\*\* \[|failed to execute)",
    re.IGNORECASE,
)

# Noise that must not crowd out the real error: compiler warnings and notes, and
# Buildozer's trailing boilerplate (banner + the environment dump it prints
# underneath "Buildozer failed to execute the last command").
NOISE_RE = re.compile(
    r"(warning:|\snote:|to allow an error code|deprecated"
    r"|^\s*\d+\.\s|^remote:|Resolving |^Receiving |^Counting |^Compressing )",
    re.IGNORECASE,
)
BOILERPLATE_RE = re.compile(
    r"(^#\s{2,}\S+ = |^#\s*$|^# (Buildozer failed|The error might|Please read"
    r"|In case of a bug report|Ensure build layout|Android packaging failed"
    r"|Tap .* to share))",
)
# python-for-android logs "Building <recipe> for <arch>" (build.py), which is
# what tells you *which* recipe blew up.
RECIPE_RE = re.compile(
    r"\b(?:Building|Prebuilding|Postbuilding|Unpacking|Downloading) ([\w.\-]+) for \b"
)

# Lines that are worth keeping even when they also look like noise: the real
# diagnostics. Everything matching NOISE_RE/BOILERPLATE_RE and not this is cut.
STRONG_RE = re.compile(
    r"(error:|\[ERROR\]|^\s*ERROR|Traceback \(most recent call last\)"
    r"|undefined reference|undeclared identifier|\*\*\* \[|No such file or directory"
    r"|No recipe named|Couldn't find|Command failed|Error running command"
    r"|BuildInterruptingException|RequirementNotFound|exit code \d+)",
    re.IGNORECASE,
)

MAX_SUMMARY_CHARS = 60000
MAX_ANNOTATION_CHARS = 1000
ANNOTATION_LINES = 6
CONTEXT_BEFORE = 6
CONTEXT_AFTER = 2
SIGNAL_BLOCKS = 10          # how many of the last error clusters to keep
MAX_BLOCK_LINES = 100
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


def is_cut(line):
    """True for compiler warnings / Buildozer boilerplate that hide the real error."""
    if STRONG_RE.search(line):
        return False
    return bool(NOISE_RE.search(line) or BOILERPLATE_RE.search(line))


def signal_indices(lines):
    """Positions of lines that look like the cause of the failure."""
    out = []
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or len(text) > 500:
            continue
        if is_cut(text):
            continue
        if SIGNAL_RE.search(text) or STRONG_RE.search(text):
            out.append(index)
    return out


def excerpt_from(lines):
    """The last few error clusters, oldest first, with a bit of context."""
    signals = signal_indices(lines)
    if not signals:
        return [line for line in lines[-TAIL_FALLBACK_LINES:] if line.strip()], None

    picked = signals[-SIGNAL_BLOCKS:]
    keep = set()
    for index in picked:
        keep.update(range(max(0, index - CONTEXT_BEFORE),
                          min(len(lines), index + 1 + CONTEXT_AFTER)))

    ordered = sorted(keep)
    ranges, start, previous = [], ordered[0], ordered[0]
    for index in ordered[1:]:
        if index - previous > 3:
            ranges.append((start, previous))
            start = index
        previous = index
    ranges.append((start, previous))

    block = []
    for first, last in ranges:
        for i in range(first, last + 1):
            line = lines[i].rstrip()
            if not line.strip():
                continue
            # keep diagnostics; keep context only when it is not
            # compiler-warning noise or Buildozer boilerplate
            if not is_cut(line):
                block.append(line)
    return block[-MAX_BLOCK_LINES:], last_recipe(lines)


def last_recipe(lines):
    recipe = None
    for line in lines:
        match = RECIPE_RE.search(line)
        if match:
            recipe = match.group(1)
    return recipe


def clean(text):
    return " ".join(text.split())


def publish_annotations(block, lines):
    """The decisive lines only -- the runner keeps ~10 annotations per job."""
    candidates = [
        clean(line) for line in lines
        if line.strip() and not is_cut(line)
        and (SIGNAL_RE.search(line) or STRONG_RE.search(line)) and len(line) < 500
    ]
    picked = []
    for text in reversed(candidates):
        if text not in picked:
            picked.append(text)
        if len(picked) >= ANNOTATION_LINES:
            break
    if not picked:
        picked = [clean(line) for line in block[-ANNOTATION_LINES:]]
    packed = " | ".join(picked)[:MAX_ANNOTATION_CHARS]
    if packed:
        safe = packed.replace("%", "%25").replace("\r", "%0D")
        print(f"::error::{safe}")


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


def main():
    logs = log_candidates()
    if not logs:
        print("::error::No build log found (build.log / buildozer.log / .buildozer/**/*.log)")
        publish_pr_comment(
            "The build died before Buildozer produced a log. Open the run page and "
            "read the `Build debug APK with Buildozer` step directly."
        )
        return 0

    block, chosen, recipe, signals_lines = [], logs[0], None, []
    for log in logs[:3]:
        try:
            raw = read_lines(log)
            block, recipe = excerpt_from(raw)
            signals_lines = raw
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Could not read {log}: {exc}")
            continue
        if block:
            chosen = log
            break

    if not block:
        block = [f"(no readable content in {chosen})"]

    where = f" while building recipe `{recipe}`" if recipe else ""
    title = f"Buildozer failed{where} - excerpt from {chosen} ({os.path.getsize(chosen)} bytes)"
    fenced = "```\n" + "\n".join(block) + "\n```"
    summary = (
        f"## {title}\n\n{fenced}\n\n"
        "_Complete output: Actions → the failing run → "
        "`Build debug APK with Buildozer`, or the `build-log` artifact._"
    )

    print(f"=== excerpt from {chosen} ({len(block)} lines) ===")
    print("\n".join(block))
    print("=== end of excerpt ===")

    publish_annotations(block, signals_lines or block)

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")

    for line in (f"check run: {publish_check_run(title, summary)}",
                 f"PR comment: {publish_pr_comment(summary)}"):
        print(line)
        print(f"::notice::{clean(line)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::ci_failure_report.py crashed: {exc}")
        sys.exit(0)
