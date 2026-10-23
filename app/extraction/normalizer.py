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

DISCOUNT_KEYWORDS = ("off", "discount", "save", "%")
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


def is_discount_string(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in DISCOUNT_KEYWORDS)


def detect_currency(text: str) -> str | None:
    lower = text.lower()
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in lower:
            return code
    return None


def parse_numeric_price(price_str: str) -> float | None:
    clean = re.sub(r"[^\d.,]", "", price_str).strip()
    if not clean:
        return None

    if "," in clean and "." in clean:
        if clean.rfind(",") > clean.rfind("."):
            clean = clean.replace(".", "").replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif "," in clean:
        parts = clean.split(",")
        if len(parts[-1]) == 2:
            clean = "".join(parts[:-1]) + "." + parts[-1]
        else:
            clean = clean.replace(",", "")

    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


def extract_price_and_currency(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None

    clean = clean_text(text)
    if is_likely_phone_number(clean) or is_discount_string(clean):
        return None, None

    currency = detect_currency(clean)

    price_match = re.search(
        r"(?:(?:₹|Rs\.?|INR|\$|USD|€|EUR|£|GBP|CAD|AUD)\s*)?([0-9]{1,3}(?:[,\.][0-9]{3})*(?:[,\.][0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)(?:\s*(?:/-|INR|USD|EUR|GBP))?",
        clean,
        re.IGNORECASE,
    )

    if not price_match:
        return None, currency

    candidate_number = price_match.group(1)
    if not currency and not any(marker in clean.lower() for marker in ("price", "mrp", "/-", "sale")):
        return None, None

    price = parse_numeric_price(candidate_number)
    return price, currency


def parse_rating(text: str | None) -> float | None:
    if not text:
        return None
    clean = clean_text(text)

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:out of|\/)\s*5", clean, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            return val if 0.0 <= val <= 5.0 else None
        except ValueError:
            pass

    match_star = re.search(r"(\d+(?:\.\d+)?)\s*(?:★|stars?|star|rating)?", clean, re.IGNORECASE)
    if match_star:
        try:
            val = float(match_star.group(1))
            if 0.0 <= val <= 5.0:
                return val
            if 5.0 < val <= 10.0:
                return round(val / 2.0, 1)
        except ValueError:
            pass

    return None


def parse_review_count(text: str | None) -> int | None:
    if not text:
        return None
    clean = clean_text(text)

    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\s*(?:reviews?|ratings?|votes?)?", clean, re.IGNORECASE)
    if k_match:
        try:
            return int(float(k_match.group(1)) * 1000)
        except ValueError:
            pass

    parentheses_match = re.search(r"\(\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*(?:reviews?|ratings?|votes?)?\s*\)", clean, re.IGNORECASE)
    if parentheses_match:
        digits = parentheses_match.group(1).replace(",", "")
        try:
            return int(digits)
        except ValueError:
            pass

    keyword_match = re.search(r"\b([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*(?:reviews?|ratings?|votes?)\b", clean, re.IGNORECASE)
    if keyword_match:
        digits = keyword_match.group(1).replace(",", "")
        try:
            return int(digits)
        except ValueError:
            pass

    exact_num = re.fullmatch(r"\(?\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*\)?", clean)
    if exact_num:
        digits = exact_num.group(1).replace(",", "")
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
