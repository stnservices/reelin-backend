"""Currency endpoints for public access."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.currency import Currency

router = APIRouter()


@router.get("")
async def list_currencies(
    db: AsyncSession = Depends(get_db),
):
    """List all active currencies for event creation dropdown."""
    query = select(Currency).where(Currency.is_active == True).order_by(Currency.name)
    result = await db.execute(query)
    currencies = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "symbol": c.symbol,
        }
        for c in currencies
    ]
