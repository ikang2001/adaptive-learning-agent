from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.enums import Difficulty, TaskOrigin, TaskType


@dataclass(frozen=True, slots=True)
class KnowledgePriority:
    knowledge_id: str
    school_weight: float
    mastery_gap: float
    error_recency: float
    error_frequency: float
    forgetting_risk: float
    stage_weight: float
    prerequisite_blocked: bool = False

    @property
    def score(self) -> float:
        base = (
            self.school_weight * 0.30
            + self.mastery_gap * 0.30
            + self.error_recency * 0.15
            + self.error_frequency * 0.10
            + self.forgetting_risk * 0.10
            + self.stage_weight * 0.05
        )
        return round(min(1.0, base + (0.15 if self.prerequisite_blocked else 0)), 4)


@dataclass(frozen=True, slots=True)
class CandidateQuestion:
    question_id: str
    knowledge_id: str
    difficulty: Difficulty
    question_type: str
    estimated_p50_minutes: int
    estimated_p75_minutes: int
    school_weight: float
    weakness: float
    difficulty_fit: float
    spacing_score: float
    diversity_score: float

    @property
    def selection_score(self) -> float:
        return round(
            self.weakness * 0.35
            + self.school_weight * 0.25
            + self.difficulty_fit * 0.15
            + self.spacing_score * 0.15
            + self.diversity_score * 0.10,
            4,
        )


@dataclass(frozen=True, slots=True)
class LearningCandidate:
    knowledge_id: str
    task_type: TaskType
    title: str
    description: str
    resource_section_id: str | None
    suggested_scope: str | None
    planned_units: int
    unit_type: str
    estimated_p50_minutes: int
    estimated_p75_minutes: int
    priority: float
    origin: TaskOrigin = TaskOrigin.SYSTEM
    is_personal: bool = False
    task_identity: str | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityDay:
    day: date
    available_minutes: int


@dataclass(frozen=True, slots=True)
class TaskDraft:
    day: date
    task_type: TaskType
    knowledge_id: str
    question_ids: tuple[str, ...]
    estimated_min_minutes: int
    estimated_max_minutes: int
    priority: float
    reason: str
    title: str = "学习任务"
    description: str = ""
    resource_section_id: str | None = None
    suggested_scope: str | None = None
    planned_units: int | None = None
    unit_type: str | None = None
    origin: TaskOrigin = TaskOrigin.SYSTEM
    is_personal: bool = False
    system_suggested_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class PlanDraft:
    tasks: tuple[TaskDraft, ...] = field(default_factory=tuple)

    def total_max_minutes(self, day: date) -> int:
        return sum(task.estimated_max_minutes for task in self.tasks if task.day == day)


class PlanningStrategy:
    version = "planner_v1"

    def build_week(
        self,
        availability: list[AvailabilityDay],
        priorities: list[KnowledgePriority],
        candidates: list[CandidateQuestion],
        excluded_question_ids: set[str] | None = None,
    ) -> PlanDraft:
        excluded = excluded_question_ids or set()
        ranked_priorities = sorted(priorities, key=lambda item: item.score, reverse=True)
        ranked_questions = sorted(candidates, key=lambda item: item.selection_score, reverse=True)
        selected_ids = set(excluded)
        tasks: list[TaskDraft] = []
        for day in availability:
            remaining = day.available_minutes
            for priority in ranked_priorities:
                matching = [
                    item
                    for item in ranked_questions
                    if item.knowledge_id == priority.knowledge_id
                    and item.question_id not in selected_ids
                    and item.estimated_p75_minutes <= remaining
                ]
                if not matching:
                    continue
                selected = self._fit_questions(matching, remaining)
                if not selected:
                    continue
                tasks.append(self._make_task(day.day, priority, selected))
                used = sum(item.estimated_p75_minutes for item in selected)
                remaining -= used
                selected_ids.update(item.question_id for item in selected)
                smallest_question = min(
                    (item.estimated_p75_minutes for item in ranked_questions), default=1
                )
                if remaining < smallest_question:
                    break
        return PlanDraft(tasks=tuple(tasks))

    def build_learning_week(
        self,
        availability: list[AvailabilityDay],
        candidates: list[LearningCandidate],
    ) -> PlanDraft:
        ranked = sorted(candidates, key=lambda item: item.priority, reverse=True)
        selected: set[str] = set()
        tasks: list[TaskDraft] = []
        for day in availability:
            remaining = day.available_minutes
            for candidate in ranked:
                identity = (
                    candidate.task_identity or f"{candidate.knowledge_id}:{candidate.task_type}"
                )
                if identity in selected or candidate.estimated_p75_minutes > remaining:
                    continue
                tasks.append(
                    TaskDraft(
                        day=day.day,
                        task_type=candidate.task_type,
                        knowledge_id=candidate.knowledge_id,
                        question_ids=(),
                        estimated_min_minutes=candidate.estimated_p50_minutes,
                        estimated_max_minutes=candidate.estimated_p75_minutes,
                        priority=candidate.priority,
                        reason=f"knowledge_priority={candidate.priority:.2f}; learning task",
                        title=candidate.title,
                        description=candidate.description,
                        resource_section_id=candidate.resource_section_id,
                        suggested_scope=candidate.suggested_scope,
                        planned_units=candidate.planned_units,
                        unit_type=candidate.unit_type,
                        origin=candidate.origin,
                        is_personal=candidate.is_personal,
                        system_suggested_minutes=candidate.estimated_p50_minutes,
                    )
                )
                selected.add(identity)
                remaining -= candidate.estimated_p75_minutes
                if remaining < 15:
                    break
        return PlanDraft(tasks=tuple(tasks))

    @staticmethod
    def _fit_questions(
        candidates: list[CandidateQuestion], available_minutes: int
    ) -> list[CandidateQuestion]:
        selected: list[CandidateQuestion] = []
        used = 0
        for candidate in candidates:
            if used + candidate.estimated_p75_minutes > available_minutes:
                continue
            selected.append(candidate)
            used += candidate.estimated_p75_minutes
            if len(selected) >= 5:
                break
        return selected

    @staticmethod
    def _make_task(
        day: date,
        priority: KnowledgePriority,
        questions: list[CandidateQuestion],
    ) -> TaskDraft:
        hardest = max(
            questions, key=lambda item: list(Difficulty).index(item.difficulty)
        ).difficulty
        task_type = {
            Difficulty.BASIC: TaskType.BASIC_QUESTION,
            Difficulty.MEDIUM: TaskType.MEDIUM_QUESTION,
            Difficulty.COMPREHENSIVE: TaskType.COMPREHENSIVE_QUESTION,
            Difficulty.TRUE_EXAM: TaskType.TRUE_EXAM_QUESTION,
        }[hardest]
        return TaskDraft(
            day=day,
            task_type=task_type,
            knowledge_id=priority.knowledge_id,
            question_ids=tuple(item.question_id for item in questions),
            estimated_min_minutes=sum(item.estimated_p50_minutes for item in questions),
            estimated_max_minutes=sum(item.estimated_p75_minutes for item in questions),
            priority=priority.score,
            reason=f"priority={priority.score:.2f}; school and mastery weighted",
        )
