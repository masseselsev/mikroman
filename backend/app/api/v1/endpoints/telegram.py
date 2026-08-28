from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.common import APIResponse

router = APIRouter(prefix="/telegram", tags=["Telegram Bot"])

# Reference to telegram bot service instance (attached on app startup)
telegram_bot_service = None


def set_telegram_service(service):
    global telegram_bot_service
    telegram_bot_service = service


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive and process Telegram updates via Webhook."""
    if not telegram_bot_service:
        raise HTTPException(status_code=503, detail="Telegram bot service not initialized")

    update_data = await request.json()
    await telegram_bot_service.process_webhook_update(update_data)
    return {"ok": True}


@router.post("/test", response_model=APIResponse[bool])
async def test_telegram_alert():
    """Send a test message to configured Telegram admins."""
    if not telegram_bot_service or not telegram_bot_service.bot:
        return APIResponse(success=False, message="Telegram bot token not configured", data=False)

    await telegram_bot_service.send_alert_to_admins("🧪 *MikroMan Test Alert*\nYour Telegram integration is working perfectly!")
    return APIResponse(data=True, message="Test alert sent to Telegram admins")
