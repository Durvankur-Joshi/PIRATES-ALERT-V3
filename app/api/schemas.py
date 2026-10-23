from enum import Enum
from typing import Any
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
    fingerprint: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    recovery_status: str | None = Field(default=None, alias="recoveryStatus")
    warnings: list[str] = Field(default_factory=list)


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
    gemini_available: bool = Field(default=False, alias="geminiAvailable")
    gemini_model: str | None = Field(default=None, alias="geminiModel")
    timestamp: str


class ApiInfoResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    name: str
    version: str
    description: str
    endpoints: dict[str, str]
