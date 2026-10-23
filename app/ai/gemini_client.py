import asyncio
import json
import time
from typing import Any, Callable, Optional
from google import genai
from google.genai import types
from app.ai.strategy_schemas import ExtractionStrategy
from app.config import settings


class GeminiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._mock_handler: Optional[Callable[[str], Any]] = None

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def set_mock_handler(self, handler: Optional[Callable[[str], Any]]) -> None:
        self._mock_handler = handler

    async def generate_strategy(
        self,
        prompt: str,
        reason: str = "strategy_learning",
    ) -> tuple[Optional[ExtractionStrategy], dict[str, Any]]:
        start_time = time.monotonic()
        metrics: dict[str, Any] = {
            "reason": reason,
            "model": self.model,
            "timestamp": time.time(),
        }

        if self._mock_handler is not None:
            try:
                res = self._mock_handler(prompt)
                duration_ms = int((time.monotonic() - start_time) * 1000)
                metrics["duration_ms"] = duration_ms
                if isinstance(res, ExtractionStrategy):
                    metrics["success"] = True
                    return res, metrics
                if isinstance(res, dict):
                    metrics["success"] = True
                    return ExtractionStrategy.model_validate(res), metrics
                if isinstance(res, str):
                    try:
                        parsed_dict = json.loads(res)
                        metrics["success"] = True
                        return ExtractionStrategy.model_validate(parsed_dict), metrics
                    except Exception as e:
                        raise ValueError(f"Failed to parse Gemini response as JSON: {e}") from e
                if isinstance(res, Exception):
                    raise res
            except Exception as e:
                metrics["success"] = False
                metrics["error"] = f"AI strategy generation failed: {e}"
                metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
                return None, metrics

        if not self.is_available():
            metrics["success"] = False
            metrics["error"] = "GEMINI_API_KEY is not configured"
            return None, metrics

        def _sync_call() -> tuple[Optional[ExtractionStrategy], dict[str, Any]]:
            try:
                client = genai.Client(api_key=self.api_key)
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractionStrategy,
                    temperature=0.1,
                )
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )

                usage_dict: dict[str, Any] = {}
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    um = response.usage_metadata
                    usage_dict = {
                        "prompt_tokens": getattr(um, "prompt_token_count", None),
                        "candidates_tokens": getattr(um, "candidates_token_count", None),
                        "total_tokens": getattr(um, "total_token_count", None),
                    }

                parsed = getattr(response, "parsed", None)
                if parsed and isinstance(parsed, ExtractionStrategy):
                    return parsed, usage_dict

                text_content = getattr(response, "text", "")
                if text_content:
                    data = json.loads(text_content)
                    return ExtractionStrategy.model_validate(data), usage_dict

                return None, {"error": "Empty response from Gemini"}
            except Exception as e:
                safe_err = str(e)
                if self.api_key and self.api_key in safe_err:
                    safe_err = safe_err.replace(self.api_key, "[REDACTED]")
                return None, {"error": safe_err}

        try:
            strategy, call_info = await asyncio.wait_for(
                asyncio.to_thread(_sync_call),
                timeout=float(self.timeout_seconds),
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            metrics["duration_ms"] = duration_ms
            metrics.update(call_info)
            metrics["success"] = strategy is not None
            return strategy, metrics
        except asyncio.TimeoutError:
            metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
            metrics["success"] = False
            metrics["error"] = f"Gemini API call timed out after {self.timeout_seconds}s"
            return None, metrics
        except Exception as e:
            metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
            metrics["success"] = False
            metrics["error"] = str(e)
            return None, metrics


gemini_client = GeminiClient()
