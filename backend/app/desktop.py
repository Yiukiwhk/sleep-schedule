"""桌面（单机）模式的本地档案。

SSP_MODE=desktop 时：
  * 没有登录/注册，所有请求一律视为本地档案用户；
  * 没有管理后台，is_admin 始终为 False；
  * 写入冷却与数据保留期由 config 里的桌面默认值控制。

本模块只负责「找出/创建本地用户」，不复制任何业务逻辑。
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .core.security import hash_password
from .db import write_session
from .models import User


def is_desktop() -> bool:
    """当前进程是否为桌面模式。"""
    return bool(settings.DESKTOP)


def local_username() -> str:
    """本地档案的用户名（默认「本机」）。"""
    return settings.DESKTOP_USERNAME or "本机"


def _query_local(session: Session) -> User | None:
    stmt = select(User).where(User.username == local_username())
    return session.execute(stmt).scalar_one_or_none()


def ensure_local_user() -> User | None:
    """幂等地保证本地档案存在，返回该用户。

    桌面版永远不会走密码登录，password_hash 只写一个随机值占位：
    即使有人绕过路由层直接调用登录逻辑，也不可能猜到明文。
    """
    try:
        with write_session() as session:
            user = _query_local(session)
            if user is None:
                user = User(
                    username=local_username(),
                    password_hash=hash_password(secrets.token_urlsafe(32)),
                    is_admin=False,
                    is_active=True,
                )
                session.add(user)
            else:
                # 旧数据目录里可能残留管理员标记：桌面版没有后台，一律降级。
                user.is_admin = False
                user.is_active = True
            session.flush()
            return user
    except IntegrityError:
        # 并发启动：另一个进程/请求已经建好了，读回来即可。
        with write_session() as session:
            return _query_local(session)


def local_user(db: Session) -> User | None:
    """在请求会话里取本地档案；缺失时补建。"""
    user = _query_local(db)
    if user is not None:
        return user
    ensure_local_user()
    # 结束可能存在的旧读快照，确保能看到刚提交的档案。
    db.rollback()
    return _query_local(db)
