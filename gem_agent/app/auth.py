"""Sign-in (spec 4.1): a simple username on the LAN to start, kept in a cookie; the company's auth can replace
`current_user` later. Each user sees only their own runs, downloads and memory."""
from __future__ import annotations

import re

from fastapi import HTTPException, Request, Response

COOKIE = "gem_user"
_NAME = re.compile(r"^[\w.@-]{1,64}$")


def current_user(request: Request) -> str:
    user = request.cookies.get(COOKIE, "")
    if not _NAME.match(user):
        raise HTTPException(status_code=401, detail="sign in first")
    return user


def sign_in(response: Response, name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise HTTPException(status_code=422, detail="use letters, digits, dots, dashes or underscores")
    response.set_cookie(COOKIE, name, max_age=30 * 86400, httponly=True, samesite="lax")
    return name
