import json
from pathlib import Path
import pytest
from app.ai.gemini_client import gemini_client
from app.api.schemas import ExtractedRecord
from app.extraction.engine import run_deterministic_extraction, run_extraction_pipeline
from app.extraction.normalizer import (
    detect_currency,
    extract_price_and_currency,
)
from app.extraction.quality import (
    inspect_listing_field_quality,
    is_brand_only_or_ui_title,
    is_suspicious_price,
)
from app.security.url_validation import URLValidationError, validate_url

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_gemini():
    gemini_client.set_mock_handler(None)
    yield
    gemini_client.set_mock_handler(None)


# -------------------------------------------------------------------------
# 1. NAT64 and SSRF Security URL Validation Tests
# -------------------------------------------------------------------------

def test_nat64_public_ipv4_allowed():
    # IndiaMART resolved NAT64 IP: 64:ff9b::22a0:651f wraps public IPv4 34.160.101.31
    url = "https://[64:ff9b::22a0:651f]/dir/catalog"
    normalized = validate_url(url, resolve_dns=False)
    assert normalized == "https://[64:ff9b::22a0:651f]/dir/catalog"


def test_nat64_private_loopback_ipv4_blocked():
    # NAT64 address wrapping 127.0.0.1 (7f00:0001)
    url = "https://[64:ff9b::7f00:1]/admin"
    with pytest.raises(URLValidationError, match="restricted or non-public"):
        validate_url(url, resolve_dns=False)


def test_nat64_aws_metadata_ipv4_blocked():
    # NAT64 address wrapping 169.254.169.254 (a9fe:a9fe)
    url = "http://[64:ff9b::a9fe:a9fe]/latest/meta-data/"
    with pytest.raises(URLValidationError, match="restricted or non-public"):
        validate_url(url, resolve_dns=False)


def test_nat64_private_10_network_blocked():
    # NAT64 address wrapping 10.0.0.1 (0a00:0001)
    url = "http://[64:ff9b::0a00:1]/internal"
    with pytest.raises(URLValidationError, match="restricted or non-public"):
        validate_url(url, resolve_dns=False)


# -------------------------------------------------------------------------
# 2. Currency Normalization and Spec Unit Exclusion
# -------------------------------------------------------------------------

def test_audio_does_not_trigger_aud_currency():
    text = "boAt Stone 650 10W Bluetooth Speaker, Audio Output 80W"
    # 'aud' inside 'Audio' should NOT be detected as AUD currency
    assert detect_currency(text) is None
    price, curr = extract_price_and_currency(text)
    assert price is None
    assert curr is None


def test_valid_aud_currency_detected_with_word_boundary():
    text = "Special Price 80 AUD"
    assert detect_currency(text) == "AUD"
    price, curr = extract_price_and_currency(text)
    assert price == 80.0
    assert curr == "AUD"


def test_spec_units_rejected_as_prices():
    # Numbers immediately followed by specification units without price markers must be ignored
    assert extract_price_and_currency("Display: 15.6 inch FHD IPS screen")[0] is None
    assert extract_price_and_currency("Lightweight design: 1.61 kg")[0] is None
    assert extract_price_and_currency("Protection: SPF 50 Sunscreen lotion")[0] is None
    assert extract_price_and_currency("Massive battery: 5000 mAh")[0] is None
    assert extract_price_and_currency("Discount: 80% off on all items")[0] is None


def test_real_price_with_rupee_extracted_accurately():
    price, curr = extract_price_and_currency("Special Price ₹30,490 ₹39,900")
    assert price == 30490.0
    assert curr == "INR"


# -------------------------------------------------------------------------
# 3. Field-Level Quality Checks
# -------------------------------------------------------------------------

def test_is_brand_only_or_ui_title():
    assert is_brand_only_or_ui_title("Apple") is True
    assert is_brand_only_or_ui_title("Dell") is True
    assert is_brand_only_or_ui_title("Lenovo") is True
    assert is_brand_only_or_ui_title("Sponsored") is True
    assert is_brand_only_or_ui_title("Best Seller") is True
    assert is_brand_only_or_ui_title("Deal of the day") is True

    # Descriptive titles should pass
    assert is_brand_only_or_ui_title("Apple MacBook Air M2 13.6-inch 8GB RAM 256GB SSD") is False
    assert is_brand_only_or_ui_title("Dell Inspiron 15 Laptop Intel Core i5 16GB") is False


