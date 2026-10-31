"""Read-only local libraries. Not registered as model tools.

All mutations/scans share one lock. File reads use directory descriptors and
O_NOFOLLOW, anchored to the registered root identity (macOS/Linux). Platforms
without these guarantees fail explicitly, never fall back to unsafe path reads.
"""
from __future__ import annotations

import asyncio
import errno
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import threading
from time import monotonic
from uuid import uuid4

from verilex_runtime.storage.database import Database
from verilex_runtime.tools.registry import ToolError
from .source import QuoteSnapshot, SourceSnapshot
from .positions import locate


class KnowledgeService:
    MAX_FILE_BYTES = 32 * 1024 * 1024
    MAX_DOCUMENTS = 5000
    MAX_TOTAL_BYTES = 256 * 1024 * 1024
    MAX_ENTRIES = 20000
    SCAN_SECONDS = 10

    def _read_error(self, error):
        if isinstance(error, UnicodeDecodeError):
            return "文件不是有效的 UTF-8 编码，无法保真读取。"
        if isinstance(error, ValueError):
            return "文件含有空字符，不是可读取的 Markdown 纯文本。"
        if str(error) == "file_limit":
            return f"文件超过单文件 {self.MAX_FILE_BYTES // (1024 * 1024)} MB 上限。"
        if str(error) == "not_regular_file" or getattr(error, 'errno', None) == errno.ELOOP:
            return "此项不是普通文件或为符号链接，无法读取。"
        if str(error) == "file_changed":
            return "文件在读取时发生变化，下一次同步会重试。"
        if isinstance(error, PermissionError):
            return "没有此文件的读取权限，请检查文件访问权限。"
        if isinstance(error, FileNotFoundError):
            return "文件已移动或删除，下一次同步会重新核对。"
        return "文件暂时无法访问，请确认文件已下载到本机且所在磁盘可用。"

    def __init__(self, database: Database, ima=None):
        self.db = database
        self.ima = ima
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.sync_all)
            except Exception:
                # One failed pass must not kill future syncs. Per-library errors
                # are persisted by sync_all without exposing paths in logs.
                pass
            for _ in range(50):
                if self._stop.is_set():
                    return
                await asyncio.sleep(0.1)

    def _library(self, connection, library_id):
        if library_id.startswith("ima:") and self.ima is not None:
            return self.ima.library(library_id)
        row = connection.execute(
            "SELECT * FROM knowledge_libraries WHERE id=? AND detached=0", (library_id,)
        ).fetchone()
        if row is None:
            raise ToolError("knowledge_not_found", "该知识库已解除关联或不存在。")
        return row

    @staticmethod
    def _public(row):
        value = dict(row)
        value.pop("device", None)
        value.pop("inode", None)
        value.pop("detached", None)
        value["excluded"] = json.loads(value["excluded"])
        value["directories"] = json.loads(value["directories"])
        return value

    def libraries(self):
        with self.db.connect() as c:
            rows = c.execute("""SELECT l.*,
                (SELECT count(*) FROM knowledge_documents d WHERE d.library_id=l.id AND d.status='ready') AS count
                FROM knowledge_libraries l WHERE detached=0 ORDER BY name,id""").fetchall()
        return [self._public(row) for row in rows] + (self.ima.libraries() if self.ima else [])

    def associate(self, root: str, name: str | None = None):
        try:
            path = Path(root).expanduser().resolve(strict=True)
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode):
                raise OSError()
        except (OSError, ValueError, RuntimeError):
            raise ToolError("knowledge_root_invalid", "无法访问这个文件夹，请重新选择。") from None
        title = (name or path.name or str(path)).strip()
        if not title or len(title) > 120:
            raise ToolError("knowledge_name_invalid", "名称请使用1至120个字符。")
        with self._lock, self.db.connect() as c:
            existing = c.execute("SELECT * FROM knowledge_libraries WHERE root=?", (str(path),)).fetchone()
            if existing is not None and not existing["detached"]:
                return existing["id"]
            if c.execute("SELECT count(*) FROM knowledge_libraries WHERE detached=0").fetchone()[0] >= 50:
                raise ToolError("knowledge_limit", "已达到本地知识库数量上限（50个）。")
            identifier = existing["id"] if existing is not None else str(uuid4())
            c.execute("""INSERT INTO knowledge_libraries(id,name,root,device,inode) VALUES(?,?,?,?,?)
                ON CONFLICT(root) DO UPDATE SET name=excluded.name,device=excluded.device,
                inode=excluded.inode,detached=0,status='pending',error=NULL""",
                      (identifier, title, str(path), info.st_dev, info.st_ino))
            return identifier

    @staticmethod
    def _excluded(relative: str, exclusions: list[str]) -> bool:
        return any(relative == p or relative.startswith(p + "/") for p in exclusions)

    def update(self, identifier, *, name=None, excluded=None, root=None):
        if identifier.startswith("ima:"):
            raise ToolError("ima_read_only", "请在 ima 中管理资料名称与文件夹。")
        with self._lock, self.db.connect() as c:
            self._library(c, identifier)
            if name is not None:
                if not name.strip() or len(name.strip()) > 120:
                    raise ToolError("knowledge_name_invalid", "名称请使用1至120个字符。")
                c.execute("UPDATE knowledge_libraries SET name=? WHERE id=?", (name.strip(), identifier))
            if excluded is not None:
                for item in excluded:
                    p = PurePosixPath(item)
                    if not item or len(item) > 4096 or "\x00" in item or p.is_absolute() or any(x in (".", "..", "") for x in item.split("/")) or "\\" in item:
                        raise ToolError("knowledge_exclusion_invalid", "请选择库内的子目录。")
                c.execute("UPDATE knowledge_libraries SET excluded=?,status='pending' WHERE id=?",
                          (json.dumps(sorted(set(excluded))), identifier))
                for d in c.execute("SELECT id,path FROM knowledge_documents WHERE library_id=?", (identifier,)).fetchall():
                    if self._excluded(d["path"], excluded):
                        c.execute("DELETE FROM knowledge_documents WHERE id=?", (d["id"],))
            if root is not None:
                try:
                    path = Path(root).expanduser().resolve(strict=True)
                    info = path.stat()
                    if not stat.S_ISDIR(info.st_mode):
                        raise OSError()
                    c.execute("UPDATE knowledge_libraries SET root=?,device=?,inode=?,status='pending',error=NULL WHERE id=?",
                              (str(path), info.st_dev, info.st_ino, identifier))
                    c.execute("DELETE FROM knowledge_documents WHERE library_id=?", (identifier,))
                except (OSError, ValueError, RuntimeError, sqlite3.IntegrityError):
                    raise ToolError("knowledge_root_invalid", "无法使用该文件夹，或它已关联其他知识库。") from None

    def detach(self, identifier):
        if identifier.startswith("ima:") and self.ima:
            return self.ima.detach(identifier)
        with self._lock, self.db.connect() as c:
            self._library(c, identifier)
            c.execute("DELETE FROM knowledge_documents WHERE library_id=?", (identifier,))
            c.execute("UPDATE knowledge_libraries SET detached=1,status='detached' WHERE id=?", (identifier,))

    def _read_bytes(self, library, relative):
        parts = PurePosixPath(relative).parts
        if not parts or any(p in ("..", ".", "") or p.startswith(".") for p in parts) or PurePosixPath(relative).is_absolute():
            raise OSError("invalid_relative_path")
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise OSError("safe_read_unavailable")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        fds = []
        try:
            fd = os.open(library["root"], flags | os.O_DIRECTORY)
            fds.append(fd)
            root_stat = os.fstat(fd)
            if (root_stat.st_dev, root_stat.st_ino) != (library["device"], library["inode"]):
                raise OSError("root_changed")
            for part in parts[:-1]:
                fd = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
                fds.append(fd)
            fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=fd)
            fds.append(fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("not_regular_file")
            if before.st_size > self.MAX_FILE_BYTES:
                raise OSError("file_limit")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(65536, self.MAX_FILE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > self.MAX_FILE_BYTES:
                    raise OSError("file_limit")
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise OSError("file_changed")
            return b"".join(chunks)
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def sync_all(self):
        for library in self.libraries():
            if library.get("kind") == "ima":
                continue
            if self._stop.is_set():
                return
            try:
                self.sync(library["id"])
            except Exception:
                with self.db.connect() as c:
                    c.execute("UPDATE knowledge_libraries SET status='error',error=? WHERE id=? AND detached=0",
                              ("同步失败，请重试。", library["id"]))

    def sync(self, identifier):
        if identifier.startswith("ima:") and self.ima:
            self.ima.documents(identifier)
            return
        with self._lock:
            with self.db.connect() as c:
                library = dict(self._library(c, identifier))
            root = Path(library["root"])
            try:
                info = root.stat()
                if root.is_symlink() or (info.st_dev, info.st_ino) != (library["device"], library["inode"]):
                    raise OSError()
                if not root.is_dir():
                    raise OSError()
            except OSError:
                with self.db.connect() as c:
                    c.execute("UPDATE knowledge_libraries SET status='unavailable',error=? WHERE id=?",
                              ("文件夹暂时无法访问或已移动，请恢复访问或重新关联。", identifier))
                return
            exclusions = json.loads(library["excluded"])
            rows, directories = [], set(json.loads(library["directories"]))
            errors = []
            total, entries = 0, 0
            started = monotonic()
            complete = True
            def failed(_):
                errors.append("部分目录无法读取，未将其资料判为删除。")
            for folder, dirs, files in os.walk(root, followlinks=False, onerror=failed):
                relative_dir = Path(folder).relative_to(root).as_posix()
                kept = []
                for d in sorted(dirs):
                    if entries >= self.MAX_ENTRIES or monotonic() - started > self.SCAN_SECONDS:
                        complete = False
                        break
                    rel = d if relative_dir == "." else relative_dir + "/" + d
                    entries += 1
                    if not d.startswith(".") and not (Path(folder) / d).is_symlink():
                        directories.add(rel)
                        if not self._excluded(rel, exclusions):
                            kept.append(d)
                dirs[:] = kept
                if not complete:
                    break
                for filename in sorted(files):
                    entries += 1
                    if self._stop.is_set():
                        return
                    if entries > self.MAX_ENTRIES or len(rows) >= self.MAX_DOCUMENTS or monotonic() - started > self.SCAN_SECONDS:
                        complete = False
                        break
                    if filename.startswith(".") or Path(filename).suffix.lower() not in (".md", ".markdown"):
                        continue
                    relative = filename if relative_dir == "." else relative_dir + "/" + filename
                    if self._excluded(relative, exclusions):
                        continue
                    try:
                        raw = self._read_bytes(library, relative)
                        body = raw.decode("utf-8", errors="strict")
                        if "\x00" in body:
                            raise ValueError()
                        total += len(raw)
                        if total > self.MAX_TOTAL_BYTES:
                            complete = False
                            break
                        version = SourceSnapshot(identifier, relative, relative, raw).version
                        rows.append((relative, version, raw, body, "ready", None))
                    except (OSError, ValueError) as error:
                        rows.append((relative, "", b"", "", "error", self._read_error(error)))
                if not complete:
                    break
                if entries > self.MAX_ENTRIES or monotonic() - started > self.SCAN_SECONDS:
                    complete = False
                    break
            if not complete:
                errors.append("资料超过本轮处理上限，请排除部分子目录后重试。")
            # Do not publish a pass if the root changed during enumeration.
            try:
                current = root.stat()
                if (current.st_dev, current.st_ino) != (library["device"], library["inode"]):
                    raise OSError()
            except OSError:
                with self.db.connect() as c:
                    c.execute("UPDATE knowledge_libraries SET status='unavailable',error=? WHERE id=?",
                              ("文件夹在同步时发生变化，请重新关联。", identifier))
                return
            with self.db.connect() as c:
                old = {d["path"]: dict(d) for d in c.execute("SELECT id,path,version,status,error FROM knowledge_documents WHERE library_id=?", (identifier,))}
                seen = set()
                for relative, version, raw, body, state, error in rows:
                    seen.add(relative)
                    previous = old.get(relative)
                    if previous and previous["version"] == version and previous["status"] == state and previous["error"] == error:
                        continue
                    c.execute("""INSERT INTO knowledge_documents(id,library_id,path,version,raw,body,status,error)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(library_id,path) DO UPDATE SET
                        version=excluded.version,raw=excluded.raw,body=excluded.body,status=excluded.status,error=excluded.error""",
                              (str(uuid4()), identifier, relative, version, raw, body, state, error))
                for relative, previous in old.items():
                    if relative not in seen:
                        if complete and not errors:
                            c.execute("DELETE FROM knowledge_documents WHERE id=?", (previous["id"],))
                        else:
                            c.execute("UPDATE knowledge_documents SET status='pending',error=? WHERE id=?",
                                      ("尚未重新核对。", previous["id"]))
                state = "partial" if errors or any(row[4] == "error" for row in rows) else "ready"
                c.execute("UPDATE knowledge_libraries SET status=?,error=?,directories=?,synced_at=? WHERE id=?",
                          (state, " ".join(dict.fromkeys(errors)) or None, json.dumps(sorted(directories)),
                           datetime.now(timezone.utc).isoformat(), identifier))

    def documents(self, identifier, query="", offset=0, limit=100, folder_id=""):
        if identifier.startswith("ima:") and self.ima:
            if not 0 <= offset <= 20000 or not 1 <= limit <= 100 or len(query) > 256:
                raise ToolError("knowledge_query_invalid", "查询或页码超出范围。")
            return self.ima.documents(identifier, query, offset, limit, folder_id)
        if not 0 <= offset <= 20000 or not 1 <= limit <= 100 or len(query) > 256 or "\x00" in query:
            raise ToolError("knowledge_query_invalid", "查询或页码超出范围。")
        with self.db.connect() as c:
            library = self._library(c, identifier)
            if library["status"] in ("unavailable", "error"):
                raise ToolError("knowledge_unavailable", library["error"] or "知识库暂时无法使用。")
            sql = """SELECT id,path,version,status,error,
                substr(body,max(1,instr(body,?)-80),max(200,length(?)+200)) AS snippet,
                CASE WHEN ? != '' THEN instr(body,?)-1 ELSE -1 END AS match_start
                FROM knowledge_documents WHERE library_id=?"""
            args = [query, query, query, query, identifier]
            if query:
                sql += " AND status='ready' AND (instr(path,?)>0 OR instr(body,?)>0)"
                args += [query, query]
                if len(query) >= 3:
                    sql += " AND rowid IN (SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH ?)"
                    args += ['"' + query.replace('"', '""') + '"']
            if not query:
                # Select the page using the (library_id, path) index before loading
                # document bodies. A browse refresh should not scan every long file.
                sql = """SELECT id,path,version,status,error,substr(body,1,200) AS snippet,
                    -1 AS match_start FROM knowledge_documents WHERE rowid IN (
                        SELECT rowid FROM knowledge_documents
                        WHERE library_id=? ORDER BY path LIMIT ? OFFSET ?
                    ) ORDER BY path"""
                args = [identifier]
            started = monotonic()
            c.set_progress_handler(lambda: int(monotonic() - started > 1), 1000)
            try:
                statement = sql if not query else sql + " ORDER BY path LIMIT ? OFFSET ?"
                rows = c.execute(statement, [*args, limit + 1, offset]).fetchall()
            except sqlite3.OperationalError as error:
                if getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
                    raise
                message = "本次搜索未完成，请缩小查询范围。" if query else "资料列表加载超时，请重试。"
                raise ToolError("knowledge_query_incomplete", message) from None
        items = []
        for row in rows[:limit]:
            item = dict(row)
            position = item['match_start']
            item.update(match_start=position if position >= 0 else None,
                        match_end=position + len(query) if position >= 0 else None,
                        read_start=max(0, position - 300))
            items.append(item)
        return {"items": items, "has_more": len(rows) > limit}

    def _source(self, c, identifier, document_id, version):
        library = self._library(c, identifier)
        row = c.execute("SELECT * FROM knowledge_documents WHERE id=? AND library_id=? AND status='ready'",
                        (document_id, identifier)).fetchone()
        if row is None or self._excluded(row["path"], json.loads(library["excluded"])):
            raise ToolError("knowledge_not_found", "资料已移除或尚未完成同步。")
        try:
            raw = self._read_bytes(library, row["path"])
            source = SourceSnapshot(identifier, document_id, row["path"], raw)
        except (OSError, ValueError):
            raise ToolError("knowledge_unavailable", "原文件暂时无法读取，请刷新或重新关联。") from None
        if source.version != version or source.version != row["version"]:
            raise ToolError("knowledge_source_changed", "原文已变化，请刷新资料后重新打开。")
        return row, source

    def read(self, identifier, document_id, version, start=0, max_chars=20000):
        if type(start) is not int or start < 0 or not 1 <= max_chars <= 20000:
            raise ToolError("knowledge_range_invalid", "原文位置无效。")
        if identifier.startswith("ima:") and self.ima:
            return self.ima.read(identifier, document_id, version, start, max_chars)
        with self._lock, self.db.connect() as c:
            row, source = self._source(c, identifier, document_id, version)
            if start > len(source.text):
                raise ToolError("knowledge_range_invalid", "原文位置无效。")
            end = min(start + max_chars, len(source.text))
            return {"id": document_id, "path": row["path"], "version": version,
                    "text": source.text[start:end], "start": start, "end": end,
                    "total_chars": len(source.text), "has_more": end < len(source.text)}

    def matches(self, identifier, document_id, version, query, start=0):
        if not isinstance(query, str) or not 1 <= len(query) <= 256 or "\x00" in query:
            raise ToolError("knowledge_query_invalid", "请输入1至256字的原文关键词。")
        if type(start) is not int or start < 0:
            raise ToolError("knowledge_range_invalid", "原文位置无效。")
        if identifier.startswith("ima:"):
            raise ToolError("knowledge_positions_unavailable", "此来源尚不支持原文位置定位。")
        with self._lock, self.db.connect() as c:
            _, source = self._source(c, identifier, document_id, version)
            if start > len(source.text):
                raise ToolError("knowledge_range_invalid", "原文位置无效。")
            return {**locate(source.text, query, start), 'version': version,
                    'notice': '命中位置是定位线索；读取后才能生成引用。'}

    def save_quote(self, quote: QuoteSnapshot):
        """Trusted internal call only. No public quote creation API."""
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM turns WHERE id=? AND thread_id=?", (quote.turn_id, quote.thread_id)).fetchone() is None:
                raise ToolError("knowledge_not_found", "引用所属对话不存在。")
            identifier = str(uuid4())
            c.execute("INSERT INTO knowledge_quotes VALUES(?,?,?,?,?,?)",
                      (identifier, quote.thread_id, quote.turn_id, quote.library_id, quote.document_id,
                       json.dumps(asdict(quote), ensure_ascii=False)))
            return identifier

    def quote_view(self, identifier, thread_id):
        value = self.historical_quote(identifier, thread_id)
        with self._lock, self.db.connect() as c:
            library = c.execute('SELECT detached FROM knowledge_libraries WHERE id=?', (value['library_id'],)).fetchone()
            doc = c.execute('SELECT id FROM knowledge_documents WHERE id=? AND library_id=?', (value['document_id'], value['library_id'])).fetchone()
            # Index rows are rebuildable (exclude/reinclude, detach/reassociate).
            # Resolve the saved relative path only within its original library;
            # current bytes are still checked by read before reporting fidelity.
            if doc is None:
                doc = c.execute('SELECT id FROM knowledge_documents WHERE library_id=? AND path=?',
                                (value['library_id'], value['source_name'])).fetchone()
            state = 'current'
            if library is None or library['detached']:
                state = 'detached'
            elif doc is None:
                state = 'removed'
            else:
                try:
                    self.read(value['library_id'], doc['id'], value['version'], value['start'], 1)
                except ToolError as error:
                    state = 'changed' if error.code == 'knowledge_source_changed' else 'unavailable'
        return {**value, 'source_state': state}

    def historical_quote(self, identifier, thread_id):
        with self.db.connect() as c:
            row = c.execute("SELECT payload FROM knowledge_quotes WHERE id=? AND thread_id=?", (identifier, thread_id)).fetchone()
            if row is None:
                raise ToolError("knowledge_not_found", "该引用不存在。")
            return json.loads(row[0])
