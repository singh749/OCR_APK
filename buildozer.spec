[app]
title = OCR Metal Scanner
package.name = ocrscanner
package.domain = org.metalocr

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

# Requirements for Android (Tesseract instead of EasyOCR)
requirements = python3,kivy,opencv,numpy,Pillow,sqlite3,pyjnius,pytesseract,tesseract

# Android specific permissions
android.permissions = CAMERA,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,INTERNET

# Android API and NDK versions
android.api = 33
android.minapi = 21
android.ndk_api = 33
android.ndk = 25b

android.accept_sdk_license = True
android.enable_androidx = True

orientation = portrait
fullscreen = 0
log_level = 2
warn_on_root = 0

[buildozer]
warn_on_root = 0
