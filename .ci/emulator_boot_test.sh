#!/usr/bin/env bash
# Boot the freshly built APK on an emulator and prove the app really starts.
#
# Everything else in this pipeline can only say "an APK was produced". A Python
# AttributeError in a Kivy callback, a .kv file that references a handler that
# does not exist, or a layout that renders into a 400x750 box all build
# perfectly and fail on the phone. This step is the phone.
#
# main.py writes greppable one-line records to logcat under the tag `python`
# (`OCRDIAG startup ...`, `OCRDIAG layout window=1080x2400 ...`), which is what
# makes both of those failure modes measurable here instead of eyeballed.
#
# Usage: emulator_boot_test.sh <apk-path> [package-name]
set -uo pipefail

APK="${1:?usage: emulator_boot_test.sh <apk> [package]}"
PKG="${2:-org.metalocr.ocrscanner}"
LOGCAT=emulator-logcat.txt
DIAG=ui-report.txt
PROBLEMS=boot-problems.txt
: > "$LOGCAT"
: > "$DIAG"
: > "$PROBLEMS"

note() { echo "::notice::$*"; }
say() { echo "$*"; echo "* $*" >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true; }

fail() {
  echo "::error::$1"
  say "**FAIL:** $1"
  # The workflow publishes this file as a check run, which is the only way the
  # device-side error survives outside the runner.
  {
    echo "### $1"
    echo
    echo '```'
    grep -a -E "OCRDIAG|FATAL EXCEPTION|Traceback|E python|libc *: *Fatal|ANR in|Force finishing|has died|Unable to start activity|SecurityException|android.os.*Exception|ImportError|ModuleNotFoundError" \
      "$LOGCAT" | tail -60
    echo '```'
  } > logcat-excerpt.txt
  exit 1
}

if [ ! -f "$APK" ]; then
  echo "::error::APK not found: $APK"
  exit 1
fi

adb wait-for-device
say "device: $(adb shell getprop ro.build.version.release 2>/dev/null) (API $(adb shell getprop ro.build.version.sdk 2>/dev/null)), sdk=$(adb shell getprop ro.product.cpu.abi 2>/dev/null)"

adb install -r -g "$APK" >install.log 2>&1 || fail "adb install failed: $(tail -3 install.log | tr '\n' ' ')"
say "installed $(du -h "$APK" | cut -f1) APK"

adb logcat -c
adb shell am force-stop "$PKG"

# `monkey ... LAUNCHER` starts the app the way the home-screen icon does, without
# hard-coding the activity class name (which differs per bootstrap).
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >launch.log 2>&1 \
  || fail "could not launch $PKG (is the launcher activity declared?)"

# 40 x 2s: the first launch of a Kivy app has to unpack the Python bundle, so
# "not up after 80 seconds" really is broken. Overridable so the check can be run
# against a fake adb in seconds instead of minutes.
pid=""
for _i in $(seq 1 "${BOOT_WAIT_TRIES:-40}"); do
  sleep "${BOOT_WAIT_STEP:-2}" 
  pid="$(adb shell pidof "$PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
  [ -n "$pid" ] && break
done
if [ -z "$pid" ]; then
  adb logcat -d > "$LOGCAT" 2>&1
  fail "app never stayed up: no process for $PKG after 80s (crash on open)"
fi

# Give Kivy time to draw a frame and to log the layout record, then confirm the
# process is *still* there: a crash on the first tap-equivalent timer shows up here.
sleep "${BOOT_SETTLE:-12}"
after="$(adb shell pidof "$PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
adb logcat -d > "$LOGCAT" 2>&1
adb exec-out screencap -p > emulator-screenshot.png 2>/dev/null || true

if [ -z "$after" ]; then
  fail "app started (pid $pid) then died within 12s - crash after startup"
fi
say "app alive: pid $pid -> $after"

grep -a "OCRDIAG" "$LOGCAT" | sed -E 's/^[0-9-]+ [0-9:.]+ +[0-9]+ +[0-9]+ +[A-Z] +//' > "$DIAG" 2>/dev/null || true
if [ ! -s "$DIAG" ]; then
  fail "no OCRDIAG records in logcat: main.py never finished build(), so Python died during import or build"
fi

# Unambiguous death / exception markers, whatever the app managed to log.
if grep -q -a -E "FATAL EXCEPTION|Fatal signal|ANR in |Force finishing activity" "$LOGCAT"; then
  grep -a -E "FATAL EXCEPTION|Fatal signal|ANR in |Force finishing activity" "$LOGCAT" | head -5 > boot-problems.txt
  fail "Android reported a fatal error: $(head -1 boot-problems.txt | cut -c1-160)"
fi
if grep -q -a -E "E python  *: *Traceback" "$LOGCAT"; then
  fail "Python logged a traceback during startup (see the emulator boot log check run)"
fi

# The "interface looks tiny" bug: Kivy laid everything out inside the size that
# Window.size forced, so the recorded window is far smaller than the screen.
win="$(grep -a -o -E "OCRDIAG layout window=[0-9]+x[0-9]+" "$DIAG" | head -1 | grep -o -E "[0-9]+x[0-9]+")"
if [ -z "$win" ]; then
  fail "no 'OCRDIAG layout window=' record: build() reported no layout, layout check skipped"
fi
w="${win%x*}"; h="${win#*x}"
if [ "$w" -lt 720 ] || [ "$h" -lt 1000 ]; then
  fail "UI rendered into ${win}px instead of filling the screen (Window.size forced on Android?)"
fi
note "window ${win}px, app alive, no fatal markers"
say "layout window=${win} $(grep -a -o -E 'dpi=[0-9.]+ density=[0-9.]+' "$DIAG" | head -1)"
say "records:"
say '```'
while IFS= read -r line; do note "$line"; say "$line"; done < "$DIAG"
say '```'
echo "=== log:"
cat "$DIAG"
echo "=== boot test passed"
