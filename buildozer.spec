[app]
title = OCR Metal Scanner
package.name = ocrscanner
package.domain = org.metalocr

# Source directory where the main python live
source.dir = .

# List of dependencies to include for Android
# Note: 'torch' and 'easyocr' are extremely heavy on Android. 
requirements = python3,kivy,opencv,numpy,Pillow,easyocr,sqlite3,pyjnius

# Android specific permissions
android.permissions = CAMERA,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,INTERNET

# Android API and NDK versions
android.api = 33
android.minapi = 21
android.ndk_api = 33
android.ndk = 25b

# Use the modern AndroidX and Java 17
android.accept_sdk_license = True
android.enable_androidx = True

# Orientation (Portrait for mobile scanning)
orientation = portrait

# Fullscreen mode
fullscreen = 0

# Buildozer logging
log_level = 2
warn_on_root = 0

[buildozer]
warn_on_root = 0
