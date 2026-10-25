from fastapi import HTTPException, Request

from tglib.app.container import Container
from tglib.app.security import COOKIE_NAME


def get_ctx(request: Request) -> Container:
    return request.app.state.ctx


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def require_auth(request: Request) -> None:
    ctx: Container = request.app.state.ctx
    if not ctx.auth.verify(request.cookies.get(COOKIE_NAME)):
        raise api_error(401, "unauthenticated", "يجب تسجيل الدخول أولاً.")
