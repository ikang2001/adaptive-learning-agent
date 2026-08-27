from __future__ import annotations

import base64
import hashlib
import importlib
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar

import httpx
from docx import Document
from markdown_it import MarkdownIt
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import (
    ResourceImportStatus,
    ResourceStatus,
    ResourceType,
)
from app.errors import AppError
from app.infrastructure.db.models import (
    KnowledgeNode,
    LearningResource,
    ResourceChunk,
    ResourceImportRun,
    ResourceKnowledgeMapping,
    ResourceReviewDecision,
    ResourceSection,
    ResourceVersion,
)

fitz: Any = importlib.import_module("pymupdf")


@dataclass(frozen=True, slots=True)
class ParsedSection:
    title: str
    path: str
    level: int
    sequence: int
    page_start: int | None
    page_end: int | None
    text: str
    method: str


class ResourceService:
    allowed_extensions: ClassVar[set[str]] = {
        ".pdf",
        ".docx",
        ".md",
        ".markdown",
        ".jpg",
        ".jpeg",
        ".png",
    }

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create_upload(
        self,
        user_id: uuid.UUID,
        title: str,
        resource_type: ResourceType,
        school_profile_id: uuid.UUID | None,
        filename: str,
        media_type: str | None,
        content: bytes,
    ) -> ResourceImportRun:
        extension = Path(filename).suffix.lower()
        if extension not in self.allowed_extensions:
            raise AppError(422, "UNSUPPORTED_RESOURCE_TYPE", "unsupported resource file type")
        max_bytes = (
            self._settings.resource_max_image_bytes
            if extension in {".jpg", ".jpeg", ".png"}
            else self._settings.resource_max_document_bytes
        )
        if not content or len(content) > max_bytes:
            raise AppError(422, "RESOURCE_SIZE_INVALID", "resource file size is outside the limit")
        digest = hashlib.sha256(content).hexdigest()
        duplicate = await self._session.scalar(
            select(ResourceVersion).where(ResourceVersion.content_hash == digest)
        )
        if duplicate is not None:
            raise AppError(409, "DUPLICATE_RESOURCE", "the same file has already been uploaded")
        resource = LearningResource(
            title=title,
            resource_type=resource_type,
            status=ResourceStatus.PROCESSING,
            school_profile_id=school_profile_id,
            description="",
            version=1,
        )
        self._session.add(resource)
        await self._session.flush()
        storage_root = Path(self._settings.resource_storage_root)
        storage_root.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        path = storage_root / f"{resource.id}-{digest[:12]}{extension}"
        path.write_bytes(content)
        version = ResourceVersion(
            resource_id=resource.id,
            version_number=1,
            original_filename=filename,
            media_type=media_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream",
            content_hash=digest,
            storage_path=str(path),
            size_bytes=len(content),
            parser_version="resource_parser_v1",
        )
        self._session.add(version)
        await self._session.flush()
        resource.current_version_id = version.id
        run = ResourceImportRun(
            resource_version_id=version.id,
            requested_by_user_id=user_id,
            status=ResourceImportStatus.QUEUED,
            progress=0,
            version=1,
        )
        self._session.add(run)
        await self._session.commit()
        return run

    async def create_upload_bundle(
        self,
        user_id: uuid.UUID,
        title: str,
        resource_type: ResourceType,
        school_profile_id: uuid.UUID | None,
        files: list[tuple[str, str | None, bytes]],
    ) -> ResourceImportRun:
        if not files or len(files) > self._settings.resource_max_image_count:
            raise AppError(422, "RESOURCE_FILE_COUNT_INVALID", "upload 1 to 50 files")
        if len(files) == 1:
            filename, media_type, content = files[0]
            return await self.create_upload(
                user_id,
                title,
                resource_type,
                school_profile_id,
                filename,
                media_type,
                content,
            )
        if any(
            Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png"}
            for filename, _, _ in files
        ):
            raise AppError(422, "MULTI_UPLOAD_IMAGES_ONLY", "multiple uploads must all be images")
        if any(len(content) > self._settings.resource_max_image_bytes for _, _, content in files):
            raise AppError(422, "RESOURCE_SIZE_INVALID", "one or more images exceed the limit")
        images: list[Image.Image] = []
        try:
            for _, _, content in files:
                with Image.open(BytesIO(content)) as opened_image:
                    images.append(opened_image.convert("RGB"))
            output = BytesIO()
            images[0].save(output, format="PDF", save_all=True, append_images=images[1:])
            return await self.create_upload(
                user_id,
                title,
                resource_type,
                school_profile_id,
                f"{title}.pdf",
                "application/pdf",
                output.getvalue(),
            )
        finally:
            for converted_image in images:
                converted_image.close()

    async def parse_run(self, run_id: uuid.UUID) -> ResourceImportRun:
        run = await self._session.get(ResourceImportRun, run_id, with_for_update=True)
        if run is None:
            raise AppError(404, "RESOURCE_IMPORT_NOT_FOUND", "resource import does not exist")
        version = await self._session.get(ResourceVersion, run.resource_version_id)
        if version is None:
            raise RuntimeError("resource import has no version")
        resource = await self._session.get(LearningResource, version.resource_id)
        if resource is None:
            raise RuntimeError("resource version has no resource")
        run.status = ResourceImportStatus.PARSING
        run.started_at = datetime.now(UTC)
        run.progress = 0.1
        await self._session.flush()
        sections = await self._parse_file(Path(version.storage_path), version.media_type)
        run.status = ResourceImportStatus.MAPPING
        run.progress = 0.65
        nodes = list((await self._session.scalars(select(KnowledgeNode))).all())
        for parsed in sections:
            section = ResourceSection(
                resource_version_id=version.id,
                title=parsed.title[:512],
                section_path=parsed.path[:1024],
                level=parsed.level,
                sequence=parsed.sequence,
                page_start=parsed.page_start,
                page_end=parsed.page_end,
                suggested_units=3 if resource.resource_type is ResourceType.COURSE else 10,
                unit_type="节" if resource.resource_type is ResourceType.COURSE else "题",
                version=1,
            )
            self._session.add(section)
            await self._session.flush()
            self._session.add(
                ResourceChunk(
                    resource_version_id=version.id,
                    section_id=section.id,
                    sequence=parsed.sequence,
                    page_start=parsed.page_start,
                    page_end=parsed.page_end,
                    text=parsed.text,
                    extraction_method=parsed.method,
                )
            )
            candidates = self._map_knowledge(parsed, nodes)
            for node, confidence in candidates:
                self._session.add(
                    ResourceKnowledgeMapping(
                        section_id=section.id,
                        knowledge_id=node.id,
                        confidence=confidence,
                        source="RULE_LLM_CANDIDATE",
                        reviewer_confirmed=False,
                    )
                )
        run.status = ResourceImportStatus.REVIEW_REQUIRED
        run.progress = 1
        run.finished_at = datetime.now(UTC)
        run.result = {"sections": len(sections)}
        resource.status = ResourceStatus.REVIEW_REQUIRED
        await self._session.commit()
        return run

    async def get_import(self, run_id: uuid.UUID) -> ResourceImportRun:
        run = await self._session.get(ResourceImportRun, run_id)
        if run is None:
            raise AppError(404, "RESOURCE_IMPORT_NOT_FOUND", "resource import does not exist")
        return run

    async def pending_review(self) -> list[LearningResource]:
        return list(
            (
                await self._session.scalars(
                    select(LearningResource)
                    .where(LearningResource.status == ResourceStatus.REVIEW_REQUIRED)
                    .order_by(LearningResource.created_at)
                )
            ).all()
        )

    async def published_sections(
        self, knowledge_id: uuid.UUID | None = None
    ) -> list[dict[str, object]]:
        query = (
            select(ResourceSection, LearningResource, ResourceKnowledgeMapping)
            .join(
                ResourceKnowledgeMapping, ResourceKnowledgeMapping.section_id == ResourceSection.id
            )
            .join(ResourceVersion, ResourceVersion.id == ResourceSection.resource_version_id)
            .join(LearningResource, LearningResource.id == ResourceVersion.resource_id)
            .where(
                LearningResource.status == ResourceStatus.PUBLISHED,
                ResourceKnowledgeMapping.reviewer_confirmed.is_(True),
            )
            .order_by(LearningResource.title, ResourceSection.sequence)
        )
        if knowledge_id is not None:
            query = query.where(ResourceKnowledgeMapping.knowledge_id == knowledge_id)
        result = await self._session.execute(query)
        return [
            {
                "id": section.id,
                "title": section.title,
                "resource_id": resource.id,
                "resource_title": resource.title,
                "resource_type": resource.resource_type,
                "knowledge_id": mapping.knowledge_id,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "suggested_units": section.suggested_units,
                "unit_type": section.unit_type,
            }
            for section, resource, mapping in result.tuples().all()
        ]

    async def sections_for_review(self, resource_id: uuid.UUID) -> list[dict[str, object]]:
        resource = await self._session.get(LearningResource, resource_id)
        if resource is None or resource.current_version_id is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "resource does not exist")
        result = await self._session.execute(
            select(ResourceSection, ResourceKnowledgeMapping, KnowledgeNode)
            .outerjoin(
                ResourceKnowledgeMapping,
                ResourceKnowledgeMapping.section_id == ResourceSection.id,
            )
            .outerjoin(KnowledgeNode, KnowledgeNode.id == ResourceKnowledgeMapping.knowledge_id)
            .where(ResourceSection.resource_version_id == resource.current_version_id)
            .order_by(ResourceSection.sequence)
        )
        grouped: dict[uuid.UUID, dict[str, object]] = {}
        for section, mapping, node in result.tuples().all():
            item = grouped.setdefault(
                section.id,
                {
                    "id": section.id,
                    "title": section.title,
                    "section_path": section.section_path,
                    "level": section.level,
                    "sequence": section.sequence,
                    "page_start": section.page_start,
                    "page_end": section.page_end,
                    "version": section.version,
                    "mappings": [],
                },
            )
            if mapping and node:
                mappings = item["mappings"]
                if isinstance(mappings, list):
                    mappings.append(
                        {
                            "knowledge_id": node.id,
                            "knowledge_name": node.name,
                            "confidence": mapping.confidence,
                            "confirmed": mapping.reviewer_confirmed,
                        }
                    )
        return list(grouped.values())

    async def update_section(
        self,
        section_id: uuid.UUID,
        expected_version: int,
        title: str,
        page_start: int | None,
        page_end: int | None,
        knowledge_ids: list[uuid.UUID],
    ) -> ResourceSection:
        section = await self._session.get(ResourceSection, section_id, with_for_update=True)
        if section is None:
            raise AppError(404, "RESOURCE_SECTION_NOT_FOUND", "resource section does not exist")
        if section.version != expected_version:
            raise AppError(409, "VERSION_CONFLICT", "resource section changed")
        section.title = title
        section.page_start = page_start
        section.page_end = page_end
        section.version += 1
        existing = list(
            (
                await self._session.scalars(
                    select(ResourceKnowledgeMapping).where(
                        ResourceKnowledgeMapping.section_id == section.id
                    )
                )
            ).all()
        )
        for mapping in existing:
            await self._session.delete(mapping)
        await self._session.flush()
        for knowledge_id in knowledge_ids:
            self._session.add(
                ResourceKnowledgeMapping(
                    section_id=section.id,
                    knowledge_id=knowledge_id,
                    confidence=1.0,
                    source="REVIEWER",
                    reviewer_confirmed=True,
                )
            )
        await self._session.commit()
        return section

    async def publish(
        self, reviewer_user_id: uuid.UUID, resource_id: uuid.UUID, reason: str | None
    ) -> LearningResource:
        resource = await self._session.get(LearningResource, resource_id, with_for_update=True)
        if resource is None:
            raise AppError(404, "RESOURCE_NOT_FOUND", "resource does not exist")
        if resource.current_version_id is None:
            raise AppError(422, "RESOURCE_HAS_NO_VERSION", "resource has no parsed version")
        unconfirmed = await self._session.scalar(
            select(ResourceKnowledgeMapping.id)
            .join(ResourceSection, ResourceSection.id == ResourceKnowledgeMapping.section_id)
            .where(
                ResourceSection.resource_version_id == resource.current_version_id,
                ResourceKnowledgeMapping.reviewer_confirmed.is_(False),
            )
        )
        if unconfirmed is not None:
            raise AppError(
                422, "RESOURCE_MAPPING_UNCONFIRMED", "confirm every section mapping first"
            )
        resource.status = ResourceStatus.PUBLISHED
        resource.published_at = datetime.now(UTC)
        resource.version += 1
        self._session.add(
            ResourceReviewDecision(
                resource_id=resource.id,
                reviewer_user_id=reviewer_user_id,
                decision="PUBLISHED",
                reason=reason,
            )
        )
        await self._session.commit()
        return resource

    async def _parse_file(self, path: Path, media_type: str) -> list[ParsedSection]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return await self._parse_pdf(path)
        if suffix == ".docx":
            return self._parse_docx(path)
        if suffix in {".md", ".markdown"}:
            return self._parse_markdown(path)
        if suffix in {".jpg", ".jpeg", ".png"}:
            text = await self._vision_extract(path, media_type)
            return [ParsedSection(path.stem, path.stem, 1, 1, 1, 1, text, "QWEN_VISION")]
        raise AppError(422, "UNSUPPORTED_RESOURCE_TYPE", "unsupported resource file type")

    async def _parse_pdf(self, path: Path) -> list[ParsedSection]:
        document = fitz.open(str(path))
        sections: list[ParsedSection] = []
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            method = "PYMUPDF"
            if not text:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                image_path = path.with_name(f"{path.stem}-page-{index}.png")
                pix.save(image_path)
                text = await self._vision_extract(image_path, "image/png")
                image_path.unlink(missing_ok=True)
                method = "QWEN_VISION"
            title = next(
                (line.strip() for line in text.splitlines() if line.strip()), f"第 {index} 页"
            )
            sections.append(
                ParsedSection(title[:120], f"page/{index}", 1, index, index, index, text, method)
            )
        document.close()
        return sections

    @staticmethod
    def _parse_docx(path: Path) -> list[ParsedSection]:
        document = Document(str(path))
        sections: list[ParsedSection] = []
        current_title = path.stem
        buffer: list[str] = []
        sequence = 1
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = paragraph.style.name if paragraph.style else ""
            if style_name.startswith("Heading"):
                if buffer:
                    sections.append(
                        ParsedSection(
                            current_title,
                            f"section/{sequence}",
                            1,
                            sequence,
                            None,
                            None,
                            "\n".join(buffer),
                            "DOCX",
                        )
                    )
                    sequence += 1
                current_title = text
                buffer = []
            else:
                buffer.append(text)
        if buffer or not sections:
            sections.append(
                ParsedSection(
                    current_title,
                    f"section/{sequence}",
                    1,
                    sequence,
                    None,
                    None,
                    "\n".join(buffer),
                    "DOCX",
                )
            )
        return sections

    @staticmethod
    def _parse_markdown(path: Path) -> list[ParsedSection]:
        text = path.read_text(encoding="utf-8")
        tokens = MarkdownIt().parse(text)
        sections: list[ParsedSection] = []
        headings: list[tuple[str, int, int]] = []
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and index + 1 < len(tokens):
                level = int(token.tag[1:])
                headings.append(
                    (tokens[index + 1].content, level, token.map[0] if token.map else 0)
                )
        if not headings:
            return [ParsedSection(path.stem, path.stem, 1, 1, None, None, text, "MARKDOWN")]
        lines = text.splitlines()
        for sequence, (title, level, start) in enumerate(headings, start=1):
            end = headings[sequence][2] if sequence < len(headings) else len(lines)
            sections.append(
                ParsedSection(
                    title,
                    f"section/{sequence}",
                    level,
                    sequence,
                    None,
                    None,
                    "\n".join(lines[start:end]),
                    "MARKDOWN",
                )
            )
        return sections

    async def _vision_extract(self, path: Path, media_type: str) -> str:
        if self._settings.use_fake_model:
            with Image.open(path) as image:
                return (
                    f"扫描资源页面，尺寸 {image.width}x{image.height}。等待 Reviewer 修订章节内容。"
                )
        data = base64.b64encode(path.read_bytes()).decode()  # noqa: ASYNC240
        payload = {
            "model": self._settings.qwen_plus_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        },
                        {
                            "type": "text",
                            "text": "提取这页讲义的标题、目录层级和正文，保留公式语义。",
                        },
                    ],
                }
            ],
        }
        async with httpx.AsyncClient(
            base_url=self._settings.qwen_base_url,
            headers={"Authorization": f"Bearer {self._settings.qwen_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(connect=5, read=120, write=30, pool=5),
        ) as client:
            response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])

    @staticmethod
    def _map_knowledge(
        section: ParsedSection, nodes: list[KnowledgeNode]
    ) -> list[tuple[KnowledgeNode, float]]:
        haystack = f"{section.title}\n{section.text}".lower()
        exact = [
            node for node in nodes if node.name.lower() in haystack or node.code.lower() in haystack
        ]
        if exact:
            return [(node, 0.9) for node in exact[:3]]
        return [(nodes[0], 0.25)] if nodes else []
