#!/usr/bin/env bash
# Boot the freshly built APK on an emulator and prove the app really starts.
#
# Everything else in this pipeline can only say "an APK was produced". A Python
# exception during import, a .kv file that calls a handler that does not exist, a
# Java bridge that fails on the device, or a UI laid out into a 400x750 box all
# build perfectly and fail on the phone. This step is the phone.
#
# main.py writes greppable one-line records to logcat under the `python` tag
# (`OCRDIAG startup`, `OCRDIAG layout window=1080x2400 ...`, `OCRDIAG fatal ...`),
# which is what makes those failure modes assertable instead of eyeballed.
#
# Evidence rule: an artifact cannot be read from outside the runner, but a job
# annotation can (`/check-runs/<job id>/annotations`), so every verdict prints the
# log lines that justify it into the `::error::`/`::notice::` message itself.
#
# Usage: emulator_boot_test.sh <apk-path> [package-name]
set -uo pipefail

APK="${1:?usage: emulator_boot_test.sh <apk> [package]}"
PKG="${2:-org.metalocr.ocrscanner}"
LOGCAT=emulator-logcat.txt
DIAG=ui-report.txt
: > "$LOGCAT"
: > "$DIAG"
: > boot-problems.txt

say() { echo "$*"; echo "* $*" >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true; }

# A cold emulator is loud: telephony and Bluetooth die, SurfaceFlinger complains,
# `ANR in com.android.phone` turns up while nothing of ours has gone wrong. The
# first version of this test blamed the app for exactly that noise, so every
# fatal marker below has to name this package (or the app's own pid, once known)
# before it can fail the build - an unscoped grep is a false alarm factory.
fatal_marker() {
  local log="$1" pid="$2" out=""
  # logcat writes `ANR in <package>, PID: 1234`, so the package name has to be
# followed by a comma or whitespace - matching only a space misses every ANR.
out="$(grep -a -E "ANR in ${PKG}([[:space:]]|,|\$)" "$log" 2>/dev/null | head -1)"
  if [ -z "$out" ] && grep -a -q "FATAL EXCEPTION" "$log" 2>/dev/null \
     && grep -a -q "Process: ${PKG}" "$log" 2>/dev/null; then
    out="$(grep -a -A2 "FATAL EXCEPTION" "$log" | grep -a -E "Process:|Exception|Error" | head -2 | tr '\n' ' ')"
  fi
  if [ -z "$out" ]; then
    if [ -n "$pid" ]; then
      out="$(grep -a -E "Fatal signal|SIGSEGV|SIGABRT" "$log" 2>/dev/null | grep -a -E "${PKG}|pid: ${pid}" | head -1)"
    fi
  fi
  [ -n "$out" ] || out="$(grep -a -E "Force finishing activity ${PKG}" "$log" 2>/dev/null | head -1)"
  [ -n "$out" ] || return 1
  printf '%s' "$out" | cut -c1-200
}

# The excerpt of device log that explains the verdict, capped so the annotation is
# not truncated by GitHub's size limit.
log_tail() {
  {
    echo "--- python-tagged lines: $(grep -a -c "python  *:" "$LOGCAT" 2>/dev/null || echo 0)"
    grep -a -E "OCRDIAG|FATAL EXCEPTION|Fatal signal|ANR in |Force finishing|Traceback|E python|ImportError|ModuleNotFoundError|OSError|dlopen|has died|Unable to start activity|SecurityException" \
      "$LOGCAT" 2>/dev/null | tail -40
    echo "--- last 15 lines of logcat"
    tail -15 "$LOGCAT" 2>/dev/null
  } | cut -c1-300
}

fail() {
  local reason="$1"
  # The picture the user would have seen, even (especially) when it is wrong:
  # a blank or half-drawn frame is the evidence for a layout complaint.
  adb exec-out screencap -p > emulator-screenshot.png 2>/dev/null || true
  echo "::error::BOOT TEST FAILED: $reason"
  echo "::error::$(log_tail | tr '\n' '|' | tr -d '\r' | cut -c1-16000)"
  say "**FAIL:** $reason"
  # Written next to the log so it survives in the logcat file for the reporter
  # step, which publishes it as the "Emulator boot log" check run.
  { echo "BOOT TEST FAILED: $reason"; log_tail; } >> "$LOGCAT"
  exit 1
}

if [ ! -f "$APK" ]; then
  echo "::error::APK not found: $APK"
  exit 1
fi

adb wait-for-device
say "device: android $(adb shell getprop ro.build.version.release 2>/dev/null | tr -d '\r') / api $(adb shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r') / abi $(adb shell getprop ro.product.cpu.abi 2>/dev/null | tr -d '\r')"

adb install -r -g "$APK" >install.log 2>&1 || fail "adb install failed: $(tail -3 install.log | tr '\n' ' ')"
say "installed $(du -h "$APK" | cut -f1) $(basename "$APK")"

adb logcat -c
adb shell am force-stop "$PKG"
# `monkey ... LAUNCHER` starts the app the way the home-screen icon does, without
# hard-coding the activity class (which differs per bootstrap).
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >launch.log 2>&1 \
  || fail "could not launch $PKG: $(tail -3 launch.log | tr '\n' ' ')"