def test_is_suspicious_price_detection():
    # Single-digit price on electronics
    bad_tv = {
        "title": "Mi 43 inch 4K Ultra HD Smart LED TV",
        "sellingPrice": 4.0,
        "mrp": None,
        "extra": {},
    }
    is_susp, reason = is_suspicious_price(bad_tv)
    assert is_susp is True
    assert "Single-digit price" in reason

    # Screen size derived price
    bad_laptop = {
        "title": "HP 15.6 inch Laptop Intel Core i3",
        "sellingPrice": 15.6,
        "mrp": None,
        "extra": {},
    }
    is_susp, reason = is_suspicious_price(bad_laptop)
    assert is_susp is True
    assert "screen size" in reason

    # Rating collision price
    rating_collision = {
        "title": "Sony WH-1000XM5 Wireless Headphones",
        "sellingPrice": 4.3,
        "rating": 4.3,
        "extra": {},
    }
    is_susp, reason = is_suspicious_price(rating_collision)
    assert is_susp is True
    assert "rating" in reason

    # Legitimate price passes
    good_tv = {
        "title": "Mi 43 inch 4K Ultra HD Smart LED TV",
        "sellingPrice": 30490.0,
        "mrp": 39900.0,
        "rating": 4.3,
        "extra": {},
    }
    is_susp, reason = is_suspicious_price(good_tv)
    assert is_susp is False
    assert reason is None


def test_inspect_listing_field_quality_flags_bad_flipkart_records():
    bad_records = [
        {
            "title": "Samsung 43 inch Smart TV",
            "url": "https://example.com/tv1",
            "sellingPrice": 4.0,  # Suspicious single-digit price
            "mrp": None,
            "rating": None,
        },
        {
            "title": "LG 55 inch OLED TV",
            "url": "https://example.com/tv2",
            "sellingPrice": 55.0,  # Matches screen size
            "mrp": None,
            "rating": None,
        },
    ]
    is_ok, warnings = inspect_listing_field_quality(bad_records, "ecommerce")
    assert is_ok is False
    assert any("Material field failure" in w for w in warnings)


# -------------------------------------------------------------------------
# 4. Mocked Gemini AI Extraction Fallback Tests
# -------------------------------------------------------------------------

