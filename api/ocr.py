"""
OCR Bill Scanning Pipeline
BillWise - Rule-Based Household Bill Prioritization and Financial Risk Assessment System

Supports:
  - Images (JPEG, PNG, WEBP, HEIC, BMP, TIFF)
  - PDFs. Digital PDFs have their text read directly (near-100% accurate);
    scanned PDFs are rendered with PyMuPDF and OCR'd (no Poppler needed).

What changed vs. the old version:
  1. Digital PDFs skip OCR entirely and read the embedded text.
  2. Photos are rotated upright using EXIF before OCR (phone photos were often sideways).
  3. Better preprocessing (upscale to ~2000px, autocontrast, binarize) and up to three
     OCR passes; the pass that yields the best result wins.
  4. Amount and due date are found by LABELS ("Total Amount Due", "Due Date"), not by
     "largest number" / "earliest date". Old logic is kept only as a fallback.
  5. Merchant matching uses whole words, so "account" no longer matches "co".

Public interface is unchanged: scan_bill_image(file_obj, is_pdf=False) returns a dict with
amount, due_date, merchant, raw_text (plus two extras: method, confidence).
"""

import io
import os
import re
from datetime import datetime

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

try:
    import pymupdf as fitz  # PyMuPDF: reads PDF text and renders pages, no Poppler needed
except ImportError:  # pragma: no cover
    fitz = None

try:  # optional: much better binarization for photos with shadows / uneven light
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


# ---- Tesseract path (Windows) ----
if os.name == 'nt':
    possible_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        r'C:\Users\jeeya\AppData\Local\Programs\Tesseract-OCR\tesseract.exe',
    ]
    for path in possible_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break


# ============================================================
# IMAGE PREPROCESSING
# ============================================================

TARGET_WIDTH = 2000   # Tesseract works best when text is ~30+ px tall
MAX_WIDTH = 3200      # don't feed it huge images (slow, no gain)


def _prepare_gray(image):
    """Upright, flatten transparency, grayscale, resize, autocontrast."""
    image = ImageOps.exif_transpose(image)  # phone photos carry rotation in EXIF

    if image.mode in ('RGBA', 'LA', 'P'):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, 'white')
        background.paste(rgba, mask=rgba.split()[-1])
        image = background

    gray = image.convert('L')

    width, height = gray.size
    if width < TARGET_WIDTH:
        scale = TARGET_WIDTH / width
        gray = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    elif width > MAX_WIDTH:
        scale = MAX_WIDTH / width
        gray = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    return ImageOps.autocontrast(gray, cutoff=1)


def _sharpen(gray):
    gray = ImageEnhance.Contrast(gray).enhance(1.3)
    return gray.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))


def _otsu_threshold(hist):
    """Pure-Python Otsu threshold from a 256-bin histogram."""
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = 0
    weight_b = 0
    best_var = 0
    threshold = 128
    for i, h in enumerate(hist):
        weight_b += h
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += i * h
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / weight_f
        var = weight_b * weight_f * (mean_b - mean_f) ** 2
        if var > best_var:
            best_var = var
            threshold = i
    return threshold


def _binarize(gray):
    """Black text on white. Adaptive (OpenCV) when available, else global Otsu."""
    if cv2 is not None and np is not None:
        arr = cv2.medianBlur(np.array(gray), 3)
        out = cv2.adaptiveThreshold(
            arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15
        )
        return Image.fromarray(out)

    smooth = gray.filter(ImageFilter.MedianFilter(3))
    threshold = _otsu_threshold(smooth.histogram())
    return smooth.point(lambda p: 255 if p > threshold else 0)


def _ocr(image, psm):
    """Run Tesseract; return (text with line breaks, mean word confidence 0-100)."""
    config = f'--oem 3 --psm {psm} -c preserve_interword_spaces=1'
    data = pytesseract.image_to_data(
        image, lang='eng', config=config, output_type=pytesseract.Output.DICT
    )

    lines = {}
    confidences = []
    for i, word in enumerate(data['text']):
        word = word.strip()
        if not word:
            continue
        try:
            conf = float(data['conf'][i])
        except (TypeError, ValueError):
            conf = -1
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        lines.setdefault(key, []).append(word)
        if conf >= 0:
            confidences.append(conf)

    text = '\n'.join(' '.join(words) for _, words in sorted(lines.items()))
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text, mean_conf


# ============================================================
# TEXT HELPERS
# ============================================================

def _lines(text):
    return [_fix_digits(ln.strip()) for ln in text.replace('\u00a0', ' ').splitlines() if ln.strip()]


