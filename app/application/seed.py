from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    Difficulty,
    QuestionQuality,
    ResourceStatus,
    ResourceType,
    SourceType,
)
from app.infrastructure.db.models import (
    ExamProfile,
    KnowledgeNode,
    KnowledgePrerequisite,
    LearningResource,
    Question,
    QuestionKnowledge,
    ResourceKnowledgeMapping,
    ResourceSection,
    ResourceVersion,
    SchoolKnowledgeStat,
    SchoolProfile,
    TrueExam,
    TrueExamQuestion,
)


@dataclass(frozen=True, slots=True)
class KnowledgeSeed:
    code: str
    name: str
    parent: str | None
    prerequisite: str | None


KNOWLEDGE_SEEDS = (
    KnowledgeSeed("SYS", "控制系统基础", None, None),
    KnowledgeSeed("MODEL", "数学模型", "SYS", None),
    KnowledgeSeed("TRANSFER", "传递函数", "MODEL", "MODEL"),
    KnowledgeSeed("BLOCK", "结构图化简", "MODEL", "TRANSFER"),
    KnowledgeSeed("TIME", "时域分析", None, "TRANSFER"),
    KnowledgeSeed("STABILITY", "稳定性", "TIME", "TRANSFER"),
    KnowledgeSeed("ROUTH", "劳斯判据", "STABILITY", "STABILITY"),
    KnowledgeSeed("STEADY", "稳态误差", "TIME", "TRANSFER"),
    KnowledgeSeed("ROOT", "根轨迹", None, "TRANSFER"),
    KnowledgeSeed("ROOT_RULE", "根轨迹绘制规则", "ROOT", "TRANSFER"),
    KnowledgeSeed("BREAK", "分离点与会合点", "ROOT", "ROOT_RULE"),
    KnowledgeSeed("ANGLE", "出射角与入射角", "ROOT", "ROOT_RULE"),
    KnowledgeSeed("FREQ", "频域分析", None, "TRANSFER"),
    KnowledgeSeed("BODE", "Bode 图", "FREQ", "TRANSFER"),
    KnowledgeSeed("NYQUIST", "Nyquist 判据", "FREQ", "STABILITY"),
    KnowledgeSeed("COMP", "系统校正", None, "BODE"),
    KnowledgeSeed("LEAD", "超前滞后校正", "COMP", "BODE"),
    KnowledgeSeed("STATE", "状态空间", None, "MODEL"),
)


