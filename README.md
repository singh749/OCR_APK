# OCR Number Scanner (Kivy → Android APK)

Scans engraved / stamped metal serial numbers with OpenCV preprocessing and an
OCR engine, and keeps a local scan history in SQLite.

* UI: `main.py` + `main.kv` (Kivy)
* Vision / OCR: `ocr_engine.py`
* History: `database.py` (SQLite, `ocr_history.db`)
* Android packaging: `buildozer.spec`
* CI build: `.github/workflows/build-apk.yml`

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

## Why the workflow used to fail (all 7 runs failed the same way)

| # | Error in Actions | Cause / fix |
|---|---|---|
| 1 | `Install System Dependencies` → `Process completed with exit code 100` | `ubuntu-latest` is Ubuntu 24.04, where `libtinfo5`, `libncurses5-dev` and `libncursesw5-dev` no longer exist in the archive, so `apt-get install` aborted. They are replaced by `libtinfo6` / `libncurses-dev`, and the runner image is pinned to `ubuntu-24.04` (the `latest` alias moves to 26.04 on 2026-10-19). Missing real Buildozer deps (`ant`/`ccache`/`autopoint`/`libltdl-dev`/`patch`/`lbzip2`/`make`) were added. |
| 2 | `# Errors in buildozer.spec: [app] One of "version" or "version.regex" must be set` | `buildozer.spec` had no version at all → added `version = 1.0`. |
| 3 | `Couldn't find the tesseract recipe` | `requirements` listed `pytesseract` and `tesseract`; python-for-android has no recipe for either (nor for `easyocr`/`torch`). Only real recipes are listed now: `python3,kivy,android,pyjnius,sqlite3,numpy,pillow,opencv`. |
| 4 | `android.ndk_api = 33` | `ndk_api` is the *minimum* SDK, so the APK would have refused to install below Android 13. Dropped; `android.api`/`minapi`/`ndk` are left at buildozer + p4a's matching defaults instead of hand-pinned versions. |
| 5 | `Node.js 20 is deprecated … actions/checkout@v4, actions/setup-python@v5` | Actions bumped to `checkout@v7`, `setup-python@v7`, `setup-java@v6`, `upload-artifact@v7`. |
| 6 | Red ✕ with no explanation | Added a `Show effective buildozer.spec` step, an *Attach build logs on failure* step (`buildozer.log` / p4a `build.log`), an explicit `JAVA_HOME` via `setup-java`, `timeout-minutes: 120`, and `PYTHONFORANDROID_PREREQUISITES_INSTALL_INTERACTIVE=0` so p4a never blocks on a question. |
| 7 | Two dead copies of the workflow in the repo root | `build-apk.yml` (duplicate) and `github_workflows_build-apk.yml` (a Gradle `./gradlew assembleRelease` build for a project that has no `gradlew`, no `app/` module and no `android/` project — this is a Buildozer project) were removed; `.github/workflows/build-apk.yml` is the single source of truth. A `.gitignore` for `.buildozer/`, `bin/`, `*.db` and `temp_images/` was added. |

---

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
