from typing import Any, Dict

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "status_title": "📊 *MikroTik Router Status*",
        "board_name": "Device",
        "version": "RouterOS Version",
        "cpu_load": "CPU Load",
        "memory": "Memory",
        "temperature": "Temperature",
        "voltage": "Voltage",
        "uptime": "Uptime",
        "wan_ip": "WAN IP",
        "gateway_speed": "Current Throughput",
        "users_title": "👥 *Active Network Users*",
        "no_users": "No users configured yet.",
        "user_card": "👤 *{name}* ({active_devices}/{total_devices} devices)\n⬇ {speed_down} | ⬆ {speed_up}\nToday: ⬇ {today_in} | ⬆ {today_out}\nLimit: `{limit}` | Status: {status}",
        "status_paused": "⏸ Paused",
        "status_active": "✅ Active",
        "btn_pause": "⏸ Pause Internet",
        "btn_resume": "▶ Resume Access",
        "btn_limit": "⚡ Speed Limit",
        "btn_refresh": "🔄 Refresh",
        "btn_reboot": "⚠️ Reboot Router",
        "btn_confirm_reboot": "✅ Yes, Reboot Now",
        "btn_cancel": "❌ Cancel",
        "alert_new_device": "🔔 *New Device Discovered!*\nHost: `{hostname}`\nIP: `{ip}`\nMAC: `{mac}`\nVendor: `{vendor}`",
        "alert_high_cpu": "⚠️ *High CPU Alert!*\nCPU load has reached {cpu_load}%",
        "alert_high_temp": "🔥 *High Temperature Alert!*\nBoard temperature: {temp}°C",
        "alert_ip_change": "🌐 *WAN IP Changed!*\nNew IP: `{new_ip}`\nPrevious IP: `{old_ip}`",
        "reboot_prompt": "⚠️ Are you sure you want to reboot the MikroTik router?",
        "reboot_in_progress": "🔄 Reboot command sent to router...",
        "reboot_cancelled": "❌ Reboot cancelled.",
        "access_denied": "⛔ Access Denied. Your Telegram Chat ID is not authorized.",
        "limit_applied": "✅ Speed limit for *{user}* set to `{limit}`",
        "pause_applied": "⏸ Internet paused for *{user}*",
        "resume_applied": "▶ Internet resumed for *{user}*",
    },
    "ru": {
        "status_title": "📊 *Статус роутера MikroTik*",
        "board_name": "Устройство",
        "version": "Версия RouterOS",
        "cpu_load": "Загрузка CPU",
        "memory": "Память",
        "temperature": "Температура",
        "voltage": "Напряжение",
        "uptime": "Аптайм",
        "wan_ip": "WAN IP",
        "gateway_speed": "Текущая скорость",
        "users_title": "👥 *Пользователи сети*",
        "no_users": "Пользователи ещё не настроены.",
        "user_card": "👤 *{name}* ({active_devices}/{total_devices} устр.)\n⬇ {speed_down} | ⬆ {speed_up}\nСегодня: ⬇ {today_in} | ⬆ {today_out}\nЛимит: `{limit}` | Статус: {status}",
        "status_paused": "⏸ На паузе",
        "status_active": "✅ Активен",
        "btn_pause": "⏸ Пауза",
        "btn_resume": "▶ Возобновить",
        "btn_limit": "⚡ Лимит скорости",
        "btn_refresh": "🔄 Обновить",
        "btn_reboot": "⚠️ Перезагрузить",
        "btn_confirm_reboot": "✅ Да, перезагрузить",
        "btn_cancel": "❌ Отмена",
        "alert_new_device": "🔔 *Обнаружено новое устройство!*\nИмя: `{hostname}`\nIP: `{ip}`\nMAC: `{mac}`\nПроизводитель: `{vendor}`",
        "alert_high_cpu": "⚠️ *Высокая нагрузка CPU!*\nЗагрузка процессора достигла {cpu_load}%",
        "alert_high_temp": "🔥 *Высокая температура!*\nТемпература платы: {temp}°C",
        "alert_ip_change": "🌐 *Изменился WAN IP!*\nНовый IP: `{new_ip}`\nСтарый IP: `{old_ip}`",
        "reboot_prompt": "⚠️ Вы уверены, что хотите перезагрузить роутер MikroTik?",
        "reboot_in_progress": "🔄 Команда перезагрузки отправлена на роутер...",
        "reboot_cancelled": "❌ Перезагрузка отменена.",
        "access_denied": "⛔ Доступ запрещён. Ваш Telegram Chat ID не авторизован.",
        "limit_applied": "✅ Для пользователя *{user}* установлен лимит скорости `{limit}`",
        "pause_applied": "⏸ Интернет приостановлен для *{user}*",
        "resume_applied": "▶ Доступ в интернет возобновлен для *{user}*",
    }
}


def get_text(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Retrieve localized message by key and format with parameters."""
    lang_dict = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
    template = lang_dict.get(key, TRANSLATIONS["en"].get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def format_bytes(bytes_count: int) -> str:
    """Format bytes count into human readable units (B, KB, MB, GB, TB)."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 ** 2:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 ** 3:
        return f"{bytes_count / (1024 ** 2):.1f} MB"
    elif bytes_count < 1024 ** 4:
        return f"{bytes_count / (1024 ** 3):.2f} GB"
    else:
        return f"{bytes_count / (1024 ** 4):.2f} TB"


def format_speed(bps: int) -> str:
    """Format bits-per-second into human readable units (bps, Kbps, Mbps, Gbps)."""
    if bps < 1000:
        return f"{bps} bps"
    elif bps < 1000 ** 2:
        return f"{bps / 1000:.1f} Kbps"
    elif bps < 1000 ** 3:
        return f"{bps / (1000 ** 2):.1f} Mbps"
    else:
        return f"{bps / (1000 ** 3):.2f} Gbps"
