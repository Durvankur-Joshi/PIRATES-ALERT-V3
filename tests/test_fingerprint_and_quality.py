import pytest
from app.ai.gemini_client import gemini_client
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.extraction.engine import run_extraction_pipeline
from app.extraction.quality import evaluate_extraction_quality
from app.rules.fingerprint import compute_structural_fingerprint
from app.rules.repository import strategy_repository


PAGE_HTML_ORIGINAL = """
<!DOCTYPE html>
<html>
<head><title>Artisan Crafts</title></head>
<body>
  <header><nav><a href="/">Home</a><a href="/about">About</a></nav></header>
  <main>
    <div class="product-grid">
      <div class="card" role="article">
        <h2 class="name">Handmade Ceramic Mug</h2>
        <span class="cost">$24.00</span>
        <span class="stars">4.8</span>
        <a class="item-link" href="/products/ceramic-mug">View Mug</a>
      </div>
      <div class="card" role="article">
        <h2 class="name">Woven Wool Blanket</h2>
        <span class="cost">$85.00</span>
        <span class="stars">4.9</span>
        <a class="item-link" href="/products/wool-blanket">View Blanket</a>
      </div>
      <div class="card" role="article">
        <h2 class="name">Oak Cutting Board</h2>
        <span class="cost">$45.00</span>
        <span class="stars">4.7</span>
        <a class="item-link" href="/products/cutting-board">View Board</a>
      </div>
    </div>
  </main>
  <footer><p>&copy; 2026 Artisan Crafts</p></footer>
</body>
</html>
"""

PAGE_HTML_CONTENT_CHANGED = """
<!DOCTYPE html>
<html>
<head><title>Artisan Crafts - Summer Collection</title></head>
<body>
  <header><nav><a href="/">Home</a><a href="/about">About</a></nav></header>
  <main>
    <div class="product-grid">
      <!-- Order flipped, titles, prices, ratings completely different -->
      <div class="card" role="article">
        <h2 class="name">Linen Bath Towel</h2>
        <span class="cost">$32.50</span>
        <span class="stars">5.0</span>
        <a class="item-link" href="/products/linen-towel">View Towel</a>
      </div>
      <div class="card" role="article">
        <h2 class="name">Ceramic Flower Vase</h2>
        <span class="cost">$58.00</span>
        <span class="stars">4.6</span>
        <a class="item-link" href="/products/flower-vase">View Vase</a>
      </div>
      <div class="card" role="article">
        <h2 class="name">Brass Candle Holder</h2>
        <span class="cost">$19.99</span>
        <span class="stars">4.2</span>
        <a class="item-link" href="/products/candle-holder">View Holder</a>
      </div>
    </div>
  </main>
  <footer><p>&copy; 2026 Artisan Crafts</p></footer>
</body>
</html>
"""

