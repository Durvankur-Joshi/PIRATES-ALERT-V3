import re
from dataclasses import asdict, dataclass
from typing import Any, Optional
from app.extraction.normalizer import deduplicate_records
from app.extraction.validator import validate_extracted_records

KNOWN_STANDALONE_BRANDS = {
    "apple", "dell", "lenovo", "hp", "asus", "acer", "samsung", "sony", "lg",
    "nike", "adidas", "puma", "boat", "noise", "realme", "redmi", "xiaomi",
    "oneplus", "motorola", "oppo", "vivo", "panasonic", "philips", "toshiba"
}

GENERIC_UI_TITLES = {
    "sponsored", "results", "best seller", "deal of the day", "options: 2 sizes",
    "view options", "add to cart", "buy now", "out of stock", "in stock",
    "free delivery", "amazon's choice", "brands related to your search"
}


def is_brand_only_or_ui_title(title: str | None) -> bool:
    if not title:
        return True
    clean = title.strip().lower()
    if clean in GENERIC_UI_TITLES:
        return True
    if clean in KNOWN_STANDALONE_BRANDS:
        return True
    words = clean.split()
    if len(words) == 1 and (clean in KNOWN_STANDALONE_BRANDS or len(clean) < 4):
        return True
    return False


def is_suspicious_price(record: dict[str, Any]) -> tuple[bool, str | None]:
    price = record.get("sellingPrice") if "sellingPrice" in record else record.get("selling_price")
    if price is None:
        return False, None

    if price <= 0:
        return True, "Price is zero or negative"

    title = str(record.get("title") or "").lower()
    extra_dict = record.get("extra") or {}
    extra_str = str(extra_dict).lower()
    combined_text = f"{title} {extra_str}"

    # Suspicious rating collision: price matches rating exactly
    rating = record.get("rating")
    if rating is not None and abs(price - float(rating)) < 0.05:
        return True, f"Price {price} matches product rating {rating}"

    # Single-digit integer price on consumer electronics / major goods
    if price in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0):
        if any(
            kw in title
            for kw in (
                "laptop", "tv", "television", "phone", "smartphone", "led",
                "refrigerator", "monitor", "tablet", "camera", "pro", "intel",
                "core", "ryzen", "inch", "soundbar", "audio", "speaker"
            )
        ):
            return True, f"Single-digit price {price} is implausible for consumer electronics"

    # Screen size collision: price matches common screen diagonal in inches present in title
    for size in (11.6, 13.3, 14.0, 15.6, 16.0, 17.3, 24.0, 27.0, 32.0, 40.0, 43.0, 50.0, 55.0, 65.0, 75.0, 85.0):
        if abs(price - size) < 0.05 and (f"{size}" in title or f"{int(size)}" in title):
            if re.search(r'\b(?:inch(?:es)?|cm|centimeters?|diagonal|screen)\b|"', title):
                return True, f"Price {price} matches screen size specification in title"

    # Weight collision: price matches weight in kg
    if re.search(rf"\b{re.escape(str(price))}\s*kg\b", combined_text) or re.search(rf"\b{re.escape(str(price))}kg\b", combined_text):
        return True, f"Price {price} matches weight specification"

    # SPF collision: price matches SPF value (e.g. SPF 50, SPF 30)
    int_price = int(price) if price.is_integer() else price
    if re.search(rf"\bspf\s*{re.escape(str(int_price))}\b", combined_text):
        return True, f"Price {price} matches SPF specification"

    # Percentage collision: price matches discount percentage (e.g. "80% off")
    if re.search(rf"\b{re.escape(str(int_price))}\s*%\b", combined_text):
        return True, f"Price {price} matches discount percentage"

    return False, None


def inspect_listing_field_quality(records: list[dict[str, Any]], page_type: str) -> tuple[bool, list[str]]:
    if not records:
        return False, ["No records to inspect"]

    warnings: list[str] = []
    total = len(records)

    suspicious_price_count = 0
    missing_price_count = 0
    brand_only_title_count = 0
    valid_price_count = 0

    for r in records:
        is_susp, reason = is_suspicious_price(r)
        price = r.get("sellingPrice") if "sellingPrice" in r else r.get("selling_price")
        if is_susp:
            suspicious_price_count += 1
            if reason and len(warnings) < 3:
                warnings.append(reason)
        elif price is not None and price > 0:
            valid_price_count += 1
        else:
            missing_price_count += 1

        title = str(r.get("title") or "")
        if is_brand_only_or_ui_title(title):
            brand_only_title_count += 1

    if page_type == "ecommerce":
        if suspicious_price_count > 0:
            warnings.append(
                f"Material field failure: Detected {suspicious_price_count} suspicious price(s) derived from specifications or ratings"
            )
            return False, warnings

        bad_price_rate = (suspicious_price_count + missing_price_count) / total
        if bad_price_rate >= 0.20:
            warnings.append(
                f"Material field failure: {bad_price_rate:.0%} of e-commerce records have missing or suspicious prices"
            )
            return False, warnings

        if brand_only_title_count / total >= 0.50:
            warnings.append(
                f"Material field failure: {brand_only_title_count / total:.0%} of records have brand-only or UI titles"
            )
            return False, warnings

        if valid_price_count == 0:
            warnings.append("Material field failure: No valid prices found for e-commerce listing")
            return False, warnings

    return True, warnings


