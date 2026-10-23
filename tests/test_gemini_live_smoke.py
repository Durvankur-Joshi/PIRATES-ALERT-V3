import os
from pathlib import Path
import pytest
from app.ai.gemini_client import GeminiClient
from app.ai.strategy_schemas import ExtractionStrategy

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="Live Gemini smoke test requires GEMINI_API_KEY in environment",
)
@pytest.mark.asyncio
async def test_live_gemini_flash_smoke():
    client = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    assert client.is_available() is True

    test_prompt = """
    Propose an ExtractionStrategy for extracting items from:
    <div class="product-grid">
        <div class="card"><a href="/p/1"><h3>Widget A</h3></a><span class="price">$10</span></div>
        <div class="card"><a href="/p/2"><h3>Widget B</h3></a><span class="price">$20</span></div>
    </div>
    """
    strategy, metrics = await client.generate_strategy(test_prompt, reason="live_smoke_test")
    assert strategy is not None
    assert isinstance(strategy, ExtractionStrategy)
    assert metrics.get("success") is True
