from pathlib import Path
import re
from bs4 import BeautifulSoup
import pytest
from app.discovery.candidate_detector import (
    detect_candidate_records,
    extract_card_extra,
    extract_card_prices,
    extract_card_rating_and_reviews,
    extract_title_and_url_from_card,
)
from app.extraction.normalizer import (
    extract_price_and_currency,
    parse_numeric_price,
    parse_rating,
    parse_review_count,
)
from app.extraction.quality import evaluate_extraction_quality
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.ai.strategy_learner import execute_strategy

EXTENSION_DIR = Path(__file__).parent.parent / "extension"


# ==============================================================================
# Task 1: CSP & Popup Event Handler Compliance
# ==============================================================================

def test_popup_html_has_no_inline_event_handlers():
    popup_html = (EXTENSION_DIR / "popup.html").read_text(encoding="utf-8")
    # Must not contain any inline event handlers (e.g. onclick, onsubmit, onload)
    inline_handlers = re.findall(r"\son[a-z]+=", popup_html, re.IGNORECASE)
    assert not inline_handlers, f"Disallowed inline event handlers found in popup.html: {inline_handlers}"

    # Form must not contain onsubmit
    assert 'onsubmit="return false;"' not in popup_html
    assert 'onsubmit=' not in popup_html


def test_popup_buttons_registered_in_popup_js():
    popup_html = (EXTENSION_DIR / "popup.html").read_text(encoding="utf-8")
    popup_js = (EXTENSION_DIR / "popup.js").read_text(encoding="utf-8")

    soup = BeautifulSoup(popup_html, "html.parser")
    button_ids = [btn.get("id") for btn in soup.find_all("button") if btn.get("id")]
    form_id = soup.find("form").get("id")

    assert form_id == "extraction-form"
    assert "extraction-form" in popup_js
    assert 'preventDefault()' in popup_js

    # Verify key interactive buttons exist and are referenced in JS
    for b_id in ["use-active-tab-btn", "copy-json-btn", "download-json-btn", "toggle-strategies-btn"]:
        assert b_id in button_ids
        assert b_id in popup_js


# ==============================================================================
# Task 3: Indian Rupee Price and MRP Parsing
# ==============================================================================

@pytest.mark.parametrize(
    "raw_text,expected_price,expected_currency",
    [
        ("₹1,61,990", 161990.0, "INR"),
        ("₹71,890", 71890.0, "INR"),
        ("₹1,79,900", 179900.0, "INR"),
        ("₹1,61,990.00", 161990.0, "INR"),
        ("Rs. 1,61,990", 161990.0, "INR"),
        ("INR 71,890/-", 71890.0, "INR"),
        ("₹ 1,79,900/-", 179900.0, "INR"),
        ("₹12,34,567", 1234567.0, "INR"),
        ("$1,499.00", 1499.0, "USD"),
        ("€1.499,00", 1499.0, "EUR"),
    ],
)
def test_indian_rupee_and_foreign_price_parsing(raw_text, expected_price, expected_currency):
    price, currency = extract_price_and_currency(raw_text)
    assert price == expected_price
    assert currency == expected_currency


def test_rupee_lakh_grouping_not_parsed_as_decimals():
    # Crucial regression test: ₹1,61,990 must not become 1.61
    price_161k = parse_numeric_price("1,61,990", currency_hint="INR")
    assert price_161k == 161990.0
    assert price_161k != 1.61

    price_71k = parse_numeric_price("71,890", currency_hint="INR")
    assert price_71k == 71890.0
    assert price_71k != 71.89

    price_179k = parse_numeric_price("1,79,900", currency_hint="INR")
    assert price_179k == 179900.0
    assert price_179k != 1.79


def test_split_price_elements_parsing():
    # Trailing dot from .a-price-whole
    price = parse_numeric_price("1,61,990.", currency_hint="INR")
    assert price == 161990.0

    price_with_cents = parse_numeric_price("1,61,990.50", currency_hint="INR")
    assert price_with_cents == 161990.50


def test_rejects_emi_and_pure_discounts():
    emi_price, emi_cur = extract_price_and_currency("EMI starts at ₹7,854 per month")
    assert emi_price is None

    emi_short, _ = extract_price_and_currency("₹7,854/mo")
    assert emi_short is None

    disc_price, _ = extract_price_and_currency("(10% off)")
    assert disc_price is None

    save_price, _ = extract_price_and_currency("Save ₹18,000 with coupon")
    assert save_price is None


# ==============================================================================
# Task 4: Rating and Review Count
# ==============================================================================