# OCR often reads 0 as O/o and 1 as I/l inside numbers like "1,2O0.OO"
_FIXABLE_NUMBER = re.compile(r'(?<![A-Za-z0-9])[0-9OoIl,]*\d[0-9OoIl,]*\.[0-9OoIl]{2}(?![A-Za-z0-9])')
_DIGIT_FIX = str.maketrans('OoIl', '0011')


def _fix_digits(line):
    return _FIXABLE_NUMBER.sub(lambda m: m.group(0).translate(_DIGIT_FIX), line)


# ============================================================
# AMOUNT EXTRACTION
# ============================================================

# 1,234.56 | 1234.56 | 12.00   (always two decimals; skips percentages and negatives)
AMOUNT_RE = re.compile(r'(?<![\d.,\-])((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?![\d%])')

# (priority, pattern). Higher priority wins.
AMOUNT_KEYWORDS = [
    (100, re.compile(r'total\s+amount\s+due|amount\s+due|total\s+due|net\s+amount\s+due', re.I)),
    (95, re.compile(r'(?:please\s+)?pay\s+(?:this\s+)?amount|amount\s+payable|balance\s+due|amount\s+to\s+pay', re.I)),
    (80, re.compile(r'grand\s+total|total\s+amount|total\s+payable', re.I)),
    (60, re.compile(r'current\s+(?:charges|bill)|bill\s+amount', re.I)),
    (50, re.compile(r'\btotal\b', re.I)),
    (45, re.compile(r'\bamount\b', re.I)),
]

# Lines about these are NOT the amount to pay (unless a strong keyword is also on the line)
AMOUNT_NEGATIVE = re.compile(
    r'\b(previous|prev|subtotal|sub-total|vat|tax|discount|change|cash|tendered|late|penalt\w*|'
    r'surcharge|reading|kwh|rate|arrears|deposit|adjustment|credit|after)\b',
    re.I,
)

# Never the amount to pay now, even if the line also says "amount due"
# (e.g. "Amount due after due date", "Late payment charge")
AMOUNT_HARD_NEGATIVE = re.compile(r'\b(after|late|penalt\w*|surcharge|overdue)\b', re.I)

STRONG_AMOUNT_SCORE = 90


def _amounts_in(line):
    return [(m.start(), float(m.group(1).replace(',', ''))) for m in AMOUNT_RE.finditer(line)]


def _best_keyword(low_line):
    """Highest-priority amount keyword on the line -> (score, end_position)."""
    best = (0, 0)
    for score, pattern in AMOUNT_KEYWORDS:
        match = pattern.search(low_line)
        if match and score > best[0]:
            best = (score, match.end())
    return best


def extract_amount(text):
    """
    Find the amount to pay.

    1. Look for labelled amounts ("Total Amount Due ... 1,234.56"), on the same line or the
       next two lines. The strongest label wins; ties go to the larger value.
    2. Fallback: the largest amount that isn't on a subtotal/VAT/penalty line.
    """
    lines = _lines(text)
    best = None  # (score, value)

    for i, line in enumerate(lines):
        low = line.lower()
        score, kw_end = _best_keyword(low)
        if score == 0 or AMOUNT_HARD_NEGATIVE.search(low):
            continue
        if score < STRONG_AMOUNT_SCORE and AMOUNT_NEGATIVE.search(low):
            continue

        value = None
        same_line = [(pos, v) for pos, v in _amounts_in(line) if v > 0]
        after_label = [v for pos, v in same_line if pos >= kw_end]
        if after_label:
            value = after_label[0]
        elif same_line:
            value = same_line[-1][1]
        else:
            for nxt in lines[i + 1:i + 3]:
                if _best_keyword(nxt.lower())[0]:
                    break
                found = [v for _, v in _amounts_in(nxt) if v > 0]
                if found:
                    value = found[0]
                    break

        if value is None:
            continue
        if best is None or score > best[0] or (score == best[0] and value > best[1]):
            best = (score, value)

    if best:
        return best[1]

    everything, clean = [], []
    for line in lines:
        values = [v for _, v in _amounts_in(line) if v > 0]
        everything.extend(values)
        if not AMOUNT_NEGATIVE.search(line.lower()):
            clean.extend(values)
    pool = clean or everything
    return max(pool) if pool else None


# ============================================================
# DUE DATE EXTRACTION
# ============================================================

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_MON = (r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
        r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)')

