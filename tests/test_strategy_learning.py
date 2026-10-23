from pathlib import Path
import pytest
from app.ai.gemini_client import gemini_client
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.config import settings
from app.extraction.engine import run_extraction_pipeline
from app.rules.fingerprint import compute_structural_fingerprint
from app.rules.repository import StrategyRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def clean_gemini_client():
    gemini_client.set_mock_handler(None)
    yield
    gemini_client.set_mock_handler(None)


@pytest.fixture
def temp_strategy_repo(tmp_path: Path):
    db_file = tmp_path / "test_strategies.db"
    return StrategyRepository(db_path=str(db_file))


def build_mock_artisan_strategy() -> ExtractionStrategy:
    return ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_container_selector="#artisan-showcase",
        record_item_selector="section.artisan-unit",
        fields={
            "title": FieldExtractionRule(selector="span.product-title-text", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a.view-btn", attribute="href", required=True),
            "productId": FieldExtractionRule(selector=None, attribute="data-item-id"),
            "imageUrl": FieldExtractionRule(selector="img", attribute="src"),
            "sellingPrice": FieldExtractionRule(selector="span.active-val", attribute="text"),
            "mrp": FieldExtractionRule(selector="span.striked-val", attribute="text"),
            "rating": FieldExtractionRule(selector="div.client-rating", attribute="text"),
        },
        rationale="Select artisan units under #artisan-showcase container",
    )


def build_mock_papers_strategy() -> ExtractionStrategy:
    return ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="generic_listing",
        record_container_selector="#papers-feed",
        record_item_selector="div.paper-entry",
        fields={
            "title": FieldExtractionRule(selector="span.paper-title-text", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a.read-link", attribute="href", required=True),
            "productId": FieldExtractionRule(selector=None, attribute="data-paper-id"),
            "extra": {
                "authors": FieldExtractionRule(selector="div.authors-list", attribute="text"),
                "subject": FieldExtractionRule(selector="div.subject-badge", attribute="text"),
            },
        },
        rationale="Extract academic preprint entries without prices or images",
    )


@pytest.mark.asyncio
async def test_learns_unfamiliar_ecommerce(monkeypatch, tmp_path):
    db_file = tmp_path / "ecom_strat.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")
    mock_strategy = build_mock_artisan_strategy()

    gemini_calls = 0

    def mock_ai(prompt: str):
        nonlocal gemini_calls
        gemini_calls += 1
        return mock_strategy

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "ai_learned"
    assert result["metadata"]["recordCount"] == 3
    assert result["metadata"]["aiCalls"] == 1
    assert gemini_calls == 1

    first = result["records"][0]
    assert first["title"] == "Handmade Ceramic Coffee Mug"
    assert first["sellingPrice"] == 1850.0
    assert first["mrp"] == 2200.0
    assert first["currency"] == "INR"
    assert first["productId"] == "ART-001"


@pytest.mark.asyncio
async def test_learns_unfamiliar_generic_without_prices_or_images(monkeypatch, tmp_path):
    db_file = tmp_path / "generic_strat.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_generic.html")
    mock_strategy = build_mock_papers_strategy()

    gemini_calls = 0

    def mock_ai(prompt: str):
        nonlocal gemini_calls
        gemini_calls += 1
        return mock_strategy

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://open-preprints.org/cs", "generic_listing")

    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "ai_learned"
    assert result["metadata"]["recordCount"] == 3
    assert gemini_calls == 1

    for r in result["records"]:
        assert r["sellingPrice"] is None
        assert r["imageUrl"] is None
        assert "Deterministic Neural Parsing" in result["records"][0]["title"]


