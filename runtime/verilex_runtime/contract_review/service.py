"""合同审查编排：文件校验→场景线程与附件→意见落库（运行时援引核验）→状态机。

审查轮次由前端在返回的线程上按既有聊天通道启动（审批、停止、事件流全部
复用）；意见通过 contract_opinion 工具逐条到达，本服务在落库前执行
显式来源与成功读取记录对账；法律适用与意见内容由模型及用户判断。
"""
from __future__ import annotations

import hashlib
import asyncio
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.documents.docx_package import checked_bytes
from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.sessions.thread_service import ThreadService

from verilex_runtime.contract_review.annotator import CommentSpec, ContractAnnotator
from verilex_runtime.contract_review.citations import CitationGate, source_index_from_tool_calls
from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.contract_review.opinions import Citation, Opinion, render_comment_text

PARTIES = frozenset({"party_a", "party_b", "neutral"})
STRICTNESSES = frozenset({"loose", "normal", "strict"})
CITATION_KINDS = frozenset({"web", "mcp", "knowledge", "tool"})

_MAX_CITATIONS = 8


def _export_filename(stem: str, *, report: bool = False, edited: bool = False) -> str:
    """Keep the suggested copy name portable and within the native save limit."""
    suffix = "（修改副本）.docx" if edited else "（审查意见书）.docx" if report else "（批注版）.docx"
    stem = "".join(
        "_" if ord(char) < 32 or 127 <= ord(char) <= 159
        or char in '/\\:*?"<>|' or '\u202a' <= char <= '\u202e'
        or '\u2066' <= char <= '\u2069' else char
        for char in stem
    ).strip().lstrip(".")
    budget = 240 - len(suffix.encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").strip()
    return f"{stem or '合同'}{suffix}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded(value: object, limit: int, *, minimum: int = 1,
             code: str = "contract_annotation_write_failed") -> str:
    if (not isinstance(value, str) or len(value) < minimum
            or len(value) > limit or (minimum > 0 and not value.strip())):
        raise ContractReviewError(code, "field out of bounds")
    return value


