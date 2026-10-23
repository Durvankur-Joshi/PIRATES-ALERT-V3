from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException, status
from app.ai.gemini_client import gemini_client
from app.api.schemas import (
    ApiInfoResponse,
    ExtractionRequest,
    ExtractionResponse,
    HealthResponse,
)
from app.browser.page_loader import (
    BotChallengeDetectedError,
    EmptyPageError,
    NavigationError,
    PageLoadTimeoutError,
    load_rendered_page,
)
from app.browser.playwright_manager import playwright_manager
from app.config import settings
from app.extraction.engine import run_extraction_pipeline
from app.rules.repository import strategy_repository
from app.security.url_validation import URLValidationError, validate_url


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        await strategy_repository.init_db()
    except Exception:
        pass
    try:
        await playwright_manager.start()
    except Exception:
        pass
    yield
    await playwright_manager.close()


app = FastAPI(
    title="PIRATES ALERT V3 API",
    version="0.2.0",
    description="Deterministic and Gemini-assisted listing extraction service",
    lifespan=lifespan,
)


@app.get("/", response_model=ApiInfoResponse)
async def root() -> ApiInfoResponse:
    return ApiInfoResponse(
        name="PIRATES ALERT V3 API",
        version="0.2.0",
        description="Deterministic and Gemini-assisted e-commerce and generic listing extraction backend",
        endpoints={
            "GET /health": "Service health check and browser/AI readiness status",
            "POST /api/v1/extract": "Render, extract, or learn strategies from search or listing pages",
            "GET /": "Service metadata and endpoint overview",
        },
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    pw_available = await playwright_manager.is_available()
    ai_available = gemini_client.is_available()
    return HealthResponse(
        status="healthy" if pw_available else "degraded",
        playwrightAvailable=pw_available,
        geminiAvailable=ai_available,
        geminiModel=settings.gemini_model if ai_available else None,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post(
    "/api/v1/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
)
async def extract_records(payload: ExtractionRequest) -> ExtractionResponse:
    target_url = payload.url
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
    )

    return ExtractionResponse.model_validate(result)
