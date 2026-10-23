from typing import Any
from urllib.parse import urlparse

BOILERPLATE_TITLES = {
    "home",
    "about",
    "about us",
    "contact",
    "contact us",
    "privacy policy",
    "terms of use",
    "terms of service",
    "terms & conditions",
    "sign in",
    "sign up",
    "log in",
    "login",
    "cart",
    "checkout",
    "view cart",
    "my account",
    "careers",
    "help",
    "help center",
    "faqs",
    "faq",
    "cookie policy",
    "sitemap",
    "feedback",
    "follow us",
    "next",
    "previous",
    "more",
    "read more",
    "view all",
    "view",
    "view details",
    "details",
    "buy",
    "buy now",
    "order now",
    "shop now",
    "click here",
    "see more",
    "pdf",
    "apply",
    "apply now",
    "download",
    "submit",
    "get started",
    "learn more",
    "more info",
    "explore",
}


def is_valid_url_string(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_boilerplate_title(title: str) -> bool:
    cleaned = title.strip().lower()
    if len(cleaned) < 3:
        return True
    return cleaned in BOILERPLATE_TITLES


def validate_single_record(record: dict[str, Any], page_type: str) -> tuple[bool, str | None]:
    title = (record.get("title") or "").strip()
    if not title:
        return False, "Missing or empty title"

    if is_boilerplate_title(title):
        return False, f"Title appears to be navigation or boilerplate: '{title}'"

    url = record.get("url")
    if not is_valid_url_string(url):
        return False, f"Invalid or missing record URL: '{url}'"

    image_url = record.get("imageUrl") or record.get("image_url")
    if image_url and not is_valid_url_string(image_url):
        record["imageUrl"] = None
        record["image_url"] = None

    selling_price = record.get("sellingPrice") if "sellingPrice" in record else record.get("selling_price")
    if selling_price is not None and selling_price <= 0:
        record["sellingPrice"] = None
        record["selling_price"] = None

    mrp = record.get("mrp")
    if mrp is not None and mrp <= 0:
        record["mrp"] = None

    rating = record.get("rating")
    if rating is not None and not (0.0 <= rating <= 10.0):
        record["rating"] = None

    review_count = record.get("reviewCount") if "reviewCount" in record else record.get("review_count")
    if review_count is not None and review_count < 0:
        record["reviewCount"] = None
        record["review_count"] = None

    if page_type == "ecommerce":
        has_price = selling_price is not None
        has_image = bool(image_url)
        has_sku = bool(record.get("productId") or record.get("product_id"))
        if not (has_price or has_image or has_sku):
            return False, "E-commerce record missing price, image, and product ID"

    return True, None


def validate_extracted_records(
    records: list[dict[str, Any]], page_type: str
) -> tuple[bool, str, list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if not records:
        return False, "failed", [], ["No candidate listing records were discovered"]

    valid_records: list[dict[str, Any]] = []
    dropped_count = 0

    for item in records:
        is_valid, reason = validate_single_record(item, page_type)
        if is_valid:
            valid_records.append(item)
        else:
            dropped_count += 1
            if reason and len(warnings) < 10:
                warnings.append(reason)

    if not valid_records:
        warnings.append("All candidate records failed validation criteria")
        return False, "failed", [], warnings

    if dropped_count > 0:
        warnings.append(f"{dropped_count} candidate records dropped during validation")
        status = "partial"
    else:
        status = "passed"

    return True, status, valid_records, warnings
