import asyncio
import hmac

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from tglib.api.deps import api_error, get_ctx
from tglib.app.container import Container
from tglib.app.security import COOKIE_NAME, MAX_AGE

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


@router.get("/status")
async def status(request: Request, ctx: Container = Depends(get_ctx)):
    return {
        "authenticated": ctx.auth.verify(request.cookies.get(COOKIE_NAME)),
        "password_configured": ctx.auth.configured,
    }


@router.post("/login")
async def login(body: LoginIn, response: Response, ctx: Container = Depends(get_ctx)):
    if not ctx.auth.configured:
        raise api_error(
            503, "no_password", "لم يتم ضبط كلمة مرور الواجهة (APP_PASSWORD) في ملف ‎.env"
        )
    if not ctx.auth.check_password(body.password):
        await asyncio.sleep(1)  # slows down guessing
        raise api_error(401, "wrong_password", "كلمة المرور غير صحيحة.")
    response.set_cookie(
        COOKIE_NAME,
        ctx.auth.issue(),
        max_age=MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=ctx.settings.cookie_secure,
    )
    return {"ok": True}


@router.get("/desktop", include_in_schema=False)
async def desktop_login(token: str = "", ctx: Container = Depends(get_ctx)):
    """Sign-in link used by the Windows desktop launcher (see desktop/launcher.py)."""
    expected = ctx.settings.desktop_token
    if not expected or not ctx.auth.configured or not hmac.compare_digest(
        token.encode(), expected.encode()
    ):
        await asyncio.sleep(1)
        return RedirectResponse("/", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        ctx.auth.issue(),
        max_age=MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=ctx.settings.cookie_secure,
    )
    return response


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
