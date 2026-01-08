"""API v1 routers."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.oauth import router as oauth_router
from app.api.v1.users import router as users_router
from app.api.v1.events import router as events_router
from app.api.v1.enrollments import router as enrollments_router
from app.api.v1.catches import router as catches_router
from app.api.v1.leaderboard import router as leaderboard_router
from app.api.v1.clubs import router as clubs_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.locations import router as locations_router
from app.api.v1.fish import router as fish_router
from app.api.v1.sponsors import router as sponsors_router
from app.api.v1.live import router as live_router
from app.api.v1.reports import router as reports_router
from app.api.v1.teams import router as teams_router
from app.api.v1.uploads import router as uploads_router
from app.api.v1.rules import router as rules_router
from app.api.v1.currencies import router as currencies_router
from app.api.v1.billing import router as billing_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.contestations import router as contestations_router
from app.api.v1.achievements import router as achievements_router
from app.api.v1.settings import router as settings_router
from app.api.v1.subscriptions import router as subscriptions_router
from app.api.v1.pro import router as pro_router
from app.api.v1.organizer_messages import router as organizer_messages_router
from app.api.v1.admin_messages import router as admin_messages_router
from app.api.v1.follows import router as follows_router
from app.api.v1.app import router as app_router
from app.api.v1.waypoints import router as waypoints_router
from app.api.v1.forecast import router as forecast_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.admin_settings import router as admin_settings_router
from app.api.v1.admin_organizer_permissions import router as admin_organizer_permissions_router
from app.api.v1.admin_partners import router as admin_partners_router
from app.api.v1.admin_statistics import router as admin_statistics_router
from app.api.v1.trout_area import router as trout_area_router
from app.api.v1.ta_public import router as ta_public_router
from app.api.v1.trout_shore import router as trout_shore_router
from app.api.v1.minigame import router as minigame_router
from app.api.v1.public import router as public_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(oauth_router, prefix="/auth", tags=["OAuth"])
router.include_router(users_router, prefix="/users", tags=["Users"])
router.include_router(events_router, prefix="/events", tags=["Events"])
router.include_router(enrollments_router, prefix="/enrollments", tags=["Enrollments"])
router.include_router(catches_router, prefix="/catches", tags=["Catches"])
router.include_router(leaderboard_router, prefix="/leaderboard", tags=["Leaderboard"])
router.include_router(clubs_router, prefix="/clubs", tags=["Clubs"])
router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
router.include_router(locations_router, prefix="/locations", tags=["Locations"])
router.include_router(fish_router, prefix="/fish", tags=["Fish"])
router.include_router(sponsors_router, prefix="/sponsors", tags=["Sponsors"])
router.include_router(live_router, prefix="/live", tags=["Live Scoring"])
router.include_router(reports_router, prefix="/events", tags=["Reports"])
router.include_router(teams_router, prefix="/events", tags=["Teams"])
router.include_router(uploads_router, prefix="/uploads", tags=["Uploads"])
router.include_router(rules_router, prefix="/rules", tags=["Rules"])
router.include_router(currencies_router, prefix="/currencies", tags=["Currencies"])
router.include_router(billing_router, prefix="/billing", tags=["Billing"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
router.include_router(contestations_router, prefix="/events", tags=["Contestations"])
router.include_router(achievements_router, prefix="/achievements", tags=["Achievements"])
router.include_router(settings_router, prefix="/settings", tags=["Settings"])
router.include_router(subscriptions_router, tags=["Subscriptions"])
router.include_router(pro_router, tags=["Pro Features"])
router.include_router(organizer_messages_router, tags=["Organizer Messages"])
router.include_router(admin_messages_router, tags=["Admin Messages"])
router.include_router(follows_router, prefix="/users", tags=["Follows"])
router.include_router(app_router, prefix="/app", tags=["App"])
router.include_router(waypoints_router, tags=["Waypoints"])
router.include_router(forecast_router, tags=["Forecast"])
router.include_router(analytics_router, tags=["Analytics"])
router.include_router(recommendations_router, tags=["Recommendations"])
router.include_router(admin_settings_router, prefix="/admin/settings", tags=["Admin Settings"])
router.include_router(admin_organizer_permissions_router, prefix="/admin/organizer-permissions", tags=["Admin Organizer Permissions"])
router.include_router(admin_partners_router, prefix="/admin/partners", tags=["Admin Partners"])
router.include_router(admin_statistics_router, prefix="/admin/statistics", tags=["Admin Statistics"])
router.include_router(trout_area_router, prefix="/ta", tags=["Trout Area (TA)"])
router.include_router(ta_public_router, tags=["TA Public"])  # Public endpoints, prefix already in router
router.include_router(trout_shore_router, prefix="/tsf", tags=["Trout Shore Fishing (TSF)"])
router.include_router(minigame_router, prefix="/minigame", tags=["Minigame"])
router.include_router(public_router, tags=["Public"])  # No prefix, already has /public
