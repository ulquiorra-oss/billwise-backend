"""
OCR Bill Scanning Pipeline
BillWise - Rule-Based Household Bill Prioritization and Financial Risk Assessment System

Supports:
  - Images (JPEG, PNG, WEBP, HEIC, BMP, TIFF)
  - PDFs (first page is rendered via PyMuPDF — no Poppler needed)
"""

import io
import os
import re
from datetime import datetime
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

try:
    import pymupdf as fitz  # PyMuPDF — renders PDF pages to images, no external Poppler needed
except ImportError:  # pragma: no cover
    fitz = None


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

def preprocess_image(image):
    """Grayscale, upscale small images, boost contrast, sharpen."""
    image = image.convert('L')

    width, height = image.size
    if width < 1000:
        scale = 1000 / width
        image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = image.filter(ImageFilter.SHARPEN)
    return image


# ============================================================
# EXTRACTION PATTERNS
# ============================================================

# Matches: ₱1,234.56 | PHP 1,234.56 | 1,234.56 | P 1234.56 | $1,234.56
AMOUNT_PATTERN = re.compile(
    r'(?:₱|PHP|P|Php|php|\$|USD)\s*([0-9]{1,3}(?:[,\s][0-9]{3})*(?:\.[0-9]{2})?)'
    r'|(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})(?![0-9])',
    re.IGNORECASE
)

DATE_PATTERNS = [
    re.compile(r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})', re.IGNORECASE),
    re.compile(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})'),
    re.compile(r'(\d{4})-(\d{2})-(\d{2})'),
]

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# Words that are almost never a real merchant name — used to skip past them
GENERIC_HEADERS = {
    'receipt', 'invoice', 'statement', 'bill', 'official',
    'summary', 'notice', 'tax', 'billing',
}

# Words that strongly suggest a merchant/company name
COMPANY_HINTS = (
    'corporation', 'corp', 'inc', 'inc.', 'company', 'co.', 'co',
    'ltd', 'llc', 'enterprise', 'holdings', 'group',
    'meralco', 'maynilad', 'pldt', 'globe', 'converge', 'cepalco',
    'moresco', 'smart', 'manila water', 'metro', 'electric', 'water',
)


# ============================================================
# FIELD EXTRACTORS
# ============================================================

def extract_amount(text):
    """Return the largest currency-like value found in the text."""
    amounts = []
    for match in AMOUNT_PATTERN.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        try:
            cleaned = raw.replace(',', '').replace(' ', '')
            amounts.append(float(cleaned))
        except ValueError:
            continue
    return max(amounts) if amounts else None


def extract_due_date(text):
    """Return the earliest future date found, else the earliest overall, as YYYY-MM-DD."""
    candidates = []

    for match in DATE_PATTERNS[0].finditer(text):
        month_str = match.group(0).split()[0].rstrip('.').lower()[:3]
        day = int(match.group(1))
        year = int(match.group(2))
        if month_str in MONTH_MAP:
            try:
                candidates.append(datetime(year, MONTH_MAP[month_str], day))
            except ValueError:
                pass

    for match in DATE_PATTERNS[1].finditer(text):
        month, day, year = map(int, match.groups())
        try:
            candidates.append(datetime(year, month, day))
        except ValueError:
            pass

    for match in DATE_PATTERNS[2].finditer(text):
        year, month, day = map(int, match.groups())
        try:
            candidates.append(datetime(year, month, day))
        except ValueError:
            pass

    if not candidates:
        return None

    now = datetime.now()
    future = [d for d in candidates if d >= now]
    chosen = min(future) if future else min(candidates)
    return chosen.strftime('%Y-%m-%d')


def extract_merchant(text):
    """
    Try to identify the biller/merchant name.

    Strategy:
      1. Prefer lines that look like a company name ("Meralco", "PLDT Inc", etc.)
      2. Otherwise, take the first alphabetic line that isn't a generic header
         ("Receipt", "Invoice", "Statement", ...).
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    # 1. Company-name hints first
    for line in lines[:15]:
        lower = line.lower()
        if any(hint in lower for hint in COMPANY_HINTS) and len(line) <= 80:
            return line

    # 2. First non-generic line
    for line in lines[:10]:
        lower = line.lower().rstrip(':').strip()
        alpha_count = sum(c.isalpha() for c in line)
        if alpha_count < 3 or len(line) > 60:
            continue
        # Skip "Receipt", "Invoice", etc. (or "Receipt No. XXXX")
        if lower in GENERIC_HEADERS:
            continue
        if any(lower.startswith(h) for h in GENERIC_HEADERS):
            continue
        return line

    return None


# ============================================================
# PDF HANDLING
# ============================================================

def pdf_first_page_to_image(file_obj, dpi=200):
    """
    Render the first page of a PDF into a PIL Image so it can go through
    the same OCR pipeline as a photographed bill.
    """
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


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def scan_bill_image(file_obj, is_pdf=False):
    """
    Full pipeline: preprocess -> OCR -> extract fields.

    Accepts a photographed bill (JPEG/PNG/etc.) or, when is_pdf=True,
    a PDF receipt — the first page of the PDF is scanned.

    Returns:
        dict with keys: amount, due_date, merchant, raw_text
    """
    image = pdf_first_page_to_image(file_obj) if is_pdf else Image.open(file_obj)
    processed = preprocess_image(image)
    raw_text = pytesseract.image_to_string(processed, lang='eng')

    return {
        'amount': extract_amount(raw_text),
        'due_date': extract_due_date(raw_text),
        'merchant': extract_merchant(raw_text),
        'raw_text': raw_text,
    }