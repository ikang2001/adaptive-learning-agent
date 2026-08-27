from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import StudentStage
from app.errors import AppError
from app.infrastructure.db.models import (
    SchoolProfile,
    Student,
    StudentAvailability,
    StudentAvailabilityTemplate,
)


@dataclass(frozen=True, slots=True)
class StudentProfileData:
    id: uuid.UUID
    target_school_id: uuid.UUID | None
    exam_subject: str
    exam_date: date | None
    current_stage: StudentStage
    version: int


class StudentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_profile(self, user_id: uuid.UUID) -> StudentProfileData:
        student = await self._student_for_user(user_id)
        return self._to_data(student)

    async def upsert_profile(
        self,
        user_id: uuid.UUID,
        target_school_id: uuid.UUID,
        exam_date: date,
        expected_version: int | None,
    ) -> StudentProfileData:
        school = await self._session.get(SchoolProfile, target_school_id)
        if school is None or school.status != "ACTIVE":
            raise AppError(422, "SCHOOL_UNAVAILABLE", "target school is unavailable")
        student = await self._session.scalar(
            select(Student).where(Student.user_id == user_id).with_for_update()
        )
        if student is None:
            student = Student(
                user_id=user_id,
                target_school_id=target_school_id,
                exam_date=exam_date,
                current_stage=StudentStage.FOUNDATION,
            )
            self._session.add(student)
        else:
            if expected_version is None or expected_version != student.version:
                raise AppError(
                    409,
                    "VERSION_CONFLICT",
                    "student profile changed; reload before updating",
                    {"current_version": student.version},
                )
            student.target_school_id = target_school_id
            student.exam_date = exam_date
            student.version += 1
        await self._session.commit()
        return self._to_data(student)

    async def replace_availability(
        self, user_id: uuid.UUID, availability: list[tuple[date, int]]
    ) -> list[StudentAvailability]:
        student = await self._student_for_user(user_id)
        days = [day for day, _ in availability]
        if len(days) != len(set(days)):
            raise AppError(422, "DUPLICATE_AVAILABILITY_DATE", "each date may appear once")
        await self._session.execute(
            delete(StudentAvailability).where(StudentAvailability.student_id == student.id)
        )
        rows = [
            StudentAvailability(
                student_id=student.id,
                available_date=day,
                available_minutes=minutes,
            )
            for day, minutes in availability
        ]
        self._session.add_all(rows)
        await self._session.commit()
        return rows

    async def replace_availability_template(
        self, user_id: uuid.UUID, values: list[tuple[int, int]]
    ) -> list[StudentAvailabilityTemplate]:
        student = await self._student_for_user(user_id)
        if {weekday for weekday, _ in values} != set(range(7)):
            raise AppError(
                422, "INVALID_WEEKDAY_TEMPLATE", "template must include weekdays 0 through 6"
            )
        await self._session.execute(
            delete(StudentAvailabilityTemplate).where(
                StudentAvailabilityTemplate.student_id == student.id
            )
        )
        rows = [
            StudentAvailabilityTemplate(
                student_id=student.id, weekday=weekday, available_minutes=minutes
            )
            for weekday, minutes in values
        ]
        self._session.add_all(rows)
        await self._session.commit()
        return rows

    async def _student_for_user(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student

    @staticmethod
    def _to_data(student: Student) -> StudentProfileData:
        return StudentProfileData(
            id=student.id,
            target_school_id=student.target_school_id,
            exam_subject=student.exam_subject,
            exam_date=student.exam_date,
            current_stage=student.current_stage,
            version=student.version,
        )
