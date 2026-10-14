# OCR Number Scanner (Kivy → Android APK)

Scans engraved / stamped metal serial numbers with OpenCV preprocessing and an
OCR engine, and keeps a local scan history in SQLite.

* UI: `main.py` + `main.kv` (Kivy)
* Vision / OCR: `ocr_engine.py`
* History: `database.py` (SQLite, `ocr_history.db`)
* Android packaging: `buildozer.spec`
* CI build: `.github/workflows/build-apk.yml`

**Current status:** the pipeline builds green — run
[38575116951](https://github.com/singh749/OCR_APK/actions/runs/38575116951)
produced `OCR-Scanner-APK` (~42 MB, `arm64-v8a` debug APK) in 30 min. Getting
there meant fixing ten separate errors, listed below; each one was visible only
after the previous one was gone.

---

## Android APK from GitHub Actions

The **Build Android APK** workflow builds a debug APK with Buildozer and uploads
it as an artifact:

1. Open the **Actions** tab → **Build Android APK**.
2. Either push to `main`, or press **Run workflow** (the *workflow_dispatch*
   button) to build on demand.
3. When the run turns green, scroll to the bottom of the run page and download
   **OCR-Scanner-APK** (`*-arm64-v8a-debug.apk`).
4. Copy it to an arm64 Android phone (Android 7.0+) and install it
   (*Settings → Install unknown apps* must be allowed for your file manager).

The first build takes **roughly 30–45 min**: Buildozer downloads the Android SDK
+ NDK and cross-compiles CPython, Kivy, numpy, Pillow and OpenCV. Later builds
reuse the cached `.buildozer` directory only on the same machine (locally) — on
GitHub each run starts from scratch, so the `arm64-v8a` ABI is the only one
built. Add `armeabi-v7a` to `android.archs` in `buildozer.spec` if you need
32-bit devices.

### Build it locally instead

```bash
sudo apt update
sudo apt install -y autoconf automake autopoint gettext ccache cmake git \
  lbzip2 make patch libffi-dev libltdl-dev libncurses-dev libssl-dev \
  libtinfo6 libtool pkg-config python3 python3-dev python3-pip python3-venv \
  unzip wget zip zlib1g-dev openjdk-17-jdk
python3 -m venv venv && . venv/bin/activate
pip install buildozer "cython<3.2"
buildozer android debug      # APK lands in ./bin/
```

## Desktop run

```bash
python install_dependencies.py     # or: pip install -r requirements.txt
python main.py
```

`run.bat` does the same on Windows using `.\venv` when it exists.

---

## Why the workflow used to fail (10 errors, found one at a time)

| # | Error in Actions | Cause / fix |
|---|---|---|
| 1 | `Install System Dependencies` → `Process completed with exit code 100` | `ubuntu-latest` is Ubuntu 24.04, where `libtinfo5`, `libncurses5-dev` and `libncursesw5-dev` no longer exist in the archive, so `apt-get install` aborted. They are replaced by `libtinfo6` / `libncurses-dev`, and the runner image is pinned to `ubuntu-24.04` (the `latest` alias moves to 26.04 on 2026-10-19). Missing real Buildozer deps (`ant`/`ccache`/`autopoint`/`libltdl-dev`/`patch`/`lbzip2`/`make`) were added. |
| 2 | `# Errors in buildozer.spec: [app] One of "version" or "version.regex" must be set` | `buildozer.spec` had no version at all → added `version = 1.0`. |
| 3 | `Couldn't find the tesseract recipe` | `requirements` listed `pytesseract` and `tesseract`; python-for-android has no recipe for either (nor for `easyocr`/`torch`). Only names that exist as p4a recipes are listed now (`python3`, `hostpython3`, `kivy`, `android`, `pyjnius`, `sqlite3`, `numpy`, `pillow`, `opencv` — verified against `pythonforandroid/recipes/`). |
| 4 | `android.ndk_api = 33` | `ndk_api` is the *minimum* SDK, so the APK would have refused to install below Android 13. Dropped; `android.api`/`minapi`/`ndk` are left at buildozer + p4a's matching defaults instead of hand-pinned versions. |
| 5 | `Node.js 20 is deprecated … actions/checkout@v4, actions/setup-python@v5` | Actions bumped to `checkout@v7`, `setup-python@v7`, `setup-java@v6`, `upload-artifact@v7`. |
| 6 | Red ✕ with no explanation | Added a `Show effective buildozer.spec` step, the failure report described below, an explicit `JAVA_HOME` via `setup-java`, `timeout-minutes: 120`, and `PYTHONFORANDROID_PREREQUISITES_INSTALL_INTERACTIVE=0` so p4a never blocks on a question. |
| 7 | Two dead copies of the workflow in the repo root | `build-apk.yml` (duplicate) and `github_workflows_build-apk.yml` (a Gradle `./gradlew assembleRelease` build for a project that has no `gradlew`, no `app/` module and no `android/` project — this is a Buildozer project) were removed; `.github/workflows/build-apk.yml` is the single source of truth. A `.gitignore` for `.buildozer/`, `bin/`, `*.db` and `temp_images/` was added. |
| 8 | Build died ~25 min in, inside the Kivy recipe | python-for-android master builds **CPython 3.14.2** for the device, and no released Kivy supports 3.14 (Kivy 2.3.1 supports 3.8–3.13, see kivy/kivy#9225), so the compile fails with `_PyLong_AsByteArray called with incorrect number of arguments` — the same bug that broke the `kivy/buildozer:latest` Docker image (buildozer#2040). Fix: pin the interpreter that is *compiled into the APK*: `requirements = python3==3.13.16,hostpython3==3.13.16,...`. Both must be pinned, because p4a aborts when they differ (`python3 should have same version as hostpython3`). |
| 9 | `ERROR: charset_normalizer-3.5.2-cp313-cp313-android_24_arm64_v8a.whl is not a supported wheel on this platform` (31 min in, while installing Kivy's Python dependencies) | python-for-android cross-compiles `charset-normalizer` (pulled in by `requests`, which the Kivy recipe depends on) into a wheel tagged for Android, and then installs it with a *host* pip that only accepts Linux wheels. Fixed upstream by kivy/python-for-android#3366 (take the prebuilt Android wheels from PyPI), which lives on p4a's **develop** branch — so `p4a.branch = develop` is set in `buildozer.spec` (that is also what Buildozer's own CI and install docs use). |
| 10 | The build used an older python-for-android than `p4a.branch` asks for | Buildozer only clones p4a if `~/.buildozer/android/platform/python-for-android` is missing and otherwise keeps the existing checkout (it re-pulls on `buildozer android update`), so a stale tree silently wins. The workflow deletes it before building and prints the revision that was used, so every log says which p4a produced the APK. |

### Reading a failure yourself

The log is 30k+ lines, so the workflow extracts and publishes the meaningful
part for you. Every failed run gives you:

* a **“Build log excerpt” check run** on the commit — Actions → the run →
  *Show all checks* → Build log excerpt; it holds the log region around
  Buildozer's own `Buildozer failed to execute the last command` banner;
* the same text in the **job summary** of the `Report the failure` step;
* a **comment on the pull request** (for `pull_request` runs);
* the **`build-log` artifact** with the complete, unfiltered `build.log`.

This is `.ci/ci_failure_report.py`. The build step pipes its console through a
small `sed` filter on purpose: a job only keeps ~10 annotations, and thousands
of harmless SDL2 `warning:` lines would otherwise use them all up.

---

### The two that only showed up on the phone

| # | Symptom | Cause and fix |
| --- | --- | --- |
| 11 | The APK installed fine, then closed itself the moment **Camera**, **Copy** or the History search box was used | `main.kv` called `app.open_camera()`, `app.copy_number()` and `app.search_history()`, and no such method existed on the App class: an exception inside a button callback is uncaught, so Android killed the app. `.kv` files are **not executed at build time**, so no build step could ever have shown this. All three exist now, every handler runs through `@safe` (a readable, copyable dialog instead of a silent close), and CI now fails the build on it - see `.ci/check_kv_wiring.py` |
| 12 | Everything rendered small and clipped, as if the app were a 400x750 card floating on the screen | `main.py` set `Window.size = (400, 750)` at import time. On a desktop that resizes a window; on Android the window size *is* the surface Kivy lays the whole app out into, so the UI was built at 400px wide and shown on a full-resolution framebuffer. The call is now desktop-only, guarded (`kivy.core.window.Window` is `None` when no window provider exists) and moved into `build()`. Row heights use `dp()`, the preview fills the space above the buttons and overlays its own placeholder, and every text label binds `text_size` so long text wraps instead of running off |

Nothing was wrong with the screen density: `android:anyDensity="true"` is already set by
python-for-android's bootstrap manifest, so chasing `anyDensity`/`compatible-screens` is a
wasted afternoon. If the UI still looks off on a device, the fix is in the layout numbers,
not in the manifest.

### pyjnius and nested Android classes

`MediaStore.Images.Media` and `Build.VERSION` are **not** fields of their outer class, so
`autoclass("android.provider.MediaStore").Images` raises `AttributeError` on the device - the
build cannot see it, the Camera button finds it immediately. Nested classes have to be named
the way the JVM does, with `$`:

```python
Images = autoclass("android.provider.MediaStore$Images$Media")   # EXTERNAL_CONTENT_URI, ...
Build_VERSION = autoclass("android.os.Build$VERSION")            # SDK_INT
```

That is the form python-for-android's own helpers use (`android/storage.py`,
`android/permissions.py`), so copy from those when a call misbehaves. Only fields that really
are static (`MediaStore.ACTION_IMAGE_CAPTURE`, `Intent.FLAG_GRANT_WRITE_URI_PERMISSION`,
`Activity.RESULT_OK`) may be read off the outer class.

## The job that runs the app, not just builds it

`build-android` can only say "an APK was produced". Every real complaint about this app has
been about what happens *on the phone*, which no build step can see, so a second job
(`boot-test`) builds the same code for `x86_64`, installs it on an Android 13 emulator,
launches it and asserts on logcat (`.ci/emulator_boot_test.sh`):

| Check | What it catches |
| --- | --- |
| `OCRDIAG startup` appears in logcat, polled until it does | **"it closes as soon as I open it"** — and it is polled, not slept on, because a *first* launch unpacks the Python bundle and a fixed timeout blamed the app for it |
| the pid is still there when the layout record lands | a crash after the first frame (a broken timer, a bad popup) |
| `FATAL EXCEPTION` / `Fatal signal` / `ANR in` absent in logcat | Java- and native-level deaths |
| no `E python : Traceback` in logcat | a Python exception during import or `build()` |
| `OCRDIAG layout window=<W>x<H> ... laid_out=yes` with W >= 720 and no row measured 0px |
  the **"interface renders tiny"** and **"half the screen is blank"** bugs, measured instead of
  eyeballed. `laid_out=yes` is required because before Kivy's first layout pass every size is the
  100x100 widget default, which looks identical to a collapsed layout |
| at least one `OCRDIAG` record at all | `build()` never finished, i.e. Python died very early |

The records come from `main.py`'s `log_diag()`: `OCRDIAG startup ...`, `OCRDIAG problem ...`,
`OCRDIAG layout window=... preview=... laid_out=yes`, `OCRDIAG fatal ...`, and
`OCRDIAG engine method=... alts=...` after each scan (which engine answered). On a real phone the same lines
are the whole diagnosis:

```
adb logcat -d -s python:V | grep OCRDIAG
```

On failure the job publishes the logcat excerpt as an **"Emulator boot log"** check run (and
a PR comment), and uploads `emulator-logcat.txt`, `ui-report.txt` and `emulator-screenshot.png`
as the `emulator-evidence` artifact - the screenshot is the one thing a "the layout looks
wrong" report cannot be argued with.

Why the phone's APK is not tested here: an `arm64-v8a` APK cannot be installed on an `x86_64`
emulator (there is no translation layer in that direction), so a device run needs a second
architecture. Adding `x86_64` to `android.archs` in `buildozer.spec` would put both in one
APK, at the cost of building every recipe twice.

## Startup is deliberately boring

Three rules in `main.py` exist only so that "the app opens" is never at the mercy of anything
optional, and they are the reason the last crash-on-open was so expensive to chase:

* **no pyjnius at startup.** `ensure_activity_listener()` registers the Android activity-result
  callback the first time Camera or Upload is used, not in `build()`.
* **no dialog at startup.** Missing modules are named in the result panel and in logcat; a
  `Popup` created during `build()` is a crash surface of its own.
* **no `import kivy.core.window` anywhere.** That import is what *selects* a window provider,
  and when none works Kivy calls `sys.exit(1)` - which no `try/except` can catch. `Window` is
  read from `sys.modules` instead, so the app can only ever see a window Kivy already made.
  Error dialogs use a `Label` in a `ScrollView` (with a Copy button), never a `TextInput`.

## Guards that run before the (30-minute) build

```
Check the .kv wiring against main.py   # .ci/check_kv_wiring.py
Test the OCR result pipeline           # tests/test_ocr_pipeline.py
```

The first one fails the build when `main.kv` calls `app.something()` (or `root.something()`)
that `main.py` does not define, or reads `self.ids.<name>` for an `id:` the kv never declares
- both are guaranteed `AttributeError`/`KeyError` on the phone, and both were invisible to
every other step. The second drives `ocr_engine.extract_numbers()` with a stubbed Tesseract,
because that code path cannot run on an Android host at all (no engine recipe), which is
exactly why its bugs went unnoticed: it used to report the *preprocessing variant* as the
engine name, hard-code 65% confidence, raise `UnboundLocalError` whenever a variant read
nothing, and glue the printed label onto the value (`"NO U6234"` -> `"NOU6234"`).

Label words are now removed from the **raw text while its spaces still exist**, which is the
only reliable signal that `NO` is a label rather than the start of `NOW12345`: the second
stays whole, the first becomes `U6234`.

## On the phone

* **Camera** hands the job to the system camera app and asks for a full-resolution photo,
  written to `Pictures/OCRScanner` through MediaStore and read back from the activity result.
  Nothing is scaled down, so small characters stay legible, and no storage permission is
  needed on Android 10+. A plain `file://` path would be rejected outright
  (`FileUriExposedException`), and buildozer cannot inject the `FileProvider` that would be
  the alternative, which is why the write goes through `ContentResolver.insert`.
* **History search** and **Copy** are plain Kivy: search filters the stored rows, Copy uses
  the App's clipboard.
* If a button ever shows the red dialog, tap **Copy** and paste it into an issue - that text
  is the actual cause, which a crash on the phone never shows you.

## OCR engine on Android

The phone reads numbers with **Google ML Kit Text Recognition v2**, bundled in the
APK. `buildozer.spec` carries the whole wiring:

```
android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.0
```

python-for-android turns each entry into `implementation '<coordinate>'` in the
generated `build.gradle`, whose template already declares `google()` and
`mavenCentral()`, so there is no Gradle file to maintain and no extra repository
to add. That artifact is the *bundled* variant — its POM pulls
`text-recognition-bundled-common` — so the model is inside the APK: no network, no
Play Services model download, works in a shed. AndroidX is required by ML Kit and
is auto-enabled by p4a for target API > 28.

`ocr_engine.read_with_mlkit()` is the call. Three things about it are deliberate:

* Nothing Java is imported at module level, exactly as in `main.py`. `ANDROID`
  plus a Context argument decide whether ML Kit is even attempted, so a desktop
  run, a test run and an APK built without the Gradle line all take the same
  quiet path: `read_with_mlkit()` returns `None` and EasyOCR/Tesseract answer
  instead. A missing engine is a missing feature, never a crash.
* `Tasks.await(...)` is fetched with `getattr(Tasks, "await")`, because `await` is
  a reserved word in Python and `Tasks.await(x)` is a `SyntaxError`.
* The result is flattened by *probing* for `getTextBlocks`/`getBlocks`/
  `getRecognizedBlocks` and falling back to `Text.getText()`. Those getters moved
  between ML Kit versions while `getText()` never has, so an ML Kit upgrade cannot
  make the number disappear; per-line confidence is used when present and left
  `None` (rendered as a flat mid-range score) when it is not. The Java `List` is
  walked with `size()`/`get()` because pyjnius does not promise Python iteration.

ML Kit recognises the whole photo and does its own dewarping, so the OpenCV
variants (`preprocess_for_engraved`) are only run for the desktop engines — on a
phone one recognisation pass is the fastest thing available. `Engine: ML Kit OCR`
in the result panel and `OCRDIAG engine method=ML Kit` in logcat say which path
answered; **“No OCR engine on this device”** now means the import of
`ocr_engine` itself failed, not that a phone has no engine.

`tests/test_ocr_pipeline.py` covers the Python side of all this with a stubbed
`jnius` (`install_fake_jnius`), including the missing-bridge and
Java-throws-on-the-device cases — the parts that used to be untestable.

## Notes on `requirements.txt`

`requirements.txt` is for the **desktop** environment only — the APK gets its
packages from `buildozer.spec` → `requirements`. The Windows-only
`kivy-deps.*` wheels are now guarded with `; sys_platform == "win32"` markers, so
`pip install -r requirements.txt` no longer fails on Linux/macOS.