DATE_MDY_TEXT = re.compile(rf'\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b', re.I)
DATE_DMY_TEXT = re.compile(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(\d{{4}})\b', re.I)
DATE_ISO = re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b')
DATE_NUMERIC = re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b')

DUE_STRONG = re.compile(
    r'due\s*date|date\s+due|pay(?:ment)?\s+(?:on\s+or\s+)?before|on\s+or\s+before|'
    r'payment\s+due|due\s+on|due\s+by|pay\s+by|settle\s+before',
    re.I,
)
DUE_WEAK = re.compile(r'\bdue\b', re.I)

# Dates on these lines are NOT the due date
DATE_NEGATIVE = re.compile(
    r'bill(?:ing)?\s+date|statement\s+date|invoice\s+date|date\s+(?:of\s+)?issue|issued|'
    r'reading|period|cut-?off|disconnect\w*|date\s+paid|paid\s+on|transaction\s+date|receipt\s+date',
    re.I,
)


def _make_date(year, month, day):
    if year < 100:
        year += 2000
    if not (2000 <= year <= 2100):
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _dates_in(line):
    """All dates on a line as [(position, datetime)], in reading order."""
    found = []

    for m in DATE_MDY_TEXT.finditer(line):
        d = _make_date(int(m.group(3)), MONTH_MAP[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            found.append((m.start(), d))

    for m in DATE_DMY_TEXT.finditer(line):
        d = _make_date(int(m.group(3)), MONTH_MAP[m.group(2).lower()[:3]], int(m.group(1)))
        if d:
            found.append((m.start(), d))

    for m in DATE_ISO.finditer(line):
        d = _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))

    for m in DATE_NUMERIC.finditer(line):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Philippine bills are usually MM/DD/YYYY; flip only when that's impossible.
        month, day = (b, a) if a > 12 and b <= 12 else (a, b)
        d = _make_date(y, month, day)
        if d:
            found.append((m.start(), d))

    return sorted(found, key=lambda item: item[0])


def _date_after_label(lines, pattern):
    """First date that belongs to a line matching `pattern` (same line or the next two)."""
    for i, line in enumerate(lines):
        low = line.lower()
        match = pattern.search(low)
        if not match:
            continue

        same = _dates_in(line)
        after = [d for pos, d in same if pos >= match.end()]
        if after:
            return after[0]
        if same:
            return same[-1][1]

        for nxt in lines[i + 1:i + 3]:
            dates = [d for _, d in _dates_in(nxt)]
            if not dates:
                continue
            # Header row like "Billing Date   Due Date" followed by a row of values:
            # pick the value in the same column position as the due-date label.
            labels = sorted(
                [(m.start(), 'due') for m in DUE_STRONG.finditer(low)]
                + [(m.start(), 'other') for m in DATE_NEGATIVE.finditer(low)]
            )
            if len(labels) > 1 and len(labels) == len(dates):
                due_index = next(idx for idx, (_, kind) in enumerate(labels) if kind == 'due')
                return dates[due_index]
            return dates[0]
    return None


def extract_due_date(text):
    """
    Find the due date, as YYYY-MM-DD.

    1. Date next to "Due Date" / "Pay on or before" / "Payment due" and similar labels.
    2. Date next to any line containing the word "due".
    3. Fallback: among dates NOT on billing/statement/reading lines, the earliest future
       date, else the most recent one.
    """
    lines = _lines(text)

    chosen = _date_after_label(lines, DUE_STRONG) or _date_after_label(lines, DUE_WEAK)
    if chosen:
        return chosen.strftime('%Y-%m-%d')

    clean, everything = [], []
    for line in lines:
        dates = [d for _, d in _dates_in(line)]
        everything.extend(dates)
        if not DATE_NEGATIVE.search(line.lower()):
            clean.extend(dates)

    pool = clean or everything
    if not pool:
        return None

    now = datetime.now()
    future = [d for d in pool if d >= now]
    chosen = min(future) if future else max(pool)
    return chosen.strftime('%Y-%m-%d')


# ============================================================
# MERCHANT EXTRACTION
# ============================================================

# Words that are almost never a real merchant name
GENERIC_HEADERS = {
    'receipt', 'invoice', 'statement', 'bill', 'official', 'summary', 'notice',
    'tax', 'billing', 'page', 'original', 'duplicate', 'copy', 'customer', 'account',
}

# Whole-word hints that a line is a company name
COMPANY_HINT_RE = re.compile(
    r'\b(?:corporation|corp|inc|incorporated|company|co|ltd|llc|pbc|enterprises?|holdings|'
    r'group|cooperative|coop|electric|telecom|communications|utilities|water\s+district|'
    r'meralco|maynilad|pldt|globe|converge|cepalco|moresco|smart|manila\s+water|sky|dito)\b',
    re.I,
)

# Lines that describe the customer or contact details, not the merchant
MERCHANT_SKIP_RE = re.compile(
    r'bill\s+to|customer|account\s*(?:no|number|#)|invoice\s*(?:no|number|#)|receipt\s*(?:no|number|#)|'
    r'tel\b|phone|www\.|http|@|address|attn|total|amount|date',
    re.I,
)


def extract_merchant(text):
    """
    Identify the biller/merchant name.

    1. A line near the top that looks like a company name (whole-word match).
    2. Otherwise the first alphabetic line that isn't a generic header or customer detail.
    """
    lines = _lines(text)
    if not lines:
        return None

    for line in lines[:15]:
        if len(line) > 80 or MERCHANT_SKIP_RE.search(line):
            continue
        if COMPANY_HINT_RE.search(line):
            return line

    for line in lines[:10]:
        lower = line.lower().rstrip(':').strip()
        alpha_count = sum(c.isalpha() for c in line)
        digit_count = sum(c.isdigit() for c in line)
        if alpha_count < 3 or len(line) > 60 or digit_count > alpha_count:
            continue
        if MERCHANT_SKIP_RE.search(line):
            continue
        if lower in GENERIC_HEADERS or any(lower.startswith(h) for h in GENERIC_HEADERS):
            continue
        return line

    return None


# ============================================================
# PDF HANDLING
# ============================================================

def _pdf_text(file_obj, max_pages=2):
    """Embedded text of a digital PDF (empty string for scans or if PyMuPDF is missing)."""
    if fitz is None:
        return ''
    file_obj.seek(0)
    doc = fitz.open(stream=file_obj.read(), filetype='pdf')
    try:
        pages = min(doc.page_count, max_pages)
        return '\n'.join(doc.load_page(i).get_text('text', sort=True) for i in range(pages))
    finally:
        doc.close()


def pdf_first_page_to_image(file_obj, dpi=300):
    """Render the first page of a PDF into a PIL Image (for scanned PDFs)."""
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF is required to scan PDF receipts. Install it with `pip install pymupdf`."
        )

    file_obj.seek(0)
    doc = fitz.open(stream=file_obj.read(), filetype='pdf')
    try:
        if doc.page_count == 0:
            raise ValueError('The PDF has no pages.')

        page = doc.load_page(0)
        zoom = dpi / 72  # PDF points are 72 per inch
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes('png')))
    finally:
        doc.close()