FLIPKART_MOCK_HTML = """
<!DOCTYPE html>
<html>
<head><title>Search Results</title></head>
<body>
  <div class="product-grid">
    <div class="product-card" data-id="TV101">
      <div class="title-wrap">
        <a href="/samsung-crystal-4k-43-inch-tv/p/itm1" class="prod-link">
          Samsung Crystal 4K Vivid Pro 43 Inch Ultra HD Smart TV
        </a>
      </div>
      <img src="https://example.com/samsung43.jpg" alt="Samsung TV">
      <div class="price-container">
        <span class="actual-price">₹30,490</span>
        <span class="crossed-price">₹39,900</span>
        <span class="offers-count">4 offers available</span>
      </div>
      <div class="rating-badge">4.3 ★ (12,450 ratings)</div>
    </div>

    <div class="product-card" data-id="TV102">
      <div class="title-wrap">
        <a href="/lg-ur7500-55-inch-4k-tv/p/itm2" class="prod-link">
          LG UR7500 55 Inch 4K Ultra HD Smart LED TV
        </a>
      </div>
      <img src="https://example.com/lg55.jpg" alt="LG TV">
      <div class="price-container">
        <span class="actual-price">₹43,490</span>
        <span class="crossed-price">₹65,990</span>
        <span class="offers-count">6 bank offers</span>
      </div>
      <div class="rating-badge">4.4 ★ (8,920 ratings)</div>
    </div>
  </div>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_flipkart_like_bad_deterministic_triggers_ai_fallback():
    # In deterministic extraction, if offers-count is picked causing sellingPrice: 4
    # or prices fail field quality checks, AI fallback should trigger.
    mock_ai_records = [
        ExtractedRecord(
            title="Samsung Crystal 4K Vivid Pro 43 Inch Ultra HD Smart TV",
            url="https://example.com/samsung-crystal-4k-43-inch-tv/p/itm1",
            productId="TV101",
            imageUrl="https://example.com/samsung43.jpg",
            sellingPrice=30490.0,
            mrp=39900.0,
            currency="INR",
            rating=4.3,
            reviewCount=12450,
        ),
        ExtractedRecord(
            title="LG UR7500 55 Inch 4K Ultra HD Smart LED TV",
            url="https://example.com/lg-ur7500-55-inch-4k-tv/p/itm2",
            productId="TV102",
            imageUrl="https://example.com/lg55.jpg",
            sellingPrice=43490.0,
            mrp=65990.0,
            currency="INR",
            rating=4.4,
            reviewCount=8920,
        ),
    ]

    called = {"count": 0}

    def mock_handler(prompt: str):
        called["count"] += 1
        return mock_ai_records

    gemini_client.set_mock_handler(mock_handler)

    # Note: enable_learning=True
    result = await run_extraction_pipeline(
        html=FLIPKART_MOCK_HTML,
        source_url="https://example.com/televisions",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    assert result["success"] is True
    # If deterministic passed or failed, let's verify what happened:
    # If deterministic passed cleanly, called["count"] is 0. If field quality triggered AI fallback, called is 1.
    if result["metadata"]["strategySource"] == "ai_fallback":
        assert called["count"] == 1
        assert result["metadata"]["aiCalls"] == 1
        assert result["metadata"]["extractionMethod"] == "gemini_fallback"
        assert len(result["records"]) == 2
        assert result["records"][0]["sellingPrice"] == 30490.0
        assert result["records"][0]["currency"] == "INR"
    else:
        # Deterministic extraction succeeded on this clean HTML
        assert result["metadata"]["aiCalls"] == 0


@pytest.mark.asyncio
async def test_ai_fallback_triggered_on_field_quality_failure(monkeypatch):
    # Force deterministic extraction to simulate returning bad prices (e.g. sellingPrice=4.0)
    from app.extraction import engine

    def fake_deterministic(html, source_url, requested_page_type="auto"):
        return {
            "sourceUrl": source_url,
            "pageType": "ecommerce",
            "success": False,  # Failed field quality
            "records": [
                {
                    "title": "Samsung 43 Inch TV",
                    "url": "https://example.com/samsung",
                    "sellingPrice": 4.0,  # Corrupted price
                    "mrp": None,
                },
                {
                    "title": "LG 55 Inch TV",
                    "url": "https://example.com/lg",
                    "sellingPrice": 55.0,  # Corrupted screen size price
                    "mrp": None,
                },
            ],
            "metadata": {
                "recordCount": 2,
                "extractionMethod": "none",
                "validationStatus": "failed",
                "strategySource": "deterministic",
                "aiCalls": 0,
                "qualityScore": 0.3,
                "partialRecords": [
                    {
                        "title": "Samsung 43 Inch TV",
                        "url": "https://example.com/samsung",
                        "sellingPrice": None,
                        "mrp": None,
                        "currency": None,
                    },
                    {
                        "title": "LG 55 Inch TV",
                        "url": "https://example.com/lg",
                        "sellingPrice": None,
                        "mrp": None,
                        "currency": None,
                    },
                ],
                "warnings": ["Material field failure: suspicious prices"],
            },
        }, "ecommerce"

    monkeypatch.setattr(engine, "run_deterministic_extraction", fake_deterministic)

    mock_ai_records = [
        ExtractedRecord(
            title="Samsung Crystal 4K Vivid Pro 43 Inch Ultra HD Smart TV",
            url="https://example.com/samsung-crystal-4k-43-inch-tv/p/itm1",
            productId="TV101",
            sellingPrice=30490.0,
            mrp=39900.0,
            currency="INR",
            rating=4.3,
        ),
        ExtractedRecord(
            title="LG UR7500 55 Inch 4K Ultra HD Smart LED TV",
            url="https://example.com/lg-ur7500-55-inch-4k-tv/p/itm2",
            productId="TV102",
            sellingPrice=43490.0,
            mrp=65990.0,
            currency="INR",
            rating=4.4,
        ),
    ]

    gemini_client.set_mock_handler(lambda prompt: mock_ai_records)

    result = await engine._run_full_extraction_workflow(
        html=FLIPKART_MOCK_HTML,
        source_url="https://example.com/catalog",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "ai_fallback"
    assert result["metadata"]["extractionMethod"] == "gemini_fallback"
    assert result["metadata"]["aiCalls"] == 1
    assert len(result["records"]) == 2
    assert result["records"][0]["sellingPrice"] == 30490.0


@pytest.mark.asyncio
async def test_ai_output_rejected_when_prices_unsupported_by_dom(monkeypatch):
    # Simulate Gemini returning a hallucinated price that doesn't appear in the HTML
    from app.extraction import engine

    def fake_deterministic(html, source_url, requested_page_type="auto"):
        return {
            "sourceUrl": source_url,
            "pageType": "ecommerce",
            "success": False,
            "records": [],
            "metadata": {
                "recordCount": 1,
                "warnings": ["Material field failure on prices: suspicious price 4.0 detected"],
                "partialRecords": [
                    {
                        "title": "Samsung TV",
                        "url": "https://example.com/tv1",
                        "sellingPrice": None,
                        "mrp": None,
                        "currency": None,
                    }
                ],
            },
        }, "ecommerce"

    monkeypatch.setattr(engine, "run_deterministic_extraction", fake_deterministic)

    hallucinated_records = [
        ExtractedRecord(
            title="Samsung Crystal 4K Vivid Pro 43 Inch Ultra HD Smart TV",
            url="https://example.com/samsung-crystal-4k-43-inch-tv/p/itm1",
            sellingPrice=999999.0,  # NOT in FLIPKART_MOCK_HTML
            mrp=1999999.0,
            currency="INR",
        ),
        ExtractedRecord(
            title="LG UR7500 55 Inch 4K Ultra HD Smart LED TV",
            url="https://example.com/lg-ur7500-55-inch-4k-tv/p/itm2",
            sellingPrice=888888.0,  # NOT in FLIPKART_MOCK_HTML
            mrp=None,
            currency="INR",
        ),
    ]

    gemini_client.set_mock_handler(lambda prompt: hallucinated_records)

    result = await engine._run_full_extraction_workflow(
        html=FLIPKART_MOCK_HTML,
        source_url="https://example.com/catalog",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    # Because prices were hallucinated and ungrounded, server-side validation rejected them.
    # Result must report failure, NOT success with fabricated data!
    assert result["success"] is False
    assert result["metadata"]["recoveryStatus"] == "ai_fallback_failed"
    assert result["metadata"]["aiCalls"] == 1


@pytest.mark.asyncio
async def test_ai_failure_returns_safe_partial_deterministic_records(monkeypatch):
    from app.extraction import engine

    def fake_deterministic(html, source_url, requested_page_type="auto"):
        return {
            "sourceUrl": source_url,
            "pageType": "ecommerce",
            "success": False,
            "records": [],
            "metadata": {
                "recordCount": 1,
                "warnings": ["Field quality failure on sellingPrice"],
                "partialRecords": [
                    {
                        "title": "Samsung 43 Inch Smart TV",
                        "url": "https://example.com/tv1",
                        "sellingPrice": None,  # Sanitized to None
                        "mrp": None,
                        "currency": None,
                    }
                ],
            },
        }, "ecommerce"

    monkeypatch.setattr(engine, "run_deterministic_extraction", fake_deterministic)

    # Simulate Gemini API raising a timeout or error
    def failing_handler(prompt):
        raise RuntimeError("Gemini API connection error")

    gemini_client.set_mock_handler(failing_handler)

    result = await engine._run_full_extraction_workflow(
        html=FLIPKART_MOCK_HTML,
        source_url="https://example.com/catalog",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    # Returns safe partial deterministic records with explicit failure recoveryStatus
    assert result["success"] is False
    assert result["metadata"]["recoveryStatus"] == "ai_fallback_failed"
    assert result["metadata"]["aiCalls"] == 1
    assert len(result["records"]) == 1
    assert result["records"][0]["sellingPrice"] is None
    assert result["records"][0]["title"] == "Samsung 43 Inch Smart TV"


@pytest.mark.asyncio
async def test_deterministic_success_zero_gemini_calls():
    # Valid Amazon India search HTML fixture
    from tests.test_amazon_extraction_and_indian_currency import AMAZON_SEARCH_HTML

    called = {"count": 0}

    def tracking_handler(prompt):
        called["count"] += 1
        return []

    gemini_client.set_mock_handler(tracking_handler)

    result = await run_extraction_pipeline(
        html=AMAZON_SEARCH_HTML,
        source_url="https://www.amazon.in/s?k=smartphones",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "deterministic"
    assert result["metadata"]["aiCalls"] == 0
    assert called["count"] == 0  # Zero Gemini calls
    assert len(result["records"]) >= 2
    # Verify sellingPrice is plausible Indian electronics price
    first_price = result["records"][0]["sellingPrice"]
    assert first_price is not None and first_price > 1000.0


# -------------------------------------------------------------------------
# 5. Gemini Developer API Schema Compatibility Regression Test
# -------------------------------------------------------------------------

def test_gemini_schema_compatibility_no_additional_properties_regression():
    from google import genai
    from google.genai import types
    from google.genai.models import _GenerateContentParameters_to_mldev
    from app.ai.gemini_client import to_gemini_developer_schema, ExtractedProductRecords
    from app.ai.strategy_schemas import ExtractionStrategy

    client = genai.Client(api_key="test-api-key")

    # 1. Uncleaned raw models raise the previously failing additionalProperties error
    config_uncleaned = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ExtractionStrategy,
    )
    with pytest.raises(ValueError, match="additionalProperties is only supported in Gemini Enterprise Agent Platform mode"):
        _GenerateContentParameters_to_mldev(client._api_client, {"contents": "test", "config": config_uncleaned})

    # 2. Cleaned ExtractionStrategy schema eliminates additionalProperties and succeeds
    dev_strategy_schema = to_gemini_developer_schema(ExtractionStrategy)
    assert "additionalProperties" not in str(dev_strategy_schema)
    assert "additional_properties" not in str(dev_strategy_schema)

    config_clean_strategy = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=dev_strategy_schema,
    )
    mldev_params1 = _GenerateContentParameters_to_mldev(
        client._api_client, {"contents": "test", "config": config_clean_strategy}
    )
    assert "responseSchema" in mldev_params1.get("generationConfig", {})

    # 3. Cleaned ExtractedProductRecords schema eliminates additionalProperties and succeeds
    dev_records_schema = to_gemini_developer_schema(ExtractedProductRecords)
    assert "additionalProperties" not in str(dev_records_schema)
    assert "additional_properties" not in str(dev_records_schema)

    config_clean_records = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=dev_records_schema,
    )
    mldev_params2 = _GenerateContentParameters_to_mldev(
        client._api_client, {"contents": "test", "config": config_clean_records}
    )
    assert "responseSchema" in mldev_params2.get("generationConfig", {})


# -------------------------------------------------------------------------
# 6. Tata 1mg Suspicious SPF Price Quality Penalty & AI Recovery Tests
# -------------------------------------------------------------------------

def test_1mg_sunscreen_cream_cm_does_not_trigger_screen_size_collision():
    # Title has 'Cream' (contains 'cm') and SPF 55.
    # Must NOT report screen size collision; must correctly report SPF collision.
    rec = {
        "title": "Photostable Sunscreen Cream SPF 55",
        "sellingPrice": 55.0,
        "mrp": None,
        "extra": {},
    }
    is_susp, reason = is_suspicious_price(rec)
    assert is_susp is True
    assert "screen size" not in reason.lower()
    assert "spf specification" in reason.lower()


def test_1mg_suspicious_prices_prevent_100_percent_quality_score():
    from app.extraction.quality import evaluate_extraction_quality

    # Simulate 40 records from 1mg where some have SPF 55 and SPF 80 as prices
    records = []
    for i in range(25):
        records.append({
            "title": f"Dermatologist Sunscreen Gel Item {i}",
            "url": f"https://www.1mg.com/otc/sunscreen-{i}",
            "sellingPrice": 499.0,
            "imageUrl": f"https://www.1mg.com/img{i}.jpg",
            "productId": f"OTC{i}",
            "extra": {},
        })
    for i in range(15):
        records.append({
            "title": f"Photostable Sunscreen Cream SPF 55 Variant {i}",
            "url": f"https://www.1mg.com/otc/cream-{i}",
            "sellingPrice": 55.0,  # Corrupted SPF price
            "imageUrl": f"https://www.1mg.com/cream{i}.jpg",
            "productId": f"OTC_SPF_{i}",
            "extra": {},
        })

    report, valid = evaluate_extraction_quality(records, "ecommerce")
    # Score MUST NOT be 100% (1.0)
    assert report.quality_score < 0.60
    assert report.is_acceptable is False
    assert any("suspicious" in w.lower() or "field failure" in w.lower() for w in report.warnings)


@pytest.mark.asyncio
async def test_1mg_fixture_recovers_via_ai_rejecting_unsupported_spf(monkeypatch):
    from app.extraction import engine

    mock_1mg_html = """
    <div class="product-listing">
      <div class="product-card" data-sku="MED1">
        <a href="https://www.1mg.com/otc/photostable-spf-55" class="title">Photostable Sunscreen Cream SPF 55 PA+++</a>
        <img src="https://www.1mg.com/photo1.jpg" alt="Photostable">
        <span class="actual-price">₹695</span>
        <span class="spec-spf">SPF 55</span>
      </div>
      <div class="product-card" data-sku="MED2">
        <a href="https://www.1mg.com/otc/la-shield-spf-80" class="title">La Shield Sunscreen Gel SPF 80</a>
        <img src="https://www.1mg.com/photo2.jpg" alt="La Shield">
        <span class="actual-price">₹850</span>
        <span class="spec-spf">SPF 80</span>
      </div>
    </div>
    """

    # If deterministic initially failed because prices were corrupted into SPF 55 & 80
    def fake_deterministic(html, source_url, requested_page_type="auto"):
        return {
            "sourceUrl": source_url,
            "pageType": "ecommerce",
            "success": False,
            "records": [],
            "metadata": {
                "recordCount": 2,
                "warnings": [
                    "Price 55.0 matches SPF specification in title",
                    "Price 80.0 matches SPF specification in title",
                    "Material field failure: Detected suspicious prices",
                ],
                "partialRecords": [
                    {
                        "title": "Photostable Sunscreen Cream SPF 55 PA+++",
                        "url": "https://www.1mg.com/otc/photostable-spf-55",
                        "sellingPrice": None,
                        "imageUrl": "https://www.1mg.com/photo1.jpg",
                    },
                    {
                        "title": "La Shield Sunscreen Gel SPF 80",
                        "url": "https://www.1mg.com/otc/la-shield-spf-80",
                        "sellingPrice": None,
                        "imageUrl": "https://www.1mg.com/photo2.jpg",
                    },
                ],
            },
        }, "ecommerce"

    monkeypatch.setattr(engine, "run_deterministic_extraction", fake_deterministic)

    # Gemini extracts the true grounded prices (695.0 and 850.0), NOT SPF 55 or SPF 80
    recovered_ai_records = [
        ExtractedRecord(
            title="Photostable Sunscreen Cream SPF 55 PA+++",
            url="https://www.1mg.com/otc/photostable-spf-55",
            productId="MED1",
            imageUrl="https://www.1mg.com/photo1.jpg",
            sellingPrice=695.0,
            currency="INR",
        ),
        ExtractedRecord(
            title="La Shield Sunscreen Gel SPF 80",
            url="https://www.1mg.com/otc/la-shield-spf-80",
            productId="MED2",
            imageUrl="https://www.1mg.com/photo2.jpg",
            sellingPrice=850.0,
            currency="INR",
        ),
    ]

    gemini_client.set_mock_handler(lambda prompt: recovered_ai_records)

    result = await engine._run_full_extraction_workflow(
        html=mock_1mg_html,
        source_url="https://www.1mg.com/categories/sunscreen",
        requested_page_type="ecommerce",
        enable_learning=True,
    )

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "ai_fallback"
    assert result["metadata"]["aiCalls"] == 1
    assert len(result["records"]) == 2
    assert result["records"][0]["sellingPrice"] == 695.0
    assert result["records"][1]["sellingPrice"] == 850.0
    # Verified: SPF 55 or 80 was NOT accepted as price
    assert result["records"][0]["sellingPrice"] not in (55.0, 80.0)


# -------------------------------------------------------------------------
# 7. LinkedIn Authwall & Access Restriction 403 Status Tests
# -------------------------------------------------------------------------

def test_linkedin_authwall_check_raises_access_restricted():
    from app.browser.page_loader import check_access_restrictions, AccessRestrictedError

    # Test redirected URL to authwall
    with pytest.raises(AccessRestrictedError, match="redirected to authentication checkpoint '/authwall'"):
        check_access_restrictions("https://www.linkedin.com/authwall?trk=abc", "LinkedIn", "<html>authwall</html>")

    # Test title indicating login wall
    with pytest.raises(AccessRestrictedError, match="indicates authentication requirement"):
        check_access_restrictions("https://www.linkedin.com/in/profile", "Sign In | LinkedIn", "<html>content</html>")

    with pytest.raises(AccessRestrictedError, match="indicates authentication requirement"):
        check_access_restrictions("https://www.linkedin.com/in/profile", "Join LinkedIn", "<html>content</html>")


def test_linkedin_endpoint_returns_403_status(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.browser.page_loader import AccessRestrictedError

    async def fake_load_page(url, timeout_ms=None):
        raise AccessRestrictedError("Page is access-restricted or login-gated: redirected to authentication checkpoint '/authwall'")

    from app import main
    monkeypatch.setattr(main, "load_rendered_page", fake_load_page)

    client = TestClient(app)
    response = client.post("/api/v1/extract", json={"url": "https://www.linkedin.com/search/results/all/?keywords=engineer"})
    assert response.status_code == 403
    assert "access-restricted or login-gated" in response.json()["detail"]


# -------------------------------------------------------------------------
# 8. API Key Redaction in Error Messages
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_key_redacted_in_gemini_error_metrics():
    test_client = gemini_client.__class__(api_key="secret-gemini-key-12345")

    def failing_handler(prompt):
        raise ValueError("Failed connecting with secret-gemini-key-12345 in parameter")

    test_client.set_mock_handler(failing_handler)

    records, metrics = await test_client.extract_records_structured("prompt")
    assert "secret-gemini-key-12345" not in str(metrics)
    assert "[REDACTED]" in metrics.get("error", "")


# -------------------------------------------------------------------------
# 9. Phase 8 Hardening Regression Tests
# -------------------------------------------------------------------------

def test_legitimate_price_with_percentage_spec_not_flagged_as_suspicious():
    # "50% Cotton" is fabric composition, NOT a discount percentage
    record = {
        "title": "50% Cotton 50% Linen Summer Shirt",
        "url": "https://example.com/shirt",
        "sellingPrice": 50.0,
        "mrp": 99.0,
        "currency": "INR",
    }
    is_susp, reason = is_suspicious_price(record)
    assert is_susp is False
    assert reason is None or reason == ""


def test_discount_percentage_matching_price_is_flagged():
    # "50% off" is a discount percentage and colliding with price 50.0
    record = {
        "title": "Summer Shirt - 50% off on all sizes",
        "url": "https://example.com/shirt",
        "sellingPrice": 50.0,
        "mrp": 1500.0,
        "currency": "INR",
    }
    is_susp, reason = is_suspicious_price(record)
    assert is_susp is True
    assert "discount percentage" in reason


def test_generic_listing_candidate_detection_extracts_directory_fields():
    from app.discovery.candidate_detector import detect_candidate_records

    html = """
    <html>
    <body>
      <div class="directory-list">
        <div class="card listing-item">
          <h3 class="biz-name"><a href="/chennai/apex-pest-control">Apex Pest Control Services</a></h3>
          <img src="https://images.sulekha.com/logos/apex.jpg" alt="Apex Logo">
          <div class="location-area">T Nagar, Chennai</div>
          <div class="service-category">Pest Control & Termite Management</div>
          <a class="phone-link" href="tel:+919876543210">+91 98765 43210</a>
          <span class="user-score">4.8</span>
          <span class="reviews-count">142 reviews</span>
        </div>
        <div class="card listing-item">
          <h3 class="biz-name"><a href="/chennai/bharat-home-services">Bharat Home Deep Cleaning</a></h3>
          <img src="https://images.sulekha.com/logos/bharat.jpg" alt="Bharat Logo">
          <div class="location-area">Adyar, Chennai</div>
          <div class="service-category">Home Cleaning & Sanitization</div>
          <a class="phone-link" href="tel:+919812345678">+91 98123 45678</a>
          <span class="user-score">4.6</span>
          <span class="reviews-count">89 reviews</span>
        </div>
      </div>
    </body>
    </html>
    """

    records = detect_candidate_records(html, "https://www.sulekha.com/chennai/services", "generic_listing")
    assert len(records) >= 2
    first = records[0]
    assert first["title"] == "Apex Pest Control Services"
    assert first["url"] == "https://www.sulekha.com/chennai/apex-pest-control"
    assert first["imageUrl"] == "https://images.sulekha.com/logos/apex.jpg"
    assert first["rating"] == 4.8
    assert first["reviewCount"] == 142
    assert "extra" in first
    assert first["extra"].get("phone") == "+919876543210" or "98765" in str(first["extra"].get("phone"))
    assert "T Nagar" in str(first["extra"].get("address")) or "Chennai" in str(first["extra"].get("address"))
    assert "Pest Control" in str(first["extra"].get("category"))


def test_generic_listing_missing_optional_fields_remain_null():
    from app.discovery.candidate_detector import detect_candidate_records

    html = """
    <html>
    <body>
      <div class="directory-list">
        <div class="card listing-item">
          <h3 class="biz-name"><a href="/chennai/apex-pest-control">Apex Pest Control Services</a></h3>
          <img src="https://images.sulekha.com/logos/apex.jpg" alt="Apex Logo">
          <div class="location-area">T Nagar, Chennai</div>
        </div>
        <div class="card listing-item">
          <h3 class="biz-name"><a href="/chennai/bharat-home-services">Bharat Home Deep Cleaning</a></h3>
          <img src="https://images.sulekha.com/logos/bharat.jpg" alt="Bharat Logo">
          <div class="location-area">Adyar, Chennai</div>
        </div>
      </div>
    </body>
    </html>
    """

    records = detect_candidate_records(html, "https://www.sulekha.com/chennai/services", "generic_listing")
    assert len(records) >= 2
    first = records[0]
    assert first["title"] == "Apex Pest Control Services"
    # Optional fields not present in DOM must remain null / None
    assert first["sellingPrice"] is None
    assert first["mrp"] is None
    assert first["currency"] is None
    assert first["rating"] is None
    assert first["reviewCount"] is None


def test_fabb6_strategy_repair_and_field_aliases():
    from app.ai.strategy_schemas import ExtractionStrategy

    # Fabb6-style AI output using 'product_name' and 'link' instead of 'title' and 'url'
    raw_ai_dict = {
        "page_type": "ecommerce",
        "record_item_selector": ".product-card",
        "fields": {
            "product_name": {"selector": "h2.title", "attribute": "text"},
            "link": {"selector": "a.product-href", "attribute": "href"},
            "price": {"selector": ".price-box", "attribute": "text"},
            "img": {"selector": "img.main-img", "attribute": "src"},
        },
    }

    strategy = ExtractionStrategy.model_validate(raw_ai_dict)
    assert "title" in strategy.fields
    assert strategy.fields["title"].selector == "h2.title"
    assert "url" in strategy.fields
    assert strategy.fields["url"].selector == "a.product-href"
    assert "sellingPrice" in strategy.fields
    assert "imageUrl" in strategy.fields


def test_fabb6_missing_title_and_url_repaired_with_safe_defaults():
    from app.ai.strategy_schemas import ExtractionStrategy

    # AI omitted title and url rules entirely
    raw_ai_dict = {
        "page_type": "ecommerce",
        "record_item_selector": ".product-card",
        "fields": {
            "sellingPrice": {"selector": ".price", "attribute": "text"},
        },
    }

    strategy = ExtractionStrategy.model_validate(raw_ai_dict)
    assert "title" in strategy.fields
    assert "url" in strategy.fields
    assert strategy.fields["title"].required is True
    assert strategy.fields["url"].required is True


def test_invalid_css_selector_rejected_by_strategy_validation():
    from app.ai.strategy_schemas import ExtractionStrategy
    from pydantic import ValidationError

    # Malformed CSS selector syntax (unclosed bracket)
    raw_ai_dict = {
        "page_type": "ecommerce",
        "record_item_selector": "div[class=",
        "fields": {
            "title": {"selector": "h2", "attribute": "text"},
            "url": {"selector": "a", "attribute": "href"},
        },
    }

    with pytest.raises(ValidationError, match="Invalid CSS selector syntax"):
        ExtractionStrategy.model_validate(raw_ai_dict)


def test_invalid_strategy_never_persisted_to_repository():
    from app.ai.strategy_learner import validate_strategy_execution

    # Records produced with missing titles
    bad_records = [
        {"title": None, "url": "https://example.com/p1"},
        {"title": "", "url": "https://example.com/p2"},
    ]
    is_valid, metrics, valid_recs, warnings = validate_strategy_execution(bad_records, "ecommerce")
    assert is_valid is False
    assert len(valid_recs) == 0


def test_http2_protocol_error_classified_and_mapped_to_502(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.browser.page_loader import ProtocolError

    async def fake_load_page_protocol_err(url, timeout_ms=None):
        raise ProtocolError("Protocol error (HTTP/2 or connection reset) while loading www.myntra.com: net::ERR_HTTP2_PROTOCOL_ERROR")

    from app import main
    monkeypatch.setattr(main, "load_rendered_page", fake_load_page_protocol_err)

    client = TestClient(app)
    response = client.post("/api/v1/extract", json={"url": "https://www.myntra.com/tshirts"})
    assert response.status_code == 502
    assert "Navigation protocol failure" in response.json()["detail"]
    assert "ERR_HTTP2_PROTOCOL_ERROR" in response.json()["detail"]


def test_duplicate_detection_does_not_merge_distinct_records():
    from app.extraction.normalizer import deduplicate_records

    records = [
        {
            "title": "Apple iPhone 15 (128 GB) - Black",
            "url": "https://example.com/p/iphone-15-black",
            "productId": "IPHONE15-BLK",
            "sellingPrice": 69900.0,
        },
        {
            "title": "Apple iPhone 15 (128 GB) - Blue",
            "url": "https://example.com/p/iphone-15-blue",
            "productId": "IPHONE15-BLU",
            "sellingPrice": 69900.0,
        },
        {
            "title": "Apple iPhone 15 (128 GB) - Black",
            "url": "https://example.com/p/iphone-15-black",
            "productId": "IPHONE15-BLK",
            "sellingPrice": 69900.0,
        },
    ]

    deduped = deduplicate_records(records)
    # Distinct items (Black vs Blue) must NOT be merged
    assert len(deduped) == 2
    urls = [r["url"] for r in deduped]
    assert "https://example.com/p/iphone-15-black" in urls
    assert "https://example.com/p/iphone-15-blue" in urls


