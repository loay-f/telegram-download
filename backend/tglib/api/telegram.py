from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.engine.errors import AuthFlowError, TelegramError

router = APIRouter(prefix="/api/telegram", tags=["telegram"], dependencies=[Depends(require_auth)])


class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    password: str


async def _run(coro):
    try:
        await coro
    except AuthFlowError as e:
        raise api_error(400, e.code, e.message)
    except TelegramError as e:
        raise api_error(502, "telegram", f"خطأ من تيليغرام: {e}")


@router.get("/status")
async def status(ctx: Container = Depends(get_ctx)):
    return ctx.account.status()


@router.post("/send-code")
async def send_code(body: PhoneIn, ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.send_code(body.phone))
    return ctx.account.status()


@router.post("/qr")
async def start_qr(ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.start_qr())
    return ctx.account.status()


@router.post("/resend-code")
async def resend_code(ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.resend_code())
    return ctx.account.status()


@router.post("/code")
async def submit_code(body: CodeIn, ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.submit_code(body.code))
    return ctx.account.status()


@router.post("/password")
async def submit_password(body: PasswordIn, ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.submit_password(body.password))
    return ctx.account.status()


@router.post("/cancel")
async def cancel(ctx: Container = Depends(get_ctx)):
    await ctx.account.cancel_login()
    return ctx.account.status()


@router.post("/logout")
async def logout(ctx: Container = Depends(get_ctx)):
    await _run(ctx.account.log_out())
    return ctx.account.status()
