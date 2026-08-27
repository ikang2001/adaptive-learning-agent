from fastapi import APIRouter

from app.api.routes.agents import router as agents_router
from app.api.routes.auth import router as auth_router
from app.api.routes.catalog import router as catalog_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.mock_exams import router as mock_exams_router
from app.api.routes.plans import router as plans_router
from app.api.routes.practice import router as practice_router
from app.api.routes.resources import router as resources_router
from app.api.routes.review import router as review_router
from app.api.routes.school_change import router as school_change_router
from app.api.routes.students import router as students_router
from app.api.routes.true_exams import router as true_exams_router
from app.api.routes.unlocks import router as unlocks_router

api_router = APIRouter()
api_router.include_router(agents_router)
api_router.include_router(auth_router)
api_router.include_router(catalog_router)
api_router.include_router(jobs_router)
api_router.include_router(mock_exams_router)
api_router.include_router(plans_router)
api_router.include_router(practice_router)
api_router.include_router(review_router)
api_router.include_router(resources_router)
api_router.include_router(school_change_router)
api_router.include_router(students_router)
api_router.include_router(true_exams_router)
api_router.include_router(unlocks_router)
