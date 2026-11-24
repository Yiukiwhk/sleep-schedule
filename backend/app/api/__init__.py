"""API 路由包。

桌面版（SSP_MODE=desktop）不装配管理后台路由：build_api_router(include_admin=False)。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import account, admin, advice, auth, records, settings_api


def build_api_router(*, include_admin: bool = True) -> APIRouter:
    """装配 /api 路由；include_admin=False 时整块不注册（桌面版）。"""
    router = APIRouter(prefix="/api")
    router.include_router(auth.router)
    router.include_router(records.router)
    router.include_router(advice.router)
    router.include_router(settings_api.router)
    router.include_router(account.router)
    if include_admin:
        router.include_router(admin.router)
    return router


# 兼容旧导入（服务端模式默认包含管理后台）。
api_router = build_api_router()

__all__ = ["api_router", "build_api_router"]
