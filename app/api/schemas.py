from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class PageTypeRequested(str, Enum):
    AUTO = "auto"
    ECOMMERCE = "ecommerce"
    GENERIC_LISTING = "generic_listing"


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    url: str
    page_type: PageTypeRequested = Field(default=PageTypeRequested.AUTO, alias="pageType")


class ExtractedRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    title: str
    url: str
    product_id: str | None = Field(default=None, alias="productId")
    image_url: str | None = Field(default=None, alias="imageUrl")
    selling_price: float | None = Field(default=None, alias="sellingPrice")
    mrp: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = Field(default=None, alias="reviewCount")
    extra: dict[str, Any] = Field(default_factory=dict)


class ExtractionMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    record_count: int = Field(alias="recordCount")
    extraction_method: str = Field(alias="extractionMethod")
    validation_status: str = Field(alias="validationStatus")
    strategy_source: str = Field(default="deterministic", alias="strategySource")
    ai_calls: int = Field(default=0, alias="aiCalls")
    strategy_id: Optional[str] = Field(default=None, alias="strategyId")
    strategy_version: Optional[int] = Field(default=None, alias="strategyVersion")
    quality_score: Optional[float] = Field(default=None, alias="qualityScore")
    fingerprint: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    recovery_status: Optional[str] = Field(default=None, alias="recoveryStatus")
    warnings: list[str] = Field(default_factory=list)
    request_id: Optional[str] = Field(default=None, alias="requestId")
    execution_time_ms: Optional[float] = Field(default=None, alias="executionTimeMs")
    stage_durations: dict[str, float] = Field(default_factory=dict, alias="stageDurations")


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    source_url: str = Field(alias="sourceUrl")
    page_type: str = Field(alias="pageType")
    success: bool
    records: list[ExtractedRecord]
    metadata: ExtractionMetadata


class HealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    status: str
    playwright_available: bool = Field(alias="playwrightAvailable")
    database_available: bool = Field(default=True, alias="databaseAvailable")
    gemini_available: bool = Field(default=False, alias="geminiAvailable")
    gemini_model: Optional[str] = Field(default=None, alias="geminiModel")
    timestamp: str


class ApiInfoResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    name: str
    version: str
    description: str
    endpoints: dict[str, str]


class StrategyVersionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    strategy_id: str = Field(alias="strategyId")
    version: int
    hostname: str
    page_type: str = Field(alias="pageType")
    fingerprint: str
    strategy_schema_version: str = Field(alias="strategySchemaVersion")
    created_at: str = Field(alias="createdAt")
    update_source: str = Field(alias="updateSource")
    validation_status: str = Field(alias="validationStatus")
    validation_metrics: dict[str, Any] = Field(default_factory=dict, alias="validationMetrics")
    status: str
    change_reason: Optional[str] = Field(default=None, alias="changeReason")
    is_active: bool = Field(alias="isActive")


class StrategyListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    hostname: str
    page_type: Optional[str] = Field(default=None, alias="pageType")
    total_versions: int = Field(alias="totalVersions")
    versions: list[StrategyVersionResponse]


class RollbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    hostname: str = Field(min_length=1, max_length=255)
    page_type: str = Field(min_length=1, max_length=50, alias="pageType")
    target_version: int = Field(ge=1, alias="targetVersion")
    reason: str = Field(min_length=3, max_length=500)


class RollbackResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    success: bool
    strategy_id: str = Field(alias="strategyId")
    hostname: str
    page_type: str = Field(alias="pageType")
    previous_version: Optional[int] = Field(default=None, alias="previousVersion")
    active_version: int = Field(alias="activeVersion")
    reason: str
    timestamp: str


class OperationalMetricsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uptime_seconds: float = Field(alias="uptimeSeconds")
    total_requests: int = Field(alias="totalRequests")
    successful_extractions: int = Field(alias="successfulExtractions")
    failed_extractions: int = Field(alias="failedExtractions")
    deterministic_extractions: int = Field(alias="deterministicExtractions")
    saved_strategy_reuse_count: int = Field(alias="savedStrategyReuseCount")
    ai_learned_extractions: int = Field(alias="aiLearnedExtractions")
    total_ai_calls: int = Field(alias="totalAiCalls")
    total_rollbacks: int = Field(alias="totalRollbacks")
    avg_processing_time_ms: float = Field(alias="avgProcessingTimeMs")
