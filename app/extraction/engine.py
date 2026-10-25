import asyncio
from typing import Any, Optional
from app.ai.gemini_client import gemini_client
from app.ai.strategy_learner import (
    execute_strategy,
    learn_and_validate_strategy,
    validate_strategy_execution,
)
from app.ai.product_extractor import extract_products_with_ai
from app.ai.strategy_schemas import ExtractionStrategy
from app.config import settings
from app.discovery.candidate_detector import detect_candidate_records
from app.discovery.page_classifier import classify_page
from app.discovery.structured_data import extract_from_structured_data
from app.extraction.normalizer import deduplicate_records
from app.extraction.quality import (
    evaluate_extraction_quality,
    inspect_listing_field_quality,
    is_suspicious_price,
)
from app.extraction.validator import is_valid_url_string, validate_extracted_records
from app.monitoring.observability import (
    StageTimer,
    generate_request_id,
    log_extraction_event,
    metrics_collector,
)
from app.rules.fingerprint import compute_structural_fingerprint
from app.rules.repository import generate_strategy_id, strategy_repository


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


def sanitize_safe_partial_records(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_records: list[dict[str, Any]] = []
    for r in raw_records:
        title = str(r.get("title") or "").strip()
        url = r.get("url")
        if not title or not url or not is_valid_url_string(url):
            continue
        rec = dict(r)
        is_susp, _ = is_suspicious_price(rec)
        if is_susp:
            rec["sellingPrice"] = None
            rec["mrp"] = None
            rec["currency"] = None
        rating = rec.get("rating")
        if rating is not None and not (0.0 <= rating <= 5.0):
            rec["rating"] = None
        safe_records.append(rec)
    return deduplicate_records(safe_records)


def run_deterministic_extraction(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
) -> tuple[dict[str, Any], str]:
    sd_records, sd_type = extract_from_structured_data(html, source_url)
    page_type = classify_page(html, structured_data_type=sd_type, requested_type=requested_page_type)

    partial_candidates: list[dict[str, Any]] = []

    if sd_records:
        report, valid_records = evaluate_extraction_quality(
            sd_records, page_type, total_candidates=len(sd_records)
        )
        if report.is_acceptable and len(valid_records) >= 2:
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": {
                    "recordCount": len(valid_records),
                    "extractionMethod": "structured_data",
                    "validationStatus": "passed",
                    "strategySource": "deterministic",
                    "aiCalls": 0,
                    "qualityScore": report.quality_score,
                    "metrics": report.to_dict(),
                    "warnings": report.warnings,
                },
            }, page_type
        partial_candidates = sanitize_safe_partial_records(sd_records)

    dom_records = detect_candidate_records(html, source_url, page_type)
    if dom_records:
        report, valid_records = evaluate_extraction_quality(
            dom_records, page_type, total_candidates=len(dom_records)
        )
        if report.is_acceptable and len(valid_records) >= 2:
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": {
                    "recordCount": len(valid_records),
                    "extractionMethod": "heuristic_dom",
                    "validationStatus": "passed",
                    "strategySource": "deterministic",
                    "aiCalls": 0,
                    "qualityScore": report.quality_score,
                    "metrics": report.to_dict(),
                    "warnings": report.warnings,
                },
            }, page_type
        partial_candidates = sanitize_safe_partial_records(dom_records)

    quality_warnings = report.warnings if "report" in locals() else ["No credible listing records found on the page deterministically"]

    return {
        "sourceUrl": source_url,
        "pageType": page_type,
        "success": False,
        "records": partial_candidates,
        "metadata": {
            "recordCount": len(partial_candidates),
            "extractionMethod": "none",
            "validationStatus": "failed",
            "strategySource": "deterministic",
            "aiCalls": 0,
            "qualityScore": 0.0,
            "partialRecords": partial_candidates,
            "metrics": {},
            "warnings": quality_warnings,
        },
    }, page_type


