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


def _options_case(case, calls):
    """The ML Kit options class, in the shape the given case claims it has."""
    class Builder:
        def build(self):
            calls["builder"] = calls.get("builder", 0) + 1
            return "latin"

    if case == "no-default-options":
        return type("TextRecognizerOptions", (), {"Builder": staticmethod(Builder)})
    return type("TextRecognizerOptions", (), {"DEFAULT_OPTIONS": "latin",
                                             "Builder": staticmethod(Builder)})


def install_fake_jnius(case):
    """Pretend this process is an APK that has ML Kit.

    Injects a `jnius` (and `android`) module whose `autoclass()` returns stubs, so
    the code under test stays the real thing: the getter probing that survives ML
    Kit renames, the flat-text fallback, the close-the-client rule, and the promise
    that anything failing in Java returns None instead of raising.

    `case` picks the shape of the Java side: "tree" (blocks + lines +
    confidences), "flat-only" (a Text that only has getText()), "no-bridge" (no
    jnius at all, i.e. an APK built without the Gradle dependency) and
    "java-throws" (the recognizer itself blows up).
    """
    import types

    calls = {"process": 0, "close": 0}
    saved = {name: sys.modules.get(name) for name in ("android", "jnius")}

    if case == "no-bridge":
        # None in sys.modules is CPython's "this import must fail", so the result
        # does not depend on whether pyjnius happens to be installed in the runner.
        sys.modules["android"] = None
        sys.modules["jnius"] = None

        return {"calls": calls, "undo": lambda: _restore(sys.modules, saved)}

    class JList:
        def __init__(self, items):
            self._items = list(items)

        def size(self):
            return len(self._items)

        def get(self, index):
            return self._items[index]

    class Line:
        def __init__(self, text, conf):
            self._text, self._conf = text, conf

        def getText(self):
            return self._text

        def getConfidence(self):
            return self._conf

    class Block:
        def __init__(self, lines):
            self._lines = lines

        def getLines(self):
            return JList(self._lines)

    class Text:
        def __init__(self, blocks, flat):
            self._blocks, self._flat = blocks, flat

        def getTextBlocks(self):
            if self._blocks is None:
                raise AttributeError("getTextBlocks")
            return JList(self._blocks)

        def getText(self):
            return self._flat

    if case in ("tree", "no-default-options"):
        result = Text([Block([Line("U6234", 0.91), Line("CHASSIS NO", 0.55)])],
                      "U6234\nCHASSIS NO")
    else:
        # An older/newer ML Kit whose result object only promises getText().
        result = Text(None, "U6234\nCHASSIS NO")

    class Client:
        def process(self, image):
            calls["process"] += 1
            if case == "java-throws":
                raise RuntimeError("java.lang.IllegalStateException: closed")
            return "task"

        def close(self):
            calls["close"] += 1

    client = Client()

    def await_task(task, timeout, unit):
        return result

    classes = {
        "java.io.File": lambda path: ("file:" + path),
        "android.net.Uri": type("Uri", (), {"fromFile": staticmethod(lambda f: f)}),
        "com.google.mlkit.vision.text.InputImage": type(
            "InputImage", (),
            {"fromFilePath": staticmethod(lambda ctx, uri: uri)}),
        "com.google.mlkit.vision.text.TextRecognition": type(
            "TextRecognition", (), {"getClient": staticmethod(lambda opts: client)}),
        "com.google.mlkit.vision.text.latin.TextRecognizerOptions": _options_case(
            case, calls),
        "java.util.concurrent.TimeUnit": type("TimeUnit", (), {"SECONDS": "s"}),
    }

    def autoclass(name):
        if name not in classes:
            raise RuntimeError("java.lang.ClassNotFoundException: " + name)
        return classes[name]

    tasks = type("Tasks", (), {})
    setattr(tasks, "await", staticmethod(await_task))
    classes["com.google.android.gms.tasks.Tasks"] = tasks

    jnius = types.ModuleType("jnius")
    jnius.autoclass = autoclass
    jnius.JavaException = RuntimeError
    sys.modules["android"] = types.ModuleType("android")
    sys.modules["jnius"] = jnius
    # autoclass() above is looked up by name at call time, so the entry added
    # after the classes dict was built still resolves.
    return {"calls": calls, "undo": lambda: _restore(sys.modules, saved)}


def _restore(mapping, saved):
    for name, value in saved.items():
        if value is None:
            mapping.pop(name, None)
        else:
            mapping[name] = value



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

    # --- on-device engine (ML Kit) ------------------------------------------------
    # ML Kit is Java, so nothing about it can run here for real: pyjnius cannot be
    # installed and there is no classloader. What *is* testable - and what broke in
    # the past - is the Python side: the getter probing that keeps this working
    # across ML Kit versions, the flat-text fallback, and the promise that a missing
    # or failing engine degrades instead of raising into the UI.
    print("\nML Kit bridge (jnius stubbed, so the parsing and fallbacks are real):")
    for case in ("tree", "flat-only", "no-default-options", "no-bridge", "java-throws"):
        state = install_fake_jnius(case)
        engine.ANDROID = True   # read_with_mlkit trusts this flag, nothing else
        try:
            if case == "no-bridge":
                check("a build without ML Kit gives up quietly",
                      engine.read_with_mlkit(image, object()) is None)
                check("and the desktop engine still answers",
                      engine.extract_numbers(image, context=object())["method"] == "Tesseract")
            elif case == "java-throws":
                check("an ML Kit failure does not reach the UI",
                      engine.read_with_mlkit(image, object()) is None)
            elif case == "no-default-options":
                lines = engine.read_with_mlkit(image, object())
                check("a build with only TextRecognizerOptions.Builder() still works",
                      [t for t, _c in lines] == ["U6234", "CHASSIS NO"], lines)
                check("and it used the Builder", state["calls"].get("builder") == 1,
                      state["calls"])
            elif case == "flat-only":
                lines = engine.read_with_mlkit(image, object())
                check("a Text with no block getters still yields lines",
                      [t for t, _c in lines] == ["U6234", "CHASSIS NO"], lines)
                check("and no confidence is invented for them",
                      all(c is None for _t, c in lines), lines)
            else:
                lines = engine.read_with_mlkit(image, object())
                check("blocks and lines are flattened with confidences",
                      lines == [("U6234", 0.91), ("CHASSIS NO", 0.55)], lines)
                picked = engine.extract_numbers(image, context=object())
                check("the on-device engine wins over tesseract",
                      picked["method"] == "ML Kit", picked["method"])
                check("and the serial is the one that is kept",
                      picked["cleaned"] == "U6234", picked["cleaned"])
                check("the recognizer is closed once per call, not leaked",
                      state["calls"]["close"] == state["calls"]["process"] >= 1,
                      state["calls"])
        finally:
            engine.ANDROID = False
            state["undo"]()

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
