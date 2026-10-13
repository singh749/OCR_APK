# buildozer.spec - Android APK build configuration
# Generates Android APK after desktop testing
#
# Instructions:
# 1. Install Linux or WSL (Windows Subsystem for Linux)
# 2. Install Buildozer: pip install buildozer
# 3. Build APK: buildozer android debug

[app]
# Application title (displayed on device)
title = OCR Number Scanner

# Package name (unique identifier)
package.name = ocrscanner

# Package domain
package.domain = org.sonalikha

# Source code folder
source.dir = .

# Source files include
source.include_exts = py,png,jpg,kv,atlas,db

# Exclude directories
source.exclude_dirs = tests, bin, .buildozer

# App version
version = 1.0

# Dependencies
requirements = python3,kivy,opencv,numpy,pillow,easyocr

# Orientation
orientation = portrait

# Android specific
android.permissions = CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,INTERNET

# Android API level
android.api = 31
android.minapi = 24
# android.sdk = 31 (deprecated in new buildozer)
android.ndk = 25b
android.ndk_api = 24

# Android architecture
android.archs = arm64-v8a, armeabi-v7a

# Allow backup
android.allow_backup = True

# Icon (custom app icon path)
# icon.filename = %(source.dir)s/assets/icon.png

# Presplash
# presplash.filename = %(source.dir)s/assets/presplash.png
presplash_color = #070812

# Fullscreen
fullscreen = 0

[buildozer]
# Build log level
log_level = 2

# Disable root warning prompt (needed for Colab/Docker/CI)
warn_on_root = 0