async def _run_full_extraction_workflow(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
    enable_learning: bool = True,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    req_id = request_id or generate_request_id()
    timer = StageTimer()

    timer.start_stage("deterministic")
    det_result, page_type = run_deterministic_extraction(html, source_url, requested_page_type)
    timer.end_stage()

    if det_result["success"] or not enable_learning:
        total_ms = timer.total_duration_ms()
        det_result["metadata"]["requestId"] = req_id
        det_result["metadata"]["executionTimeMs"] = total_ms
        det_result["metadata"]["stageDurations"] = timer.stage_durations

        metrics_collector.record_extraction(
            strategy_source="deterministic",
            success=det_result["success"],
            ai_calls=0,
            duration_ms=total_ms,
        )
        log_extraction_event(
            request_id=req_id,
            hostname=source_url,
            page_type=page_type,
            strategy_source="deterministic",
            record_count=det_result["metadata"]["recordCount"],
            ai_calls=0,
            quality_score=det_result["metadata"].get("qualityScore", 0.0),
            total_ms=total_ms,
            stage_durations=timer.stage_durations,
            warnings=det_result["metadata"]["warnings"],
        )
        return det_result

    fingerprint = compute_structural_fingerprint(html, source_url, page_type)
    recovery_status: Optional[str] = None

    timer.start_stage("saved_strategy")
    active_info = await strategy_repository.get_active_strategy_info(
        hostname=source_url,
        page_type=page_type,
    )

    if active_info:
        saved_strategy = active_info["strategy"]
        records = execute_strategy(html, source_url, saved_strategy)
        is_valid, val_metrics, valid_records, val_warnings = validate_strategy_execution(
            records, page_type
        )
        is_field_ok, field_warnings = inspect_listing_field_quality(valid_records, page_type)

        if is_valid and is_field_ok and len(valid_records) >= 2:
            timer.end_stage()
            total_ms = timer.total_duration_ms()
            result_meta = {
                "recordCount": len(valid_records),
                "extractionMethod": "saved_strategy",
                "validationStatus": "passed",
                "strategySource": "saved",
                "aiCalls": 0,
                "strategyId": active_info["strategy_id"],
                "strategyVersion": active_info["version"],
                "qualityScore": val_metrics.get("qualityScore", 0.0),
                "fingerprint": fingerprint,
                "metrics": val_metrics,
                "warnings": val_warnings + field_warnings,
                "requestId": req_id,
                "executionTimeMs": total_ms,
                "stageDurations": timer.stage_durations,
            }
            metrics_collector.record_extraction(
                strategy_source="saved",
                success=True,
                ai_calls=0,
                duration_ms=total_ms,
            )
            log_extraction_event(
                request_id=req_id,
                hostname=source_url,
                page_type=page_type,
                strategy_source="saved",
                strategy_id=active_info["strategy_id"],
                strategy_version=active_info["version"],
                record_count=len(valid_records),
                ai_calls=0,
                quality_score=val_metrics.get("qualityScore", 0.0),
                total_ms=total_ms,
                stage_durations=timer.stage_durations,
                warnings=val_warnings + field_warnings,
            )
            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": valid_records,
                "metadata": result_meta,
            }
        else:
            recovery_status = "saved_strategy_revalidation_failed"

    timer.end_stage()

    if not gemini_client.is_available():
        timer.end_stage()
        total_ms = timer.total_duration_ms()
        det_result["metadata"]["warnings"].append(
            "Gemini API key not configured; AI extraction fallback unavailable"
        )
        det_result["metadata"]["requestId"] = req_id
        det_result["metadata"]["executionTimeMs"] = total_ms
        det_result["metadata"]["stageDurations"] = timer.stage_durations
        metrics_collector.record_extraction(
            strategy_source="deterministic",
            success=False,
            ai_calls=0,
            duration_ms=total_ms,
        )
        return det_result

    has_field_quality_failure = bool(
        det_result["metadata"].get("partialRecords")
        or any(
            "field failure" in w.lower()
            or "suspicious" in w.lower()
            or "field quality" in w.lower()
            for w in det_result["metadata"].get("warnings", [])
        )
    )

    if has_field_quality_failure:
        # Candidate cards were detected by deterministic extractor, but failed field-level quality checks.
        # Trigger the controlled AI-assisted product extraction fallback.
        timer.start_stage("ai_fallback")
        ai_records, ai_metrics, ai_warnings = await extract_products_with_ai(
            html=html,
            source_url=source_url,
            page_type=page_type,
        )
        timer.end_stage()
        total_ms = timer.total_duration_ms()

        if ai_records and len(ai_records) >= 2:
            metrics_collector.record_extraction(
                strategy_source="ai_fallback",
                success=True,
                ai_calls=1,
                duration_ms=total_ms,
            )
            log_extraction_event(
                request_id=req_id,
                hostname=source_url,
                page_type=page_type,
                strategy_source="ai_fallback",
                record_count=len(ai_records),
                ai_calls=1,
                quality_score=ai_metrics.get("qualityScore", 0.85),
                total_ms=total_ms,
                stage_durations=timer.stage_durations,
                warnings=ai_warnings,
            )

            return {
                "sourceUrl": source_url,
                "pageType": page_type,
                "success": True,
                "records": ai_records,
                "metadata": {
                    "recordCount": len(ai_records),
                    "extractionMethod": "gemini_fallback",
                    "validationStatus": "passed",
                    "strategySource": "ai_fallback",
                    "aiCalls": 1,
                    "qualityScore": ai_metrics.get("qualityScore", 0.85),
                    "fingerprint": fingerprint,
                    "metrics": ai_metrics,
                    "recoveryStatus": recovery_status,
                    "warnings": ai_warnings,
                    "requestId": req_id,
                    "executionTimeMs": total_ms,
                    "stageDurations": timer.stage_durations,
                },
            }

        # AI Fallback failed or produced ungrounded records:
        # Return best safe partial deterministic result without fabricated data
        metrics_collector.record_extraction(
            strategy_source="ai_fallback",
            success=False,
            ai_calls=1,
            duration_ms=total_ms,
        )

        safe_partial = det_result["metadata"].get("partialRecords", [])
        merged_warnings = list(det_result["metadata"].get("warnings", []))
        merged_warnings.extend(ai_warnings or ["AI fallback extraction did not yield valid grounded records"])

        log_extraction_event(
            request_id=req_id,
            hostname=source_url,
            page_type=page_type,
            strategy_source="ai_fallback",
            record_count=len(safe_partial),
            ai_calls=1,
            quality_score=0.0,
            total_ms=total_ms,
            stage_durations=timer.stage_durations,
            warnings=merged_warnings,
            error="AI fallback extraction failed",
        )

        return {
            "sourceUrl": source_url,
            "pageType": page_type,
            "success": False,
            "records": safe_partial,
            "metadata": {
                "recordCount": len(safe_partial),
                "extractionMethod": "partial_deterministic" if safe_partial else "none",
                "validationStatus": "failed",
                "strategySource": "ai_fallback_partial" if safe_partial else "ai_fallback",
                "aiCalls": 1,
                "qualityScore": 0.0,
                "fingerprint": fingerprint,
                "metrics": ai_metrics,
                "recoveryStatus": "ai_fallback_failed",
                "warnings": merged_warnings,
                "requestId": req_id,
                "executionTimeMs": total_ms,
                "stageDurations": timer.stage_durations,
            },
        }

    # If no candidate cards were found deterministically, use the tested strategy-learning pipeline
    timer.start_stage("ai_learning")
    learned_strategy, valid_records, ai_metrics, ai_warnings = await learn_and_validate_strategy(
        html=html,
        source_url=source_url,
        page_type=page_type,
        max_attempts=settings.max_ai_attempts_per_extraction,
    )
    timer.end_stage()
    total_ms = timer.total_duration_ms()
    ai_calls = ai_metrics.get("aiCalls", 1)

    if learned_strategy and len(valid_records) >= 2:
        new_version = await strategy_repository.save_version(
            hostname=source_url,
            page_type=page_type,
            fingerprint=fingerprint,
            strategy=learned_strategy,
            validation_metrics=ai_metrics,
            validation_status="passed",
            update_source="ai_learned",
            change_reason="AI strategy learned and validated",
        )
        strat_id = generate_strategy_id(source_url, page_type)

        metrics_collector.record_extraction(
            strategy_source="ai_learned",
            success=True,
            ai_calls=ai_calls,
            duration_ms=total_ms,
        )
        log_extraction_event(
            request_id=req_id,
            hostname=source_url,
            page_type=page_type,
            strategy_source="ai_learned",
            strategy_id=strat_id,
            strategy_version=new_version,
            record_count=len(valid_records),
            ai_calls=ai_calls,
            quality_score=ai_metrics.get("qualityScore", 0.0),
            total_ms=total_ms,
            stage_durations=timer.stage_durations,
            warnings=ai_warnings,
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
                "strategyId": strat_id,
                "strategyVersion": new_version,
                "qualityScore": ai_metrics.get("qualityScore", 0.0),
                "fingerprint": fingerprint,
                "metrics": ai_metrics,
                "recoveryStatus": recovery_status,
                "warnings": ai_warnings,
                "requestId": req_id,
                "executionTimeMs": total_ms,
                "stageDurations": timer.stage_durations,
            },
        }

    metrics_collector.record_extraction(
        strategy_source="ai_learned",
        success=False,
        ai_calls=ai_calls,
        duration_ms=total_ms,
    )
    log_extraction_event(
        request_id=req_id,
        hostname=source_url,
        page_type=page_type,
        strategy_source="ai_learned",
        record_count=0,
        ai_calls=ai_calls,
        quality_score=0.0,
        total_ms=total_ms,
        stage_durations=timer.stage_durations,
        warnings=ai_warnings or ["AI strategy learning did not yield valid records"],
        error="Strategy learning failed or yielded insufficient records",
    )

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
            "qualityScore": 0.0,
            "fingerprint": fingerprint,
            "metrics": ai_metrics,
            "recoveryStatus": recovery_status,
            "warnings": ai_warnings or ["AI strategy learning did not yield valid records"],
            "requestId": req_id,
            "executionTimeMs": total_ms,
            "stageDurations": timer.stage_durations,
        },
    }


