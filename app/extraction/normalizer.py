import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

CURRENCY_SYMBOLS: dict[str, str] = {
    "₹": "INR",
    "rs.": "INR",
    "rs": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "cny": "CNY",
    "cad": "CAD",
    "c$": "CAD",
    "aud": "AUD",
    "a$": "AUD",
}

DISCOUNT_KEYWORDS = ("off", "discount", "save", "%", "cashback", "coupon")
EMI_KEYWORDS = ("emi", "installment", "installments", "per month", "/mo", "/month", "starts at", "starting at")
PHONE_PATTERNS = [
    re.compile(r"^\+?[0-9]{1,4}?[-.\s]?\(?[0-9]{1,3}?\)?[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{3,4}$"),
    re.compile(r"^[0-9]{10,12}$"),
]


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text)
    return cleaned.strip()


def normalize_url(base_url: str, link: str | None) -> str | None:
    if not link:
        return None
    trimmed = link.strip()
    if not trimmed or trimmed.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None

    full_url = urljoin(base_url, trimmed)
    parsed = urlparse(full_url)
    if parsed.scheme not in ("http", "https"):
        return None

    cleaned_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
    return cleaned_url


def is_likely_phone_number(text: str) -> bool:
    cleaned = re.sub(r"[\s\-\(\)\.]", "", text)
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.isdigit() and len(cleaned) in (10, 11, 12):
        return True
    return False


def is_emi_string(text: str) -> bool:
    lower = text.lower()
    return bool(re.search(r"\b(?:emi|installments?|per\s+month|/mo|/month|starts\s+at)\b", lower))


def is_discount_only_string(text: str) -> bool:
    lower = text.lower().strip()
    if re.fullmatch(r"\(?\s*\d+(?:\.\d+)?%\s*(?:off|discount)\s*\)?", lower):
        return True
    if lower.startswith("save ") or lower.startswith("coupon"):
        return True
    if "off" in lower and not any(curr in lower for curr in ("₹", "rs", "inr", "$", "€", "£")):
        return True
    return False


def is_discount_string(text: str) -> bool:
    return is_discount_only_string(text)


WORD_CURRENCY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:inr|rs\.?|rupees?)\b", re.IGNORECASE), "INR"),
    (re.compile(r"\b(?:usd|dollars?)\b", re.IGNORECASE), "USD"),
    (re.compile(r"\b(?:eur|euros?)\b", re.IGNORECASE), "EUR"),
    (re.compile(r"\b(?:gbp|pounds?)\b", re.IGNORECASE), "GBP"),
    (re.compile(r"\b(?:aud)\b", re.IGNORECASE), "AUD"),
    (re.compile(r"\b(?:cad)\b", re.IGNORECASE), "CAD"),
    (re.compile(r"\b(?:jpy|yen)\b", re.IGNORECASE), "JPY"),
    (re.compile(r"\b(?:cny|yuan)\b", re.IGNORECASE), "CNY"),
]

SPEC_UNIT_PATTERN = re.compile(
    r"^(?:w|watt|watts|kg|g|gm|ghz|mhz|hz|inch|inches|in|\"|cm|mm|mp|megapixel|mah|gb|tb|mb|kb|spf|rpm|v|volt|volts|pieces?|pcs|units?|stars?|ratings?|reviews?|%|off|discount)\b",
    re.IGNORECASE,
)


def detect_currency(text: str) -> str | None:
    # First check distinct symbol currencies
    for sym in ("₹", "$", "€", "£", "¥", "c$", "a$", "us$"):
        if sym in text.lower():
            return CURRENCY_SYMBOLS.get(sym, "USD")

    # Match alphabetic currency codes only with strict word boundaries
    for pat, code in WORD_CURRENCY_PATTERNS:
        if pat.search(text):
            return code
    return None


def parse_numeric_price(price_str: str, currency_hint: str | None = None) -> float | None:
    clean = re.sub(r"[^\d.,]", "", price_str).strip()
    if not clean:
        return None
    clean = clean.rstrip(".,")
    if not clean:
        return None

    if "," in clean and "." in clean:
        last_comma = clean.rfind(",")
        last_dot = clean.rfind(".")
        if last_dot > last_comma:
            clean = clean.replace(",", "")
        else:
            clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        parts = clean.split(",")
        is_rupee = (currency_hint or "").upper() in ("INR", "₹", "RS", "RS.")
        if is_rupee or len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            clean = clean.replace(",", "")
        elif len(parts) == 2 and len(parts[-1]) in (1, 2) and (currency_hint or "").upper() in ("EUR", "€"):
            clean = parts[0] + "." + parts[1]
        elif len(parts) == 2 and len(parts[-1]) in (1, 2) and not is_rupee:
            clean = parts[0] + "." + parts[1]
        else:
            clean = clean.replace(",", "")
    elif "." in clean:
        parts = clean.split(".")
        if len(parts) > 2:
            clean = clean.replace(".", "")
        elif len(parts) == 2 and len(parts[1]) == 3 and (currency_hint or "").upper() in ("EUR", "€"):
            clean = clean.replace(".", "")

    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