class DemoSeeder:
    version = "demo_v1"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self) -> dict[str, int]:
        school = await self._ensure_school()
        alternate_school = await self._ensure_alternate_school()
        nodes = await self._ensure_knowledge_nodes()
        await self._ensure_prerequisites(nodes)
        practice_questions = await self._ensure_practice_questions(school, nodes)
        true_questions = await self._ensure_true_exam_questions(school, nodes)
        await self._ensure_school_stats(school, nodes)
        alternate_nodes = dict(list(nodes.items())[:14])
        await self._ensure_school_stats(alternate_school, alternate_nodes)
        await self._ensure_exam_profile(school, nodes)
        await self._ensure_exam_profile(alternate_school, alternate_nodes)
        await self._ensure_demo_resources(school, nodes)
        await self._ensure_demo_resources(alternate_school, alternate_nodes)
        true_exams = await self._ensure_true_exams(school, true_questions)
        await self._session.commit()
        return {
            "knowledge_nodes": len(nodes),
            "practice_questions": len(practice_questions),
            "true_exam_questions": len(true_questions),
            "true_exams": len(true_exams),
            "school_profiles": 2,
        }

    async def _ensure_school(self) -> SchoolProfile:
        school = await self._session.scalar(
            select(SchoolProfile).where(SchoolProfile.code == "DEMO-801")
        )
        if school is None:
            school = SchoolProfile(
                code="DEMO-801",
                school_name="示例理工大学",
                major="控制工程",
                subject_code="801",
                subject_name="自动控制原理",
                syllabus_version="demo-2026",
                source_version=self.version,
                status="ACTIVE",
            )
            self._session.add(school)
            await self._session.flush()
        return school

    async def _ensure_alternate_school(self) -> SchoolProfile:
        school = await self._session.scalar(
            select(SchoolProfile).where(SchoolProfile.code == "DEMO-802")
        )
        if school is None:
            school = SchoolProfile(
                code="DEMO-802",
                school_name="示例科技大学",
                major="自动化",
                subject_code="802",
                subject_name="控制原理基础",
                syllabus_version="demo-alt-2026",
                source_version=self.version,
                status="ACTIVE",
            )
            self._session.add(school)
            await self._session.flush()
        return school

    async def _ensure_knowledge_nodes(self) -> dict[str, KnowledgeNode]:
        existing = {
            node.code: node
            for node in (
                await self._session.scalars(
                    select(KnowledgeNode).where(
                        KnowledgeNode.code.in_([item.code for item in KNOWLEDGE_SEEDS])
                    )
                )
            ).all()
        }
        for seed in KNOWLEDGE_SEEDS:
            if seed.code in existing:
                continue
            node = KnowledgeNode(
                code=seed.code,
                parent_id=None,
                level=1 if seed.parent is None else 2,
                name=seed.name,
                description=f"{seed.name}的原创演示知识节点",
                tree_version=self.version,
            )
            self._session.add(node)
            await self._session.flush()
            existing[seed.code] = node
        for seed in KNOWLEDGE_SEEDS:
            existing[seed.code].parent_id = existing[seed.parent].id if seed.parent else None
        return {seed.code: existing[seed.code] for seed in KNOWLEDGE_SEEDS}

    async def _ensure_prerequisites(self, nodes: dict[str, KnowledgeNode]) -> None:
        for seed in KNOWLEDGE_SEEDS:
            if not seed.prerequisite or seed.prerequisite == seed.code:
                continue
            key = {
                "knowledge_id": nodes[seed.code].id,
                "prerequisite_id": nodes[seed.prerequisite].id,
            }
            if await self._session.get(KnowledgePrerequisite, tuple(key.values())) is None:
                self._session.add(KnowledgePrerequisite(**key))

    async def _ensure_practice_questions(
        self, school: SchoolProfile, nodes: dict[str, KnowledgeNode]
    ) -> list[Question]:
        questions: list[Question] = []
        difficulties = [
            Difficulty.BASIC,
            Difficulty.MEDIUM,
            Difficulty.MEDIUM,
            Difficulty.COMPREHENSIVE,
        ]
        for node_index, seed in enumerate(KNOWLEDGE_SEEDS, start=1):
            for variant, difficulty in enumerate(difficulties, start=1):
                code = f"DEMO-P-{seed.code}-{variant}"
                question = await self._session.scalar(select(Question).where(Question.code == code))
                if question is None:
                    parameter = node_index + variant
                    question = Question(
                        code=code,
                        source_type=SourceType.DEMO,
                        school_profile_id=school.id,
                        year=None,
                        question_type="CONCEPT" if variant == 1 else "CALCULATION",
                        difficulty=difficulty,
                        score=10 if difficulty is not Difficulty.COMPREHENSIVE else 15,
                        estimated_duration_minutes={
                            Difficulty.BASIC: 8,
                            Difficulty.MEDIUM: 15,
                            Difficulty.COMPREHENSIVE: 25,
                        }[difficulty],
                        content=f"原创演示题：围绕{seed.name}完成第 {parameter} 号参数化分析。",
                        solution=f"依据{seed.name}定义建立步骤，代入参数 {parameter} 并校验结论。",
                        answer=f"演示答案 {seed.code}-{parameter}",
                        provenance={"dataset": self.version, "template": "control_demo"},
                        quality_status=QuestionQuality.VALID,
                        content_version=self.version,
                    )
                    self._session.add(question)
                    await self._session.flush()
                    self._session.add(
                        QuestionKnowledge(
                            question_id=question.id,
                            knowledge_id=nodes[seed.code].id,
                            is_primary=True,
                        )
                    )
                questions.append(question)
        return questions

    async def _ensure_true_exam_questions(
        self, school: SchoolProfile, nodes: dict[str, KnowledgeNode]
    ) -> list[Question]:
        questions: list[Question] = []
        seeds = list(KNOWLEDGE_SEEDS)
        difficulties = [Difficulty.BASIC, Difficulty.MEDIUM, Difficulty.COMPREHENSIVE]
        for index in range(30):
            year = 2023 + index // 10
            number = index % 10 + 1
            seed = seeds[index % len(seeds)]
            code = f"DEMO-T-{year}-{number}"
            question = await self._session.scalar(select(Question).where(Question.code == code))
            if question is None:
                difficulty = difficulties[number % 3]
                question = Question(
                    code=code,
                    source_type=SourceType.TRUE_EXAM,
                    school_profile_id=school.id,
                    year=year,
                    question_type="TRUE_EXAM_CALCULATION",
                    difficulty=difficulty,
                    score=15,
                    estimated_duration_minutes=18,
                    content=f"{year} 虚拟真题第 {number} 题：分析{seed.name}参数变化。",
                    solution=f"按{seed.name}标准步骤计算并验证稳定性或性能指标。",
                    answer=f"虚拟真题答案 {year}-{number}",
                    provenance={"dataset": self.version, "synthetic": True},
                    quality_status=QuestionQuality.VALID,
                    content_version=self.version,
                )
                self._session.add(question)
                await self._session.flush()
                self._session.add(
                    QuestionKnowledge(
                        question_id=question.id,
                        knowledge_id=nodes[seed.code].id,
                        is_primary=True,
                    )
                )
            questions.append(question)
        return questions

    async def _ensure_school_stats(
        self, school: SchoolProfile, nodes: dict[str, KnowledgeNode]
    ) -> None:
        for index, node in enumerate(nodes.values(), start=1):
            existing = await self._session.get(SchoolKnowledgeStat, (school.id, node.id))
            if existing is None:
                self._session.add(
                    SchoolKnowledgeStat(
                        school_profile_id=school.id,
                        knowledge_id=node.id,
                        count=10 + index,
                        total_questions=300,
                        normalized_weight=round((10 + index) / 300, 4),
                        syllabus_order=index,
                        years_covered=3,
                        last_seen_year=2025,
                        trend="STABLE",
                        source_refs=[self.version],
                    )
                )
            else:
                existing.syllabus_order = index

    async def _ensure_exam_profile(
        self, school: SchoolProfile, nodes: dict[str, KnowledgeNode]
    ) -> None:
        existing = await self._session.scalar(
            select(ExamProfile).where(
                ExamProfile.school_profile_id == school.id,
                ExamProfile.profile_version == self.version,
            )
        )
        if existing is not None:
            return
        focus_codes = list(nodes)[:10]
        self._session.add(
            ExamProfile(
                school_profile_id=school.id,
                total_score=150,
                duration_minutes=180,
                question_count=10,
                structure={"CALCULATION": 10},
                difficulty_distribution={"BASIC": 0.3, "MEDIUM": 0.4, "COMPREHENSIVE": 0.3},
                knowledge_distribution={code: 0.1 for code in focus_codes},
                profile_version=self.version,
            )
        )

    async def _ensure_true_exams(
        self, school: SchoolProfile, questions: list[Question]
    ) -> list[TrueExam]:
        exams: list[TrueExam] = []
        for offset, year in enumerate((2023, 2024, 2025)):
            exam = await self._session.scalar(
                select(TrueExam).where(
                    TrueExam.school_profile_id == school.id, TrueExam.year == year
                )
            )
            if exam is None:
                exam = TrueExam(
                    school_profile_id=school.id,
                    year=year,
                    title=f"DEMO-801 {year} 虚拟真题",
                    total_score=150,
                    duration_minutes=180,
                    source_version=self.version,
                )
                self._session.add(exam)
                await self._session.flush()
                for sequence, question in enumerate(
                    questions[offset * 10 : (offset + 1) * 10], start=1
                ):
                    self._session.add(
                        TrueExamQuestion(
                            true_exam_id=exam.id,
                            question_id=question.id,
                            sequence=sequence,
                        )
                    )
            exams.append(exam)
        return exams

    async def _ensure_demo_resources(
        self, school: SchoolProfile, nodes: dict[str, KnowledgeNode]
    ) -> None:
        for resource_type, title, prefix, unit_type, units in (
            (ResourceType.COURSE, "自动控制原理 · 示例课程", "course", "节", 3),
            (ResourceType.HANDOUT, "自动控制原理 · 示例辅导班讲义", "handout", "题", 10),
        ):
            resource = await self._session.scalar(
                select(LearningResource).where(
                    LearningResource.title == title,
                    LearningResource.school_profile_id == school.id,
                )
            )
            if resource is None:
                resource = LearningResource(
                    title=title,
                    resource_type=resource_type,
                    status=ResourceStatus.PUBLISHED,
                    school_profile_id=school.id,
                    description="用于体验知识学习计划的原创虚拟资源目录。",
                    version=1,
                    published_at=datetime.now(UTC),
                )
                self._session.add(resource)
                await self._session.flush()
            version = await self._session.scalar(
                select(ResourceVersion).where(
                    ResourceVersion.resource_id == resource.id,
                    ResourceVersion.version_number == 1,
                )
            )
            if version is None:
                version = ResourceVersion(
                    resource_id=resource.id,
                    version_number=1,
                    original_filename=f"{prefix}-demo.md",
                    media_type="text/markdown",
                    content_hash=f"{school.code}-{prefix}-{self.version}",
                    storage_path=f"demo://{school.code}-{prefix}-{self.version}",
                    size_bytes=0,
                    parser_version="demo_seed_v1",
                )
                self._session.add(version)
                await self._session.flush()
            resource.current_version_id = version.id
            for sequence, (code, node) in enumerate(nodes.items(), start=1):
                section_path = f"{prefix}/{code}"
                section = await self._session.scalar(
                    select(ResourceSection).where(
                        ResourceSection.resource_version_id == version.id,
                        ResourceSection.section_path == section_path,
                    )
                )
                if section is None:
                    section = ResourceSection(
                        resource_version_id=version.id,
                        title=node.name,
                        section_path=section_path,
                        level=1,
                        sequence=sequence,
                        page_start=sequence,
                        page_end=sequence + 2,
                        suggested_units=units,
                        unit_type=unit_type,
                        version=1,
                    )
                    self._session.add(section)
                    await self._session.flush()
                mapping = await self._session.scalar(
                    select(ResourceKnowledgeMapping).where(
                        ResourceKnowledgeMapping.section_id == section.id,
                        ResourceKnowledgeMapping.knowledge_id == node.id,
                    )
                )
                if mapping is None:
                    self._session.add(
                        ResourceKnowledgeMapping(
                            section_id=section.id,
                            knowledge_id=node.id,
                            confidence=1.0,
                            source="SEED",
                            reviewer_confirmed=True,
                        )
                    )
