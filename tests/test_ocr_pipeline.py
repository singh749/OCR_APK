"""Offline tests for the OCR result pipeline, runnable in CI without Android.

`ocr_engine.extract_numbers` only ever runs on a device when a real OCR engine is
imported, which the APK cannot do and CI has no binary for - so the whole
"which string wins" layer (candidate generation, label stripping, scoring) was
never executed anywhere. These tests drive it with a stub engine, which keeps the
real code under test and only replaces Tesseract/EasyOCR themselves.

Run:  python3 tests/test_ocr_pipeline.py     (no third-party deps beyond cv2+numpy)
"""

from __future__ import annotations

import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def install_stub_engine(read_text):
    """Pretend pytesseract exists; `read_text(image)` returns what OCR 'sees'."""
    fake = types.ModuleType("pytesseract")

    class _Output:
        DICT = "dict"

    fake.Output = _Output
    fake.image_to_string = lambda image, config="": read_text(image)
    fake.image_to_data = lambda image, config="", output_type=None: [
        {"text": "U6234", "conf": "92"},
        {"text": "NO", "conf": "80"},
        {"text": "", "conf": "-1"},
    ]
    sys.modules["pytesseract"] = fake

    import ocr_engine

    ocr_engine.pytesseract = fake
    ocr_engine.HAS_TESSERACT = True
    ocr_engine.HAS_EASYOCR = False
    return ocr_engine


def make_image(tmp_path, text="NO U6234"):
    import cv2
    import numpy as np

    img = np.full((260, 660, 3), 240, np.uint8)
    cv2.putText(img, text, (28, 172), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (20, 20, 20), 7)
    path = os.path.join(tmp_path, "plate.png")
    cv2.imwrite(path, img)
    return path


PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print("  {:4s} {}{}".format("ok" if condition else "FAIL", label,
                                "" if not detail else "  -> " + str(detail)))


def main():
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ocr-test-")
    image = make_image(tmp)

    engine = install_stub_engine(lambda img: "NO U6234\n")
    result = engine.extract_numbers(image)

    print("extract_numbers('NO U6234') ->")
    for key in sorted(result):
        if key != "all_results":
            print("   {!r}: {!r}".format(key, result[key]))

    print("shape of the result the UI reads")
    check("keys the UI needs are all present",
          {"cleaned", "best_text", "confidence", "method", "alternatives",
           "all_results", "variant"} <= set(result), sorted(result))
    check("confidence is a number in 0..100",
          isinstance(result["confidence"], (int, float)) and 0 <= result["confidence"] <= 100,
          result["confidence"])
    check("all_results values have text+confidence",
          all({"text", "confidence"} <= set(v) for v in result["all_results"].values()))
    check("alternatives are strings only",
          all(isinstance(a, str) for a in result["alternatives"]))

    print("the number the user is shown")
    check("printed label 'NO' is not glued to the serial",
          result["cleaned"] == "U6234", result["cleaned"])
    check("no label-prefixed junk is offered as an alternative",
          all(a == "U6234" or not a.startswith("NO") for a in result["alternatives"]),
          result["alternatives"])
    check("method reports the engine, not the image filter",
          result["method"] == "Tesseract", result["method"])
    check("variant reports the image filter separately",
          bool(result.get("variant")), result.get("variant"))
    check("confidence is measured, not a hard-coded 65.0",
          result["confidence"] != 65.0, result["confidence"])

    print("degenerate reads must stay informative, never raise")
    empty = install_stub_engine(lambda img: "")
    got = empty.extract_numbers(image)
    check("no text at all -> empty cleaned value, no crash",
          got["cleaned"] == "" and got["method"] in ("", "Tesseract"), got["method"])
    check("every variant still reported", len(got["all_results"]) >= 6, len(got["all_results"]))

    only_label = install_stub_engine(lambda img: "NO\n")
    got2 = only_label.extract_numbers(image)
    check("a lone label word is not turned into a serial",
          not re.fullmatch(r"[A-Z]\d{3,6}", got2["cleaned"] or ""), got2["cleaned"])

    print("unit level")
    check("strip_label_prefix removes 'NO'",
          engine.strip_label_prefix("NOU6234") == "U6234", engine.strip_label_prefix("NOU6234"))
    check("strip_label_prefix leaves a real serial alone",
          engine.strip_label_prefix("U6234") == "U6234")
    check("strip_label_prefix does not eat a leading letter that is part of the number",
          engine.strip_label_prefix("NOW12345") == "NOW12345", engine.strip_label_prefix("NOW12345"))
    check("label removal on raw text keeps a spaced serial whole",
          engine.strip_label_tokens("CHASSIS NO. 1234567").strip() == "1234567",
          repr(engine.strip_label_tokens("CHASSIS NO. 1234567")))
    check("label removal on raw text leaves NOW12345 alone",
          engine.strip_label_tokens("NOW12345") == "NOW12345",
          repr(engine.strip_label_tokens("NOW12345")))

    print("end to end on an ambiguous reading")
    awkward = install_stub_engine(lambda img: "NOW12345\n")
    got_awk = awkward.extract_numbers(image)
    check("a serial that starts with N-O-W is not shortened",
          got_awk["cleaned"] == "NOW12345", got_awk["cleaned"])
    spaced = install_stub_engine(lambda img: "CHASSIS NO U6234\n")
    got_sp = spaced.extract_numbers(image)
    check("a longer label prefix is still removed",
          got_sp["cleaned"] == "U6234", got_sp["cleaned"])
    check("normalize handles None/empty without raising",
          engine.normalize_metal_serial("") == [""] and engine.normalize_metal_serial(None) == [""])
    check("serial shape scores higher than the glued form",
          engine.score_candidate("U6234") > engine.score_candidate("NOU6234"),
          (engine.score_candidate("U6234"), engine.score_candidate("NOU6234")))

    missing = os.path.join(tmp, "does-not-exist.png")
    got3 = engine.extract_numbers(missing)
    check("unreadable file -> empty result, no exception",
          got3["cleaned"] == "" and got3["confidence"] == 0.0)

    print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