class ContractReviewService:
    def __init__(
        self,
        *,
        threads: ThreadService,
        attachments: AttachmentService,
        repository: ContractReviewRepository,
        tool_calls: ToolCallRepository,
        turns: TurnRepository,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ):
        self._threads = threads
        self._attachments = attachments
        self._repository = repository
        self._tool_calls = tool_calls
        self._turns = turns
        self._id_factory = id_factory or (lambda: f"ctr_{uuid.uuid4()}")
        self._now = now or _utc_now
        self._cancel_turn: Callable[[str], bool] | None = None
        self._start_lock = asyncio.Lock()
        self._stopped_turns: set[str] = set()
        from verilex_runtime.contract_review.editor import ContractEditor
        self.editor = ContractEditor(self)

    def bind_cancel(self, cancel: Callable[[str], bool]) -> None:
        self._cancel_turn = cancel

    async def start_review(
        self, file_bytes: bytes, filename: str, **options,
    ) -> dict:
        # Upload awaits I/O: hold the reservation through record creation so
        # another POST cannot pass the one-contract check in between.
        async with self._start_lock:
            return await self._start_review(file_bytes, filename, **options)

    async def _start_review(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        provider_id: str | None,
        model: str,
        skill: str,
        party: str,
        strictness: str,
        signature: str,
        retrieval_enabled: bool,
        document_id: str | None = None,
        document_revision: int | None = None,
        requirements: str = "",
        custom_party: str = "",
        discussion_only: bool = False,
    ) -> dict:
        while (busy := self._repository.running_review()) is not None:
            refreshed = self.reconcile(busy["id"])
            if (refreshed["status"] in {"pending", "running"}
                    or self._turns.list_active(busy["thread_id"])):
                raise ContractReviewError("contract_thread_busy")
        normalized = (filename or "").strip()
        if not normalized.lower().endswith((".doc", ".docx")):
            raise ContractReviewError("contract_file_unsupported", "not a .docx filename")
        if party not in PARTIES or strictness not in STRICTNESSES:
            raise ContractReviewError("contract_file_unsupported", "invalid review options")
        if not isinstance(requirements, str) or len(requirements) > 4000 or "\x00" in requirements:
            raise ContractReviewError("contract_requirements_invalid")
        requirements = requirements.strip()
        if not isinstance(custom_party, str) or len(custom_party) > 200 or "\x00" in custom_party:
            raise ContractReviewError("contract_file_unsupported", "invalid custom party")
        custom_party = custom_party.strip()
        signature = _bounded(signature, 80, code="contract_annotation_write_failed")
        skill = _bounded(skill, 128, code="contract_annotation_write_failed")
        if not file_bytes:
            raise ContractReviewError("contract_file_unsupported", "empty file")
        if discussion_only and document_id is None:
            raise ContractReviewError('contract_editor_selection')
        from verilex_runtime.attachments.inspection import normalize_display_name
        normalize_display_name(normalized)
        if document_id is not None:
            document = self._repository.word_document(document_id)
            if (document is None or document["revision"] != document_revision
                    or bytes(document["content"]) != file_bytes):
                raise ContractReviewError("contract_editor_conflict")
        original_bytes = file_bytes
        legacy = normalized.lower().endswith(".doc")
        if legacy:
            from verilex_runtime.attachments.legacy_doc import convert_doc
            import anyio
            file_bytes = await anyio.to_thread.run_sync(convert_doc, file_bytes)
        digest = hashlib.sha256(original_bytes).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".docx") as staged:
            staged.write(file_bytes)
            staged.flush()
            try:
                checked_bytes(Path(staged.name))
            except AttachmentDomainError:
                raise ContractReviewError("contract_file_unsupported") from None
        stem = Path(normalized).stem.strip() or normalized
        thread = self._threads.create(
            f"{stem} · 合同审查",
            model,
            provider_id=provider_id,
            scene="contract-review",
            legal_skills=(skill,),
        )

        async def chunks():
            yield file_bytes

        upload = await self._attachments.upload(thread.id, (normalized[:-4].encode("utf-8")[:250].decode("utf-8", errors="ignore") + ".docx") if legacy else normalized, None, chunks())
        now = self._now()
        record = self._repository.create_review(
            id=self._id_factory(),
            thread_id=thread.id,
            filename=normalized,
            file_digest=digest,
            original=original_bytes,
            normalized_docx=file_bytes if legacy else None,
            skill=skill,
            party=party,
            strictness=strictness,
            signature=signature,
            retrieval_enabled=retrieval_enabled,
            requirements=requirements,
            custom_party=custom_party,
            status="completed" if discussion_only else "pending",
            discussion_only=discussion_only,
            created_at=now,
            updated_at=now,
        )
        if document_id is not None:
            self._repository.link_word_document(record["id"], document_id)
        return {
            "review_id": record["id"],
            "thread_id": thread.id,
            "filename": normalized,
            "upload_id": upload.upload.id,
        }

    def submit_opinion(self, context, payload: dict) -> dict:
        thread_id = getattr(context, "thread_id", None)
        review = (
            self._repository.review_for_thread(thread_id)
            if isinstance(thread_id, str) and thread_id
            else None
        )
        if review is None:
            raise ContractReviewError("contract_not_found")
        turn_id = getattr(context, "turn_id", None)
        if self.editor.has_snapshot(context):
            # The original review and its exports never receive edited-copy
            # excerpts, even if a caller bypasses the Agent tool exposure gate.
            raise ContractReviewError("contract_editor_selection")
        if turn_id in self._stopped_turns:
            raise ContractReviewError("contract_thread_busy")
        turn = self._turns.get(turn_id) if isinstance(turn_id, str) else None
        if turn is not None and not any(active.id == turn.id for active in self._turns.list_active(thread_id)):
            raise ContractReviewError("contract_thread_busy")
        if review["status"] in {"stopped", "failed"}:
            if not any(turn.id == turn_id for turn in self._turns.list_active(thread_id)):
                raise ContractReviewError("contract_thread_busy")
            review = self._repository.update_status(review["id"], "running", updated_at=self._now())
        if not isinstance(payload, dict):
            raise ContractReviewError("contract_annotation_write_failed", "payload invalid")
        opinion = self._opinion_from_payload(payload)
        existing = self._repository.get_opinion(opinion.opinion_id)
        if existing is not None and existing["review_id"] != review["id"]:
            raise ContractReviewError("contract_opinion_not_found")
        index = source_index_from_tool_calls(
            self._tool_calls.list_by_thread(thread_id)
        )
        gated = CitationGate().validate([opinion], index)[0]
        now = self._now()
        if review["status"] in {"pending", "completed"}:
            self._repository.update_status(review["id"], "running", updated_at=now)
        self._repository.upsert_opinion(
            id=gated.opinion_id,
            review_id=review["id"],
            clause_label=gated.clause_label,
            clause_excerpt=gated.clause_excerpt,
            anchor_text=gated.anchor_text,
            occurrence=gated.occurrence,
            level=gated.level,
            category=gated.category,
            body=gated.body,
            suggestion=gated.suggestion,
            replacement_text=gated.replacement_text,
            created_at=now,
            updated_at=now,
        )
        self._repository.clear_citations(gated.opinion_id)
        for citation in gated.citations:
            self._repository.add_citation(
                id=self._id_factory(),
                opinion_id=gated.opinion_id,
                document=citation.document,
                source_kind=citation.source_kind,
                source_call_id=citation.source_call_id,
                source_label=citation.source_label,
                verified_at=citation.verified_at,
                created_at=now,
            )
        stripped = len(opinion.citations) - len(gated.citations)
        return {
            "opinion_id": gated.opinion_id,
            "citations_kept": len(gated.citations),
            "citations_stripped": stripped,
            "notice": (
                f"系统核验：{stripped} 条援引没有本次会话真实检索记录，已剥离，"
                "不会写入批注。"
                if stripped
                else "意见已保存。"
            ),
        }

    def _opinion_from_payload(self, payload: dict) -> Opinion:
        raw_id = payload.get("opinion_id")
        opinion_id = (
            raw_id if isinstance(raw_id, str) and raw_id.strip() else self._id_factory()
        )
        citations: list[Citation] = []
        raw_citations = payload.get("citations", [])
        if raw_citations is None:
            raw_citations = []
        if not isinstance(raw_citations, list) or len(raw_citations) > _MAX_CITATIONS:
            raise ContractReviewError(
                "contract_annotation_write_failed", "too many citations"
            )
        for item in raw_citations:
            if not isinstance(item, dict):
                raise ContractReviewError(
                    "contract_annotation_write_failed", "citation invalid"
                )
            document = _bounded(item.get("document"), 200)
            kind = item.get("source_kind", "tool")
            if kind not in CITATION_KINDS:
                raise ContractReviewError(
                    "contract_annotation_write_failed", "citation channel invalid"
                )
            citations.append(
                Citation(
                    document=document,
                    source_kind=kind,
                    source_label=_bounded(item.get("source_label", ""), 120, minimum=0),
                    verified_at=_bounded(item.get("verified_at", ""), 10, minimum=0),
                    source_call_id=_bounded(item.get("source_call_id", ""), 200, minimum=0),
                )
            )
        occurrence = payload.get("occurrence")
        if occurrence is not None and (
            not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 0
        ):
            raise ContractReviewError(
                "contract_annotation_write_failed", "occurrence invalid"
            )
        return Opinion(
            opinion_id=opinion_id,
            clause_label=_bounded(payload.get("clause_label"), 200),
            clause_excerpt=_bounded(payload.get("clause_excerpt"), 600),
            anchor_text=_bounded(payload.get("anchor_text"), 600),
            occurrence=occurrence,
            level=_bounded(payload.get("level", ""), 40, minimum=0) or "",
            category=_bounded(payload.get("category", ""), 40, minimum=0) or "",
            body=_bounded(payload.get("body"), 4000),
            suggestion=_bounded(payload.get("suggestion", ""), 4000, minimum=0) or "",
            adopted=True,
            edited_text=None,
            citations=tuple(citations),
            replacement_text=(_bounded(payload['replacement_text'], 4000, minimum=0)
                              if payload.get('replacement_text') is not None else None),
        )

    def update_opinion(
        self,
        review_id: str,
        opinion_id: str,
        *,
        adopted: bool | None = None,
        edited_text: str | None = None,
    ) -> dict:
        review = self._repository.get_review(review_id)
        if review is None:
            raise ContractReviewError("contract_not_found")
        opinion = self._repository.get_opinion(opinion_id)
        if opinion is None or opinion["review_id"] != review_id:
            raise ContractReviewError("contract_opinion_not_found")
        try:
            return self._repository.update_opinion(
                opinion_id,
                adopted=adopted,
                edited_text=edited_text,
                updated_at=self._now(),
            )
        except ValueError as error:
            raise ContractReviewError(
                "contract_annotation_write_failed", str(error)
            ) from None

    def opinion_with_citations(self, review_id: str) -> list[dict]:
        """List opinions of a review, each with its citation records attached."""
        opinions = []
        for row in self._repository.list_opinions(review_id):
            opinions.append({
                **row,
                "citations": [citation if citation["source_call_id"] else {**citation, "source_label": "未关联读取记录", "verified_at": ""}
                              for citation in self._repository.opinion_citations(row["id"])],
            })
        return opinions

    def reconcile(self, review_id: str) -> dict:
        from verilex_runtime.storage.models import TurnStatus

        review = self._repository.get_review(review_id)
        if review is None:
            raise ContractReviewError("contract_not_found")
        active = [turn for turn in self._turns.list_active(review["thread_id"])
                  if turn.id not in self._stopped_turns]
        history = self._turns.list(review["thread_id"])
        if active and review["status"] in {"pending", "completed", "stopped", "failed"}:
            review = self._repository.update_status(
                review_id, "running", updated_at=self._now()
            )
        if not active and history and review["status"] in {"pending", "running"}:
            if review["status"] == "pending":
                review = self._repository.update_status(
                    review_id, "running", updated_at=self._now()
                )
            final = history[-1] if history else None
            status = (
                "stopped" if final is not None and final.status in {TurnStatus.CANCELLED, TurnStatus.INTERRUPTED} else "failed"
                if final is not None and final.status is TurnStatus.FAILED
                else "completed"
            )
            review = self._repository.update_status(
                review_id, status, updated_at=self._now()
            )
        return review

    def stop(self, review_id: str) -> dict:
        review = self._repository.get_review(review_id)
        if review is None:
            raise ContractReviewError("contract_not_found")
        if review["status"] == "completed" and self._turns.list_active(review["thread_id"]):
            review = self._repository.update_status(review_id, "running", updated_at=self._now())
        for turn in self._turns.list_active(review["thread_id"]):
            self._stopped_turns.add(turn.id)
            if self._cancel_turn is not None:
                self._cancel_turn(turn.id)
        if review["status"] in ("pending", "running"):
            review = self._repository.update_status(
                review_id, "stopped", updated_at=self._now()
            )
        return review

    def export_report(self, review_id: str) -> dict:
        return self.export(review_id, report=True)

    def export(self, review_id: str, *, report: bool = False) -> dict:
        """先筛后导：只写采用的、改定后的意见；写入与验证一体，失败不产出。"""
        import base64
        import tempfile

        review = self.reconcile(review_id)
        if self._turns.list_active(review["thread_id"]) or review["status"] == "running":
            raise ContractReviewError("contract_thread_busy")
        original = self._repository.review_bytes(review_id)
        if not original:
            raise ContractReviewError(
                "contract_integrity_failed", "original bytes missing"
            )
        rows = self._repository.list_opinions(review_id)
        selected = [row for row in rows if row["adopted"]]
        if not selected:
            raise ContractReviewError(
                "contract_annotation_write_failed", "no adopted opinions"
            )
        index = source_index_from_tool_calls(
            self._tool_calls.list_by_thread(review["thread_id"])
        )
        opinions = []
        for row in selected:
            citations = tuple(
                Citation(
                    document=item["document"],
                    source_kind=item["source_kind"],
                    source_label=item["source_label"],
                    verified_at=item["verified_at"],
                    source_call_id=item.get("source_call_id", ""),
                )
                for item in self._repository.opinion_citations(row["id"])
            )
            opinion = Opinion(
                opinion_id=row["id"],
                clause_label=row["clause_label"],
                clause_excerpt=row["clause_excerpt"],
                anchor_text=row["anchor_text"],
                occurrence=row["occurrence"],
                level=row["level"],
                category=row["category"],
                body=row["body"],
                suggestion=row["suggestion"],
                adopted=True,
                edited_text=row["edited_text"],
                citations=citations,
            )
            opinions.append(opinion)
        # 导出前复核：检索记录变化可能导致既有援引不再成立，重新过一遍门禁。
        gated = CitationGate().validate(opinions, index)
        comments = [
            CommentSpec(
                anchor_text=opinion.anchor_text,
                occurrence=opinion.occurrence,
                text=render_comment_text(opinion, skill_levels={}),
                author=review["signature"],
            )
            for opinion in gated
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "original.docx"
            source.write_bytes(original)
            dest = root / "annotated.docx"
            ContractAnnotator().write(source, comments, dest)
            annotated = dest.read_bytes()
        stem = Path(review["filename"]).stem
        if report:
            from verilex_runtime.contract_review.report import build_report
            annotated = build_report(review, gated, original, self._now())
        return {
            "filename": _export_filename(stem, report=report),
            "content": base64.b64encode(annotated).decode("ascii"),
        }