@dataclass
class QualityReport:
    is_acceptable: bool
    quality_score: float
    record_count: int
    candidate_count: int
    valid_rate: float
    duplicate_rate: float
    contamination_rate: float
    field_coverage: dict[str, float]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_extraction_quality(
    raw_records: list[dict[str, Any]],
    page_type: str,
    total_candidates: Optional[int] = None,
    min_quality_score: float = 0.60,
    max_duplicate_ratio: float = 0.50,
) -> tuple[QualityReport, list[dict[str, Any]]]:
    warnings: list[str] = []
    candidate_count = total_candidates if total_candidates is not None else len(raw_records)

    if candidate_count == 0 or not raw_records:
        rep = QualityReport(
            is_acceptable=False,
            quality_score=0.0,
            record_count=0,
            candidate_count=0,
            valid_rate=0.0,
            duplicate_rate=0.0,
            contamination_rate=1.0,
            field_coverage={},
            warnings=["No candidate records found to evaluate"],
        )
        return rep, []

    deduped = deduplicate_records(raw_records)
    duplicate_count = max(0, candidate_count - len(deduped))
    duplicate_rate = duplicate_count / candidate_count if candidate_count > 0 else 0.0

    is_valid, val_status, valid_records, val_warnings = validate_extracted_records(
        deduped, page_type
    )
    warnings.extend(val_warnings)

    valid_count = len(valid_records)
    valid_rate = valid_count / candidate_count if candidate_count > 0 else 0.0
    dropped_count = candidate_count - valid_count
    contamination_rate = dropped_count / candidate_count if candidate_count > 0 else 0.0

    field_coverage: dict[str, float] = {}
    if valid_count > 0:
        titles = sum(1 for r in valid_records if r.get("title"))
        urls = sum(1 for r in valid_records if r.get("url"))
        prices = sum(
            1 for r in valid_records
            if (r.get("sellingPrice") is not None or r.get("selling_price") is not None)
            and not is_suspicious_price(r)[0]
        )
        images = sum(1 for r in valid_records if r.get("imageUrl") or r.get("image_url"))
        ratings = sum(1 for r in valid_records if r.get("rating") is not None)
        skus = sum(1 for r in valid_records if r.get("productId") or r.get("product_id"))

        field_coverage["title"] = round(titles / valid_count, 2)
        field_coverage["url"] = round(urls / valid_count, 2)
        field_coverage["price"] = round(prices / valid_count, 2)
        field_coverage["image"] = round(images / valid_count, 2)
        field_coverage["rating"] = round(ratings / valid_count, 2)
        field_coverage["productId"] = round(skus / valid_count, 2)

    # Assess title substantive quality
    descriptive_title_count = 0
    for r in valid_records:
        t = str(r.get("title") or "").strip()
        words = t.split()
        if len(words) >= 2 and len(t) >= 10 and not is_brand_only_or_ui_title(t):
            descriptive_title_count += 1
    title_quality_rate = descriptive_title_count / valid_count if valid_count > 0 else 0.0
    if valid_count >= 2 and title_quality_rate < 0.60:
        warnings.append("Extracted titles appear short or low descriptive quality")

    price_cov = field_coverage.get("price", 0.0)
    if page_type == "ecommerce" and valid_count >= 2 and price_cov < 0.50:
        warnings.append("Low price coverage for e-commerce listing")

    score = 0.0
    if valid_count >= 2:
        valid_part = min(valid_rate, 1.0) * 0.30
        count_part = min(valid_count / 10.0, 1.0) * 0.25
        uniqueness_part = max(0.0, 1.0 - duplicate_rate) * 0.20
        title_quality_part = title_quality_rate * 0.15

        completeness_part = 0.0
        if page_type == "ecommerce":
            img_cov = field_coverage.get("image", 0.0)
            completeness_part = (price_cov * 0.06) + (img_cov * 0.04)
        else:
            completeness_part = 0.10

        raw_score = valid_part + count_part + uniqueness_part + title_quality_part + completeness_part

        # Deduct directly for suspicious or invalid prices to keep score explainable and grounded
        suspicious_prices = sum(1 for r in valid_records if is_suspicious_price(r)[0])
        if suspicious_prices > 0 and valid_count > 0:
            suspicious_penalty = (suspicious_prices / valid_count) * 0.60
            raw_score = max(0.0, raw_score - suspicious_penalty)

        score = round(raw_score, 2)

    # Perform field-level quality inspection (detect suspicious prices, brand-only titles)
    field_ok, field_warnings = inspect_listing_field_quality(valid_records, page_type)
    warnings.extend(field_warnings)

    is_acceptable = (
        is_valid
        and field_ok
        and valid_count >= 2
        and score >= min_quality_score
        and duplicate_rate <= max_duplicate_ratio
    )

    if valid_count < 2:
        warnings.append(f"Insufficient valid records ({valid_count})")
    if duplicate_rate > max_duplicate_ratio:
        warnings.append(f"Excessive duplicate ratio: {duplicate_rate:.2f} > {max_duplicate_ratio:.2f}")
    if score < min_quality_score and valid_count >= 2:
        warnings.append(f"Extraction quality score {score:.2f} below threshold {min_quality_score:.2f}")

    final_quality_score = score if field_ok else min(score, 0.40)

    report = QualityReport(
        is_acceptable=is_acceptable,
        quality_score=final_quality_score,
        record_count=valid_count,
        candidate_count=candidate_count,
        valid_rate=round(valid_rate, 2),
        duplicate_rate=round(duplicate_rate, 2),
        contamination_rate=round(contamination_rate, 2),
        field_coverage=field_coverage,
        warnings=warnings,
    )

    # Ensure any record returned has suspicious prices stripped to None
    accepted_records = []
    if is_acceptable:
        for r in valid_records:
            is_susp, _ = is_suspicious_price(r)
            if is_susp:
                rec_copy = dict(r)
                rec_copy["sellingPrice"] = None
                rec_copy["selling_price"] = None
                rec_copy["currency"] = None
                accepted_records.append(rec_copy)
            else:
                accepted_records.append(r)

    return report, accepted_records