PRICE_NUMBER_PATTERN = (
    r"(?:"
    r"[0-9]{1,2}(?:,[0-9]{2})+,\s*[0-9]{3}(?:\.[0-9]{1,2})?"
    r"|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?"
    r"|[0-9]{1,3}(?:\.[0-9]{3})+,\s*[0-9]{1,2}"
    r"|[0-9]+(?:\.[0-9]{1,2})?"
    r"|[0-9]+,\s*[0-9]{2}"
    r")"
)


def extract_price_and_currency(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None

    clean = clean_text(text)
    if is_likely_phone_number(clean) or is_emi_string(clean) or is_discount_only_string(clean):
        return None, None

    currency = detect_currency(clean)

    price_match = re.search(
        rf"(?:(?:₹|Rs\.?|INR|\$|USD|€|EUR|£|GBP|CAD|AUD|C\$|A\$|¥|JPY|CNY)\s*)?({PRICE_NUMBER_PATTERN})(?:\s*(?:/-|INR|USD|EUR|GBP|CAD|AUD))?",
        clean,
        re.IGNORECASE,
    )

    if not price_match:
        return None, currency

    candidate_number = price_match.group(1)

    # Exclude candidate number if it is immediately followed by a specification unit
    # and lacks preceding currency evidence
    after_candidate = clean[price_match.end(1):].strip()
    preceding_text = clean[:price_match.start(1)]
    has_preceding_currency = bool(
        re.search(
            r"(?:₹|Rs\.?|INR|\$|USD|€|EUR|£|GBP|CAD|AUD|C\$|A\$|¥|JPY|CNY)\s*$",
            preceding_text,
            re.IGNORECASE,
        )
    )

    if not has_preceding_currency and SPEC_UNIT_PATTERN.match(after_candidate):
        return None, None

    # Exclude candidate number if preceded by specification prefix (SPF, pack of, size, etc.) without currency
    if not has_preceding_currency and re.search(r"\b(?:spf|pack(?:\s+of)?|size|rating|vol|volume|v)\s*$", preceding_text, re.IGNORECASE):
        return None, None

    if not currency and not any(marker in clean.lower() for marker in ("price", "mrp", "/-", "sale", "rs", "₹", "$", "€", "£")):
        return None, None

    price = parse_numeric_price(candidate_number, currency_hint=currency)
    return price, currency


def parse_rating(text: str | None) -> float | None:
    if not text:
        return None
    clean = clean_text(text)

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:out of|\/|von|de)\s*5(?:\s*stars?)?", clean, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            return val if 0.0 <= val <= 5.0 else None
        except ValueError:
            pass

    match_star = re.search(r"(\d+(?:\.\d+)?)\s*(?:★|(?:stars?|star)\b)", clean, re.IGNORECASE)
    if match_star:
        try:
            val = float(match_star.group(1))
            if 0.0 <= val <= 5.0:
                return val
            if 5.0 < val <= 10.0:
                return round(val / 2.0, 1)
        except ValueError:
            pass

    match_rated = re.search(r"(?:rating|rated)\s*[:\-]?\s*(\d+(?:\.\d+)?)", clean, re.IGNORECASE)
    if match_rated:
        try:
            val = float(match_rated.group(1))
            if 0.0 <= val <= 5.0:
                return val
            if 5.0 < val <= 10.0:
                return round(val / 2.0, 1)
        except ValueError:
            pass

    if re.fullmatch(r"[0-5](?:\.\d+)?", clean.strip()):
        try:
            val = float(clean.strip())
            return val if 0.0 <= val <= 5.0 else None
        except ValueError:
            pass

    return None


REVIEW_NUM_REGEX = r"(?:[0-9]{1,2}(?:,[0-9]{2})+,\s*[0-9]{3}|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"


def parse_review_count(text: str | None) -> int | None:
    if not text:
        return None
    clean = clean_text(text)

    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\b\s*(?:reviews?|ratings?|votes?)?", clean, re.IGNORECASE)
    if k_match:
        try:
            return int(float(k_match.group(1)) * 1000)
        except ValueError:
            pass

    parentheses_match = re.search(
        rf"\(\s*({REVIEW_NUM_REGEX})\s*(?:reviews?|ratings?|votes?)?\s*\)",
        clean,
        re.IGNORECASE,
    )
    if parentheses_match:
        digits = parentheses_match.group(1).replace(",", "").replace(" ", "")
        try:
            return int(digits)
        except ValueError:
            pass

    keyword_match = re.search(
        rf"\b({REVIEW_NUM_REGEX})\s*(?:customer\s+)?(?:reviews?|ratings?|votes?)\b",
        clean,
        re.IGNORECASE,
    )
    if keyword_match:
        digits = keyword_match.group(1).replace(",", "").replace(" ", "")
        try:
            return int(digits)
        except ValueError:
            pass

    exact_num = re.fullmatch(rf"\(?\s*({REVIEW_NUM_REGEX})\s*\)?", clean)
    if exact_num:
        digits = exact_num.group(1).replace(",", "").replace(" ", "")
        try:
            return int(digits)
        except ValueError:
            pass

    return None


def deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_identifiers: set[str] = set()
    unique_records: list[dict[str, Any]] = []

    for item in records:
        url = item.get("url")
        product_id = item.get("productId") or item.get("product_id")
        title = item.get("title", "")

        identifier = url or (f"{product_id}::{title}" if product_id else f"{title}")
        if not identifier or identifier in seen_identifiers:
            continue

        seen_identifiers.add(identifier)
        unique_records.append(item)

    return unique_records
