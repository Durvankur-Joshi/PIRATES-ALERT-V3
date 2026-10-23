import asyncio
from typing import Any, Optional
from app.ai.gemini_client import gemini_client
from app.ai.strategy_learner import (
    execute_strategy,
    learn_and_validate_strategy,
    validate_strategy_execution,
)
from app.config import settings
from app.discovery.candidate_detector import detect_candidate_records
from app.discovery.page_classifier import classify_page
from app.discovery.structured_data import extract_from_structured_data
from app.extraction.normalizer import deduplicate_records
from app.extraction.validator import validate_extracted_records
from app.rules.fingerprint import compute_structural_fingerprint
from app.rules.repository import strategy_repository


class AwaitableExtractionResult(dict):
    def __init__(self, data: dict[str, Any], coro_func=None) -> None:
        super().__init__(data)
        self._coro_func = coro_func

    def __await__(self):
        if self._coro_func is not None:
            return self._coro_func().__await__()

        async def _identity():
            return self

        return _identity().__await__()


def run_deterministic_extraction(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
) -> tuple[dict[str, Any], str]:
    sd_records, sd_type = extract_from_structured_data(html, source_url)
    page_type = classify_page(html, structured_data_type=sd_type, requested_type=requested_page_type)

    if sd_records:
        deduped_sd = deduplicate_records(sd_records)
        is_valid, val_status, valid_records, val_warnings = validate_extracted_records(deduped_sd, page_type)
        if is_valid and len(valid_records) >= 2:
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": {
                    "recordCount": len(valid_records),
                    "extractionMethod": "structured_data",
                    "validationStatus": val_status,
                    "strategySource": "deterministic",
                    "aiCalls": 0,
                    "warnings": val_warnings,
                },
            }, page_type

    dom_records = detect_candidate_records(html, source_url, page_type)
    if dom_records:
        deduped_dom = deduplicate_records(dom_records)
        is_valid, val_status, valid_records, val_warnings = validate_extracted_records(deduped_dom, page_type)
        if is_valid and len(valid_records) >= 2:
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": {
                    "recordCount": len(valid_records),
                    "extractionMethod": "heuristic_dom",
                    "validationStatus": val_status,
                    "strategySource": "deterministic",
                    "aiCalls": 0,
                    "warnings": val_warnings,
                },
            }, page_type

    return {
        "sourceUrl": source_url,
        "pageType": page_type,
        "success": False,
        "records": [],
        "metadata": {
            "recordCount": 0,
            "extractionMethod": "none",
            "validationStatus": "failed",
            "strategySource": "deterministic",
            "aiCalls": 0,
            "warnings": ["No credible listing records found on the page deterministically"],
        },
    }, page_type


async def _run_full_extraction_workflow(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
    enable_learning: bool = True,
) -> dict[str, Any]:
    det_result, page_type = run_deterministic_extraction(html, source_url, requested_page_type)
    if det_result["success"] or not enable_learning:
        return det_result

    fingerprint = compute_structural_fingerprint(html, source_url, page_type)
    recovery_status: Optional[str] = None

    saved_strategy = await strategy_repository.get_strategy(
        hostname=source_url,
        page_type=page_type,
        fingerprint=fingerprint,
    )

    if saved_strategy:
        records = execute_strategy(html, source_url, saved_strategy)
        is_valid, val_metrics, valid_records, val_warnings = validate_strategy_execution(records, page_type)

        if is_valid and len(valid_records) >= 2:
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": {
                    "recordCount": len(valid_records),
                    "extractionMethod": "saved_strategy",
                    "validationStatus": "passed",
                    "strategySource": "saved",
                    "aiCalls": 0,
                    "fingerprint": fingerprint,
                    "metrics": val_metrics,
                    "warnings": val_warnings,
                },
            }
        else:
            recovery_status = "saved_strategy_revalidation_failed"
            await strategy_repository.mark_strategy_failed(
                hostname=source_url,
                page_type=page_type,
                fingerprint=fingerprint,
            )

    if not gemini_client.is_available():
        det_result["metadata"]["warnings"].append(
            "Gemini API key not configured; AI strategy learning unavailable"
        )
        return det_result

    learned_strategy, valid_records, ai_metrics, ai_warnings = await learn_and_validate_strategy(
        html=html,
        source_url=source_url,
        page_type=page_type,
        max_attempts=settings.max_ai_attempts_per_extraction,
    )

    ai_calls = ai_metrics.get("aiCalls", 1)

    if learned_strategy and len(valid_records) >= 2:
        await strategy_repository.save_strategy(
            hostname=source_url,
            page_type=page_type,
            fingerprint=fingerprint,
            strategy=learned_strategy,
            validation_metrics=ai_metrics,
            validation_status="passed",
        )
        return {
            "sourceUrl": source_url,
            "pageType": page_type,
            "success": True,
            "records": valid_records,
            "metadata": {
                "recordCount": len(valid_records),
                "extractionMethod": "ai_strategy",
                "validationStatus": "passed",
                "strategySource": "ai_learned",
                "aiCalls": ai_calls,
                "fingerprint": fingerprint,
                "metrics": ai_metrics,
                "recoveryStatus": recovery_status,
                "warnings": ai_warnings,
            },
        }

    return {
        "sourceUrl": source_url,
        "pageType": page_type,
        "success": False,
        "records": [],
        "metadata": {
            "recordCount": 0,
            "extractionMethod": "none",
            "validationStatus": "failed",
            "strategySource": "ai_learned",
            "aiCalls": ai_calls,
            "fingerprint": fingerprint,
            "metrics": ai_metrics,
            "recoveryStatus": recovery_status,
            "warnings": ai_warnings or ["AI strategy learning did not yield valid records"],
        },
    }


def run_extraction_pipeline(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
    enable_learning: bool = True,
) -> AwaitableExtractionResult:
    det_result, _ = run_deterministic_extraction(html, source_url, requested_page_type)
    if det_result["success"] or not enable_learning:
        return AwaitableExtractionResult(det_result)

    def _coro_factory():
        return _run_full_extraction_workflow(
            html=html,
            source_url=source_url,
            requested_page_type=requested_page_type,
            enable_learning=enable_learning,
        )

    return AwaitableExtractionResult(det_result, coro_func=_coro_factory)
