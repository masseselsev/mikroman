from typing import Optional

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse

router = APIRouter(prefix="/telegram", tags=["Telegram Bot"])

# Reference to telegram bot service instance (attached on app startup)
telegram_bot_service = None


def set_telegram_service(service):
    global telegram_bot_service
    telegram_bot_service = service


class TelegramTestRequest(BaseModel):
    bot_token: Optional[str] = None
    admin_ids: Optional[str] = None


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive and process Telegram updates via Webhook."""
    if not telegram_bot_service:
        raise HTTPException(status_code=503, detail="Telegram bot service not initialized")

    update_data = await request.json()
    await telegram_bot_service.process_webhook_update(update_data)
    return {"ok": True}


@router.post("/test", response_model=APIResponse[bool])
async def test_telegram_alert(
    payload: Optional[TelegramTestRequest] = None,
    db: AsyncSession = Depends(get_db)
):
    """Send a test message to configured Telegram admins."""
    token = payload.bot_token.strip() if payload and payload.bot_token else None
    admin_ids_str = payload.admin_ids.strip() if payload and payload.admin_ids else None

    # Fallback to DB settings if not provided in payload
    if not token:
        setting = await db.get(AppSetting, "telegram_bot_token")
        if setting and setting.value:
            token = setting.value.strip()
    if not admin_ids_str:
        setting = await db.get(AppSetting, "telegram_admin_ids")
        if setting and setting.value:
            admin_ids_str = setting.value.strip()

    # Fallback to active service
    if not token and telegram_bot_service and telegram_bot_service.config.TELEGRAM_BOT_TOKEN:
        token = telegram_bot_service.config.TELEGRAM_BOT_TOKEN
    if not admin_ids_str and telegram_bot_service and telegram_bot_service.config.TELEGRAM_ADMIN_CHAT_IDS:
        admin_ids_str = ",".join(str(i) for i in telegram_bot_service.config.TELEGRAM_ADMIN_CHAT_IDS)

    if not token:
        return APIResponse(success=False, message="Telegram bot token not configured", data=False)

    # Parse admin IDs
    admin_ids = []
    if admin_ids_str:
        for p in admin_ids_str.split(","):
            p = p.strip()
            if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                admin_ids.append(int(p))

    try:
        test_bot = Bot(token=token)
        try:
            bot_user = await test_bot.get_me()
            bot_name = bot_user.username or bot_user.first_name
        except Exception as e:
            await test_bot.session.close()
            return APIResponse(success=False, message=f"Invalid Bot Token: {str(e)}", data=False)

        if not admin_ids:
            await test_bot.session.close()
            return APIResponse(
                success=True,
                message=f"Bot @{bot_name} connected! Please enter Admin Chat ID to receive test message.",
                data=True
            )

        sent_count = 0
        errors = []
        for chat_id in admin_ids:
            try:
                await test_bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🧪 <b>MikroMan Test Alert</b>\n\n"
                        f"Your Telegram bot @{bot_name} is successfully connected to MikroMan!\n"
                        f"Type <code>/start</code> or <code>/help</code> to view available commands."
                    ),
                    parse_mode="HTML"
                )
                sent_count += 1
            except Exception as e:
                errors.append(f"{chat_id} ({str(e)})")

        await test_bot.session.close()

        if sent_count > 0:
            msg = f"Test message sent to {sent_count} admin(s) from @{bot_name}!"
            if errors:
                msg += f" (Warnings: {'; '.join(errors)})"
            return APIResponse(data=True, message=msg)
        else:
            return APIResponse(
                success=False,
                message=f"Failed to send to admin chat: {'; '.join(errors)}. Did you press /start in the bot first?",
                data=False
            )

    except Exception as e:
        return APIResponse(success=False, message=f"Telegram error: {str(e)}", data=False)
