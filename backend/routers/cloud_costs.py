"""Витрати на серверну частину (Статистика → «Сервери й хмара»), див. services/cloud_costs.py.

GET  /api/cloud-costs          — кеш (без мережі): сервіси, напрями, всього, розподіл
POST /api/cloud-costs/refresh  — опитати провайдерів зараз (білінгові API; базу не будить)
PUT  /api/cloud-costs/config   — розподіл за напрямами (%) і ручні пункти ($/міс)
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool

try:
    from services import cloud_costs
except ImportError:
    from backend.services import cloud_costs

router = APIRouter(prefix="/api/cloud-costs")


@router.get("")
def costs_status():
    return cloud_costs.status()


@router.post("/refresh")
async def costs_refresh():
    return await run_in_threadpool(cloud_costs.refresh)


@router.put("/config")
def costs_config(allocation: Optional[Dict[str, Dict[str, float]]] = Body(None),
                 manual: Optional[List[Dict[str, Any]]] = Body(None)):
    try:
        cloud_costs.save_config(allocation=allocation, manual=manual)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return cloud_costs.status()