@pytest.mark.parametrize(
    "rating_str,expected_rating",
    [
        ("4.3 out of 5 stars", 4.3),
        ("4.5 out of 5", 4.5),
        ("4.1 / 5", 4.1),
        ("4.2 ★", 4.2),
        ("4.8 stars", 4.8),
        ("Rating: 4.4", 4.4),
        ("4.6", 4.6),
        ("Not a rating", None),
        ("128 GB", None),
        ("2 SIM", None),
    ],
)
def test_rating_parsing_and_no_fabrication(rating_str, expected_rating):
    assert parse_rating(rating_str) == expected_rating


@pytest.mark.parametrize(
    "review_str,expected_count",
    [
        ("2,345", 2345),
        ("(2,345)", 2345),
        ("1,61,990", 161990),
        ("2,345 ratings", 2345),
        ("14,892 customer reviews", 14892),
        ("2.3k reviews", 2300),
        ("15k ratings", 15000),
        ("", None),
        ("Natural Titanium", None),
    ],
)
def test_review_count_parsing(review_str, expected_count):
    assert parse_review_count(review_str) == expected_count


# ==============================================================================
# Tasks 2, 3, 4, 5: Amazon Sponsored and Organic DOM Layouts
# ==============================================================================

AMAZON_SEARCH_HTML = """
<!DOCTYPE html>
<html>
<head><title>Amazon.in : iPhone 15</title></head>
<body>
  <div class="s-main-slot s-result-list s-search-results">
    
    <!-- Sponsored Card with Brand Header, Full Title, Selling Price, MRP, EMI, Ratings & Badge -->
    <div data-asin="B0CHX1W1XY" data-component-type="s-search-result" class="sg-col-20-of-24 s-result-item s-asin AdHolder">
      <div class="sg-col-inner">
        <div class="a-section a-spacing-micro">
          <span class="a-size-base-plus a-color-base a-text-bold">Apple</span>
          <span class="a-color-secondary puis-sponsored-label-text">Sponsored</span>
        </div>
        <div class="s-product-image-container">
          <a class="a-link-normal" href="/Apple-iPhone-15-Pro-Max-256-GB/dp/B0CHX1W1XY">
            <img class="s-image" src="https://m.media-amazon.com/images/I/81+GIkwqL_L._AC_UY218_.jpg" alt="Apple">
          </a>
        </div>
        <div class="s-title-instructions-style" data-cy="title-recipe">
          <span class="a-badge-text">Best Seller</span>
          <h2 class="a-size-mini a-spacing-none a-color-base s-line-clamp-2">
            <a class="a-link-normal s-underline-text s-link-style a-text-normal" href="/Apple-iPhone-15-Pro-Max-256-GB/dp/B0CHX1W1XY">
              <span class="a-size-medium a-color-base a-text-normal">Apple iPhone 15 Pro Max (256 GB) - Blue Titanium</span>
            </a>
          </h2>
        </div>
        <div class="a-row a-size-small">
          <span aria-label="4.6 out of 5 stars">
            <i class="a-icon a-icon-star-small a-star-small-4-5">
              <span class="a-icon-alt">4.6 out of 5 stars</span>
            </i>
          </span>
          <a class="a-link-normal s-underline-text" href="/dp/B0CHX1W1XY#customerReviews">
            <span class="a-size-base s-underline-text">1,248</span>
          </a>
        </div>
        <div class="a-row a-size-base a-color-base">
          <span class="a-price" data-a-size="xl" data-a-color="base">
            <span class="a-offscreen">₹1,51,990</span>
            <span aria-hidden="true"><span class="a-price-whole">1,51,990.</span><span class="a-price-fraction">00</span></span>
          </span>
          <span class="a-price a-text-price" data-a-strike="true">
            <span class="a-offscreen">₹1,59,900</span>
          </span>
          <span>(5% off)</span>
        </div>
        <div class="a-row a-size-base a-color-secondary">
          <span>EMI starts at ₹7,369. No Cost EMI available</span>
        </div>
        <div class="a-row">
          <span class="a-color-base a-text-bold">FREE Delivery by Tomorrow</span>
        </div>
      </div>
    </div>

    <!-- Organic Card with Split Price, Missing MRP, Full Title & Ratings -->
    <div data-asin="B0CHWV4459" data-component-type="s-search-result" class="sg-col-20-of-24 s-result-item s-asin">
      <div class="sg-col-inner">
        <div class="s-product-image-container">
          <a class="a-link-normal" href="/Apple-iPhone-15-Pro-128-GB/dp/B0CHWV4459">
            <img class="s-image" src="https://m.media-amazon.com/images/I/81SigAnjpOL._AC_UY218_.jpg" alt="Apple iPhone">
          </a>
        </div>
        <div class="s-title-instructions-style" data-cy="title-recipe">
          <span class="a-badge-text">Amazon's Choice</span>
          <h2 class="a-size-mini a-spacing-none a-color-base s-line-clamp-2">
            <a class="a-link-normal s-underline-text s-link-style a-text-normal" href="/Apple-iPhone-15-Pro-128-GB/dp/B0CHWV4459">
              <span class="a-size-medium a-color-base a-text-normal">Apple iPhone 15 Pro (128 GB) - Natural Titanium</span>
            </a>
          </h2>
        </div>
        <div class="a-row a-size-small">
          <span aria-label="4.5 out of 5 stars">
            <i class="a-icon a-icon-star-small a-star-small-4-5">
              <span class="a-icon-alt">4.5 out of 5 stars</span>
            </i>
          </span>
          <a class="a-link-normal s-underline-text" href="/dp/B0CHWV4459#customerReviews">
            <span class="a-size-base s-underline-text">3,892</span>
          </a>
        </div>
        <div class="a-row a-size-base a-color-base">
          <span class="a-price" data-a-size="xl">
            <span class="a-offscreen">₹1,27,990</span>
            <span aria-hidden="true"><span class="a-price-whole">1,27,990.</span><span class="a-price-fraction">00</span></span>
          </span>
        </div>
        <div class="a-row">
          <span class="a-color-base a-text-bold">Get it by Monday, 27 October</span>
        </div>
      </div>
    </div>

    <!-- Organic Card 3: OnePlus Phone with Indian MRP and Split Elements -->
    <div data-asin="B0CQPL35Q8" data-component-type="s-search-result" class="sg-col-20-of-24 s-result-item s-asin">
      <div class="sg-col-inner">
        <div class="s-title-instructions-style">
          <h2 class="a-size-mini">
            <a class="a-link-normal a-text-normal" href="/OnePlus-Silky-Black-Storage/dp/B0CQPL35Q8">
              <span class="a-size-base-plus a-color-base a-text-normal">OnePlus 12 (Silky Black, 12GB RAM, 256GB Storage)</span>
            </a>
          </h2>
        </div>
        <div class="a-row a-size-small">
          <span aria-label="4.3 out of 5 stars">
            <i class="a-icon a-icon-star-small a-star-small-4-5"><span class="a-icon-alt">4.3 out of 5 stars</span></i>
          </span>
          <a class="a-link-normal s-underline-text" href="/dp/B0CQPL35Q8#customerReviews">
            <span class="a-size-base s-underline-text">7,421</span>
          </a>
        </div>
        <div class="a-row a-size-base a-color-base">
          <span class="a-price">
            <span class="a-offscreen">₹64,999</span>
          </span>
          <span class="a-price a-text-price" data-a-strike="true">
            <span class="a-offscreen">₹69,999</span>
          </span>
        </div>
      </div>
    </div>

  </div>
</body>
</html>
"""


