import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import Settings
from backend.app.core.config import settings as global_settings
from backend.app.core.i18n import format_bytes, format_speed, get_text
from backend.app.db.models import Router as RouterModel
from backend.app.db.models import User
from backend.app.services.router_manager import RouterManager
from backend.app.services.router_manager import router_manager as global_router_manager
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController

logger = logging.getLogger("mikroman.telegram")


class TelegramBotService:
    """Telegram Bot Service supporting Long Polling & Webhooks with bilingual multi-router commands."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        router_manager: Optional[RouterManager] = None,
        router_client: Optional[RouterOSClient] = None,
        traffic_controller: Optional[TrafficController] = None,
        config: Optional[Settings] = None
    ):
        self.session_factory = session_factory
        self.router_manager = router_manager or global_router_manager
        self.router_client = router_client
        self.traffic_controller = traffic_controller
        self.config = config or global_settings
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.polling_task: Optional[asyncio.Task] = None
        self.lang = self.config.TELEGRAM_DEFAULT_LANG

        if self.config.TELEGRAM_BOT_TOKEN:
            self._init_bot()

    def _init_bot(self) -> None:
        self.bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher()
        bot_router = Router()
        self._register_handlers(bot_router)
        self.dp.include_router(bot_router)

    def _is_authorized(self, user_id: int) -> bool:
        if not self.config.TELEGRAM_ADMIN_CHAT_IDS:
            return True
        return user_id in self.config.TELEGRAM_ADMIN_CHAT_IDS

    def _register_handlers(self, bot_router: Router) -> None:
        @bot_router.message(CommandStart())
        async def cmd_start(message: Message) -> None:
            if not self._is_authorized(message.from_user.id):
                await message.answer(get_text("access_denied", lang=self.lang))
                return

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📊 Status", callback_data="cmd:status"),
                    InlineKeyboardButton(text="👥 Users", callback_data="cmd:users")
                ],
                [
                    InlineKeyboardButton(text="🔀 Routers", callback_data="cmd:routers"),
                    InlineKeyboardButton(text="⚠️ Reboot Router", callback_data="cmd:reboot_prompt")
                ]
            ])
            await message.answer(
                "⚡ *MikroMan Companion Bot*\n\nUse the buttons below or commands:\n/status - Router health & traffic\n/users - List & manage users\n/routers - Switch & view routers\n/reboot - Reboot router",
                parse_mode="Markdown",
                reply_markup=kb
            )

        @bot_router.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            if not self._is_authorized(message.from_user.id):
                await message.answer(get_text("access_denied", lang=self.lang))
                return
            await self._send_status_message(message.answer)

        @bot_router.message(Command("users"))
        async def cmd_users(message: Message) -> None:
            if not self._is_authorized(message.from_user.id):
                await message.answer(get_text("access_denied", lang=self.lang))
                return
            await self._send_users_message(message.answer)

        @bot_router.message(Command("routers"))
        async def cmd_routers(message: Message) -> None:
            if not self._is_authorized(message.from_user.id):
                await message.answer(get_text("access_denied", lang=self.lang))
                return
            await self._send_routers_message(message.answer)

        @bot_router.message(Command("reboot"))
        async def cmd_reboot(message: Message) -> None:
            if not self._is_authorized(message.from_user.id):
                await message.answer(get_text("access_denied", lang=self.lang))
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=get_text("btn_confirm_reboot", lang=self.lang), callback_data="reboot:confirm"),
                    InlineKeyboardButton(text=get_text("btn_cancel", lang=self.lang), callback_data="reboot:cancel")
                ]
            ])
            await message.answer(get_text("reboot_prompt", lang=self.lang), reply_markup=kb)

        @bot_router.callback_query(F.data.startswith("cmd:"))
        async def handle_cmd_callback(query: CallbackQuery) -> None:
            if not self._is_authorized(query.from_user.id):
                await query.answer(get_text("access_denied", lang=self.lang), show_alert=True)
                return

            action = query.data.replace("cmd:", "")
            if action == "status":
                await self._send_status_message(query.message.edit_text)
            elif action == "users":
                await self._send_users_message(query.message.edit_text)
            elif action == "routers":
                await self._send_routers_message(query.message.edit_text)
            elif action == "reboot_prompt":
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text=get_text("btn_confirm_reboot", lang=self.lang), callback_data="reboot:confirm"),
                        InlineKeyboardButton(text=get_text("btn_cancel", lang=self.lang), callback_data="reboot:cancel")
                    ]
                ])
                await query.message.edit_text(get_text("reboot_prompt", lang=self.lang), reply_markup=kb)
            await query.answer()

        @bot_router.callback_query(F.data.startswith("router:select:"))
        async def handle_router_select(query: CallbackQuery) -> None:
            if not self._is_authorized(query.from_user.id):
                await query.answer(get_text("access_denied", lang=self.lang), show_alert=True)
                return

            router_id = int(query.data.replace("router:select:", ""))
            async with self.session_factory() as session:
                r = await session.get(RouterModel, router_id)
                if r:
                    await session.execute(update(RouterModel).values(is_default=False))
                    r.is_default = True
                    r.is_active = True
                    await session.commit()
                    await query.answer(f"Switched active router to {r.name}", show_alert=True)

            await self._send_routers_message(query.message.edit_text)

        @bot_router.callback_query(F.data.startswith("user:"))
        async def handle_user_action(query: CallbackQuery) -> None:
            if not self._is_authorized(query.from_user.id):
                await query.answer(get_text("access_denied", lang=self.lang), show_alert=True)
                return

            parts = query.data.split(":")
            action = parts[1]
            user_id = int(parts[2])

            async with self.session_factory() as session:
                user = await session.get(User, user_id)
                if not user:
                    await query.answer("User not found", show_alert=True)
                    return

                client = await self.router_manager.get_client(session=session)
                if not client:
                    await query.answer("No active router connected", show_alert=True)
                    return

                ctrl = TrafficController(client)
                if action == "pause":
                    await ctrl.pause_user_internet(user_id, session)
                    await query.answer(get_text("pause_applied", lang=self.lang, user=user.name), show_alert=True)
                elif action == "resume":
                    await ctrl.resume_user_internet(user_id, session)
                    await query.answer(get_text("resume_applied", lang=self.lang, user=user.name), show_alert=True)
                elif action == "limit":
                    limit_val = parts[3]
                    await ctrl.set_user_speed_limit(user_id, limit_val, session)
                    await query.answer(get_text("limit_applied", lang=self.lang, user=user.name, limit=limit_val), show_alert=True)

            await self._send_users_message(query.message.edit_text)

        @bot_router.callback_query(F.data.startswith("reboot:"))
        async def handle_reboot_callback(query: CallbackQuery) -> None:
            if not self._is_authorized(query.from_user.id):
                await query.answer(get_text("access_denied", lang=self.lang), show_alert=True)
                return

            choice = query.data.replace("reboot:", "")
            if choice == "confirm":
                await query.message.edit_text(get_text("reboot_in_progress", lang=self.lang))
                async with self.session_factory() as session:
                    client = await self.router_manager.get_client(session=session)
                    if client:
                        await client.reboot_system()
            else:
                await query.message.edit_text(get_text("reboot_cancelled", lang=self.lang))
            await query.answer()

    async def _send_status_message(self, send_func) -> None:
        try:
            async with self.session_factory() as session:
                client = await self.router_manager.get_client(session=session)
                if not client:
                    await send_func("⚠️ No active router configured. Use /routers or the Web UI to add one.")
                    return

                res = await client.get_system_resource()
                health = await client.get_system_health()

            lines = [
                get_text("status_title", lang=self.lang),
                f"🏷 {get_text('board_name', lang=self.lang)}: `{res.board_name or 'MikroTik'}` ({res.version or 'ROS 7.x'})",
                f"⚙ {get_text('cpu_load', lang=self.lang)}: `{res.cpu_load}%` | ⏱ {get_text('uptime', lang=self.lang)}: `{res.uptime or 'N/A'}`",
                f"💾 {get_text('memory', lang=self.lang)}: `{format_bytes(res.free_memory)} free` / `{format_bytes(res.total_memory)}`",
            ]
            if health.temperature is not None:
                lines.append(f"🌡 {get_text('temperature', lang=self.lang)}: `{health.temperature}°C`")
            if health.voltage is not None:
                lines.append(f"⚡ {get_text('voltage', lang=self.lang)}: `{health.voltage}V`")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=get_text("btn_refresh", lang=self.lang), callback_data="cmd:status"),
                    InlineKeyboardButton(text="👥 Users", callback_data="cmd:users")
                ],
                [
                    InlineKeyboardButton(text="🔀 Routers", callback_data="cmd:routers")
                ]
            ])
            await send_func("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"Error preparing Telegram status message: {e}")
            await send_func(f"❌ Error fetching router status: {e}")

    async def _send_routers_message(self, send_func) -> None:
        async with self.session_factory() as session:
            res = await session.execute(select(RouterModel).order_by(RouterModel.is_default.desc(), RouterModel.id.asc()))
            routers = list(res.scalars().all())

        if not routers:
            await send_func("⚠️ No routers configured yet. Configure via Web UI Setup Wizard.")
            return

        lines = ["🔀 *Configured MikroTik Routers:*\n"]
        buttons = []

        for r in routers:
            icon = "🟢" if r.is_default else "⚪"
            status = "(Active)" if r.is_default else ""
            lines.append(f"{icon} *{r.name}* `{r.host}:{r.port}` {status}")

            if not r.is_default:
                buttons.append([InlineKeyboardButton(text=f"👉 Switch to {r.name}", callback_data=f"router:select:{r.id}")])

        buttons.append([
            InlineKeyboardButton(text=get_text("btn_refresh", lang=self.lang), callback_data="cmd:routers"),
            InlineKeyboardButton(text="📊 Status", callback_data="cmd:status")
        ])

        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await send_func("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

    async def _send_users_message(self, send_func) -> None:
        async with self.session_factory() as session:
            client = await self.router_manager.get_client(session=session)
            if not client:
                await send_func("⚠️ No active router configured.")
                return

            ctrl = TrafficController(client)
            user_stats = await ctrl.get_realtime_traffic_stats(session)

        if not user_stats:
            await send_func(get_text("no_users", lang=self.lang))
            return

        text_blocks = [get_text("users_title", lang=self.lang) + "\n"]
        buttons = []

        for u in user_stats:
            status_str = get_text("status_paused", lang=self.lang) if u["is_paused"] else get_text("status_active", lang=self.lang)
            text_blocks.append(
                get_text(
                    "user_card",
                    lang=self.lang,
                    name=u["name"],
                    active_devices=u["active_device_count"],
                    total_devices=u["device_count"],
                    speed_down=format_speed(u["current_rate_in"]),
                    speed_up=format_speed(u["current_rate_out"]),
                    today_in=format_bytes(u["bytes_in"]),
                    today_out=format_bytes(u["bytes_out"]),
                    limit=u["speed_limit"],
                    status=status_str
                ) + "\n"
            )

            # User control row
            if u["is_paused"]:
                buttons.append([InlineKeyboardButton(text=f"▶ Resume {u['name']}", callback_data=f"user:resume:{u['user_id']}")])
            else:
                buttons.append([
                    InlineKeyboardButton(text=f"⏸ Pause {u['name']}", callback_data=f"user:pause:{u['user_id']}"),
                    InlineKeyboardButton(text="⚡ 20M", callback_data=f"user:limit:{u['user_id']}:20M"),
                    InlineKeyboardButton(text="⚡ 50M", callback_data=f"user:limit:{u['user_id']}:50M"),
                    InlineKeyboardButton(text="⚡ Max", callback_data=f"user:limit:{u['user_id']}:unlimited"),
                ])

        buttons.append([
            InlineKeyboardButton(text=get_text("btn_refresh", lang=self.lang), callback_data="cmd:users"),
            InlineKeyboardButton(text="📊 Status", callback_data="cmd:status")
        ])

        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await send_func("\n".join(text_blocks), parse_mode="Markdown", reply_markup=kb)

    async def send_alert_to_admins(self, message_text: str) -> None:
        """Broadcast alert to all configured admin chat IDs."""
        if not self.bot or not self.config.TELEGRAM_ADMIN_CHAT_IDS:
            return

        for chat_id in self.config.TELEGRAM_ADMIN_CHAT_IDS:
            try:
                await self.bot.send_message(chat_id=chat_id, text=message_text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send Telegram alert to {chat_id}: {e}")

    async def start(self) -> None:
        """Start Telegram bot according to mode (polling or webhook)."""
        if not self.bot or not self.dp:
            logger.info("Telegram Bot Token not configured. Bot disabled.")
            return

        if self.config.TELEGRAM_MODE == "polling":
            logger.info("Starting Telegram Bot in Long Polling mode...")
            self.polling_task = asyncio.create_task(self.dp.start_polling(self.bot))
        elif self.config.TELEGRAM_MODE == "webhook" and self.config.TELEGRAM_WEBHOOK_URL:
            logger.info(f"Setting Telegram Webhook to {self.config.TELEGRAM_WEBHOOK_URL}...")
            await self.bot.set_webhook(self.config.TELEGRAM_WEBHOOK_URL)

    async def stop(self) -> None:
        """Stop Telegram bot."""
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
        if self.bot:
            await self.bot.session.close()

    async def process_webhook_update(self, update_dict: dict) -> None:
        """Process incoming webhook update from FastAPI route."""
        if self.bot and self.dp:
            update_obj = Update.model_validate(update_dict, context={"bot": self.bot})
            await self.dp.feed_update(self.bot, update_obj)
