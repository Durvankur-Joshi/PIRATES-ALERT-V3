import re
from bs4 import BeautifulSoup

ECOMMERCE_PATTERNS = [
    re.compile(r"(?:₹|\$|€|£|rs\.?|inr|usd)\s*[0-9,]+(?:\.[0-9]{2})?", re.IGNORECASE),
    re.compile(r"\b(?:add to cart|buy now|in stock|out of stock|free shipping|mrp|discount)\b", re.IGNORECASE),
]

GENERIC_PATTERNS = [
    re.compile(r"\b(?:apply now|job description|employment type|full-time|part-time|remote|salary|hiring|qualifications)\b", re.IGNORECASE),
    re.compile(r"\b(?:sq\s*ft|bhk|bedrooms?|bathrooms?|for rent|property type|real estate)\b", re.IGNORECASE),
    re.compile(r"\b(?:directory|profile|opening hours|business address|contact details)\b", re.IGNORECASE),
]


def score_text_signals(text: str, patterns: list[re.Pattern]) -> int:
    score = 0
    for pat in patterns:
        matches = pat.findall(text)
        score += len(matches)
    return score


def classify_page(html: str, structured_data_type: str | None = None, requested_type: str = "auto") -> str:
    if requested_type in ("ecommerce", "generic_listing"):
        return requested_type

    if structured_data_type in ("ecommerce", "generic_listing"):
        return structured_data_type

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    page_text = soup.get_text(separator=" ")

    ecommerce_score = score_text_signals(page_text, ECOMMERCE_PATTERNS)
    generic_score = score_text_signals(page_text, GENERIC_PATTERNS)

    if ecommerce_score > 0 and ecommerce_score >= generic_score:
        return "ecommerce"
    elif generic_score > 0:
        return "generic_listing"

    return "generic_listing"