def test_amazon_deterministic_title_extraction():
    soup = BeautifulSoup(AMAZON_SEARCH_HTML, "html.parser")
    cards = soup.select(".s-result-item")
    assert len(cards) >= 3

    # Sponsored Card: must extract complete title, NOT "Apple" or "Sponsored" or image alt
    title_1, url_1 = extract_title_and_url_from_card(cards[0], "https://www.amazon.in")
    assert title_1 == "Apple iPhone 15 Pro Max (256 GB) - Blue Titanium"
    assert url_1 == "https://www.amazon.in/Apple-iPhone-15-Pro-Max-256-GB/dp/B0CHX1W1XY"

    # Organic Card 2:
    title_2, url_2 = extract_title_and_url_from_card(cards[1], "https://www.amazon.in")
    assert title_2 == "Apple iPhone 15 Pro (128 GB) - Natural Titanium"
    assert url_2 == "https://www.amazon.in/Apple-iPhone-15-Pro-128-GB/dp/B0CHWV4459"

    # Organic Card 3:
    title_3, url_3 = extract_title_and_url_from_card(cards[2], "https://www.amazon.in")
    assert title_3 == "OnePlus 12 (Silky Black, 12GB RAM, 256GB Storage)"


def test_amazon_price_and_mrp_extraction():
    soup = BeautifulSoup(AMAZON_SEARCH_HTML, "html.parser")
    cards = soup.select(".s-result-item")

    # Card 1: Has both selling price (1,51,990) and MRP (1,59,900)
    sp_1, mrp_1, cur_1 = extract_card_prices(cards[0])
    assert sp_1 == 151990.0
    assert mrp_1 == 159900.0
    assert cur_1 == "INR"

    # Card 2: Has selling price (1,27,990), but no MRP -> mrp must be None (not fabricated)
    sp_2, mrp_2, cur_2 = extract_card_prices(cards[1])
    assert sp_2 == 127990.0
    assert mrp_2 is None
    assert cur_2 == "INR"

    # Card 3: Has selling price (64,999) and MRP (69,999)
    sp_3, mrp_3, cur_3 = extract_card_prices(cards[2])
    assert sp_3 == 64999.0
    assert mrp_3 == 69999.0
    assert cur_3 == "INR"