PAGE_HTML_STRUCTURAL_CHANGED = """
<!DOCTYPE html>
<html>
<head><title>Artisan Crafts</title></head>
<body>
  <header><nav><a href="/">Home</a></nav></header>
  <main>
    <!-- Completely changed from div.product-grid to table layout -->
    <table class="inventory-table">
      <tbody>
        <tr><td>Linen Bath Towel</td><td><a href="/products/towel">Details</a></td></tr>
        <tr><td>Ceramic Vase</td><td><a href="/products/vase">Details</a></td></tr>
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def test_fingerprint_stability_with_content_and_ordering_changes():
    fp1 = compute_structural_fingerprint(PAGE_HTML_ORIGINAL, "https://artisans.com/shop", "ecommerce")
    fp2 = compute_structural_fingerprint(PAGE_HTML_CONTENT_CHANGED, "https://artisans.com/shop", "ecommerce")
    assert fp1 == fp2, "Fingerprint should be identical when only text, prices, ratings, and items change"


def test_fingerprint_detects_meaningful_structural_change():
    fp_grid = compute_structural_fingerprint(PAGE_HTML_ORIGINAL, "https://artisans.com/shop", "ecommerce")
    fp_table = compute_structural_fingerprint(PAGE_HTML_STRUCTURAL_CHANGED, "https://artisans.com/shop", "ecommerce")
    assert fp_grid != fp_table, "Fingerprint must change when structure changes from div grid to table"


def test_quality_evaluation_generic_listings_without_prices_or_images():
    generic_records = [
        {"title": "Senior Python Backend Engineer", "url": "https://jobs.io/1"},
        {"title": "Lead DevOps Architect", "url": "https://jobs.io/2"},
        {"title": "Full Stack Platform Developer", "url": "https://jobs.io/3"},
    ]
    report, valid = evaluate_extraction_quality(generic_records, "generic_listing", total_candidates=3)
    assert report.is_acceptable is True
    assert report.quality_score >= 0.70
    assert report.record_count == 3
    assert len(valid) == 3


def test_quality_evaluation_rejects_excessive_duplicates():
    duplicate_records = [
        {"title": "Product Alpha", "url": "https://shop.com/alpha", "sellingPrice": 20.0},
        {"title": "Product Alpha", "url": "https://shop.com/alpha", "sellingPrice": 20.0},
        {"title": "Product Alpha", "url": "https://shop.com/alpha", "sellingPrice": 20.0},
        {"title": "Product Alpha", "url": "https://shop.com/alpha", "sellingPrice": 20.0},
        {"title": "Product Beta", "url": "https://shop.com/beta", "sellingPrice": 30.0},
    ]
    report, valid = evaluate_extraction_quality(duplicate_records, "ecommerce", total_candidates=5)
    assert report.is_acceptable is False
    assert report.duplicate_rate > 0.50
    assert any("Excessive duplicate" in w for w in report.warnings)


def test_quality_evaluation_rejects_insufficient_records():
    single_record = [{"title": "Only One Product", "url": "https://shop.com/1", "sellingPrice": 10.0}]
    report, valid = evaluate_extraction_quality(single_record, "ecommerce", total_candidates=1)
    assert report.is_acceptable is False
    assert report.record_count < 2


UNFAMILIAR_PAGE_1 = """
<!DOCTYPE html>
<html><body>
  <div id="artisan-showcase">
    <section class="artisan-unit">
      <span class="product-title-text">Handmade Ceramic Coffee Mug</span>
      <span class="active-val">₹1,850</span>
      <a href="/crafts/mug" class="view-btn">View</a>
    </section>
    <section class="artisan-unit">
      <span class="product-title-text">Pure Wool Throw Blanket</span>
      <span class="active-val">₹4,500</span>
      <a href="/crafts/blanket" class="view-btn">View</a>
    </section>
    <section class="artisan-unit">
      <span class="product-title-text">Hand Carved Wooden Bowl</span>
      <span class="active-val">₹3,200</span>
      <a href="/crafts/bowl" class="view-btn">View</a>
    </section>
  </div>
</body></html>
"""

UNFAMILIAR_PAGE_2 = """
<!DOCTYPE html>
<html><body>
  <div id="artisan-showcase">
    <section class="artisan-unit">
      <span class="product-title-text">Ceramic Dinner Plate</span>
      <span class="active-val">₹2,100</span>
      <a href="/crafts/plate" class="view-btn">View</a>
    </section>
    <section class="artisan-unit">
      <span class="product-title-text">Silk Embroidered Scarf</span>
      <span class="active-val">₹3,800</span>
      <a href="/crafts/scarf" class="view-btn">View</a>
    </section>
    <section class="artisan-unit">
      <span class="product-title-text">Brass Incense Holder</span>
      <span class="active-val">₹1,450</span>
      <a href="/crafts/holder" class="view-btn">View</a>
    </section>
  </div>
</body></html>
"""


@pytest.mark.asyncio
async def test_active_strategy_reused_with_zero_gemini_calls_despite_content_changes(monkeypatch, tmp_path):
    db_file = tmp_path / "fp_resilience.db"
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    strategy = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="section.artisan-unit",
        fields={
            "title": FieldExtractionRule(selector="span.product-title-text", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a.view-btn", attribute="href", required=True),
            "sellingPrice": FieldExtractionRule(selector="span.active-val", attribute="text", required=False),
        },
    )

    fp_initial = compute_structural_fingerprint(UNFAMILIAR_PAGE_1, "https://artisans.com/shop", "ecommerce")
    await strategy_repository.save_version(
        hostname="https://artisans.com/shop",
        page_type="ecommerce",
        fingerprint=fp_initial,
        strategy=strategy,
        validation_metrics={"qualityScore": 0.90},
        validation_status="passed",
    )

    ai_calls = 0

    def mock_ai(prompt):
        nonlocal ai_calls
        ai_calls += 1
        return strategy

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key")

    result = await run_extraction_pipeline(UNFAMILIAR_PAGE_2, "https://artisans.com/shop", "ecommerce")

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "saved"
    assert result["metadata"]["aiCalls"] == 0
    assert len(result["records"]) == 3
    assert result["metadata"]["strategyVersion"] == 1
    assert ai_calls == 0
