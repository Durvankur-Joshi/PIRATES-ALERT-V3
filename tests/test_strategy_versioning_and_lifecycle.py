import asyncio
import sqlite3
import pytest
from app.ai.strategy_schemas import ExtractionStrategy, FieldExtractionRule
from app.rules.repository import StrategyRepository, generate_strategy_id


def make_dummy_strategy(selector: str = "div.product-card") -> ExtractionStrategy:
    return ExtractionStrategy(
        strategy_version="1.0.0",
        page_type="ecommerce",
        record_item_selector=selector,
        fields={
            "title": FieldExtractionRule(selector="h2.title", attribute="text", required=True),
            "url": FieldExtractionRule(selector="a.link", attribute="href", required=True),
        },
    )


@pytest.mark.asyncio
async def test_initial_learning_saves_v1_active(tmp_path):
    db_file = tmp_path / "strat_v1.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    strat = make_dummy_strategy()
    version = await repo.save_version(
        hostname="https://myshop.com/catalog",
        page_type="ecommerce",
        fingerprint="fp_123456",
        strategy=strat,
        validation_metrics={"qualityScore": 0.85, "recordCount": 5},
        validation_status="passed",
        update_source="ai_learned",
        change_reason="Initial learned strategy",
    )

    assert version == 1
    active_info = await repo.get_active_strategy_info("https://myshop.com/catalog", "ecommerce")
    assert active_info is not None
    assert active_info["version"] == 1
    assert active_info["strategy_id"] == "strat_myshop_com_ecommerce"
    assert active_info["validation_status"] == "passed"


@pytest.mark.asyncio
async def test_subsequent_valid_strategy_creates_v2_preserving_v1(tmp_path):
    db_file = tmp_path / "strat_v2.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    strat1 = make_dummy_strategy("div.card-v1")
    v1 = await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v1",
        strategy=strat1,
        validation_metrics={"qualityScore": 0.80},
        validation_status="passed",
    )
    assert v1 == 1

    strat2 = make_dummy_strategy("div.card-v2")
    v2 = await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v2",
        strategy=strat2,
        validation_metrics={"qualityScore": 0.95},
        validation_status="passed",
    )
    assert v2 == 2

    active_info = await repo.get_active_strategy_info("myshop.com", "ecommerce")
    assert active_info["version"] == 2

    versions = await repo.list_versions("myshop.com", "ecommerce")
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[0]["isActive"] is True
    assert versions[0]["status"] == "active"
    assert versions[1]["version"] == 1
    assert versions[1]["isActive"] is False
    assert versions[1]["status"] == "superseded"


@pytest.mark.asyncio
async def test_failed_strategy_cannot_be_saved_and_preserves_active(tmp_path):
    db_file = tmp_path / "strat_fail.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    strat1 = make_dummy_strategy("div.card-good")
    v1 = await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v1",
        strategy=strat1,
        validation_metrics={"qualityScore": 0.88},
        validation_status="passed",
    )
    assert v1 == 1

    bad_strat = make_dummy_strategy("div.broken")
    with pytest.raises(ValueError, match="Cannot save unvalidated strategy"):
        await repo.save_version(
            hostname="myshop.com",
            page_type="ecommerce",
            fingerprint="fp_bad",
            strategy=bad_strat,
            validation_metrics={"qualityScore": 0.10},
            validation_status="failed",
        )

    active_info = await repo.get_active_strategy_info("myshop.com", "ecommerce")
    assert active_info["version"] == 1
    assert active_info["validation_status"] == "passed"