def test_amazon_rating_and_reviews_extraction():
    soup = BeautifulSoup(AMAZON_SEARCH_HTML, "html.parser")
    cards = soup.select(".s-result-item")

    # Card 1: 4.6 stars, 1248 reviews
    r_1, rc_1 = extract_card_rating_and_reviews(cards[0])
    assert r_1 == 4.6
    assert rc_1 == 1248

    # Card 2: 4.5 stars, 3892 reviews
    r_2, rc_2 = extract_card_rating_and_reviews(cards[1])
    assert r_2 == 4.5
    assert rc_2 == 3892

    # Card 3: 4.3 stars, 7421 reviews
    r_3, rc_3 = extract_card_rating_and_reviews(cards[2])
    assert r_3 == 4.3
    assert rc_3 == 7421


def test_amazon_extra_fields_extraction():
    soup = BeautifulSoup(AMAZON_SEARCH_HTML, "html.parser")
    cards = soup.select(".s-result-item")

    # Card 1 has Best Seller badge, Delivery, Offer
    extra_1 = extract_card_extra(cards[0], "ecommerce")
    assert extra_1.get("badge") == "Best Seller"
    assert "FREE Delivery" in extra_1.get("delivery", "")
    assert "(5% off)" in extra_1.get("offer", "")

    # Card 2 has Amazon's Choice badge and delivery
    extra_2 = extract_card_extra(cards[1], "ecommerce")
    assert extra_2.get("badge") == "Amazon's Choice"
    assert "Get it by" in extra_2.get("delivery", "")


def test_amazon_full_candidate_detection_and_quality():
    base_url = "https://www.amazon.in/s?k=iphone+15"
    records = detect_candidate_records(AMAZON_SEARCH_HTML, base_url, "ecommerce")

    assert len(records) == 3
    rec1 = records[0]
    assert rec1["title"] == "Apple iPhone 15 Pro Max (256 GB) - Blue Titanium"
    assert rec1["sellingPrice"] == 151990.0
    assert rec1["mrp"] == 159900.0
    assert rec1["rating"] == 4.6
    assert rec1["reviewCount"] == 1248
    assert rec1["productId"] == "B0CHX1W1XY"

    report, valid = evaluate_extraction_quality(records, "ecommerce", total_candidates=3)
    assert report.is_acceptable is True
    assert len(valid) == 3
    assert report.quality_score >= 0.80


def test_saved_strategy_reuse_on_amazon():
    # Strategy using standard selectors
    strategy = ExtractionStrategy(
        page_type="ecommerce",
        record_item_selector="div.s-result-item[data-asin]",
        record_container_selector="div.s-main-slot",
        fields={
            "title": FieldExtractionRule(selector="h2 a span", attribute="text"),
            "url": FieldExtractionRule(selector="h2 a", attribute="href"),
            "productId": FieldExtractionRule(selector=None, attribute="data-asin"),
            "sellingPrice": FieldExtractionRule(selector="span.a-price:not(.a-text-price)", attribute="text"),
            "mrp": FieldExtractionRule(selector="span.a-price.a-text-price", attribute="text"),
            "rating": FieldExtractionRule(selector="span.a-icon-alt", attribute="text"),
            "reviewCount": FieldExtractionRule(selector="span.s-underline-text", attribute="text"),
        },
    )

    base_url = "https://www.amazon.in/s?k=iphone+15"
    records = execute_strategy(AMAZON_SEARCH_HTML, base_url, strategy)

    assert len(records) == 3
    rec0 = records[0]
    assert rec0["title"] == "Apple iPhone 15 Pro Max (256 GB) - Blue Titanium"
    assert rec0["sellingPrice"] == 151990.0
    assert rec0["mrp"] == 159900.0
    assert rec0["currency"] == "INR"
    assert rec0["rating"] == 4.6
    assert rec0["reviewCount"] == 1248
    assert rec0["productId"] == "B0CHX1W1XY"
