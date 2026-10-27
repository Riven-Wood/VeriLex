"""Repository for the ``disabled_skills`` table.

Skills default to enabled; only the disabled set is persisted (the shared
Codex/OpenWorker semantics). Skill identity is case-insensitive because macOS
file systems are, so keys are normalized to lowercase on every read and write.
"""

from __future__ import annotations

from verilex_runtime.storage.database import Database


class SkillStateRepository:
    def __init__(self, database: Database):
        self._database = database

    def set_enabled(self, skill_name: str, enabled: bool, *, updated_at: str):
        key = skill_name.strip().lower()
        with self._database.connect() as connection:
            if enabled:
                connection.execute(
                    "DELETE FROM disabled_skills WHERE skill_name = ?",
                    (key,),
                )
                return
            cursor = connection.execute(
                "UPDATE disabled_skills SET updated_at = ? WHERE skill_name = ?",
                (updated_at, key),
            )
            if cursor.rowcount == 0:
                connection.execute(
                    "INSERT INTO disabled_skills (skill_name, updated_at) VALUES (?, ?)",
                    (key, updated_at),
                )

    def is_disabled(self, skill_name: str) -> bool:
        key = skill_name.strip().lower()
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM disabled_skills WHERE skill_name = ?",
                (key,),
            ).fetchone()
        return row is not None

    def forget(self, skill_name: str):
        key = skill_name.strip().lower()
        with self._database.connect() as connection:
            connection.execute(
                "DELETE FROM disabled_skills WHERE skill_name = ?",
                (key,),
            )

    def disabled_names(self) -> set[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT skill_name FROM disabled_skills"
            ).fetchall()
        return {row["skill_name"] for row in rows}
