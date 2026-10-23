from pathlib import Path
from app.discovery.structured_data import extract_from_structured_data
from app.extraction.engine import run_extraction_pipeline
from app.extraction.normalizer import (
    extract_price_and_currency,
    normalize_url,
    parse_rating,
    parse_review_count,
)
from app.extraction.validator import validate_extracted_records

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


def test_normalizer_price_and_currency() -> None:
    price, cur = extract_price_and_currency("₹69,999")
    assert price == 69999.0
    assert cur == "INR"

    price, cur = extract_price_and_currency("$499.99")
    assert price == 499.99
    assert cur == "USD"

    price, cur = extract_price_and_currency("€35.50")
    assert price == 35.50
    assert cur == "EUR"

    price, cur = extract_price_and_currency("£120")
    assert price == 120.0
    assert cur == "GBP"

    # Should not mistake phone numbers for prices
    phone_price, phone_cur = extract_price_and_currency("+91 9876543210")
    assert phone_price is None

    # Should not mistake pure discount percentages for prices
    disc_price, disc_cur = extract_price_and_currency("50% off discount")
    assert disc_price is None


def test_normalizer_rating_and_reviews() -> None:
    assert parse_rating("4.5 out of 5") == 4.5
    assert parse_rating("4.8/5") == 4.8
    assert parse_rating("4.2 ★") == 4.2
    assert parse_rating("Invalid Rating") is None

    assert parse_review_count("(1,240 reviews)") == 1240
    assert parse_review_count("350 ratings") == 350
    assert parse_review_count("1.5k reviews") == 1500


def test_normalizer_url() -> None:
    base = "https://example.com/catalog/electronics"
    assert normalize_url(base, "/item/123#reviews") == "https://example.com/item/123"
    assert normalize_url(base, "item/456?ref=search") == "https://example.com/catalog/item/456?ref=search"
    assert normalize_url(base, "javascript:void(0)") is None
    assert normalize_url(base, "mailto:help@example.com") is None


def test_structured_data_extraction() -> None:
    html = load_fixture("jsonld_ecommerce.html")
    records, detected_type = extract_from_structured_data(html, "https://example.com/audio")

    assert detected_type == "ecommerce"
    assert len(records) == 2

    first = records[0]
    assert first["title"] == "Sony WH-1000XM5 Noise-Canceling Headphones"
    assert first["sellingPrice"] == 29990.0
    assert first["mrp"] == 34990.0
    assert first["currency"] == "INR"
    assert first["productId"] == "SONY-XM5-BLK"
    assert first["rating"] == 4.6
    assert first["reviewCount"] == 4510


def test_pipeline_ecommerce_products() -> None:
    html = load_fixture("ecommerce_products.html")
    result = run_extraction_pipeline(html, "https://example.com/search", "auto")

    assert result["success"] is True
    assert result["pageType"] == "ecommerce"
    assert result["metadata"]["recordCount"] == 3
    assert result["metadata"]["validationStatus"] == "passed"
    assert len(result["records"]) == 3


def test_pipeline_generic_jobs() -> None:
    html = load_fixture("generic_jobs.html")
    result = run_extraction_pipeline(html, "https://example.com/careers", "auto")

    assert result["success"] is True
    assert result["pageType"] == "generic_listing"
    assert result["metadata"]["recordCount"] == 3
    assert result["metadata"]["validationStatus"] == "passed"
    assert result["records"][0]["extra"]["company"] == "Acme Cloud Systems"


def test_pipeline_listings_without_prices() -> None:
    html = load_fixture("no_prices_listing.html")
    result = run_extraction_pipeline(html, "https://example.com/repos", "generic_listing")

    assert result["success"] is True
    assert result["metadata"]["recordCount"] == 3
    for r in result["records"]:
        assert r["sellingPrice"] is None


def test_pipeline_listings_without_images() -> None:
    html = load_fixture("no_images_listing.html")
    result = run_extraction_pipeline(html, "https://example.com/commercial", "generic_listing")

    assert result["success"] is True
    assert result["metadata"]["recordCount"] == 3
    for r in result["records"]:
        assert r["imageUrl"] is None


def test_pipeline_deduplication() -> None:
    html = load_fixture("duplicate_records.html")
    result = run_extraction_pipeline(html, "https://example.com/deals", "ecommerce")

    assert result["success"] is True
    assert result["metadata"]["recordCount"] == 3
    urls = [r["url"] for r in result["records"]]
    assert len(urls) == len(set(urls))


def test_pipeline_editorial_page_fails_validation() -> None:
    html = load_fixture("no_listings_page.html")
    result = run_extraction_pipeline(html, "https://example.com/terms", "auto")

    assert result["success"] is False
    assert result["metadata"]["recordCount"] == 0
    assert result["metadata"]["validationStatus"] == "failed"
    assert len(result["records"]) == 0


def test_validator_drops_boilerplate_records() -> None:
    candidates = [
        {"title": "Privacy Policy", "url": "https://example.com/privacy"},
        {"title": "Valid Job Listing", "url": "https://example.com/jobs/1"},
    ]
    is_valid, status, valid, warnings = validate_extracted_records(candidates, "generic_listing")
    assert is_valid is True
    assert status == "partial"
    assert len(valid) == 1
    assert valid[0]["title"] == "Valid Job Listing"
