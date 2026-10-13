"""
Install Script - Install all required dependencies
Run once to prepare the environment
"""

import subprocess
import sys
import os

print("=" * 60)
print("  OCR Scanner App - Dependency Installer")
print("=" * 60)

packages = [
    # Kivy - UI framework (PC + Android)
    ("kivy[base]", "kivy"),
    ("kivy_deps.sdl2", None),   # For Windows
    ("kivy_deps.glew", None),   # For Windows
    # Image processing
    ("opencv-python", "cv2"),
    ("numpy", "numpy"),
    ("Pillow", "PIL"),
    # OCR engine - Auto download, Android compatible
    ("easyocr", "easyocr"),
    # NOTE: Tesseract excluded - requires manual binary install and incompatible with Android packaging
]

def install(package_name):
    """Install a Python package via pip"""
    print(f"\n[INSTALL] {package_name}...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  OK: {package_name}")
        return True
    else:
        print(f"  ERROR: {package_name}")
        print(f"  {result.stderr[-200:]}")
        return False

failed = []
for pkg, import_name in packages:
    if import_name:
        try:
            __import__(import_name)
            print(f"[SKIP] {pkg} - already installed")
            continue
        except ImportError:
            pass
    
    success = install(pkg)
    if not success:
        failed.append(pkg)

print("\n" + "=" * 60)

if failed:
    print("SOME PACKAGES FAILED TO INSTALL:")
    for f in failed:
        print(f"  - {f}")
    print("\nPlease try installing via requirements.txt:")
    print("  pip install -r requirements.txt")
else:
    print("All packages installed successfully!")

print("\nOn first run, EasyOCR will automatically download recognition models (~100MB).")
print("Internet is required only for initial model download; subsequent runs are fully offline.")
print("\nTo install via requirements file:")
print("  pip install -r requirements.txt")
print("\nTo start the application:")
print("  python main.py")
print("="* 60)
