"""
OCR Bill Scanning Pipeline
BillWise - Rule-Based Household Bill Prioritization and Financial Risk Assessment System
"""

import os
import re
from datetime import datetime
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

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


AMOUNT_PATTERN = re.compile(
    r'(?:₱|PHP|P|Php|php)\s*([0-9]{1,3}(?:[,\s][0-9]{3})*(?:\.[0-9]{2})?)'
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


def extract_amount(text):
    amounts = []
    for match in AMOUNT_PATTERN.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        try:
            amounts.append(float(raw.replace(',', '').replace(' ', '')))
        except ValueError:
            continue
    return max(amounts) if amounts else None


def extract_due_date(text):
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
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:10]:
        alpha_count = sum(c.isalpha() for c in line)
        if alpha_count >= 3 and len(line) <= 60:
            return line
    return None


def scan_bill_image(file_obj):
    """Full pipeline: preprocess → OCR → extract fields."""
    image = Image.open(file_obj)
    processed = preprocess_image(image)
    raw_text = pytesseract.image_to_string(processed, lang='eng')

    return {
        'amount': extract_amount(raw_text),
        'due_date': extract_due_date(raw_text),
        'merchant': extract_merchant(raw_text),
        'raw_text': raw_text,
    }