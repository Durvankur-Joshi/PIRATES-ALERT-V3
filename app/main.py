from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from fastapi import FastAPI, Header, HTTPException, Query, status
from app.ai.gemini_client import gemini_client
from app.api.schemas import (
    ApiInfoResponse,
    ExtractionRequest,
    ExtractionResponse,
    HealthResponse,
    OperationalMetricsResponse,
    RollbackRequest,
    RollbackResponse,
    StrategyListResponse,
)
from app.browser.page_loader import (
    AccessRestrictedError,
    BotChallengeDetectedError,
    EmptyPageError,
    NavigationError,
    PageLoadTimeoutError,
    load_rendered_page,
)
from app.browser.playwright_manager import playwright_manager
from app.config import settings
from app.extraction.engine import run_extraction_pipeline
from app.monitoring.observability import generate_request_id, metrics_collector
from app.rules.repository import strategy_repository
from app.security.url_validation import URLValidationError, validate_url


import logging
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("pirates_alert")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        await strategy_repository.init_db()
    except Exception as e:
        logger.error(f"Failed to initialize strategy repository during startup: {e}")
    try:
        await playwright_manager.start()
    except Exception as e:
        logger.error(f"Failed to start Playwright during startup: {e}")
    yield
    await playwright_manager.close()


app = FastAPI(
    title="PIRATES ALERT V3 API",
    version="0.4.0",
    description="Deterministic and Gemini-assisted listing extraction with strategy versioning and Chrome extension support",
    lifespan=lifespan,
)

allowed_origins: list[str] = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
if settings.chrome_extension_origin:
    for orig in settings.chrome_extension_origin.split(","):
        cleaned = orig.strip()
        if cleaned and cleaned not in allowed_origins:
            allowed_origins.append(cleaned)

allow_origin_regex = (
    r"^chrome-extension://[a-z]{32}$"
    if (settings.environment == "development" and not settings.chrome_extension_origin)
    else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/", response_model=ApiInfoResponse)
async def root() -> ApiInfoResponse:
    return ApiInfoResponse(
        name="PIRATES ALERT V3 API",
        version="0.4.0",
        description="Deterministic and Gemini-assisted e-commerce and generic listing extraction backend with Chrome extension integration",
        endpoints={
            "GET /health": "Service health check and browser/AI readiness status",
            "POST /api/v1/extract": "Render, extract, or learn strategies from search or listing pages",
            "GET /api/v1/strategies": "List immutable strategy versions for a hostname (admin/local only)",
            "POST /api/v1/strategies/rollback": "Roll back active strategy to a prior valid version (admin/local only)",
            "GET /api/v1/metrics": "Operational extraction metrics and zero-call reuse counts",
            "GET /": "Service metadata and endpoint overview",
        },
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    pw_available = await playwright_manager.is_available()
    db_available = await strategy_repository.is_available()
    ai_available = gemini_client.is_available()
    is_healthy = pw_available and db_available
    return HealthResponse(
        status="healthy" if is_healthy else "degraded",
        playwrightAvailable=pw_available,
        databaseAvailable=db_available,
        geminiAvailable=ai_available,
        geminiModel=settings.gemini_model if ai_available else None,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post(
    "/api/v1/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
)
async def extract_records(
    payload: ExtractionRequest,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
) -> ExtractionResponse:
    target_url = payload.url
    req_id = x_request_id or generate_request_id()

    try:
        validate_url(target_url)
    except URLValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL validation failed: {e}",
        )

    try:
        html, final_url = await load_rendered_page(
            url=target_url,
            timeout_ms=settings.navigation_timeout_ms,
        )
    except PageLoadTimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        )
    except BotChallengeDetectedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except AccessRestrictedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except EmptyPageError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )
    except NavigationError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    result = await run_extraction_pipeline(
        html=html,
        source_url=final_url,
        requested_page_type=payload.page_type.value,
        enable_learning=True,
        request_id=req_id,
    )

    return ExtractionResponse.model_validate(result)


@app.get(
    "/api/v1/strategies",
    response_model=StrategyListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_strategies(
    hostname: str = Query(..., min_length=1, max_length=255),
    page_type: Optional[str] = Query(None, min_length=1, max_length=50),
    limit: int = Query(50, ge=1, le=100),
) -> StrategyListResponse:
    versions = await strategy_repository.list_versions(
        hostname=hostname, page_type=page_type, limit=limit
    )
    return StrategyListResponse(
        hostname=hostname,
        page_type=page_type,
        total_versions=len(versions),
        versions=versions,
    )


@app.post(
    "/api/v1/strategies/rollback",
    response_model=RollbackResponse,
    status_code=status.HTTP_200_OK,
)
async def rollback_strategy(payload: RollbackRequest) -> RollbackResponse:
    try:
        result = await strategy_repository.rollback_strategy(
            hostname=payload.hostname,
            page_type=payload.page_type,
            target_version=payload.target_version,
            reason=payload.reason,
        )
        metrics_collector.record_rollback()
        return RollbackResponse(
            success=True,
            strategy_id=result["strategyId"],
            hostname=result["hostname"],
            page_type=result["pageType"],
            previous_version=result["previousVersion"],
            active_version=result["activeVersion"],
            reason=result["reason"],
            timestamp=result["timestamp"],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute strategy rollback",
        )


@app.get(
    "/api/v1/metrics",
    response_model=OperationalMetricsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_metrics() -> OperationalMetricsResponse:
    return OperationalMetricsResponse.model_validate(metrics_collector.get_summary())