def run_extraction_pipeline(
    html: str,
    source_url: str,
    requested_page_type: str = "auto",
    enable_learning: bool = True,
    request_id: Optional[str] = None,
) -> AwaitableExtractionResult:
    req_id = request_id or generate_request_id()
    timer = StageTimer()
    timer.start_stage("deterministic")
    det_result, page_type = run_deterministic_extraction(html, source_url, requested_page_type)
    timer.end_stage()
    total_ms = timer.total_duration_ms()

    det_result["metadata"]["requestId"] = req_id
    det_result["metadata"]["executionTimeMs"] = total_ms
    det_result["metadata"]["stageDurations"] = timer.stage_durations

    if det_result["success"]:
        metrics_collector.record_extraction(
            strategy_source="deterministic",
            success=True,
            ai_calls=0,
            duration_ms=total_ms,
        )
        log_extraction_event(
            request_id=req_id,
            hostname=source_url,
            page_type=page_type,
            strategy_source="deterministic",
            record_count=det_result["metadata"]["recordCount"],
            ai_calls=0,
            quality_score=det_result["metadata"].get("qualityScore", 0.0),
            total_ms=total_ms,
            stage_durations=timer.stage_durations,
            warnings=det_result["metadata"]["warnings"],
        )
        return AwaitableExtractionResult(det_result)

    if not enable_learning:
        metrics_collector.record_extraction(
            strategy_source="deterministic",
            success=False,
            ai_calls=0,
            duration_ms=total_ms,
        )
        return AwaitableExtractionResult(det_result)

    def _coro_factory():
        return _run_full_extraction_workflow(
            html=html,
            source_url=source_url,
            requested_page_type=requested_page_type,
            enable_learning=enable_learning,
            request_id=req_id,
        )

    return AwaitableExtractionResult(det_result, coro_func=_coro_factory)