# --- wait for the app to report itself --------------------------------------
# A *first* launch of a debug APK has to unpack the Python bundle before any
# Python runs, which on a cold emulator image can take a couple of minutes. A
# fixed sleep was what made this step fail on a perfectly good build, so the
# loop waits for evidence of life (or of death) instead of for a wall clock.
tries="${BOOT_WAIT_TRIES:-60}"
step="${BOOT_WAIT_STEP:-5}"
seen_pid=""
verdict=""
attempt=0
for _i in $(seq 1 "$tries"); do
  attempt=$((attempt + 1))
  sleep "$step"
  adb logcat -d > "$LOGCAT" 2>&1
  pid="$(adb shell pidof "$PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
  [ -n "$pid" ] && seen_pid="$pid"
  if grep -a -q "OCRDIAG startup" "$LOGCAT"; then
    verdict=started
    break
  fi
  if fatal="$(fatal_marker "$LOGCAT" "$seen_pid")"; then
    verdict=fatal
    fatal_detail="$fatal"
    break
  fi
done
# How long a cold first launch needed, in the log where the next person tuning
# this timeout will look for it.
echo "::notice::startup record ${verdict:-not-found} after ${attempt} polls of ${step}s (pid=${seen_pid:-none})"
say "startup ${verdict:-not found} after ${attempt} polls (${step}s apart)"

case "$verdict" in
  fatal)
    fail "Android reported a fatal error for ${PKG}: ${fatal_detail:-see logcat}"
    ;;
  started)
    ;;
  *)
    if [ -n "$seen_pid" ]; then
      fail "app process existed (pid $seen_pid) but never logged OCRDIAG startup within ${tries} tries at ${step}s apart - Python did not reach build()"
    fi
    fail "no process for $PKG at all within ${tries} tries at ${step}s apart - the app never launched (crash on open)"
    ;;
esac

# Now that Python is up: wait for the app to report the layout it actually got, and
# confirm the process is still there - a death on the first timer or the first draw
# shows up here.
#
# The record has to say `laid_out=yes`, and waiting for *any* layout record is not
# enough: before Kivy's first layout pass every size is the 100x100 widget default,
# which is what an early CI run reported as `preview=26x0 scanbtn=0x141` (26 =
# 100 - 2*dp(14)) and what would otherwise be blamed on the app. main.py re-arms
# its own report until the screen matches the window, so this loop only has to wait
# for that event instead of sleeping.
for _i in $(seq 1 "${BOOT_SETTLE_TRIES:-24}"); do
  sleep "${BOOT_WAIT_STEP:-5}"
  adb logcat -d > "$LOGCAT" 2>&1
  if grep -a -q "OCRDIAG layout.*laid_out=yes" "$LOGCAT"; then break; fi
  if grep -a -q "OCRDIAG fatal" "$LOGCAT"; then
    fail "main.py reported a fatal record: $(grep -a -m1 'OCRDIAG fatal' "$LOGCAT" | cut -c1-200)"
  fi
done

adb exec-out screencap -p > emulator-screenshot.png 2>/dev/null || true

grep -a "OCRDIAG" "$LOGCAT" | sed -E 's/^.*(OCRDIAG)/\1/' > "$DIAG" 2>/dev/null || true
[ -s "$DIAG" ] || fail "OCRDIAG startup was seen but no records could be extracted (log format changed?)"

# A Python traceback after startup means something in the app raised, even if
# Android itself never reported a fatal error.
if grep -a -qE "E python *+: *Traceback" "$LOGCAT"; then
  fail "Python raised during startup: $(grep -a -m1 -A3 'Traceback (most recent call last)' "$LOGCAT" | tr '\n' ' ' | cut -c1-240)"
fi

# `adb shell pidof` exit status is not trustworthy across adb versions, so the
# output is what decides.
final_pid="$(adb shell pidof "$PKG" 2>/dev/null | tr -d '\r')"
[ -n "$final_pid" ] || fail "app died after logging startup: no pid at the end of the test"

# "The interface looks tiny" and "half of it is missing", measured on the last
# layout record: Kivy was given the whole surface, not a forced 400x750 box, and
# every row on the visible screen has a non-zero size.
layout_line="$(grep -a 'OCRDIAG layout window=' "$DIAG" | tail -1)"
[ -n "$layout_line" ] || fail "no 'OCRDIAG layout window=' record: build() never reported its layout"
printf '%s' "$layout_line" | grep -q "laid_out=yes" ||
  fail "the current screen never got a layout pass - the UI is blank or unsized on this device: ${layout_line}"
win="$(printf '%s' "$layout_line" | grep -o -E "window=[0-9]+x[0-9]+" | grep -o -E "[0-9]+x[0-9]+")"
[ -n "$win" ] || fail "the layout record has no window= size: $(printf '%s' "$layout_line" | cut -c1-160)"
w="${win%x*}"
h="${win#*x}"
if [ "$w" -lt 720 ] || [ "$h" -lt 1000 ]; then
  fail "UI rendered into ${win}px instead of filling the screen (Window.size forced on Android?)"
fi
for pair in preview scanbtn result; do
  size="$(printf '%s' "$layout_line" | grep -o -E "${pair}=[0-9]+x[0-9]+" | head -1 | grep -o -E "[0-9]+x[0-9]+")"
  [ -n "$size" ] || continue   # that widget may legitimately live on another screen
  rw="${size%x*}"
  rh="${size#*x}"
  if [ "$rw" -eq 0 ] || [ "$rh" -eq 0 ]; then
    fail "row '${pair}' measured ${size}px on a laid-out screen - it is invisible on the phone: ${layout_line}"
  fi
done

echo "=== device log records"
while IFS= read -r line; do
  echo "  $line"
  echo "::notice::$line"
done < "$DIAG"
say "app started, window is ${win}px, rows laid out, no fatal markers"
say '```'
say "$(tr '\n' '|' < "$DIAG" | cut -c1-1500)"
say '```'
echo "=== boot test passed"
