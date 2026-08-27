from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.domain.enums import ErrorType, Role, TaskOrigin, TaskStatus, TaskType
from app.infrastructure.db.models import BackgroundJob, PlanTask, QuestionAttempt, UserRole
from app.infrastructure.db.session import engine, session_factory
from app.infrastructure.redis import get_redis
from app.main import app
from app.workers.tasks import execute_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 with PostgreSQL and Redis running",
    ),
]


@pytest.fixture(autouse=True)
async def reset_async_clients() -> None:
    await engine.dispose()
    get_redis.cache_clear()
    redis = get_redis()
    keys = await redis.keys("otp:*")
    if keys:
        await redis.delete(*keys)


def assert_ok(response: httpx.Response, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


async def execute_created_job(
    client: httpx.AsyncClient, headers: dict[str, str], job: dict[str, object]
) -> dict[str, object]:
    await execute_job({}, str(job["id"]))
    response = await client.get(f"/api/v1/jobs/{job['id']}", headers=headers)
    assert_ok(response)
    return response.json()


async def test_learning_plan_feedback_edit_and_unlock_boundaries() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        phone = f"+86139{uuid.uuid4().int % 100_000_000:08d}"
        assert_ok(
            await client.post("/api/v1/auth/sms-codes", json={"phone": phone, "purpose": "LOGIN"}),
            202,
        )
        login = await client.post("/api/v1/auth/sessions", json={"phone": phone, "code": "246810"})
        assert_ok(login)
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        schools = await client.get("/api/v1/schools", headers=headers)
        assert_ok(schools)
        school = schools.json()[0]
        alternate_school = schools.json()[1]
        today = date.today()
        assert_ok(
            await client.put(
                "/api/v1/me/student-profile",
                headers=headers,
                json={
                    "target_school_id": school["id"],
                    "exam_date": (today + timedelta(days=120)).isoformat(),
                },
            )
        )
        assert_ok(
            await client.put(
                "/api/v1/me/availability-template",
                headers=headers,
                json={
                    "days": [{"weekday": weekday, "available_minutes": 120} for weekday in range(7)]
                },
            )
        )

        create = await client.post(
            "/api/v1/me/plans",
            headers={**headers, "Idempotency-Key": f"plan-{uuid.uuid4()}"},
            json={"start_date": today.isoformat()},
        )
        assert_ok(create, 202)
        job = await execute_created_job(client, headers, create.json())
        assert job["status"] == "SUCCEEDED"

        plan_response = await client.get("/api/v1/me/plans/current", headers=headers)
        assert_ok(plan_response)
        plan = plan_response.json()
        assert plan["tasks"]
        assert {task["task_type"] for task in plan["tasks"]} <= {
            "COURSE_LEARNING",
            "HANDOUT_PRACTICE",
            "KNOWLEDGE_SUMMARY",
        }
        assert all(task["resource_section_id"] for task in plan["tasks"])

        async with session_factory() as session:
            session.add(
                PlanTask(
                    plan_id=uuid.UUID(plan["id"]),
                    task_date=today,
                    task_type=TaskType.COMPREHENSIVE_QUESTION,
                    title="旧版综合训练",
                    description="migration fixture",
                    target_count=5,
                    estimated_min_minutes=30,
                    estimated_max_minutes=30,
                    system_suggested_minutes=30,
                    effective_minutes=30,
                    priority=0.5,
                    status=TaskStatus.PENDING,
                    origin=TaskOrigin.LEGACY,
                    reason="legacy fixture",
                    sequence=999,
                )
            )
            await session.commit()
        current_without_legacy = await client.get("/api/v1/me/plans/current", headers=headers)
        assert_ok(current_without_legacy)
        assert all(task["origin"] != "LEGACY" for task in current_without_legacy.json()["tasks"])
        dated_without_legacy = await client.get(
            "/api/v1/me/plans/current",
            headers=headers,
            params={"from_date": today.isoformat(), "to_date": today.isoformat()},
        )
        assert_ok(dated_without_legacy)
        assert all(task["origin"] != "LEGACY" for task in dated_without_legacy.json()["tasks"])

        duplicate = await client.post(
            "/api/v1/me/plans",
            headers={**headers, "Idempotency-Key": f"plan-{uuid.uuid4()}"},
            json={"start_date": today.isoformat()},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["title"] == "ACTIVE_PLAN_EXISTS"

        today_tasks = await client.get(
            "/api/v1/me/tasks/today",
            headers=headers,
            params={"target_date": today.isoformat()},
        )
        assert_ok(today_tasks)
        task = today_tasks.json()[0]
        feedback = await client.put(
            f"/api/v1/tasks/{task['id']}/feedback",
            headers={**headers, "Idempotency-Key": f"feedback-{uuid.uuid4()}"},
            json={
                "completion_ratio": 1,
                "actual_duration_seconds": 1800,
                "perceived_difficulty": 3,
                "progress_marker": "完成本节",
                "mastery_self_score": 4,
                "completed_units": task["planned_units"] or 1,
            },
        )
        assert_ok(feedback)
        assert feedback.json()["feedback_version"] == 1

        patch = await client.patch(
            f"/api/v1/plans/{plan['id']}/tasks",
            headers={**headers, "Idempotency-Key": f"edit-{uuid.uuid4()}"},
            json={
                "expected_plan_version": plan["version"],
                "allow_over_budget": False,
                "changes": [
                    {
                        "operation": "UPDATE",
                        "task_id": plan["tasks"][1]["id"],
                        "expected_version": plan["tasks"][1]["version"],
                        "task_date": plan["tasks"][1]["task_date"],
                        "task_type": plan["tasks"][1]["task_type"],
                        "title": "学生调整后的学习任务",
                        "knowledge_id": plan["tasks"][1]["knowledge_id"],
                        "resource_section_id": plan["tasks"][1]["resource_section_id"],
                        "student_estimated_minutes": 40,
                        "sequence": plan["tasks"][1]["sequence"],
                    }
                ],
            },
        )
        assert_ok(patch)
        assert patch.json()["version"] == plan["version"] + 1
        changes = await client.get(f"/api/v1/plans/{plan['id']}/changes", headers=headers)
        assert_ok(changes)
        assert len(changes.json()) == 1

        latest_plan = patch.json()
        target_knowledge_id = task["knowledge_id"]
        related_tasks = [
            item for item in latest_plan["tasks"] if item["knowledge_id"] == target_knowledge_id
        ]
        for related in related_tasks:
            if related["id"] == task["id"]:
                continue
            related_feedback = await client.put(
                f"/api/v1/tasks/{related['id']}/feedback",
                headers={
                    **headers,
                    "Idempotency-Key": f"feedback-{uuid.uuid4()}",
                },
                json={
                    "completion_ratio": 1,
                    "actual_duration_seconds": 1800,
                    "perceived_difficulty": 3,
                    "progress_marker": "完成",
                    "mastery_self_score": 4,
                    "completed_units": related["planned_units"] or 1,
                    "correct_units": (
                        related["planned_units"] or 1
                        if related["task_type"] == "HANDOUT_PRACTICE"
                        else None
                    ),
                    "summary_text": (
                        "integration summary"
                        if related["task_type"] == "KNOWLEDGE_SUMMARY"
                        else None
                    ),
                },
            )
            assert_ok(related_feedback)

        before_unlock = await client.get("/api/v1/me/learning-unlocks", headers=headers)
        target_before = next(
            item for item in before_unlock.json() if item["knowledge_id"] == target_knowledge_id
        )
        strengthened = await client.post(
            f"/api/v1/knowledge/{target_knowledge_id}/strengthening/confirm",
            headers={
                **headers,
                "Idempotency-Key": f"strengthen-{uuid.uuid4()}",
            },
            json={"expected_version": target_before["version"]},
        )
        assert_ok(strengthened)
        chapter = await client.post(
            "/api/v1/true-exam/chapter-sessions",
            headers={
                **headers,
                "Idempotency-Key": f"chapter-session-{uuid.uuid4()}",
            },
            params={"knowledge_id": target_knowledge_id},
        )
        assert_ok(chapter)
        chapter_detail = await client.get(
            f"/api/v1/true-exam/chapter-sessions/{chapter.json()['id']}",
            headers=headers,
        )
        assert_ok(chapter_detail)
        chapter_questions = chapter_detail.json()["questions"]
        assert chapter_questions
        chapter_submit = await client.post(
            f"/api/v1/true-exam/chapter-sessions/{chapter.json()['id']}/submit",
            headers={
                **headers,
                "Idempotency-Key": f"chapter-{uuid.uuid4()}",
            },
            json={
                "results": [
                    {
                        "question_id": question["id"],
                        "score_ratio": 0 if index == 0 else 0.8,
                        "duration_seconds": 900,
                        "looked_at_solution": False,
                    }
                    for index, question in enumerate(chapter_questions)
                ]
            },
        )
        assert_ok(chapter_submit)

        true_profile = await client.get("/api/v1/me/true-exam-profile", headers=headers)
        assert_ok(true_profile)
        target_profile = next(
            item for item in true_profile.json() if item["knowledge_id"] == target_knowledge_id
        )
        assert target_profile["attempt_count"] == len(chapter_questions)
        assert target_profile["accuracy"] < 1
        async with session_factory() as session:
            recorded_error = await session.scalar(
                select(QuestionAttempt).where(
                    QuestionAttempt.chapter_true_exam_session_id == uuid.UUID(chapter.json()["id"]),
                    QuestionAttempt.agent_error_type == ErrorType.UNKNOWN,
                )
            )
            assert recorded_error is not None

        specialized_scopes = await client.get("/api/v1/me/specialized-scopes", headers=headers)
        assert_ok(specialized_scopes)
        knowledge_tree = await client.get(
            f"/api/v1/schools/{school['id']}/knowledge-tree", headers=headers
        )
        assert_ok(knowledge_tree)
        root_ids = {item["id"] for item in knowledge_tree.json() if item["parent_id"] is None}
        assert {item["chapter_id"] for item in specialized_scopes.json()} == root_ids
        target_scope = next(
            scope
            for scope in specialized_scopes.json()
            if any(point["knowledge_id"] == target_knowledge_id for point in scope["weak_points"])
        )
        target_weak_point = next(
            point
            for point in target_scope["weak_points"]
            if point["knowledge_id"] == target_knowledge_id
        )
        assert target_weak_point["attempts"] == len(chapter_questions)
        assert target_weak_point["accuracy"] < 1

        unlocks = await client.get("/api/v1/me/learning-unlocks", headers=headers)
        assert_ok(unlocks)
        assert len(unlocks.json()) == 18
        assert any(item["learning_task_total"] > 0 for item in unlocks.json())
        target_unlock = next(
            item for item in unlocks.json() if item["knowledge_id"] == target_knowledge_id
        )
        assert target_unlock["specialized_unlocked"] is True

        school_preview = await client.post(
            "/api/v1/me/target-school/change/preview",
            headers=headers,
            json={"target_school_id": alternate_school["id"]},
        )
        assert_ok(school_preview)
        assert school_preview.json()["preview"]["shared_knowledge_count"] > 0
        school_apply = await client.post(
            "/api/v1/me/target-school/change/apply",
            headers={
                **headers,
                "Idempotency-Key": f"school-change-{uuid.uuid4()}",
            },
            json={"preview_id": school_preview.json()["id"]},
        )
        assert_ok(school_apply)
        changed_profile = await client.get("/api/v1/me/student-profile", headers=headers)
        assert_ok(changed_profile)
        assert changed_profile.json()["target_school_id"] == alternate_school["id"]
        progress_after_change = await client.get("/api/v1/me/learning-unlocks", headers=headers)
        assert_ok(progress_after_change)
        preserved = next(
            item
            for item in progress_after_change.json()
            if item["knowledge_id"] == target_knowledge_id
        )
        assert preserved["status"] == "STRENGTHENED"

        mock = await client.post(
            "/api/v1/me/mock-exams",
            headers={**headers, "Idempotency-Key": f"mock-{uuid.uuid4()}"},
            json={"mock_type": "FULL"},
        )
        assert mock.status_code == 409
        assert mock.json()["title"] == "FULL_MOCK_LOCKED"


async def test_resource_upload_parse_review_and_publish() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        phone = f"+86138{uuid.uuid4().int % 100_000_000:08d}"
        assert_ok(
            await client.post("/api/v1/auth/sms-codes", json={"phone": phone, "purpose": "LOGIN"}),
            202,
        )
        login = await client.post("/api/v1/auth/sessions", json={"phone": phone, "code": "246810"})
        assert_ok(login)
        tokens = login.json()
        claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
        user_id = uuid.UUID(claims["sub"])
        async with session_factory() as session:
            session.add(UserRole(user_id=user_id, role=Role.REVIEWER))
            await session.commit()
        refreshed = await client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert_ok(refreshed)
        headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}

        upload = await client.post(
            "/api/v1/resources/uploads",
            headers=headers,
            data={"title": f"integration-{uuid.uuid4()}", "resource_type": "HANDOUT"},
            files=[
                (
                    "files",
                    (
                        "handout.md",
                        f"# Stability\nRouth criterion outline {uuid.uuid4()}".encode(),
                        "text/markdown",
                    ),
                )
            ],
        )
        assert_ok(upload, 202)
        run = upload.json()
        async with session_factory() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.user_id == user_id,
                    BackgroundJob.job_type == "PARSE_RESOURCE",
                )
                .order_by(BackgroundJob.created_at.desc())
            )
            assert job is not None
            job_id = str(job.id)
        await execute_job({}, job_id)
        status = await client.get(f"/api/v1/resource-imports/{run['id']}", headers=headers)
        assert_ok(status)
        assert status.json()["status"] == "REVIEW_REQUIRED"

        resources = await client.get("/api/v1/review/resources", headers=headers)
        assert_ok(resources)
        resource = resources.json()[-1]
        sections = await client.get(
            f"/api/v1/review/resources/{resource['id']}/sections", headers=headers
        )
        assert_ok(sections)
        assert sections.json()
        for section in sections.json():
            mapping_ids = [mapping["knowledge_id"] for mapping in section["mappings"]]
            assert mapping_ids
            confirmed = await client.patch(
                f"/api/v1/review/resource-sections/{section['id']}",
                headers={**headers, "Idempotency-Key": f"section-{uuid.uuid4()}"},
                json={
                    "expected_version": section["version"],
                    "title": section["title"],
                    "page_start": section["page_start"],
                    "page_end": section["page_end"],
                    "knowledge_ids": mapping_ids,
                },
            )
            assert_ok(confirmed)
        published = await client.post(
            f"/api/v1/review/resources/{resource['id']}/publish",
            headers={**headers, "Idempotency-Key": f"publish-{uuid.uuid4()}"},
            json={"reason": "integration review"},
        )
        assert_ok(published)
        assert published.json()["status"] == "PUBLISHED"
