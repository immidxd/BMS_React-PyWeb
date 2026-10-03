"""Стан безкоштовного ліміту Neon для банера BMS (див. services/cloud_budget.py).

GET  /api/cloud-budget/status  — кешований стан (без мережі; банер може питати часто)
POST /api/cloud-budget/refresh — оновити з Neon API зараз (control plane, базу не будить)
"""
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

try:
    from services import cloud_budget
except ImportError:
    from backend.services import cloud_budget

router = APIRouter(prefix="/api/cloud-budget")


@router.get("/status")
def budget_status():
    return cloud_budget.status(auto_refresh=False)


@router.post("/refresh")
async def budget_refresh():
    return await run_in_threadpool(cloud_budget.refresh)
