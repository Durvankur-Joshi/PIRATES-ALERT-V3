import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import soupsieve

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

FIELD_ALIAS_MAP = {
    # Title aliases
    "name": "title",
    "product_name": "title",
    "productname": "title",
    "product_title": "title",
    "producttitle": "title",
    "heading": "title",
    "item_name": "title",
    "business_name": "title",
    "company_name": "title",
    "listing_title": "title",
    # URL aliases
    "link": "url",
    "product_url": "url",
    "producturl": "url",
    "href": "url",
    "item_url": "url",
    "business_url": "url",
    "detail_url": "url",
    "target_url": "url",
    # Price aliases
    "price": "sellingPrice",
    "selling_price": "sellingPrice",
    "final_price": "sellingPrice",
    "offer_price": "sellingPrice",
    "our_price": "sellingPrice",
    "rate": "sellingPrice",
    # MRP aliases
    "original_price": "mrp",
    "list_price": "mrp",
    "regular_price": "mrp",
    "strikethrough_price": "mrp",
    "mrp_price": "mrp",
    # Image aliases
    "image": "imageUrl",
    "image_url": "imageUrl",
    "img": "imageUrl",
    "thumbnail": "imageUrl",
    "photo": "imageUrl",
    "pic": "imageUrl",
    # Product ID aliases
    "id": "productId",
    "sku": "productId",
    "product_id": "productId",
    "item_id": "productId",
    "asin": "productId",
    # Rating aliases
    "score": "rating",
    "stars": "rating",
    "rating_value": "rating",
    "avg_rating": "rating",
    # Review count aliases
    "reviews": "reviewCount",
    "review_count": "reviewCount",
    "total_reviews": "reviewCount",
    "num_reviews": "reviewCount",
    "rating_count": "reviewCount",
}


def validate_css_selector_safety(selector: str | None) -> str | None:
    if not selector:
        return None
    trimmed = selector.strip()
    if not trimmed:
        return None

    for pat in DISALLOWED_SELECTOR_PATTERNS:
        if pat.search(trimmed):
            raise ValueError(f"Unsafe or unsupported selector syntax: '{trimmed}'")

    try:
        soupsieve.compile(trimmed)
    except Exception as e:
        raise ValueError(f"Invalid CSS selector syntax '{trimmed}': {e}")

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

    @model_validator(mode="before")
    @classmethod
    def pre_validate_and_normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # Map card selector aliases if record_item_selector is missing
        if "record_item_selector" not in data:
            for alt_card_key in ("item_selector", "card_selector", "product_card_selector", "listing_card_selector"):
                if alt_card_key in data:
                    data["record_item_selector"] = data[alt_card_key]
                    break

        # If fields dictionary was placed at the top level or needs alias mapping
        raw_fields = data.get("fields")
        fields_dict: dict[str, Any] = {}
        if isinstance(raw_fields, dict):
            fields_dict = dict(raw_fields)
        elif raw_fields is None:
            # Check if fields were provided directly at root level
            for k, v in data.items():
                if k not in (
                    "strategy_version", "page_type", "record_container_selector",
                    "record_item_selector", "fields", "rationale", "confidence", "uncertainties"
                ) and isinstance(v, (dict, FieldExtractionRule)):
                    fields_dict[k] = v

        normalized_fields: dict[str, Any] = {}
        for k, v in fields_dict.items():
            canonical = FIELD_ALIAS_MAP.get(k.lower().strip(), k.strip())
            if canonical not in normalized_fields or canonical == "title":
                normalized_fields[canonical] = v

        # Repair minimum required rules if omitted by AI
        if "title" not in normalized_fields:
            normalized_fields["title"] = {
                "selector": "h1, h2, h3, h4, [class*='title'], [class*='name'], a",
                "attribute": "text",
                "required": True,
            }
        if "url" not in normalized_fields:
            normalized_fields["url"] = {
                "selector": "a[href]",
                "attribute": "href",
                "required": True,
            }

        data["fields"] = normalized_fields
        return data

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

