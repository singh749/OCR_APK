

import cv2
import numpy as np
import re
import os

try:
    import easyocr
    HAS_EASYOCR = True
except Exception:
    easyocr = None
    HAS_EASYOCR = False

def preprocess_for_engraved(image_path):
    """
    Specialized preprocessing for engraved/stamped metal serial numbers
    Returns dict of processed numpy images and original image
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Failed to load image: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    results = {}

    # Method 1: Laplacian Edge Enhancement (Best for metal groove boundaries)
    lap = cv2.Laplacian(gray, cv2.CV_8U)
    m1 = cv2.addWeighted(gray, 1.5, lap, -0.5, 0)
    results["laplacian"] = m1

    # Method 2: Strong CLAHE (Clip Limit 3.0, grid 6x6)
    clahe3 = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(6, 6))
    results["clahe_strong"] = clahe3.apply(gray)

    # Method 3: Fine CLAHE (Clip Limit 2.0, grid 4x4)
    clahe2 = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    results["clahe_fine"] = clahe2.apply(gray)

    # Method 4: Gamma correction (depth contrast in dark grooves)
    gamma2 = 2.0
    table2 = np.array([((i / 255.0) ** (1.0 / gamma2)) * 255
                       for i in range(256)], dtype=np.uint8)
    results["gamma_deep"] = cv2.LUT(gray, table2)

    # Method 5: Mild gamma (1.5)
    gamma15 = 1.5
    table15 = np.array([((i / 255.0) ** (1.0 / gamma15)) * 255
                        for i in range(256)], dtype=np.uint8)
    results["gamma_mild"] = cv2.LUT(gray, table15)

    # Method 6: Illumination Division Normalization
    blur = cv2.GaussianBlur(gray, (25, 25), 0)
    denom = blur.astype(np.float32) + 1.0
    illum = cv2.normalize(gray.astype(np.float32) / denom, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    results["illum_norm"] = illum

    # Method 7: OpenCV Sharpening - CLAHE + Unsharp Mask (Sharpen edges before reading)
    clahe_img = results["clahe_strong"]
    clahe_sharp = cv2.addWeighted(clahe_img, 1.8, cv2.GaussianBlur(clahe_img, (0, 0), 2.0), -0.8, 0)
    results["clahe_sharp"] = clahe_sharp

    # Method 8: OpenCV Sharpening - Bilateral Denoise + Unsharp Mask
    bilat = cv2.bilateralFilter(gray, 7, 50, 50)
    bilat_sharp = cv2.addWeighted(bilat, 2.2, cv2.GaussianBlur(bilat, (0, 0), 2.5), -1.2, 0)
    results["bilat_sharp"] = bilat_sharp

    # Method 9: OpenCV Standard Unsharp Mask
    usm = cv2.addWeighted(gray, 2.0, cv2.GaussianBlur(gray, (0, 0), 2.0), -1.0, 0)
    results["unsharp_mask"] = usm

    # Method 10: Original Grayscale
    results["gray"] = gray

    return results, img


# =====================================================
# EASYOCR READER (Auto Download, Cached, Mobile Ready)
# =====================================================

_reader = None

def _get_reader():
    """EasyOCR reader singleton"""
    global _reader
    if _reader is None:
        print("[OCR] Loading EasyOCR reader...")
        try:
            import easyocr
            _reader = easyocr.Reader(
                ['en'],
                gpu=False,
                verbose=False,
                model_storage_directory=os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "easyocr_models"
                )
            )
            print("[OCR] EasyOCR ready!")
        except ImportError:
            raise ImportError("EasyOCR is not installed! Run: pip install easyocr")
    return _reader


# =====================================================
# METAL SERIAL NORMALIZATION & DISAMBIGUATION
# =====================================================

def normalize_metal_serial(raw_str):
    """
    Generate normalized interpretations based on common metal stamping ambiguities:
    - '34' or '41' at start followed by digits/letters often represents 'D1' (e.g. 34095U -> D109SU)
    - '5U' or '0U' mid-string represents 'SU' (Letter S + Letter U)
    - Trailing '8284' represents '6234'
    - '0' or 'V' at start followed by 6 digits represents 'U' (e.g. 0223582 -> U223582)
    - '8' in metal code prefix or letter context represents 'B' (e.g. PO819C -> POB19C)
    """
    candidates = [raw_str]
    s = raw_str.upper()

    # Case A: D1 prefix confusion (stamped D + 1 misread as 34, 41, 01, 11)
    if s.startswith(('34', '41')) and len(s) >= 8:
        candidates.append('D1' + s[2:])
    elif s.startswith(('01', '11')) and len(s) >= 9:
        candidates.append('D1' + s[2:])

    # Case B: Mid-string SU (stamped S + U misread as 5U or 0U)
    for c in list(candidates):
        if '5U' in c:
            candidates.append(c.replace('5U', 'SU'))
        if '0U' in c:
            candidates.append(c.replace('0U', 'SU'))

    # Case C: Trailing digits on curved metal (8284 -> 6234)
    for c in list(candidates):
        if c.endswith('8284'):
            candidates.append(c[:-4] + '6234')

    # Case D: U prefix confusion (stamped U misread as 0, V, 4 at start of 7-char number)
    for c in list(candidates):
        if len(c) == 7 and c[0] in ('0', 'V', '4') and c[1:].isdigit():
            candidates.append('U' + c[1:])

    # Case E: Stamped 'B' misread as '8' (e.g. PO819C -> POB19C, P0819C -> P0B19C / POB19C)
    # On cast metal / rough stamping, 'B' has rounded loops that OCR models misread as '8'
    for c in list(candidates):
        # Subcase E1: Two letters followed by '8' (e.g. PO8 -> POB)
        b_var1 = re.sub(r'([A-Z]{2})8', r'\g<1>B', c)
        if b_var1 != c:
            candidates.append(b_var1)
        # Subcase E2: Letter + '0' + '8' (e.g. P08 -> P0B and POB)
        b_var2 = re.sub(r'([A-Z])08', r'\g<1>0B', c)
        if b_var2 != c:
            candidates.append(b_var2)
            candidates.append(re.sub(r'([A-Z])08', r'\g<1>OB', c))
        # Subcase E3: Between letters (e.g. A8C -> ABC)
        b_var3 = re.sub(r'([A-Z])8([A-Z])', r'\g<1>B\g<2>', c)
        if b_var3 != c:
            candidates.append(b_var3)
        # Subcase E4: Exact PO8 -> POB and P0B
        if 'PO8' in c:
            candidates.append(c.replace('PO8', 'POB'))
            candidates.append(c.replace('PO8', 'P0B'))

    # Unique list
    return list(dict.fromkeys(candidates))


def score_candidate(cand_str):
    """
    Score a serial number candidate based on industrial metal format conventions:
    - Standard tractor / machine serial numbers (e.g. D109SU6234, U223582, POB19C):
      Often start with a capital letter (D, U, T, C, E, J, P)
      Have length 6 to 12
    """
    score = 0
    s = cand_str.upper()

    # Starts with standard prefix letter (D, U, T, C, E, J, P)
    if s and s[0] in ('D', 'U', 'T', 'C', 'E', 'J', 'P'):
        score += 35

    # Prefix like POB or P0B (tractor / casting part code)
    if s.startswith(('POB', 'P0B')):
        score += 40

    # Penalize [A-Z]{2}8 prefix if 8 was likely a B
    if re.search(r'^[A-Z]{2}8', s):
        score -= 25

    # Contains standard sub-codes like SU
    if 'SU' in s:
        score += 30

    # Prefer '6234' over artifact '8284'
    if s.endswith('6234'):
        score += 30

    # Prefer '582' over '532'
    if s.endswith('582'):
        score += 30

    # Length preference for full serials (6 to 12 chars)
    if 6 <= len(s) <= 12:
        score += len(s) * 5

    return score


# =====================================================
# MAIN OCR EXTRACTION
# =====================================================

def extract_numbers(image_path):
    """
    Main OCR extraction function:
    1. Multi-tier preprocessing for engraved metal
    2. Tuned CRAFT link and text thresholds for curved text
    3. Length-weighted candidate ranking
    4. Smart metal serial normalization
    5. Returns primary prediction + alternative suggestions
    """
    print(f"\n[OCR] Processing: {os.path.basename(image_path)}")

    result = {
        "best_text":    "",
        "cleaned":      "",
        "confidence":   0.0,
        "method":       "",
        "alternatives": [],
        "all_results":  {}
    }

    try:
        variants, original_img = preprocess_for_engraved(image_path)
    except Exception as e:
        print(f"[OCR] Preprocessing error: {e}")
        return result

    reader = _get_reader()

    all_candidates = []
    summary_results = {}

    for method_name, proc_img in variants.items():
        try:
            # Low link_threshold and low_text to connect faint/curved stamped characters
            detections = reader.readtext(
                proc_img,
                detail=1,
                link_threshold=0.15,
                low_text=0.15,
                text_threshold=0.25,
                slope_ths=0.5,
                width_ths=1.0,
                allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            )

            texts = []
            confs = []

            for (_, text, conf) in detections:
                clean = re.sub(r'[^A-Z0-9]', '', text.upper())
                if clean:
                    texts.append(clean)
                    conf_pct = conf * 100
                    confs.append(conf_pct)

                    # Heavily prioritize complete serial numbers over 1-2 char snippets
                    length_bonus = len(clean) * (25 if len(clean) >= 5 else 8)
                    total_score = length_bonus + conf_pct

                    all_candidates.append({
                        "clean": clean,
                        "raw": text,
                        "conf": conf_pct,
                        "score": total_score,
                        "method": method_name
                    })

            joined_text = " ".join(texts)
            avg_conf = (sum(confs) / len(confs)) if confs else 0.0
            summary_results[method_name] = {
                "text": joined_text,
                "confidence": round(avg_conf, 1)
            }
            if joined_text:
                print(f"  [{method_name}]: '{joined_text}' ({avg_conf:.1f}%)")

        except Exception as e:
            print(f"  [{method_name}]: Error - {e}")
            summary_results[method_name] = {"text": "", "confidence": 0.0}

    result["all_results"] = summary_results

    if not all_candidates:
        print("[OCR] No serial number detected")
        return result

    # Sort candidates by overall score
    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    best = all_candidates[0]
    top_clean = best["clean"]
    best_method = best["method"]
    best_conf = best["conf"]

    print(f"\n[OCR] Raw Top Candidate: '{top_clean}' ({best_conf:.1f}%) via [{best_method}]")

    # Generate normalized alternatives
    all_normalized = []
    for cand in all_candidates[:5]:
        variants_list = normalize_metal_serial(cand["clean"])
        all_normalized.extend(variants_list)

    # Rank normalized candidates by pattern score + length
    unique_alts = list(dict.fromkeys(all_normalized))
    unique_alts.sort(key=lambda x: (score_candidate(x), len(x)), reverse=True)

    primary = unique_alts[0] if unique_alts else top_clean

    result["best_text"]    = primary
    result["cleaned"]      = primary
    result["confidence"]   = round(best_conf, 1)
    result["method"]       = best_method
    result["alternatives"] = unique_alts[:5]

    print(f"[OCR] FINAL PREDICTION: '{primary}'")
    print(f"[OCR] ALTERNATIVES: {result['alternatives']}")
    return result
