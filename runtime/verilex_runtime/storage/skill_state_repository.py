"""Repository for the ``disabled_skills`` table.

Skills default to enabled; only the disabled set is persisted (the shared
Codex/OpenWorker semantics). Skill identity is case-insensitive because macOS
file systems are, so keys are normalized to lowercase on every read and write.
"""

from __future__ import annotations

from verilex_runtime.storage.database import Database
import uuid


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
            connection.execute("DELETE FROM skill_category_assignments WHERE skill_name = ?", (key,))

    def categories(self) -> list[dict[str, str]]:
        with self._database.connect() as connection:
            rows = connection.execute("SELECT id, name FROM skill_categories ORDER BY name_key").fetchall()
        return [dict(row) for row in rows]

    def create_category(self, name: str) -> dict[str, str]:
        category = {"id": "custom:" + uuid.uuid4().hex, "name": name}
        with self._database.connect() as connection:
            connection.execute("INSERT INTO skill_categories (id, name, name_key) VALUES (?, ?, ?)",
                               (category["id"], name, name.casefold()))
        return category

    def rename_category(self, category_id: str, name: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute("UPDATE skill_categories SET name = ?, name_key = ? WHERE id = ?",
                                        (name, name.casefold(), category_id))
        return cursor.rowcount > 0

    def delete_category(self, category_id: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute("DELETE FROM skill_categories WHERE id = ?", (category_id,))
            if cursor.rowcount:
                connection.execute("DELETE FROM skill_category_assignments WHERE category_id = ?", (category_id,))
        return cursor.rowcount > 0

    def category_assignments(self) -> dict[str, str]:
        with self._database.connect() as connection:
            rows = connection.execute("SELECT skill_name, category_id FROM skill_category_assignments").fetchall()
        return {row["skill_name"]: row["category_id"] for row in rows}

    def set_category(self, skill_name: str, category_id: str | None, builtin_ids: set[str]) -> bool:
        with self._database.connect() as connection:
            if category_id is not None and category_id not in builtin_ids:
                if connection.execute("SELECT 1 FROM skill_categories WHERE id = ?", (category_id,)).fetchone() is None:
                    return False
            if category_id is None:
                connection.execute("DELETE FROM skill_category_assignments WHERE skill_name = ?", (skill_name.strip().lower(),))
            else:
                connection.execute(
                    "INSERT INTO skill_category_assignments (skill_name, category_id) VALUES (?, ?) "
                    "ON CONFLICT(skill_name) DO UPDATE SET category_id = excluded.category_id",
                    (skill_name.strip().lower(), category_id))
        return True

    def disabled_names(self) -> set[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT skill_name FROM disabled_skills"
            ).fetchall()
        return {row["skill_name"] for row in rows}
