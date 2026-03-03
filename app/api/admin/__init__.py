"""Admin API routers."""

from fastapi import APIRouter

from app.api.admin.users import router as users_router
from app.api.admin.actions import router as actions_router
from app.api.admin.settings import router as settings_router
from app.api.admin.clubs import router as clubs_router
from app.api.admin.billing import router as billing_router
from app.api.admin.notifications import router as notifications_router
from app.api.admin.pro import router as pro_router
from app.api.admin.ml_models import router as ml_models_router
from app.api.admin.ml_predictions import router as ml_predictions_router
from app.api.admin.audit import router as audit_router
from app.api.admin.events import router as events_router

router = APIRouter(tags=["Admin"])

router.include_router(users_router, prefix="/users")
router.include_router(actions_router, prefix="/actions")
router.include_router(settings_router, prefix="/settings")
router.include_router(clubs_router)
router.include_router(billing_router, prefix="/billing")
router.include_router(notifications_router, prefix="/notifications")
router.include_router(pro_router, prefix="/pro")
router.include_router(ml_models_router, prefix="/ml")
router.include_router(ml_predictions_router, prefix="/ml/predictions")
router.include_router(audit_router, prefix="/audit")
router.include_router(events_router, prefix="/events")
