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

## OCR engine on Android — read this

`ocr_engine.py` prefers **EasyOCR** and falls back to **pytesseract**; both are
*desktop* engines (PyTorch and the `tesseract` binary cannot be packaged by
python-for-android). On the phone neither is importable, so the scan reports
**“No OCR engine on this device”** instead of crashing.

The OpenCV preprocessing (Laplacian sharpening, CLAHE, gamma, illumination
normalisation) does run on Android and is what makes engraved text readable.
To get real on-device recognition, pick one:

* **Google ML Kit Text Recognition** — recommended. Add to `buildozer.spec`:
  `android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.1`
  and call it from `ocr_engine.py` through `pyjnius` (`autoclass`). Android
  Studio-compatible Gradle deps are handled by p4a, androidx is already
  auto-enabled for target API > 28.
* **Tesseract mobile (`com.rmtheis:tess-two` or `mlkit`-style wrapper)** via the
  same `pyjnius` route, plus the `eng.traineddata` asset.
* Bundle a small ONNX/TFLite recognizer and run it through OpenCV's `dnn`
  module (`opencv` already ships `libopencv_dnn.so` in the APK).

Whichever you choose, keep the desktop engines as the `try/except` fallbacks they
are today so the same code runs on PC and on the phone.

## Notes on `requirements.txt`

`requirements.txt` is for the **desktop** environment only — the APK gets its
packages from `buildozer.spec` → `requirements`. The Windows-only
`kivy-deps.*` wheels are now guarded with `; sys_platform == "win32"` markers, so
`pip install -r requirements.txt` no longer fails on Linux/macOS.
