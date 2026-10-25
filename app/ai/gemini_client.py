import asyncio
import json
import time
from typing import Any, Callable, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from app.ai.strategy_schemas import ExtractionStrategy
from app.api.schemas import ExtractedRecord
from app.config import settings


class ExtractedProductRecords(BaseModel):
    records: list[ExtractedRecord] = Field(default_factory=list)


def to_gemini_developer_schema(model_or_schema: Any) -> dict[str, Any]:
    """
    Transforms a Pydantic model class or JSON Schema dictionary into a
    Gemini Developer API compatible JSON schema by recursively removing
    unsupported schema keywords such as 'additionalProperties', while
    preserving all property definitions, required field checks, and nested types.
    """
    if isinstance(model_or_schema, type) and hasattr(model_or_schema, "model_json_schema"):
        schema = model_or_schema.model_json_schema()
    elif isinstance(model_or_schema, dict):
        schema = model_or_schema
    else:
        raise ValueError(f"Expected Pydantic model or schema dict, got {type(model_or_schema)}")

    def _clean(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: _clean(v)
                for k, v in node.items()
                if k not in ("additionalProperties", "additional_properties")
            }
        if isinstance(node, list):
            return [_clean(x) for x in node]
        return node

    return _clean(schema)


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
                safe_err = str(e)
                if self.api_key and self.api_key in safe_err:
                    safe_err = safe_err.replace(self.api_key, "[REDACTED]")
                metrics["error"] = f"AI strategy generation failed: {safe_err}"
                metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
                return None, metrics

        if not self.is_available():
            metrics["success"] = False
            metrics["error"] = "GEMINI_API_KEY is not configured"
            return None, metrics

        def _sync_call() -> tuple[Optional[ExtractionStrategy], dict[str, Any]]:
            try:
                client = genai.Client(api_key=self.api_key)
                dev_schema = to_gemini_developer_schema(ExtractionStrategy)
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=dev_schema,
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

                text_content = getattr(response, "text", "")
                if text_content:
                    data = json.loads(text_content)
                    return ExtractionStrategy.model_validate(data), usage_dict

                parsed = getattr(response, "parsed", None)
                if parsed:
                    if isinstance(parsed, ExtractionStrategy):
                        return parsed, usage_dict
                    if isinstance(parsed, dict):
                        return ExtractionStrategy.model_validate(parsed), usage_dict

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
            safe_err = str(e)
            if self.api_key and self.api_key in safe_err:
                safe_err = safe_err.replace(self.api_key, "[REDACTED]")
            metrics["error"] = safe_err
            return None, metrics

    async def extract_records_structured(
        self,
        prompt: str,
        reason: str = "ai_fallback",
    ) -> tuple[list[ExtractedRecord], dict[str, Any]]:
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
                if isinstance(res, ExtractedProductRecords):
                    metrics["success"] = True
                    return res.records, metrics
                if isinstance(res, list):
                    validated = [
                        ExtractedRecord.model_validate(r) if not isinstance(r, ExtractedRecord) else r
                        for r in res
                    ]
                    metrics["success"] = True
                    return validated, metrics
                if isinstance(res, dict):
                    metrics["success"] = True
                    parsed = ExtractedProductRecords.model_validate(res)
                    return parsed.records, metrics
                if isinstance(res, str):
                    try:
                        parsed_dict = json.loads(res)
                        metrics["success"] = True
                        if isinstance(parsed_dict, list):
                            return [
                                ExtractedRecord.model_validate(r) if not isinstance(r, ExtractedRecord) else r
                                for r in parsed_dict
                            ], metrics
                        parsed = ExtractedProductRecords.model_validate(parsed_dict)
                        return parsed.records, metrics
                    except Exception as e:
                        raise ValueError(f"Failed to parse Gemini records response as JSON: {e}") from e
                if isinstance(res, Exception):
                    raise res
            except Exception as e:
                metrics["success"] = False
                safe_err = str(e)
                if self.api_key and self.api_key in safe_err:
                    safe_err = safe_err.replace(self.api_key, "[REDACTED]")
                metrics["error"] = f"AI product extraction failed: {safe_err}"
                metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
                return [], metrics

        if not self.is_available():
            metrics["success"] = False
            metrics["error"] = "GEMINI_API_KEY is not configured"
            return [], metrics

        def _sync_call() -> tuple[list[ExtractedRecord], dict[str, Any]]:
            try:
                client = genai.Client(api_key=self.api_key)
                dev_schema = to_gemini_developer_schema(ExtractedProductRecords)
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=dev_schema,
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

                text_content = getattr(response, "text", "")
                if text_content:
                    data = json.loads(text_content)
                    if isinstance(data, list):
                        return [ExtractedRecord.model_validate(r) for r in data], usage_dict
                    return ExtractedProductRecords.model_validate(data).records, usage_dict

                parsed = getattr(response, "parsed", None)
                if parsed:
                    if isinstance(parsed, ExtractedProductRecords):
                        return parsed.records, usage_dict
                    if isinstance(parsed, dict):
                        return ExtractedProductRecords.model_validate(parsed).records, usage_dict

                return [], {"error": "Empty response from Gemini"}
            except Exception as e:
                safe_err = str(e)
                if self.api_key and self.api_key in safe_err:
                    safe_err = safe_err.replace(self.api_key, "[REDACTED]")
                return [], {"error": safe_err}

        try:
            records, call_info = await asyncio.wait_for(
                asyncio.to_thread(_sync_call),
                timeout=float(self.timeout_seconds),
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            metrics["duration_ms"] = duration_ms
            metrics.update(call_info)
            metrics["success"] = bool(records)
            return records, metrics
        except asyncio.TimeoutError:
            metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
            metrics["success"] = False
            metrics["error"] = f"Gemini API call timed out after {self.timeout_seconds}s"
            return [], metrics
        except Exception as e:
            metrics["duration_ms"] = int((time.monotonic() - start_time) * 1000)
            metrics["success"] = False
            safe_err = str(e)
            if self.api_key and self.api_key in safe_err:
                safe_err = safe_err.replace(self.api_key, "[REDACTED]")
            metrics["error"] = safe_err
            return [], metrics


gemini_client = GeminiClient()
