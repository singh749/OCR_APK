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
source.exclude_dirs = bin, build, dist, tests, temp_images, easyocr_models, venv, .venv, __pycache__
source.exclude_patterns = license,authors.*

# ---------------------------------------------------------------------------
# Python packages compiled into the APK.
#
# Every entry must be the name of a python-for-android *recipe*. p4a has no
# recipe for `tesseract`, `pytesseract` or `easyocr`/`torch`, which is why the
# previous spec died with "Couldn't find the tesseract recipe". Tesseract is a
# desktop binary and cannot be shipped this way; PyTorch has no Android build
# here either. See README.md for the on-device options.
#
# The python3/hostpython3 pin is the important one: python-for-android master
# builds CPython 3.14.2 for the device, but no released Kivy supports 3.14
# (Kivy 2.3.1 supports 3.8-3.13), so the Kivy recipe dies with
#     _PyLong_AsByteArray called with incorrect number of arguments
# Pin 3.13 for BOTH recipes: p4a refuses to build if the two differ
# ("python3 should have same version as hostpython3"), which is why pinning
# only python3==... is a dead end.
# ---------------------------------------------------------------------------
# `pillow` is deliberately NOT in this list, even though requirements.txt has it
# for desktop: nothing in main.py / ocr_engine.py / database.py imports PIL, and
# p4a's Pillow recipe pulls `png` + `jpeg` + `freetype` native builds with it, so
# shipping it meant three extra ABI-specific `.so` files and a bigger bundle to
# unpack on first launch - all of which can only fail on a handset nobody here can
# reach. The preview still decodes jpg/png without it: Kivy's `img_sdl2` provider
# lists ('bmp','jpg','jpeg','jpe','lbm','pcx','png','pnm', ...) and python-for-android
# builds SDL2_image with its own bundled codecs (its recipe clones the submodules).
# buildozer's own optional use of PIL is satisfied by its pip install, not by this
# line, so the tooling is unaffected either.
requirements = python3==3.13.16,hostpython3==3.13.16,kivy,android,pyjnius,sqlite3,numpy,opencv

# Which python-for-android branch to build with. `develop` is what buildozer's
# own CI and install docs use, and it is required here: the master snapshot
# still dies while installing Kivy's `requests` dependency chain with
#   ERROR: charset_normalizer-3.5.x-cp313-...-android_24_arm64_v8a.whl
#   is not a supported wheel on this platform
# (kivy/buildozer#2051), fixed by kivy/python-for-android#3366, which teaches
# p4a to take the prebuilt Android wheels from PyPI instead of pip-installing
# its own cross-compiled wheel with a host interpreter that rejects the tag.
# Buildozer keeps an existing clone, so bump this with `buildozer android update`.
p4a.branch = develop

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

# CAMERA is declared for a future in-app viewfinder; the "Camera" button itself
# only needs it indirectly, because it hands the job to the system camera app
# through an intent. Storage permissions are capped with maxSdkVersion so they
# apply to old devices only: on Android 10+ the app writes/reads its photos
# through MediaStore content URIs, which need no permission at all (and a
# file:// path would be rejected outright by the camera app).
# One line on purpose: buildozer splits this list on commas itself and has
# no line-continuation syntax, so a backslash would end up inside a permission
# name and quietly produce a broken <uses-permission> entry.
# Only INTERNET, and only normal (never runtime) permissions: the Camera button
# hands the job to the system camera app through an intent and the photo goes to
# MediaStore, so nothing here needs a dangerous permission on Android 10+ (a
# file:// path would be rejected outright, which is why MediaStore is used). A
# shorter list is one less thing for the manifest and the startup surface.
# The advanced "(name=...;maxSdkVersion=32)" syntax is supported by p4a, but it
# rewrites the generated manifest - exactly what must not move while phone
# reports are being chased.
android.permissions = INTERNET

# On-device OCR. python-for-android turns every entry here into an
# `implementation '<coordinate>'` line in the generated build.gradle, and its
# template already declares the `google()` repository, so this is the whole
# wiring - no gradle file to maintain. `text-recognition` is ML Kit Text
# Recognition v2 with the latin model *bundled* (its POM pulls
# text-recognition-bundled-common), so it reads a photo with no network and no
# Play Services model download, which is what a bill serial has to be scanned by
# in a shed. Version 16.0.0 was checked against Google's Maven index.
#
# If a build ever fails to resolve this, the app is not dead: main.py only hands
# an activity to the engine at scan time, and a missing class makes ocr_engine
# print "ML Kit not in this build" and fall back to EasyOCR/Tesseract on desktop.
android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.0

orientation = portrait
fullscreen = 0

# `android.enable_androidx` is switched on automatically for target API > 28.

[buildozer]
log_level = 2
warn_on_root = 0
