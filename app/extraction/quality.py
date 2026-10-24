from dataclasses import asdict, dataclass
from typing import Any, Optional
from app.extraction.normalizer import deduplicate_records
from app.extraction.validator import validate_extracted_records


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
        prices = sum(1 for r in valid_records if r.get("sellingPrice") is not None or r.get("selling_price") is not None)
        images = sum(1 for r in valid_records if r.get("imageUrl") or r.get("image_url"))
        ratings = sum(1 for r in valid_records if r.get("rating") is not None)
        skus = sum(1 for r in valid_records if r.get("productId") or r.get("product_id"))

        field_coverage["title"] = round(titles / valid_count, 2)
        field_coverage["url"] = round(urls / valid_count, 2)
        field_coverage["price"] = round(prices / valid_count, 2)
        field_coverage["image"] = round(images / valid_count, 2)
        field_coverage["rating"] = round(ratings / valid_count, 2)
        field_coverage["productId"] = round(skus / valid_count, 2)

    score = 0.0
    if valid_count >= 2:
        valid_part = min(valid_rate, 1.0) * 0.40
        count_part = min(valid_count / 10.0, 1.0) * 0.30
        uniqueness_part = max(0.0, 1.0 - duplicate_rate) * 0.20

        completeness_part = 0.0
        if page_type == "ecommerce":
            price_cov = field_coverage.get("price", 0.0)
            img_cov = field_coverage.get("image", 0.0)
            completeness_part = (price_cov * 0.05) + (img_cov * 0.05)
        else:
            completeness_part = 0.10

        score = round(valid_part + count_part + uniqueness_part + completeness_part, 2)

    is_acceptable = (
        is_valid
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

    report = QualityReport(
        is_acceptable=is_acceptable,
        quality_score=score,
        record_count=valid_count,
        candidate_count=candidate_count,
        valid_rate=round(valid_rate, 2),
        duplicate_rate=round(duplicate_rate, 2),
        contamination_rate=round(contamination_rate, 2),
        field_coverage=field_coverage,
        warnings=warnings,
    )

    return report, valid_records if is_acceptable else []
