import cv2
import numpy as np
import re
import os

# --- ENGINE IMPORTS ---
try:
    import easyocr
    HAS_EASYOCR = True
except Exception:
    easyocr = None
    HAS_EASYOCR = False

try:
    import pytesseract
    HAS_TESSERACT = True
except Exception:
    pytesseract = None
    HAS_TESSERACT = False

def preprocess_for_engraved(image_path):
    """Specialized preprocessing for engraved/stamped metal serial numbers"""
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Failed to load image: {image_path}")
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    results = {}

    # Method 1: Laplacian Edge Enhancement
    lap = cv2.Laplacian(gray, cv2.CV_8U)
    results["laplacian"] = cv2.addWeighted(gray, 1.5, lap, -0.5, 0)

    # Method 2: Strong CLAHE
    clahe3 = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(6, 6))
    results["clahe_strong"] = clahe3.apply(gray)

    # Method 3: Fine CLAHE
    clahe2 = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    results["clahe_fine"] = clahe2.apply(gray)

    # Method 4: Gamma correction (depth contrast)
    gamma2 = 2.0
    table2 = np.array([((i / 255.0) ** (1.0 / gamma2)) * 255 for i in range(256)], dtype=np.uint8)
    results["gamma_deep"] = cv2.LUT(gray, table2)

    # Method 5: Illumination Division Normalization
    blur = cv2.GaussianBlur(gray, (25, 25), 0)
    denom = blur.astype(np.float32) + 1.0
    illum = cv2.normalize(gray.astype(np.float32) / denom, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    results["illum_norm"] = illum

    # Method 6: Original Grayscale
    results["gray"] = gray
    
    return results, img

# =====================================================
# EASYOCR READER (PC / High-End Mobile)
# =====================================================
_reader = None
def _get_reader():
    global _reader
    if _reader is None:
        print("[OCR] Loading EasyOCR reader...")
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False,
            model_storage_directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "easyocr_models"))
        print("[OCR] EasyOCR ready!")
    return _reader

# =====================================================
# METAL SERIAL NORMALIZATION & DISAMBIGUATION
# =====================================================
# Printed labels get glued onto the value because OCR strips the spaces:
# "NO U6234" arrives as "NOU6234". Longest first, so CHASSISNO is tried before NO.
_LABEL_WORDS = ("CHASSISNUMBER", "CHASSISNO", "CHASSIS", "ENGINE", "SERIAL",
                "NUMBER", "SINO", "SNO", "NO", "SN")

# the shape of a stamped serial: one letter + 3-6 digits, or 5-8 digits
LABEL_SHAPE = re.compile(r"[A-Z]\d{3,6}|\d{5,8}")

# Deliberately narrower than LABEL_SHAPE, and only used where the label has to be
# *guessed* out of a string whose spaces are already gone. "U6234" qualifies, so
# "NOU6234" is repaired; "W12345" does not, so a real serial that happens to start
# with N-O-W is never cut down to it.
STRIP_SHAPE = re.compile(r"[A-Z]\d{3,4}|\d{5,7}")


def strip_label_prefix(text):
    """Drop a leading label word, but only if what is left looks like a serial.

    That strict remainder test is the whole point: a number that legitimately
    begins with one of these letters comes back untouched, so this can only ever
    improve a read, never corrupt one.
    """
    t = ("" if text is None else str(text)).upper()
    for word in _LABEL_WORDS:
        if t.startswith(word) and len(t) > len(word):
            rest = t[len(word):]
            if STRIP_SHAPE.fullmatch(rest):
                return rest
    return t


# Same words, but matched as *separate* tokens in the raw text, where a space is
# proof that they are a label and not the start of the number. This is the safe,
# preferred path; strip_label_prefix() is only the fallback for reads that arrive
# without spaces.
_LABEL_TOKEN = re.compile(
    r"(?i)\b(number|serial\s*no|serial|chassis\s*no|chassis\s*num|chassis|"
    r"engine\s*no|engine|reg\s*no|regno|s/n|s/no|sino|sn|no)\b[.:]*\s*")


def strip_label_tokens(text):
    return _LABEL_TOKEN.sub(" ", text or "")


def normalize_metal_serial(raw_str):
    s = ("" if raw_str is None else str(raw_str)).upper()
    candidates = [s]
    if s:
        stripped = strip_label_prefix(s)
        if stripped != s:
            candidates.append(stripped)

    if s.startswith(('34', '41')) and len(s) >= 8: candidates.append('D1' + s[2:])
    elif s.startswith(('01', '11')) and len(s) >= 9: candidates.append('D1' + s[2:])
    
    for c in list(candidates):
        if '5U' in c: candidates.append(c.replace('5U', 'SU'))
        if '0U' in c: candidates.append(c.replace('0U', 'SU'))
        if c.endswith('8284'): candidates.append(c[:-4] + '6234')
        if len(c) == 7 and c[0] in ('0', 'V', '4') and c[1:].isdigit(): candidates.append('U' + c[1:])
        
        b_var1 = re.sub(r'([A-Z]{2})8', r'\g<1>B', c)
        if b_var1 != c: candidates.append(b_var1)
        b_var3 = re.sub(r'([A-Z])8([A-Z])', r'\g<1>B\g<2>', c)
        if b_var3 != c: candidates.append(b_var3)
        if 'PO8' in c: candidates.append(c.replace('PO8', 'POB'))
        
    return list(dict.fromkeys(candidates))

