[app]

title = OCR Metal Scanner
author = singh749
package.name = ocrscanner
package.domain = org.metalocr

# buildozer aborts with `# Errors in buildozer.spec` if neither `version` nor
# `version.regex` is present, so it has to be set explicitly.
version = 1.0

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas
# keep build junk out of the APK payload
source.exclude_dirs = bin, build, dist, temp_images, easyocr_models, venv, .venv, __pycache__
source.exclude_patterns = license,authors.*

# ---------------------------------------------------------------------------
# Python packages compiled into the APK.
#
# Every entry must be the name of a python-for-android *recipe*. p4a has no
# recipe for `tesseract`, `pytesseract` or `easyocr`/`torch`, which is why the
# previous spec died with "Couldn't find the tesseract recipe". Tesseract is a
# desktop binary and cannot be shipped this way; PyTorch has no Android build
# here either. See README.md for the on-device options.
# ---------------------------------------------------------------------------
requirements = python3,kivy,android,pyjnius,sqlite3,numpy,pillow,opencv

# One ABI only: each extra ABI doubles the compile time of numpy/OpenCV.
# Add armeabi-v7a (or x86_64 for emulators) once the build is green.
android.archs = arm64-v8a

# android.api / android.minapi / android.ndk are deliberately NOT pinned here:
# buildozer and p4a agree on a matching, currently supported set
# (target API 36, min API 24, NDK r28c). The old `android.ndk_api = 33` line was
# wrong twice over -- ndk_api is the *minimum* SDK, so it would have made the
# APK refuse to install below Android 13 and it was also higher than the
# recommended value.

android.accept_sdk_license = True

android.permissions = CAMERA,INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

orientation = portrait
fullscreen = 0

# `android.enable_androidx` is switched on automatically for target API > 28.

[buildozer]
log_level = 2
warn_on_root = 0
