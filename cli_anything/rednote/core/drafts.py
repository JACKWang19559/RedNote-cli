"""Offline drafts with transactional, persistent undo and redo.

The character limits are simple Unicode code-point checks for local drafting;
they are not a claim about Xiaohongshu's server-side publishing rules.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import RedNoteError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _encode(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _decode(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        document = json.loads(value)
        required = {
            "id",
            "title",
            "content",
            "tags",
            "images",
            "created_at",
            "updated_at",
        }
        if not isinstance(document, dict) or not required.issubset(document):
            raise ValueError("Invalid draft record")
        if not all(
            isinstance(document[key], str) for key in required - {"tags", "images"}
        ):
            raise ValueError("Invalid draft strings")
        if not all(
            isinstance(document[key], list)
            and all(isinstance(item, str) for item in document[key])
            for key in ("tags", "images")
        ):
            raise ValueError("Invalid draft lists")
        return document
    except (ValueError, TypeError) as exc:
        raise RedNoteError(
            "STORAGE_ERROR",
            "草稿数据库中的记录已损坏。",
            "请先备份 drafts.sqlite3 再进行修复。",
        ) from exc


def _validate(
    title: str, content: str, tags: list[str], images: list[str]
) -> dict[str, Any]:
    if not isinstance(title, str) or not title.strip():
        raise RedNoteError("DRAFT_INVALID", "草稿标题不能为空。")
    title = title.strip()
    if len(title) > 20 or "\n" in title or "\r" in title:
        raise RedNoteError("DRAFT_INVALID", "草稿标题必须为单行，最多 20 个字符。")
    if not isinstance(content, str) or len(content) > 1000:
        raise RedNoteError("DRAFT_INVALID", "草稿正文必须为文本，最多 1000 个字符。")
    normalized: dict[str, list[str]] = {}
    for key, values in (("tags", tags), ("images", images)):
        if not isinstance(values, (list, tuple)) or not all(
            isinstance(value, str) for value in values
        ):
            raise RedNoteError("DRAFT_INVALID", f"{key} 必须是文本列表。")
        cleaned: list[str] = []
        for value in values:
            value = value.strip()
            if key == "tags":
                value = value.lstrip("#").strip()
            if not value or "\n" in value or "\r" in value:
                raise RedNoteError("DRAFT_INVALID", f"{key} 不能包含空值或换行。")
            if value not in cleaned:
                cleaned.append(value)
        normalized[key] = cleaned
    return {"title": title, "content": content, **normalized}


class DraftStore:
    """Persist drafts locally; every mutation and history change is atomic."""

    def __init__(self, home: Path):
        self.home = Path(home).expanduser()
        self.path = self.home / "drafts.sqlite3"
        try:
            self.home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RedNoteError("STORAGE_ERROR", "无法创建草稿数据目录。") from exc
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RedNoteError("STORAGE_ERROR", "草稿数据库版本较新，请升级 CLI。")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS history "
                "(position INTEGER PRIMARY KEY, draft_id TEXT NOT NULL, before_doc TEXT, after_doc TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS history_state "
                "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), cursor INTEGER NOT NULL)"
            )
            connection.execute("INSERT OR IGNORE INTO history_state VALUES (1, 0)")
            connection.execute("PRAGMA user_version = 1")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            if connection is not None:
                connection.rollback()
            raise RedNoteError(
                "STORAGE_ERROR",
                "无法读写草稿数据库。",
                "检查数据目录权限，或备份并检查 drafts.sqlite3。",
            ) from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _get(connection: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT document FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            raise RedNoteError(
                "DRAFT_NOT_FOUND",
                "找不到指定的草稿。",
                "使用 draft list 查看已有草稿 ID。",
            )
        return _decode(row[0])

    @staticmethod
    def _restore(
        connection: sqlite3.Connection, draft_id: str, document: dict[str, Any] | None
    ) -> None:
        if document is None:
            connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        else:
            connection.execute(
                "INSERT INTO drafts (id, document) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET document = excluded.document",
                (draft_id, _encode(document)),
            )

    @staticmethod
    def _record(
        connection: sqlite3.Connection,
        draft_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        cursor = connection.execute(
            "SELECT cursor FROM history_state WHERE singleton = 1"
        ).fetchone()[0]
        connection.execute("DELETE FROM history WHERE position > ?", (cursor,))
        connection.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?)",
            (
                cursor + 1,
                draft_id,
                _encode(before) if before is not None else None,
                _encode(after) if after is not None else None,
            ),
        )
        connection.execute(
            "UPDATE history_state SET cursor = ? WHERE singleton = 1", (cursor + 1,)
        )

    def create(
        self,
        title: str,
        content: str = "",
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        fields = _validate(
            title,
            content,
            [] if tags is None else tags,
            [] if images is None else images,
        )
        timestamp = _now()
        document = {
            "id": uuid4().hex,
            **fields,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._connection() as connection:
            self._restore(connection, document["id"], document)
            self._record(connection, document["id"], None, document)
        return document

    def list(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            documents = [
                _decode(row[0])
                for row in connection.execute("SELECT document FROM drafts")
            ]
        return sorted(
            documents,
            key=lambda document: (document["updated_at"], document["id"]),
            reverse=True,
        )

    def get(self, draft_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            return self._get(connection, draft_id)

    def update(
        self,
        draft_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            before = self._get(connection, draft_id)
            values = {
                "title": title,
                "content": content,
                "tags": tags,
                "images": images,
            }
            fields = _validate(
                **{
                    key: before[key] if value is None else value
                    for key, value in values.items()
                }
            )
            if all(before[key] == value for key, value in fields.items()):
                return before
            after = {**before, **fields, "updated_at": _now()}
            self._restore(connection, draft_id, after)
            self._record(connection, draft_id, before, after)
            return after

    def delete(self, draft_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            before = self._get(connection, draft_id)
            self._restore(connection, draft_id, None)
            self._record(connection, draft_id, before, None)
            return before

    def _travel(self, redo: bool) -> dict[str, Any]:
        with self._connection() as connection:
            cursor = connection.execute(
                "SELECT cursor FROM history_state WHERE singleton = 1"
            ).fetchone()[0]
            position = cursor + 1 if redo else cursor
            row = connection.execute(
                "SELECT draft_id, before_doc, after_doc FROM history WHERE position = ?",
                (position,),
            ).fetchone()
            if row is None:
                raise RedNoteError(
                    "DRAFT_HISTORY_EMPTY",
                    "没有可重做的操作。" if redo else "没有可撤销的操作。",
                )
            document = _decode(row[2] if redo else row[1])
            self._restore(connection, row[0], document)
            connection.execute(
                "UPDATE history_state SET cursor = ? WHERE singleton = 1",
                (cursor + (1 if redo else -1),),
            )
            return {
                "action": "redo" if redo else "undo",
                "id": row[0],
                "draft": document,
            }

    def undo(self) -> dict[str, Any]:
        return self._travel(False)

    def redo(self) -> dict[str, Any]:
        return self._travel(True)

    def export(
        self, draft_id: str, path: Path, format: str = "markdown"
    ) -> dict[str, Any]:
        document = self.get(draft_id)
        target = Path(path).expanduser()
        if format == "json":
            content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        elif format == "markdown":
            parts = [f"# {document['title']}", document["content"]]
            if document["tags"]:
                parts.append(" ".join(f"#{tag}" for tag in document["tags"]))
            for index, image in enumerate(document["images"], 1):
                # Angle-bracket destinations preserve spaces in local image paths.
                destination = image.replace("<", "%3C").replace(">", "%3E")
                parts.append(f"![图片 {index}](<{destination}>)")
            content = "\n\n".join(parts) + "\n"
        else:
            raise RedNoteError("DRAFT_INVALID", "导出格式必须为 json 或 markdown。")
        try:
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        except FileExistsError as exc:
            raise RedNoteError("FILE_EXISTS", "导出文件已存在，请选择新路径。") from exc
        except (OSError, ValueError) as exc:
            raise RedNoteError(
                "EXPORT_ERROR", "无法写入导出文件。", "检查目标目录是否存在且可写。"
            ) from exc
        return {"id": draft_id, "format": format, "path": str(target.resolve())}
