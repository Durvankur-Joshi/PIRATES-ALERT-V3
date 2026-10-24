import asyncio
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse
import uuid

logger = logging.getLogger("pirates_alert")


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def sanitize_url_for_logging(url: str) -> str:
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        return "invalid_url"


class StageTimer:
    def __init__(self) -> None:
        self.start_time = time.perf_counter()
        self.stage_durations: dict[str, float] = {}
        self._current_stage: Optional[str] = None
        self._stage_start: float = 0.0

    def start_stage(self, name: str) -> None:
        if self._current_stage:
            self.end_stage()
        self._current_stage = name
        self._stage_start = time.perf_counter()

    def end_stage(self) -> None:
        if self._current_stage:
            duration = (time.perf_counter() - self._stage_start) * 1000.0
            self.stage_durations[self._current_stage] = round(duration, 2)
            self._current_stage = None

    def total_duration_ms(self) -> float:
        if self._current_stage:
            self.end_stage()
        return round((time.perf_counter() - self.start_time) * 1000.0, 2)


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.start_time = time.time()
        self.total_requests = 0
        self.successful_extractions = 0
        self.failed_extractions = 0
        self.deterministic_extractions = 0
        self.saved_strategy_reuse_count = 0
        self.ai_learned_extractions = 0
        self.total_ai_calls = 0
        self.total_rollbacks = 0
        self.total_processing_time_ms = 0.0

    def record_extraction(
        self,
        strategy_source: str,
        success: bool,
        ai_calls: int,
        duration_ms: float,
    ) -> None:
        self.total_requests += 1
        if success:
            self.successful_extractions += 1
        else:
            self.failed_extractions += 1

        self.total_ai_calls += ai_calls
        self.total_processing_time_ms += duration_ms

        if strategy_source == "deterministic":
            self.deterministic_extractions += 1
        elif strategy_source == "saved":
            self.saved_strategy_reuse_count += 1
        elif strategy_source == "ai_learned":
            self.ai_learned_extractions += 1

    def record_rollback(self) -> None:
        self.total_rollbacks += 1

    def get_summary(self) -> dict[str, Any]:
        uptime_seconds = round(time.time() - self.start_time, 2)
        avg_latency = (
            round(self.total_processing_time_ms / self.total_requests, 2)
            if self.total_requests > 0
            else 0.0
        )
        return {
            "uptimeSeconds": uptime_seconds,
            "totalRequests": self.total_requests,
            "successfulExtractions": self.successful_extractions,
            "failedExtractions": self.failed_extractions,
            "deterministicExtractions": self.deterministic_extractions,
            "savedStrategyReuseCount": self.saved_strategy_reuse_count,
            "aiLearnedExtractions": self.ai_learned_extractions,
            "totalAiCalls": self.total_ai_calls,
            "totalRollbacks": self.total_rollbacks,
            "avgProcessingTimeMs": avg_latency,
        }


metrics_collector = MetricsCollector()


def log_extraction_event(
    request_id: str,
    hostname: str,
    page_type: str,
    strategy_source: str,
    record_count: int,
    ai_calls: int,
    quality_score: Optional[float],
    total_ms: float,
    stage_durations: dict[str, float],
    warnings: list[str],
    strategy_id: Optional[str] = None,
    strategy_version: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    event_data = {
        "requestId": request_id,
        "hostname": hostname,
        "pageType": page_type,
        "strategySource": strategy_source,
        "strategyId": strategy_id,
        "strategyVersion": strategy_version,
        "recordCount": record_count,
        "aiCalls": ai_calls,
        "qualityScore": quality_score,
        "totalMs": total_ms,
        "stages": stage_durations,
        "warningsCount": len(warnings),
        "error": error,
    }
    logger.info("EXTRACTION_EVENT: %s", json.dumps(event_data))