def _has_real_text(text):
    return len(re.sub(r'\s', '', text or '')) >= 40


# ============================================================
# PIPELINE
# ============================================================

def _extract_fields(text):
    return {
        'amount': extract_amount(text),
        'due_date': extract_due_date(text),
        'merchant': extract_merchant(text),
    }


def _ocr_best(image):
    """
    OCR the image up to three ways and keep the best result.
    Stops early once the first pass is clearly good.
    """
    gray = _prepare_gray(image)

    attempts = [
        lambda: (_sharpen(gray), 6),   # grayscale, uniform text block
        lambda: (_binarize(gray), 6),  # black/white, helps with shadows and noise
        lambda: (gray, 4),             # single column of varying sizes
    ]

    best = None  # (score, text, confidence, fields)
    for make in attempts:
        prepared, psm = make()
        text, confidence = _ocr(prepared, psm)
        fields = _extract_fields(text)

        score = confidence
        score += 15 if fields['amount'] is not None else 0
        score += 15 if fields['due_date'] else 0
        score += 5 if fields['merchant'] else 0

        if best is None or score > best[0]:
            best = (score, text, confidence, fields)

        if confidence >= 85 and fields['amount'] is not None and fields['due_date']:
            break

    _, text, confidence, fields = best
    return text, confidence, fields


def scan_bill_image(file_obj, is_pdf=False):
    """
    Full pipeline: (read PDF text | preprocess + OCR) -> extract fields.

    Returns:
        dict with keys: amount, due_date, merchant, raw_text, method, confidence
        method is 'pdf_text' or 'ocr'; confidence is the OCR confidence (100 for pdf_text).
    """
    if is_pdf:
        text = _pdf_text(file_obj)
        if _has_real_text(text):
            fields = _extract_fields(text)
            if fields['amount'] is not None:
                return {**fields, 'raw_text': text, 'method': 'pdf_text', 'confidence': 100.0}

            # Text layer exists but has no readable amount (e.g. amounts are images): try OCR too.
            ocr_text, confidence, ocr_fields = _ocr_best(pdf_first_page_to_image(file_obj))
            merged = {k: fields[k] if fields[k] is not None else ocr_fields[k] for k in fields}
            return {**merged, 'raw_text': text + '\n' + ocr_text, 'method': 'pdf_text+ocr',
                    'confidence': confidence}

        image = pdf_first_page_to_image(file_obj)
    else:
        file_obj.seek(0)
        image = Image.open(file_obj)

    text, confidence, fields = _ocr_best(image)
    return {**fields, 'raw_text': text, 'method': 'ocr', 'confidence': round(confidence, 1)}