@pytest.mark.asyncio
async def test_strategy_persistence_and_zero_calls_on_subsequent_request(monkeypatch, tmp_path):
    db_file = tmp_path / "reuse_test.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")
    mock_strategy = build_mock_artisan_strategy()

    gemini_calls = 0

    def mock_ai(prompt: str):
        nonlocal gemini_calls
        gemini_calls += 1
        return mock_strategy

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    first_run = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert first_run["success"] is True
    assert first_run["metadata"]["strategySource"] == "ai_learned"
    assert gemini_calls == 1

    # Second request with identical page structure must reuse saved rule and make ZERO Gemini calls
    second_run = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert second_run["success"] is True
    assert second_run["metadata"]["strategySource"] == "saved"
    assert second_run["metadata"]["aiCalls"] == 0
    assert gemini_calls == 1  # Total calls remains exactly 1, exactly 0 new calls


@pytest.mark.asyncio
async def test_saved_strategy_failure_triggers_recovery(monkeypatch, tmp_path):
    db_file = tmp_path / "recovery_test.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")
    fingerprint = compute_structural_fingerprint(html, "https://artisan-shop.org/catalog", "ecommerce")

    # Save a broken strategy that selects a non-existent element
    broken_strategy = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="div.non_existent_element",
        fields={
            "title": FieldExtractionRule(selector="a", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a", attribute="href", required=True),
        },
    )
    await strategy_repository.save_strategy(
        hostname="https://artisan-shop.org/catalog",
        page_type="ecommerce",
        fingerprint=fingerprint,
        strategy=broken_strategy,
        validation_metrics={"recordCount": 0},
        validation_status="passed",
    )

    valid_strategy = build_mock_artisan_strategy()
    gemini_calls = 0

    def mock_ai(prompt: str):
        nonlocal gemini_calls
        gemini_calls += 1
        return valid_strategy

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert result["success"] is True
    assert result["metadata"]["strategySource"] == "ai_learned"
    assert result["metadata"]["recoveryStatus"] == "saved_strategy_revalidation_failed"
    assert gemini_calls == 1


