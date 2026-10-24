import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional
from app.ai.strategy_schemas import ExtractionStrategy
from app.config import settings
from app.rules.fingerprint import normalize_hostname


def generate_strategy_id(hostname: str, page_type: str) -> str:
    norm_host = normalize_hostname(hostname).replace(".", "_")
    return f"strat_{norm_host}_{page_type}"


class StrategyRepository:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.strategy_db_path
        self._lock = asyncio.Lock()
        self._initialized = False

    def _get_connection(self) -> sqlite3.Connection:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                hostname TEXT NOT NULL,
                page_type TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                strategy_schema_version TEXT NOT NULL,
                strategy_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                update_source TEXT NOT NULL,
                validation_metrics TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                status TEXT NOT NULL,
                change_reason TEXT,
                UNIQUE (strategy_id, version)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_strategies (
                hostname TEXT NOT NULL,
                page_type TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                active_version INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (hostname, page_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rollback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                hostname TEXT NOT NULL,
                page_type TEXT NOT NULL,
                from_version INTEGER NOT NULL,
                to_version INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
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
        self._migrate_legacy_strategies(conn)
        conn.commit()
        return conn

    def _migrate_legacy_strategies(self, conn: sqlite3.Connection) -> None:
        try:
            cur = conn.execute(
                """
                SELECT hostname, page_type, fingerprint, strategy_json,
                       strategy_schema_version, created_at, updated_at,
                       validation_metrics, validation_status
                FROM extraction_strategies
                """
            )
            legacy_rows = cur.fetchall()
            for row in legacy_rows:
                norm_host = normalize_hostname(row["hostname"])
                pt = row["page_type"]
                strat_id = generate_strategy_id(norm_host, pt)

                exists = conn.execute(
                    "SELECT 1 FROM strategy_versions WHERE strategy_id = ? AND version = 1",
                    (strat_id,),
                ).fetchone()

                if not exists:
                    conn.execute(
                        """
                        INSERT INTO strategy_versions (
                            strategy_id, version, hostname, page_type, fingerprint,
                            strategy_schema_version, strategy_json, created_at,
                            update_source, validation_metrics, validation_status,
                            status, change_reason
                        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'legacy_migration', ?, ?, ?, 'Migrated from Phase 2 table')
                        """,
                        (
                            strat_id,
                            norm_host,
                            pt,
                            row["fingerprint"],
                            row["strategy_schema_version"],
                            row["strategy_json"],
                            row["created_at"],
                            row["validation_metrics"],
                            row["validation_status"],
                            "active" if row["validation_status"] == "passed" else "failed",
                        ),
                    )

                    if row["validation_status"] == "passed":
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO active_strategies (
                                hostname, page_type, strategy_id, active_version,
                                fingerprint, updated_at
                            ) VALUES (?, ?, ?, 1, ?, ?)
                            """,
                            (
                                norm_host,
                                pt,
                                strat_id,
                                row["fingerprint"],
                                row["updated_at"],
                            ),
                        )
        except Exception:
            pass

    async def init_db(self) -> None:
        async with self._lock:
            def _sync_init() -> None:
                with self._get_connection():
                    pass

            await asyncio.to_thread(_sync_init)
            self._initialized = True

    async def is_available(self) -> bool:
        try:
            def _check() -> bool:
                with self._get_connection() as conn:
                    cur = conn.execute("SELECT 1")
                    return bool(cur.fetchone())

            return await asyncio.to_thread(_check)
        except Exception:
            return False

    async def save_version(
        self,
        hostname: str,
        page_type: str,
        fingerprint: str,
        strategy: ExtractionStrategy,
        validation_metrics: dict[str, Any],
        validation_status: str,
        update_source: str = "ai_learned",
        change_reason: Optional[str] = None,
    ) -> int:
        if not self._initialized:
            await self.init_db()

        if validation_status != "passed":
            raise ValueError("Cannot save unvalidated strategy as a successful version")

        norm_host = normalize_hostname(hostname)
        strat_id = generate_strategy_id(norm_host, page_type)
        now_iso = datetime.now(timezone.utc).isoformat()
        strategy_json = json.dumps(strategy.model_dump(mode="json"))
        metrics_json = json.dumps(validation_metrics)

        async with self._lock:
            def _sync_save() -> int:
                with self._get_connection() as conn:
                    cur = conn.execute(
                        "SELECT MAX(version) as max_v FROM strategy_versions WHERE strategy_id = ?",
                        (strat_id,),
                    )
                    row = cur.fetchone()
                    next_version = (row["max_v"] or 0) + 1

                    conn.execute(
                        """
                        UPDATE strategy_versions
                        SET status = 'superseded'
                        WHERE strategy_id = ? AND status = 'active'
                        """,
                        (strat_id,),
                    )

                    conn.execute(
                        """
                        INSERT INTO strategy_versions (
                            strategy_id, version, hostname, page_type, fingerprint,
                            strategy_schema_version, strategy_json, created_at,
                            update_source, validation_metrics, validation_status,
                            status, change_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                        """,
                        (
                            strat_id,
                            next_version,
                            norm_host,
                            page_type,
                            fingerprint,
                            strategy.strategy_version,
                            strategy_json,
                            now_iso,
                            update_source,
                            metrics_json,
                            validation_status,
                            change_reason,
                        ),
                    )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO active_strategies (
                            hostname, page_type, strategy_id, active_version,
                            fingerprint, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            norm_host,
                            page_type,
                            strat_id,
                            next_version,
                            fingerprint,
                            now_iso,
                        ),
                    )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO extraction_strategies (
                            hostname, page_type, fingerprint, strategy_json,
                            strategy_schema_version, created_at, updated_at,
                            validation_metrics, validation_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    return next_version

            return await asyncio.to_thread(_sync_save)

    async def get_active_strategy_info(
        self, hostname: str, page_type: str
    ) -> Optional[dict[str, Any]]:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)

        def _sync_get_info() -> Optional[dict[str, Any]]:
            with self._get_connection() as conn:
                cur = conn.execute(
                    """
                    SELECT a.strategy_id, a.active_version, a.fingerprint as active_fingerprint,
                           v.strategy_json, v.strategy_schema_version, v.validation_metrics,
                           v.validation_status, v.update_source, v.created_at
                    FROM active_strategies a
                    JOIN strategy_versions v
                      ON a.strategy_id = v.strategy_id AND a.active_version = v.version
                    WHERE a.hostname = ? AND a.page_type = ?
                    """,
                    (norm_host, page_type),
                )
                row = cur.fetchone()
                if not row:
                    return None

                try:
                    strat_data = json.loads(row["strategy_json"])
                    strategy_obj = ExtractionStrategy.model_validate(strat_data)
                    return {
                        "strategy_id": row["strategy_id"],
                        "version": row["active_version"],
                        "fingerprint": row["active_fingerprint"],
                        "strategy": strategy_obj,
                        "strategy_schema_version": row["strategy_schema_version"],
                        "validation_metrics": json.loads(row["validation_metrics"]),
                        "validation_status": row["validation_status"],
                        "update_source": row["update_source"],
                        "created_at": row["created_at"],
                    }
                except Exception:
                    return None

        return await asyncio.to_thread(_sync_get_info)

    async def get_strategy(
        self, hostname: str, page_type: str, fingerprint: Optional[str] = None
    ) -> Optional[ExtractionStrategy]:
        info = await self.get_active_strategy_info(hostname, page_type)
        if info:
            return info["strategy"]

        if fingerprint:
            norm_host = normalize_hostname(hostname)

            def _sync_get_by_fp() -> Optional[ExtractionStrategy]:
                with self._get_connection() as conn:
                    cur = conn.execute(
                        """
                        SELECT strategy_json, validation_status FROM extraction_strategies
                        WHERE hostname = ? AND page_type = ? AND fingerprint = ?
                        """,
                        (norm_host, page_type, fingerprint),
                    )
                    row = cur.fetchone()
                    if row and row["validation_status"] == "passed":
                        try:
                            return ExtractionStrategy.model_validate(json.loads(row["strategy_json"]))
                        except Exception:
                            return None
                    return None

            return await asyncio.to_thread(_sync_get_by_fp)

        return None

    async def list_versions(
        self, hostname: str, page_type: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)

        def _sync_list() -> list[dict[str, Any]]:
            with self._get_connection() as conn:
                params: list[Any] = [norm_host]
                query = """
                    SELECT v.strategy_id, v.version, v.hostname, v.page_type, v.fingerprint,
                           v.strategy_schema_version, v.created_at, v.update_source,
                           v.validation_metrics, v.validation_status, v.status, v.change_reason,
                           CASE WHEN a.active_version = v.version THEN 1 ELSE 0 END as is_active
                    FROM strategy_versions v
                    LEFT JOIN active_strategies a
                           ON v.strategy_id = a.strategy_id AND v.hostname = a.hostname AND v.page_type = a.page_type
                    WHERE v.hostname = ?
                """
                if page_type:
                    query += " AND v.page_type = ?"
                    params.append(page_type)

                query += " ORDER BY v.version DESC LIMIT ?"
                params.append(limit)

                cur = conn.execute(query, params)
                results: list[dict[str, Any]] = []
                for row in cur.fetchall():
                    metrics = {}
                    try:
                        metrics = json.loads(row["validation_metrics"])
                    except Exception:
                        pass
                    results.append(
                        {
                            "strategyId": row["strategy_id"],
                            "version": row["version"],
                            "hostname": row["hostname"],
                            "pageType": row["page_type"],
                            "fingerprint": row["fingerprint"],
                            "strategySchemaVersion": row["strategy_schema_version"],
                            "createdAt": row["created_at"],
                            "updateSource": row["update_source"],
                            "validationMetrics": metrics,
                            "validationStatus": row["validation_status"],
                            "status": row["status"],
                            "changeReason": row["change_reason"],
                            "isActive": bool(row["is_active"]),
                        }
                    )
                return results

        return await asyncio.to_thread(_sync_list)

    async def rollback_strategy(
        self, hostname: str, page_type: str, target_version: int, reason: str
    ) -> dict[str, Any]:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)
        strat_id = generate_strategy_id(norm_host, page_type)
        now_iso = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            def _sync_rollback() -> dict[str, Any]:
                with self._get_connection() as conn:
                    target_row = conn.execute(
                        """
                        SELECT version, fingerprint, strategy_json, strategy_schema_version,
                               validation_status, validation_metrics
                        FROM strategy_versions
                        WHERE strategy_id = ? AND version = ?
                        """,
                        (strat_id, target_version),
                    ).fetchone()

                    if not target_row:
                        raise ValueError(f"Strategy version {target_version} not found for {norm_host} ({page_type})")

                    if target_row["validation_status"] != "passed":
                        raise ValueError(
                            f"Cannot rollback to version {target_version} with status '{target_row['validation_status']}'"
                        )

                    active_row = conn.execute(
                        "SELECT active_version FROM active_strategies WHERE hostname = ? AND page_type = ?",
                        (norm_host, page_type),
                    ).fetchone()

                    current_version = active_row["active_version"] if active_row else None
                    if current_version == target_version:
                        raise ValueError(f"Version {target_version} is already the active strategy")

                    if current_version is not None:
                        conn.execute(
                            """
                            UPDATE strategy_versions
                            SET status = 'rolled_back'
                            WHERE strategy_id = ? AND version = ?
                            """,
                            (strat_id, current_version),
                        )

                    conn.execute(
                        """
                        UPDATE strategy_versions
                        SET status = 'active'
                        WHERE strategy_id = ? AND version = ?
                        """,
                        (strat_id, target_version),
                    )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO active_strategies (
                            hostname, page_type, strategy_id, active_version,
                            fingerprint, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            norm_host,
                            page_type,
                            strat_id,
                            target_version,
                            target_row["fingerprint"],
                            now_iso,
                        ),
                    )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO extraction_strategies (
                            hostname, page_type, fingerprint, strategy_json,
                            strategy_schema_version, created_at, updated_at,
                            validation_metrics, validation_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            norm_host,
                            page_type,
                            target_row["fingerprint"],
                            target_row["strategy_json"],
                            target_row["strategy_schema_version"],
                            now_iso,
                            now_iso,
                            target_row["validation_metrics"],
                            target_row["validation_status"],
                        ),
                    )

                    conn.execute(
                        """
                        INSERT INTO rollback_history (
                            strategy_id, hostname, page_type, from_version,
                            to_version, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            strat_id,
                            norm_host,
                            page_type,
                            current_version or 0,
                            target_version,
                            reason,
                            now_iso,
                        ),
                    )
                    conn.commit()

                    return {
                        "strategyId": strat_id,
                        "hostname": norm_host,
                        "pageType": page_type,
                        "previousVersion": current_version,
                        "activeVersion": target_version,
                        "reason": reason,
                        "timestamp": now_iso,
                    }

            return await asyncio.to_thread(_sync_rollback)

    async def save_strategy(
        self,
        hostname: str,
        page_type: str,
        fingerprint: str,
        strategy: ExtractionStrategy,
        validation_metrics: dict[str, Any],
        validation_status: str,
    ) -> None:
        if validation_status == "passed":
            await self.save_version(
                hostname=hostname,
                page_type=page_type,
                fingerprint=fingerprint,
                strategy=strategy,
                validation_metrics=validation_metrics,
                validation_status=validation_status,
                update_source="ai_learned",
            )

    async def mark_strategy_failed(
        self, hostname: str, page_type: str, fingerprint: str
    ) -> None:
        pass

    async def delete_strategy(
        self, hostname: str, page_type: str, fingerprint: str
    ) -> bool:
        if not self._initialized:
            await self.init_db()

        norm_host = normalize_hostname(hostname)
        strat_id = generate_strategy_id(norm_host, page_type)

        async with self._lock:
            def _sync_delete() -> bool:
                with self._get_connection() as conn:
                    cur = conn.execute(
                        "DELETE FROM active_strategies WHERE hostname = ? AND page_type = ?",
                        (norm_host, page_type),
                    )
                    conn.execute(
                        "DELETE FROM extraction_strategies WHERE hostname = ? AND page_type = ?",
                        (norm_host, page_type),
                    )
                    conn.execute(
                        "UPDATE strategy_versions SET status = 'superseded' WHERE strategy_id = ?",
                        (strat_id,),
                    )
                    conn.commit()
                    return cur.rowcount > 0

            return await asyncio.to_thread(_sync_delete)


strategy_repository = StrategyRepository()
