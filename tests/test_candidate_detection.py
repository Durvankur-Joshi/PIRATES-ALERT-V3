from pathlib import Path
from app.discovery.candidate_detector import detect_candidate_records

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


def test_detects_ecommerce_product_cards() -> None:
    html = load_fixture("ecommerce_products.html")
    records = detect_candidate_records(html, "https://example.com/search", "ecommerce")

    assert len(records) == 3
    first = records[0]
    assert "Galaxy Ultra" in first["title"]
    assert first["url"] == "https://example.com/items/galaxy-ultra-256gb?pid=PROD-101"
    assert first["productId"] == "PROD-101"
    assert first["sellingPrice"] == 69999.0
    assert first["mrp"] == 79999.0
    assert first["currency"] == "INR"
    assert first["rating"] == 4.5
    assert first["reviewCount"] == 1240


def test_detects_generic_job_cards() -> None:
    html = load_fixture("generic_jobs.html")
    records = detect_candidate_records(html, "https://example.com/careers", "generic_listing")

    assert len(records) == 3
    first = records[0]
    assert "Senior Python Backend Engineer" in first["title"]
    assert first["url"] == "https://example.com/jobs/senior-python-backend-engineer"
    assert first["sellingPrice"] is None
    assert first["extra"].get("company") == "Acme Cloud Systems"
    assert "Bengaluru" in first["extra"].get("location", "")


def test_detects_directory_entries() -> None:
    html = load_fixture("directory_entries.html")
    records = detect_candidate_records(html, "https://example.com/directory", "generic_listing")

    assert len(records) == 3
    titles = [r["title"] for r in records]
    assert any("Apex Dental Care" in t for t in titles)
    assert any("Green Leaf" in t for t in titles)
    assert any("Metro Veterinary" in t for t in titles)


def test_ignores_nav_and_footer_extracts_only_main_listings() -> None:
    html = load_fixture("repeated_with_nav_footer.html")
    records = detect_candidate_records(html, "https://example.com/store", "ecommerce")

    assert len(records) == 3
    titles = [r["title"] for r in records]
    assert not any(t in ("Home", "Deals", "Categories", "About Us", "Privacy Policy") for t in titles)
    assert any("Espresso Maker" in t for t in titles)
    assert any("Coffee Grinder" in t for t in titles)
    assert any("Milk Frother" in t for t in titles)


def test_handles_malformed_html() -> None:
    html = load_fixture("malformed_markup.html")
    records = detect_candidate_records(html, "https://example.com/deals", "ecommerce")

    assert len(records) >= 2
    titles = [r["title"] for r in records]
    assert any("Ergonomic Vertical Mouse" in t for t in titles)
    assert any("RGB Mechanical Keyboard" in t for t in titles)


def test_no_candidates_on_editorial_page() -> None:
    html = load_fixture("no_listings_page.html")
    records = detect_candidate_records(html, "https://example.com/privacy", "generic_listing")

    assert len(records) == 0