@pytest.mark.asyncio
async def test_rollback_restores_prior_version(tmp_path):
    db_file = tmp_path / "strat_rollback.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    strat1 = make_dummy_strategy("div.card-v1")
    await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v1",
        strategy=strat1,
        validation_metrics={"qualityScore": 0.80},
        validation_status="passed",
    )

    strat2 = make_dummy_strategy("div.card-v2")
    await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v2",
        strategy=strat2,
        validation_metrics={"qualityScore": 0.90},
        validation_status="passed",
    )

    rollback_res = await repo.rollback_strategy(
        hostname="myshop.com",
        page_type="ecommerce",
        target_version=1,
        reason="V2 selector broke on pagination",
    )

    assert rollback_res["activeVersion"] == 1
    assert rollback_res["previousVersion"] == 2
    assert rollback_res["reason"] == "V2 selector broke on pagination"

    active_info = await repo.get_active_strategy_info("myshop.com", "ecommerce")
    assert active_info["version"] == 1

    versions = await repo.list_versions("myshop.com", "ecommerce")
    assert versions[0]["version"] == 2
    assert versions[0]["isActive"] is False
    assert versions[0]["status"] == "rolled_back"
    assert versions[1]["version"] == 1
    assert versions[1]["isActive"] is True
    assert versions[1]["status"] == "active"


@pytest.mark.asyncio
async def test_rollback_rejects_already_active_or_nonexistent_version(tmp_path):
    db_file = tmp_path / "strat_rollback_invalid.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    strat1 = make_dummy_strategy()
    await repo.save_version(
        hostname="myshop.com",
        page_type="ecommerce",
        fingerprint="fp_v1",
        strategy=strat1,
        validation_metrics={"qualityScore": 0.85},
        validation_status="passed",
    )

    with pytest.raises(ValueError, match="already the active strategy"):
        await repo.rollback_strategy("myshop.com", "ecommerce", 1, "test")

    with pytest.raises(ValueError, match="not found"):
        await repo.rollback_strategy("myshop.com", "ecommerce", 99, "test")


@pytest.mark.asyncio
async def test_migration_from_legacy_extraction_strategies(tmp_path):
    db_file = tmp_path / "legacy_migration.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE extraction_strategies (
            hostname TEXT NOT NULL,
            page_type TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            strategy_json TEXT NOT NULL,
            strategy_schema_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            validation_metrics TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            PRIMARY KEY (hostname, page_type, fingerprint)
        )
        """
    )
    strat = make_dummy_strategy()
    import json
    conn.execute(
        """
        INSERT INTO extraction_strategies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-shop.org",
            "ecommerce",
            "legacy_fp_111",
            json.dumps(strat.model_dump(mode="json")),
            "1.0.0",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            json.dumps({"qualityScore": 0.88}),
            "passed",
        ),
    )
    conn.commit()
    conn.close()

    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    active_info = await repo.get_active_strategy_info("legacy-shop.org", "ecommerce")
    assert active_info is not None
    assert active_info["version"] == 1
    assert active_info["fingerprint"] == "legacy_fp_111"
    assert active_info["update_source"] == "legacy_migration"

    versions = await repo.list_versions("legacy-shop.org", "ecommerce")
    assert len(versions) == 1
    assert versions[0]["isActive"] is True


@pytest.mark.asyncio
async def test_concurrent_saves_create_unique_sequential_versions(tmp_path):
    db_file = tmp_path / "concurrent_saves.db"
    repo = StrategyRepository(db_path=str(db_file))
    await repo.init_db()

    async def save_worker(idx: int):
        strat = make_dummy_strategy(f"div.product-{idx}")
        return await repo.save_version(
            hostname="concurrent-shop.com",
            page_type="ecommerce",
            fingerprint=f"fp_{idx}",
            strategy=strat,
            validation_metrics={"qualityScore": 0.90},
            validation_status="passed",
            change_reason=f"Concurrent save {idx}",
        )

    tasks = [save_worker(i) for i in range(5)]
    results = await asyncio.gather(*tasks)

    assert sorted(results) == [1, 2, 3, 4, 5]
    versions = await repo.list_versions("concurrent-shop.com", "ecommerce")
    assert len(versions) == 5
    version_numbers = [v["version"] for v in versions]
    assert len(version_numbers) == len(set(version_numbers))
