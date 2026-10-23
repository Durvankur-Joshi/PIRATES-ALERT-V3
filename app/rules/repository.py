import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional
from app.ai.strategy_schemas import ExtractionStrategy
from app.config import settings
from app.rules.fingerprint import normalize_hostname


class StrategyRepository:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.strategy_db_path
        self._lock = asyncio.Lock()
        self._initialized = False

    def _get_connection(self) -> sqlite3.Connection:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_strategies (
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
        conn.commit()
        return conn

    async def init_db(self) -> None:
        async with self._lock:
            def _sync_init() -> None:
                with self._get_connection():
                    pass

            await asyncio.to_thread(_sync_init)
            self._initialized = True

    async def get_strategy(
        self, hostname: str, page_type: str, fingerprint: str
    ) -> Optional[ExtractionStrategy]:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)

        def _sync_get() -> Optional[ExtractionStrategy]:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT strategy_json, validation_status FROM extraction_strategies
                    WHERE hostname = ? AND page_type = ? AND fingerprint = ?
                    """,
                    (norm_host, page_type, fingerprint),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                if row["validation_status"] != "passed":
                    return None

                try:
                    data = json.loads(row["strategy_json"])
                    return ExtractionStrategy.model_validate(data)
                except Exception:
                    return None

        return await asyncio.to_thread(_sync_get)

    async def save_strategy(
        self,
        hostname: str,
        page_type: str,
        fingerprint: str,
        strategy: ExtractionStrategy,
        validation_metrics: dict[str, Any],
        validation_status: str,
    ) -> None:
        if not self._initialized:
            await self.init_db()

        if validation_status != "passed":
            return

        norm_host = normalize_hostname(hostname)
        now_iso = datetime.now(timezone.utc).isoformat()
        strategy_json = json.dumps(strategy.model_dump(mode="json"))
        metrics_json = json.dumps(validation_metrics)

        async with self._lock:
            def _sync_save() -> None:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO extraction_strategies (
                            hostname, page_type, fingerprint, strategy_json,
                            strategy_schema_version, created_at, updated_at,
                            validation_metrics, validation_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(hostname, page_type, fingerprint) DO UPDATE SET
                            strategy_json = excluded.strategy_json,
                            strategy_schema_version = excluded.strategy_schema_version,
                            updated_at = excluded.updated_at,
                            validation_metrics = excluded.validation_metrics,
                            validation_status = excluded.validation_status
                        """,
                        (
                            norm_host,
                            page_type,
                            fingerprint,
                            strategy_json,
                            strategy.strategy_version,
                            now_iso,
                            now_iso,
                            metrics_json,
                            validation_status,
                        ),
                    )
                    conn.commit()

            await asyncio.to_thread(_sync_save)

    async def mark_strategy_failed(
        self, hostname: str, page_type: str, fingerprint: str
    ) -> None:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            def _sync_mark() -> None:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE extraction_strategies
                        SET validation_status = 'failed', updated_at = ?
                        WHERE hostname = ? AND page_type = ? AND fingerprint = ?
                        """,
                        (now_iso, norm_host, page_type, fingerprint),
                    )
                    conn.commit()

            await asyncio.to_thread(_sync_mark)

    async def delete_strategy(
        self, hostname: str, page_type: str, fingerprint: str
    ) -> bool:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)

        async with self._lock:
            def _sync_delete() -> bool:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        """
                        DELETE FROM extraction_strategies
                        WHERE hostname = ? AND page_type = ? AND fingerprint = ?
                        """,
                        (norm_host, page_type, fingerprint),
                    )
                    conn.commit()
                    return cursor.rowcount > 0

            return await asyncio.to_thread(_sync_delete)


strategy_repository = StrategyRepository()
