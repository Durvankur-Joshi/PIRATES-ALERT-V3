from typing import Any, Optional
from bs4 import BeautifulSoup, Tag
from app.ai.evidence_builder import build_learning_evidence
from app.ai.gemini_client import gemini_client
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.config import settings
from app.extraction.normalizer import (
    clean_text,
    deduplicate_records,
    extract_price_and_currency,
    normalize_url,
    parse_rating,
    parse_review_count,
)
from app.extraction.validator import validate_extracted_records


def extract_field_value(card: Tag, rule: FieldExtractionRule | None, base_url: str) -> Any:
    if not rule:
        return None

    target_element: Optional[Tag] = None
    if rule.selector:
        try:
            target_element = card.select_one(rule.selector)
        except Exception:
            pass

    if target_element is None and rule.fallback_selector:
        try:
            target_element = card.select_one(rule.fallback_selector)
        except Exception:
            pass

    if target_element is None:
        if not rule.selector and not rule.fallback_selector:
            target_element = card
        else:
            return None

    if rule.attribute == "text":
        return clean_text(target_element.get_text())

    val = target_element.get(rule.attribute)
    if isinstance(val, list):
        val = " ".join(val)
    return str(val).strip() if val is not None else None


def execute_strategy(
    html: str,
    source_url: str,
    strategy: ExtractionStrategy,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[Tag] = []

    if strategy.record_container_selector and strategy.record_container_selector != "body":
        try:
            cards = soup.select(f"{strategy.record_container_selector} {strategy.record_item_selector}")
        except Exception:
            pass

    if not cards:
        try:
            cards = soup.select(strategy.record_item_selector)
        except Exception:
            pass

    if not cards:
        return []

    records: list[dict[str, Any]] = []
    fields = strategy.fields

    for card in cards:
        title = extract_field_value(card, fields.get("title"), source_url)
        raw_url = extract_field_value(card, fields.get("url"), source_url)
        url = normalize_url(source_url, raw_url) if raw_url else None

        if not title or not url:
            continue

        product_id = extract_field_value(card, fields.get("productId"), source_url)
        raw_img = extract_field_value(card, fields.get("imageUrl"), source_url)
        image_url = normalize_url(source_url, raw_img) if raw_img else None

        raw_price = extract_field_value(card, fields.get("sellingPrice"), source_url)
        raw_mrp = extract_field_value(card, fields.get("mrp"), source_url)
        currency_hint = extract_field_value(card, fields.get("currency"), source_url)

        selling_price, parsed_cur = extract_price_and_currency(str(raw_price)) if raw_price else (None, None)
        mrp, _ = extract_price_and_currency(str(raw_mrp)) if raw_mrp else (None, None)
        currency = currency_hint or parsed_cur

        raw_rating = extract_field_value(card, fields.get("rating"), source_url)
        rating = parse_rating(str(raw_rating)) if raw_rating else None

        raw_reviews = extract_field_value(card, fields.get("reviewCount"), source_url)
        review_count = parse_review_count(str(raw_reviews)) if raw_reviews else None

        extra: dict[str, Any] = {}
        for fname, frule in fields.items():
            if fname not in (
                "title",
                "url",
                "productId",
                "imageUrl",
                "sellingPrice",
                "mrp",
                "currency",
                "rating",
                "reviewCount",
                "extra",
            ):
                val = extract_field_value(card, frule, source_url)
                if val:
                    extra[fname] = val

        records.append(
            {
                "title": title,
                "url": url,
                "productId": str(product_id) if product_id else None,
                "imageUrl": image_url,
                "sellingPrice": selling_price,
                "mrp": mrp,
                "currency": currency,
                "rating": rating,
                "reviewCount": review_count,
                "extra": extra,
            }
        )

    return records


from app.extraction.quality import evaluate_extraction_quality


def validate_strategy_execution(
    records: list[dict[str, Any]],
    page_type: str,
) -> tuple[bool, dict[str, Any], list[dict[str, Any]], list[str]]:
    total_candidates = len(records)
    if total_candidates == 0:
        return (
            False,
            {"recordCount": 0, "qualityScore": 0.0, "error": "Strategy matched zero records"},
            [],
            ["Selector matched 0 elements"],
        )

    report, valid_records = evaluate_extraction_quality(
        records, page_type, total_candidates=total_candidates
    )

    metrics: dict[str, Any] = {
        "candidateCount": report.candidate_count,
        "recordCount": report.record_count,
        "duplicateRatio": report.duplicate_rate,
        "qualityScore": report.quality_score,
        "validRate": report.valid_rate,
        "contaminationRate": report.contamination_rate,
        "fieldCoverage": report.field_coverage,
        "validationStatus": "passed" if report.is_acceptable else "failed",
    }

    return report.is_acceptable, metrics, valid_records, report.warnings


async def learn_and_validate_strategy(
    html: str,
    source_url: str,
    page_type: str,
    max_attempts: Optional[int] = None,
) -> tuple[Optional[ExtractionStrategy], list[dict[str, Any]], dict[str, Any], list[str]]:
    budget = max_attempts or settings.max_ai_attempts_per_extraction
    validation_feedback: Optional[str] = None
    ai_calls_made = 0
    collected_warnings: list[str] = []
    latest_metrics: dict[str, Any] = {}

    for attempt in range(1, budget + 1):
        ai_calls_made += 1
        prompt = build_learning_evidence(
            html=html,
            source_url=source_url,
            page_type=page_type,
            validation_feedback=validation_feedback,
        )

        strategy, call_metrics = await gemini_client.generate_strategy(
            prompt=prompt,
            reason=f"strategy_learning_attempt_{attempt}",
        )
        latest_metrics.update(call_metrics)

        if strategy is None:
            err = call_metrics.get("error", "AI strategy generation failed")
            collected_warnings.append(f"Attempt {attempt}: {err}")
            break

        records = execute_strategy(html, source_url, strategy)
        is_valid, val_metrics, valid_records, val_warnings = validate_strategy_execution(records, page_type)
        latest_metrics.update(val_metrics)
        latest_metrics["aiCalls"] = ai_calls_made

        if is_valid:
            return strategy, valid_records, latest_metrics, collected_warnings

        collected_warnings.extend(val_warnings)
        validation_feedback = "; ".join(val_warnings[:3])

    latest_metrics["aiCalls"] = ai_calls_made
    return None, [], latest_metrics, collected_warnings
