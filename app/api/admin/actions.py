"""Admin action logging endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount

router = APIRouter()


@router.get("")
async def list_admin_actions(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List admin action logs. Admin only."""
    # TODO: Implement admin action logging
    return {"message": "Admin actions log - to be implemented"}