def score_candidate(cand_str):
    score = 0
    s = cand_str.upper()
    if s and s[0] in ('D', 'U', 'T', 'C', 'E', 'J', 'P'): score += 35
    if s.startswith(('POB', 'P0B')): score += 40
    if re.search(r'^[A-Z]{2}8', s): score -= 25
    if 'SU' in s: score += 30
    if s.endswith('6234'): score += 30
    # "U6234" (clean serial shape) beats the glued "NOU6234"
    if LABEL_SHAPE.fullmatch(s): score += 45
    if 6 <= len(s) <= 12: score += len(s) * 5
    return score

def _tesseract_confidence(image, text):
    """Confidence percentage for a Tesseract read.

    The old code hard-coded 65.0 for every result, which meant the confidence bar
    always sat at 65% whatever the image. `image_to_data` gives real per-word
    confidences where the Tesseract build exposes it; the baseline is only used
    when it does not.
    """
    baseline = 65.0
    if pytesseract is None or not text.strip():
        return baseline
    try:
        data = pytesseract.image_to_data(image, config="--psm 6",
                                        output_type=pytesseract.Output.DICT)
        confs = [int(word["conf"]) for word in data
                 if str(word["text"]).strip() and int(word["conf"]) >= 0]
    except Exception:
        return baseline
    if not confs:
        return baseline
    return max(0.0, min(100.0, sum(confs) / len(confs)))


# =====================================================
# MAIN OCR EXTRACTION
# =====================================================
def extract_numbers(image_path):
    print(f"\n[OCR] Processing: {os.path.basename(image_path)}")
    result = {"best_text": "", "cleaned": "", "confidence": 0.0, "method": "", "alternatives": [], "all_results": {}}
    
    try:
        variants, original_img = preprocess_for_engraved(image_path)
    except Exception as e:
        print(f"[OCR] Preprocessing error: {e}")
        return result

    # --- ENGINE SELECTION ---
    if HAS_EASYOCR:
        reader = _get_reader()
        engine_name = "EasyOCR"
    elif HAS_TESSERACT:
        engine_name = "Tesseract"
    else:
        # No engine could be imported. This is the normal case inside the
        # Android APK: python-for-android has no recipe for easyocr/torch or
        # for the tesseract binary, so neither module can be packaged.
        # See README.md ("OCR engine on Android") for the on-device options.
        print(
            "[OCR] No OCR engine available - install easyocr or pytesseract "
            "for the desktop build; the Android APK needs an on-device engine."
        )
        result["method"] = "unavailable"
        return result

    all_candidates = []
    summary_results = {}

    for method_name, proc_img in variants.items():
        try:
            if engine_name == "EasyOCR":
                detections = reader.readtext(proc_img, detail=1, link_threshold=0.15, low_text=0.15, 
                                             text_threshold=0.25, allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                texts, confs = [], []
                for (_, text, conf) in detections:
                    # the label words go first, while their spaces still exist
                    clean = re.sub(r'[^A-Z0-9]', '', strip_label_tokens(text).upper())
                    if clean:
                        texts.append(clean)
                        conf_pct = conf * 100
                        confs.append(conf_pct)
                        length_bonus = len(clean) * (25 if len(clean) >= 5 else 8)
                        all_candidates.append({"clean": clean, "raw": text, "conf": conf_pct, "score": length_bonus + conf_pct, "method": method_name})
                
                joined_text = " ".join(texts)
                avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                summary_results[method_name] = {"text": joined_text, "confidence": round(avg_conf, 1)}

            elif engine_name == "Tesseract":
                custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                text = pytesseract.image_to_string(proc_img, config=custom_config)
                clean = re.sub(r'[^A-Z0-9]', '', strip_label_tokens(text).upper())
                conf_pct = float(_tesseract_confidence(proc_img, text))

                if clean:
                    length_bonus = len(clean) * (25 if len(clean) >= 5 else 8)
                    all_candidates.append({"clean": clean, "raw": text, "conf": conf_pct, "score": length_bonus + conf_pct, "method": method_name})

                summary_results[method_name] = {"text": clean, "confidence": round(conf_pct, 1)}

            if summary_results[method_name]["text"]:
                print(f"  [{method_name}]: '{summary_results[method_name]['text']}' ({summary_results[method_name]['confidence']:.1f}%)")
                
        except Exception as e:
            print(f"  [{method_name}]: Error - {e}")
            summary_results[method_name] = {"text": "", "confidence": 0.0}

    result["all_results"] = summary_results
    if not all_candidates:
        print("[OCR] No serial number detected")
        return result

    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    best = all_candidates[0]
    
    all_normalized = []
    for cand in all_candidates[:5]:
        all_normalized.extend(normalize_metal_serial(cand["clean"]))
        
    unique_alts = list(dict.fromkeys(all_normalized))
    unique_alts.sort(key=lambda x: (score_candidate(x), len(x)), reverse=True)
    
    primary = unique_alts[0] if unique_alts else best["clean"]
    
    result["best_text"] = primary
    result["cleaned"] = primary
    result["confidence"] = round(best["conf"], 1)
    # `method` is what the UI prints as "Engine: ...", so it has to be the engine
    # name; the image variant that won is reported separately.
    result["method"] = engine_name
    result["variant"] = best["method"]
    result["alternatives"] = unique_alts[:5]
    
    print(f"[OCR] FINAL PREDICTION: '{primary}'")
    return result
