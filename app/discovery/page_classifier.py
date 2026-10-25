import re
from bs4 import BeautifulSoup

ECOMMERCE_TRANSACTION_PATTERNS = [
    re.compile(r"\b(?:add to cart|buy now|in stock|out of stock|free shipping|mrp|discount|add to basket|checkout)\b", re.IGNORECASE),
]

ECOMMERCE_PRICE_PATTERNS = [
    re.compile(r"(?:₹|\$|€|£|rs\.?|inr|usd)\s*[0-9,]+(?:\.[0-9]{2})?", re.IGNORECASE),
]

GENERIC_DIRECTORY_PATTERNS = [
    re.compile(r"\b(?:enquire\s+now|get\s+quotes?|service\s+providers?|business\s+listings?|local\s+services?|call\s+now|contact\s+details|verified\s+experts?|find\s+services?|service\s+experts?|business\s+profile|local\s+business|company\s+profile)\b", re.IGNORECASE),
    re.compile(r"\b(?:pest\s+control|packers\s+(?:and|&)\s+movers|catering\s+services|cleaning\s+services|home\s+services|plumbing|electrician|carpenter|interior\s+design|coaching|training\s+institutes?|consultant)\b", re.IGNORECASE),
    re.compile(r"\b(?:years?\s+in\s+business|serving\s+since|verified\s+partner|expert\s+reviews?|contact\s+person|working\s+hours|business\s+address|locality)\b", re.IGNORECASE),
]

GENERIC_OTHER_PATTERNS = [
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

    # Transactional intent is weighted higher than mere currency mentions
    ecom_tx_score = score_text_signals(page_text, ECOMMERCE_TRANSACTION_PATTERNS)
    ecom_price_score = min(score_text_signals(page_text, ECOMMERCE_PRICE_PATTERNS), 10)
    ecommerce_score = (ecom_tx_score * 3) + ecom_price_score

    gen_dir_score = score_text_signals(page_text, GENERIC_DIRECTORY_PATTERNS)
    gen_other_score = score_text_signals(page_text, GENERIC_OTHER_PATTERNS)
    generic_score = (gen_dir_score * 3) + (gen_other_score * 2)

    if generic_score > 0 and generic_score > ecommerce_score:
        return "generic_listing"
    elif ecommerce_score > 0:
        return "ecommerce"

    return "generic_listing"
