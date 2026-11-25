"""API 路由包。

桌面版（SSP_MODE=desktop）不装配管理后台路由：build_api_router(include_admin=False)。

desktop_api 只在桌面模式装配，所以延迟导入：它的文件导入路由需要 python-multipart，
而服务端的 venv 只装服务端运行依赖（没有 multipart），顶层导入会让服务端起不来。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    account,
    admin,
    advice,
    auth,
    records,
    runtime_api,
    settings_api,
    sync_api,
)


def build_api_router(*, include_admin: bool = True, include_desktop: bool = False) -> APIRouter:
    """装配 /api 路由；include_admin=False 时整块不注册（桌面版）。"""
    router = APIRouter(prefix="/api")
    router.include_router(runtime_api.router)
    router.include_router(auth.router)
    router.include_router(records.router)
    router.include_router(advice.router)
    router.include_router(settings_api.router)
    router.include_router(account.router)
    router.include_router(sync_api.router)
    if include_admin:
        router.include_router(admin.router)
    if include_desktop:
        from . import desktop_api

        router.include_router(desktop_api.router)
    return router


# 兼容旧导入（服务端模式默认包含管理后台）。
api_router = build_api_router()

__all__ = ["api_router", "build_api_router"]
