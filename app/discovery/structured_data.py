import json
from typing import Any
from bs4 import BeautifulSoup
from app.extraction.normalizer import (
    clean_text,
    extract_price_and_currency,
    normalize_url,
    parse_numeric_price,
    parse_rating,
    parse_review_count,
)

ECOMMERCE_TYPES = {
    "product",
    "individualproduct",
    "productmodel",
    "productgroup",
    "offer",
}

GENERIC_LISTING_TYPES = {
    "jobposting",
    "realestatelisting",
    "singlefamilyresidence",
    "apartment",
    "residence",
    "place",
    "localbusiness",
    "organization",
    "event",
    "article",
    "newsarticle",
}


def parse_json_ld_string(content: str) -> list[dict[str, Any]]:
    clean = content.strip()
    if not clean:
        return []
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    return []


def collect_schema_nodes(data: Any, out_nodes: list[dict[str, Any]]) -> None:
    if isinstance(data, dict):
        if "@type" in data:
            out_nodes.append(data)
        for val in data.values():
            collect_schema_nodes(val, out_nodes)
    elif isinstance(data, list):
        for item in data:
            collect_schema_nodes(item, out_nodes)


def extract_image_url(image_field: Any) -> str | None:
    if isinstance(image_field, str):
        return image_field.strip() or None
    if isinstance(image_field, list) and image_field:
        first = image_field[0]
        if isinstance(first, str):
            return first.strip() or None
        if isinstance(first, dict):
            return first.get("url")
    if isinstance(image_field, dict):
        return image_field.get("url")
    return None


def extract_price_info(offers_field: Any) -> tuple[float | None, float | None, str | None]:
    if not offers_field:
        return None, None, None

    offer = offers_field[0] if isinstance(offers_field, list) and offers_field else offers_field
    if not isinstance(offer, dict):
        return None, None, None

    raw_price = offer.get("price") or offer.get("lowPrice")
    raw_mrp = offer.get("highPrice") or offer.get("listPrice")
    currency = offer.get("priceCurrency")

    selling_price: float | None = None
    if raw_price is not None:
        if isinstance(raw_price, (int, float)):
            selling_price = float(raw_price)
        elif isinstance(raw_price, str):
            selling_price, parsed_cur = extract_price_and_currency(raw_price)
            if not currency and parsed_cur:
                currency = parsed_cur

    mrp: float | None = None
    if raw_mrp is not None:
        if isinstance(raw_mrp, (int, float)):
            mrp = float(raw_mrp)
        elif isinstance(raw_mrp, str):
            mrp, _ = extract_price_and_currency(raw_mrp)

    return selling_price, mrp, currency


def extract_rating_info(agg_rating: Any) -> tuple[float | None, int | None]:
    if not isinstance(agg_rating, dict):
        return None, None

    raw_val = agg_rating.get("ratingValue")
    raw_count = agg_rating.get("reviewCount") or agg_rating.get("ratingCount")

    rating: float | None = None
    if raw_val is not None:
        if isinstance(raw_val, (int, float)):
            rating = float(raw_val)
        elif isinstance(raw_val, str):
            rating = parse_rating(raw_val)

    review_count: int | None = None
    if raw_count is not None:
        if isinstance(raw_count, int):
            review_count = raw_count
        elif isinstance(raw_count, str):
            review_count = parse_review_count(raw_count)

    return rating, review_count


def extract_extra_attributes(node: dict[str, Any], node_type: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}

    if "brand" in node:
        brand = node["brand"]
        extra["brand"] = brand.get("name") if isinstance(brand, dict) else str(brand)

    if "category" in node:
        extra["category"] = str(node["category"])

    if node_type == "jobposting":
        if "hiringOrganization" in node:
            org = node["hiringOrganization"]
            extra["company"] = org.get("name") if isinstance(org, dict) else str(org)
        if "jobLocation" in node:
            loc = node["jobLocation"]
            if isinstance(loc, dict):
                address = loc.get("address", {})
                if isinstance(address, dict):
                    extra["location"] = address.get("addressLocality") or address.get("streetAddress")
                else:
                    extra["location"] = str(address)
            else:
                extra["location"] = str(loc)
        if "employmentType" in node:
            extra["employmentType"] = node["employmentType"]

    elif node_type in ("realestatelisting", "singlefamilyresidence", "apartment"):
        if "numberOfRooms" in node:
            extra["rooms"] = node["numberOfRooms"]
        if "floorSize" in node:
            extra["floorSize"] = str(node["floorSize"])

    return extra


def parse_schema_node(node: dict[str, Any], base_url: str) -> tuple[dict[str, Any] | None, str | None]:
    raw_type = node.get("@type")
    if not raw_type:
        return None, None

    types = [raw_type.lower()] if isinstance(raw_type, str) else [t.lower() for t in raw_type if isinstance(t, str)]
    detected_type: str | None = None
    matched_type = types[0]

    if any(t in ECOMMERCE_TYPES for t in types):
        detected_type = "ecommerce"
    elif any(t in GENERIC_LISTING_TYPES for t in types):
        detected_type = "generic_listing"
    else:
        return None, None

    title = clean_text(node.get("name") or node.get("headline") or node.get("title"))
    if not title:
        return None, None

    raw_url = node.get("url") or node.get("@id")
    url = normalize_url(base_url, raw_url) or base_url

    raw_sku = node.get("sku") or node.get("productID") or node.get("identifier") or node.get("gtin13")
    product_id = str(raw_sku) if raw_sku else None

    raw_img = extract_image_url(node.get("image"))
    image_url = normalize_url(base_url, raw_img) if raw_img else None

    selling_price, mrp, currency = extract_price_info(node.get("offers"))
    rating, review_count = extract_rating_info(node.get("aggregateRating"))
    extra = extract_extra_attributes(node, matched_type)

    record: dict[str, Any] = {
        "title": title,
        "url": url,
        "productId": product_id,
        "imageUrl": image_url,
        "sellingPrice": selling_price,
        "mrp": mrp,
        "currency": currency,
        "rating": rating,
        "reviewCount": review_count,
        "extra": extra,
    }
    return record, detected_type


def extract_from_structured_data(html: str, base_url: str) -> tuple[list[dict[str, Any]], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", type=lambda t: t and "ld+json" in t.lower())

    records: list[dict[str, Any]] = []
    type_votes: list[str] = []

    for script in scripts:
        if not script.string:
            continue
        parsed_objects = parse_json_ld_string(script.string)
        all_nodes: list[dict[str, Any]] = []
        for obj in parsed_objects:
            collect_schema_nodes(obj, all_nodes)

        for node in all_nodes:
            rec, page_type = parse_schema_node(node, base_url)
            if rec:
                records.append(rec)
                if page_type:
                    type_votes.append(page_type)

    detected_type: str | None = None
    if type_votes:
        ecommerce_count = type_votes.count("ecommerce")
        generic_count = type_votes.count("generic_listing")
        detected_type = "ecommerce" if ecommerce_count >= generic_count else "generic_listing"

    return records, detected_type
