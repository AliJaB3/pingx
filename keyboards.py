from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_main(uid: int, is_admin: bool):
    btns = [
        [
            InlineKeyboardButton(text="ع©غŒظپâ€Œظ¾ظˆظ„ ًں’³", callback_data="wallet"),
            InlineKeyboardButton(text="ط®ط±غŒط¯ ط§ط´طھط±ط§ع© ًں›’", callback_data="buy"),
        ],
        [InlineKeyboardButton(text="ط§ط´طھط±ط§ع©â€Œظ‡ط§غŒ ظ…ظ† ًں§¾", callback_data="mysubs")],
        [InlineKeyboardButton(text="ظ¾ط´طھغŒط¨ط§ظ†غŒ ًںژ§", callback_data="support")],
    ]
    if is_admin:
        btns.insert(0, [InlineKeyboardButton(text="ظ¾ظ†ظ„ ط§ط¯ظ…غŒظ† âڑ™ï¸ڈ", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def kb_force_join(channel: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ط¹ط¶ظˆغŒطھ ط¯ط± ع©ط§ظ†ط§ظ„ ًں“£",
                    url=f"https://t.me/{channel.lstrip('@')}",
                )
            ],
            [InlineKeyboardButton(text="ط¨ط±ط±ط³غŒ ظ…ط¬ط¯ط¯ âœ…", callback_data="recheck_join")],
        ]
    )


def kb_plans(plans, is_admin: bool):
    rows = []
    for p in plans:
        import json

        flags = json.loads(p.get("flags") or "{}")
        if flags.get("admin_only") and not is_admin:
            continue
        rows.append([
            InlineKeyboardButton(
                text=f"{p['title']} آ· {p['price']:,} طھظˆظ…ط§ظ†",
                callback_data=f"plan:{p['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_mysubs(rows):
    kb = [
        [
            InlineKeyboardButton(
                text=f"ط§ط´طھط±ط§ع© #{r['id']} | {r['plan_id']} | {r['price']:,} طھظˆظ…ط§ظ†",
                callback_data=f"sub:{r['id']}",
            )
        ]
        for r in rows
    ]
    kb.append([InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def kb_sub_detail(purchase_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ط§ط±ط³ط§ظ„ ظ„غŒظ†ع©/QR ًں”—", callback_data=f"subfix:{purchase_id}")],
            [InlineKeyboardButton(text="ع†ط±ط®ط§ظ†ط¯ظ† ظ„غŒظ†ع© â™»ï¸ڈ", callback_data=f"subrevoke:{purchase_id}")],
            [InlineKeyboardButton(text="ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ظ…طµط±ظپ ًں”„", callback_data=f"substat:{purchase_id}")],
            [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="mysubs")],
        ]
    )


def kb_admin_root():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ط¯ط±ط®ظˆط§ط³طھâ€Œظ‡ط§غŒ ط´ط§ط±عک âڈ³", callback_data="admin:pending:0")],
            [InlineKeyboardButton(text="ع©ط§ط±ط¨ط±ط§ظ† ًں‘¥", callback_data="admin:users:0:")],
            [InlineKeyboardButton(text="طھغŒع©طھâ€Œظ‡ط§ ًںژںï¸ڈ", callback_data="admin:tickets:0")],
            [InlineKeyboardButton(text="ظ¾ظ„ظ†â€Œظ‡ط§ ًں“¦", callback_data="admin:plans")],
            [InlineKeyboardButton(text="ظ‚ط§ظ„ط¨ ظ¾غŒط§ظ…â€Œظ‡ط§ ًں“‌", callback_data="admin:templates")],
            [InlineKeyboardButton(text="طھظ†ط¸غŒظ…ط§طھ âڑ™ï¸ڈ", callback_data="admin:settings")],
            [InlineKeyboardButton(text="ط¨ط±ط±ط³غŒ ظ¾ظ†ظ„ 3x-ui ًں§ھ", callback_data="admin:paneltest")],
            [InlineKeyboardButton(text="ط®ط§ظ†ظ‡ ًںڈ ", callback_data="home")],
        ]
    )



