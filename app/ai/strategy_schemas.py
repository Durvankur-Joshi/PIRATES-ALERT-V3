import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_ATTRIBUTES = {
    "text",
    "href",
    "src",
    "data-src",
    "data-original",
    "data-lazy-src",
    "alt",
    "title",
    "content",
    "value",
    "data-id",
    "data-sku",
    "data-product-id",
    "data-pid",
    "data-item-id",
}

DISALLOWED_SELECTOR_PATTERNS = [
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"<script", re.IGNORECASE),
    re.compile(r"eval\(", re.IGNORECASE),
    re.compile(r"^\s*//"),
    re.compile(r"\bxpath\b", re.IGNORECASE),
]


def validate_css_selector_safety(selector: str | None) -> str | None:
    if not selector:
        return None
    trimmed = selector.strip()
    if not trimmed:
        return None

    for pat in DISALLOWED_SELECTOR_PATTERNS:
        if pat.search(trimmed):
            raise ValueError(f"Unsafe or unsupported selector syntax: '{trimmed}'")

    return trimmed


class FieldExtractionRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selector: str | None = None
    attribute: str = "text"
    required: bool = False
    fallback_selector: str | None = None

    @field_validator("selector", "fallback_selector")
    @classmethod
    def check_selector(cls, v: str | None) -> str | None:
        return validate_css_selector_safety(v)

    @field_validator("attribute")
    @classmethod
    def check_attribute(cls, v: str) -> str:
        attr = v.strip().lower()
        if attr in ALLOWED_ATTRIBUTES or attr.startswith("data-"):
            return attr
        raise ValueError(f"Attribute '{v}' is not in allowed extraction attributes")


class ExtractionStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy_version: str = "1.0.0"
    page_type: Literal["ecommerce", "generic_listing"]
    record_container_selector: str = Field(default="body")
    record_item_selector: str
    fields: dict[str, FieldExtractionRule] = Field(default_factory=dict)
    rationale: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("record_container_selector", "record_item_selector")
    @classmethod
    def check_selectors(cls, v: str) -> str:
        validated = validate_css_selector_safety(v)
        if not validated:
            raise ValueError("Selector cannot be empty")
        return validated

    @field_validator("fields")
    @classmethod
    def check_required_fields(cls, fields: dict[str, FieldExtractionRule]) -> dict[str, FieldExtractionRule]:
        if "title" not in fields:
            raise ValueError("ExtractionStrategy must define a rule for 'title'")
        if "url" not in fields:
            raise ValueError("ExtractionStrategy must define a rule for 'url'")
        return fields