@pytest.mark.asyncio
async def test_rejects_invalid_json_or_schema_from_gemini(monkeypatch, tmp_path):
    db_file = tmp_path / "invalid_json.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")

    def mock_ai(prompt: str):
        return "This is not valid JSON at all!"

    gemini_client.set_mock_handler(mock_ai)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert result["success"] is False
    assert result["metadata"]["strategySource"] == "ai_learned"
    assert any("JSONDecodeError" in w or "failed" in w for w in result["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_rejects_strategy_matching_zero_elements(monkeypatch, tmp_path):
    db_file = tmp_path / "zero_elem.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")
    zero_strategy = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="div.missing_card_selector",
        fields={
            "title": FieldExtractionRule(selector="a", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a", attribute="href", required=True),
        },
    )

    gemini_client.set_mock_handler(lambda prompt: zero_strategy)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert result["success"] is False
    assert result["metadata"]["recordCount"] == 0


@pytest.mark.asyncio
async def test_rejects_strategy_proposing_navigation_or_footer(monkeypatch, tmp_path):
    db_file = tmp_path / "nav_footer.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("repeated_with_nav_footer.html")
    nav_strategy = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="ul.footer-links li",
        fields={
            "title": FieldExtractionRule(selector="a", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a", attribute="href", required=True),
        },
    )

    gemini_client.set_mock_handler(lambda prompt: nav_strategy)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    from app.ai.strategy_learner import execute_strategy, validate_strategy_execution
    records = execute_strategy(html, "https://example.com", nav_strategy)
    is_valid, metrics, valid_records, warnings = validate_strategy_execution(records, "ecommerce")
    assert is_valid is False
    assert len(valid_records) == 0


@pytest.mark.asyncio
async def test_invalid_strategy_not_saved_and_does_not_overwrite_valid(monkeypatch, tmp_path):
    db_file = tmp_path / "overwrite_test.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    hostname = "https://artisan-shop.org/catalog"
    page_type = "ecommerce"
    fingerprint = "test-fp-123"

    valid_strat = build_mock_artisan_strategy()
    await strategy_repository.save_strategy(
        hostname=hostname,
        page_type=page_type,
        fingerprint=fingerprint,
        strategy=valid_strat,
        validation_metrics={"recordCount": 3},
        validation_status="passed",
    )

    invalid_strat = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="div.invalid",
        fields={
            "title": FieldExtractionRule(selector="a", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a", attribute="href", required=True),
        },
    )
    # Attempting to save an invalid strategy with validation_status != "passed" should do nothing
    await strategy_repository.save_strategy(
        hostname=hostname,
        page_type=page_type,
        fingerprint=fingerprint,
        strategy=invalid_strat,
        validation_metrics={"recordCount": 0},
        validation_status="failed",
    )

    loaded = await strategy_repository.get_strategy(hostname, page_type, fingerprint)
    assert loaded is not None
    assert loaded.record_item_selector == "section.artisan-unit"


@pytest.mark.asyncio
async def test_missing_gemini_api_key_leaves_deterministic_operational(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_client, "api_key", None)

    # 1. Deterministic fixture works cleanly without Gemini key
    ecom_html = load_fixture("ecommerce_products.html")
    res = await run_extraction_pipeline(ecom_html, "https://example.com/search", "ecommerce")
    assert res["success"] is True
    assert res["metadata"]["strategySource"] == "deterministic"

    # 2. Unfamiliar page produces clear diagnostic without throwing unhandled exceptions
    unfamiliar_html = load_fixture("no_listings_page.html")
    unfam_res = await run_extraction_pipeline(unfamiliar_html, "https://example.com/legal", "generic_listing")
    assert unfam_res["success"] is False
    assert any("not configured" in w for w in unfam_res["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_gemini_timeout_and_error_handling(monkeypatch, tmp_path):
    db_file = tmp_path / "timeout_test.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")

    def mock_timeout(prompt: str):
        raise TimeoutError("Simulated Gemini timeout")

    gemini_client.set_mock_handler(mock_timeout)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert result["success"] is False
    assert any("timeout" in w.lower() for w in result["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_ai_retry_limits_enforced(monkeypatch, tmp_path):
    db_file = tmp_path / "retry_test.db"
    from app.rules.repository import strategy_repository
    monkeypatch.setattr(strategy_repository, "db_path", str(db_file))
    await strategy_repository.init_db()

    html = load_fixture("unfamiliar_ecommerce.html")
    zero_strategy = ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector="div.missing_card",
        fields={
            "title": FieldExtractionRule(selector="a", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a", attribute="href", required=True),
        },
    )

    call_count = 0

    def mock_retry(prompt: str):
        nonlocal call_count
        call_count += 1
        return zero_strategy

    gemini_client.set_mock_handler(mock_retry)
    monkeypatch.setattr(gemini_client, "api_key", "test-key-123")
    monkeypatch.setattr(settings, "max_ai_attempts_per_extraction", 2)

    result = await run_extraction_pipeline(html, "https://artisan-shop.org/catalog", "ecommerce")
    assert result["success"] is False
    assert call_count == 2
    assert result["metadata"]["aiCalls"] == 2


@pytest.mark.asyncio
async def test_database_persistence_survives_reinitialization(tmp_path):
    db_file = tmp_path / "persisted_rule.db"
    repo1 = StrategyRepository(db_path=str(db_file))
    await repo1.init_db()

    strategy = build_mock_artisan_strategy()
    await repo1.save_strategy(
        hostname="example.com",
        page_type="ecommerce",
        fingerprint="fp-persistent-1",
        strategy=strategy,
        validation_metrics={"recordCount": 3},
        validation_status="passed",
    )

    # Instantiate new repository pointing to the same SQLite file
    repo2 = StrategyRepository(db_path=str(db_file))
    await repo2.init_db()

    loaded = await repo2.get_strategy("example.com", "ecommerce", "fp-persistent-1")
    assert loaded is not None
    assert loaded.record_item_selector == "section.artisan-unit"
    assert loaded.strategy_version == "1.0.0"
