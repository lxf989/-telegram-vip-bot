import os
import sqlite3
import logging
from datetime import date, datetime, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeChat,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8957110767"))
USDT_ADDRESS = os.getenv(
    "USDT_ADDRESS",
    "THSbZXqnnLUAzfgYkv9AavBD4YdHTTzNxz"
)
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

MENU = [
    ["💬 双向联系", "👤 我的资料"],
    ["🆔 查询用户ID", "💎 VIP会员"],
    ["🖼 NFT查询", "📱 +888号码查询"],
    ["👤 收藏用户名查询", "ℹ️ 免费功能"],
    ["🎁 每日签到", "👨‍💻 联系管理员"],
    ["❌ 退出菜单"],
]

PLANS = {
    "7天": (1, 7),
    "30天": (2, 30),
    "90天": (5, 90),
    "365天": (10, 365),
}

OKPAY_LINKS = {
    "7天": "https://t.me/OkayPayBot?start=transfer_by_text--ODk1NzExMDc2NyAxLjAw",
    "30天": "https://t.me/OkayPayBot?start=transfer_by_text--ODk1NzExMDc2NyAyLjAw",
    "90天": "https://t.me/OkayPayBot?start=transfer_by_text--ODk1NzExMDc2NyA1LjAw",
    "365天": "https://t.me/OkayPayBot?start=transfer_by_text--ODk1NzExMDc2NyAxMC4wMA==",
}

user_modes = {}
contact_waiting = set()
payment_waiting = set()


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT,
        points INTEGER DEFAULT 0,
        vip_until TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS checkins (
        user_id INTEGER,
        check_date TEXT,
        UNIQUE(user_id, check_date)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS replies (
        admin_message_id INTEGER PRIMARY KEY,
        user_id INTEGER
    )
    """)

    con.commit()
    con.close()


def register_user(user):
    con = db()
    cur = con.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO users
    (user_id, username, first_name, first_seen)
    VALUES (?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    cur.execute("""
    UPDATE users
    SET username=?, first_name=?
    WHERE user_id=?
    """, (user.username or "", user.first_name or "", user.id))

    con.commit()
    con.close()


def get_user(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    result = cur.fetchone()
    con.close()
    return result


def main_keyboard(user_id):
    current = user_modes.get(user_id)
    rows = []

    for row in MENU:
        new_row = []
        for button in row:
            if button == current:
                button += " ✅"
            new_row.append(button)
        rows.append(new_row)

    if current == "💬 双向联系":
        rows.insert(1, ["❌ 退出联系"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def clear_mode(user_id):
    user_modes.pop(user_id, None)
    contact_waiting.discard(user_id)
    payment_waiting.discard(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    clear_mode(user.id)
    context.user_data.clear()

    await update.message.reply_text(
        "欢迎使用 VIP 机器人！\n请选择底部功能菜单。",
        reply_markup=main_keyboard(user.id)
    )


async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    today = date.today().isoformat()

    con = db()
    cur = con.cursor()

    try:
        cur.execute(
            "INSERT INTO checkins VALUES (?, ?)",
            (user.id, today)
        )
        cur.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?",
            (user.id,)
        )
        con.commit()
        row = get_user(user.id)

        await update.message.reply_text(
            f"🎁 签到成功！获得1积分。\n当前积分：{row[4]}"
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text("今天已经签到过了，明天再来吧！")
    finally:
        con.close()


async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    row = get_user(update.effective_user.id)

    await update.message.reply_text(
        f"⭐ 当前积分：{row[4]}\n"
        "🎁 每日签到可获得1积分。\n"
        "💎 满18积分可兑换7天VIP。"
    )


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = get_user(user_id)

    if not row:
        register_user(update.effective_user)
        row = get_user(user_id)

    if row[4] < 18:
        await update.message.reply_text(
            f"积分不足，目前有 {row[4]} 积分。\n兑换需要18积分。"
        )
        return

    now = datetime.now()
    if row[5]:
        try:
            old = datetime.fromisoformat(row[5])
            if old > now:
                now = old
        except ValueError:
            pass

    expiry = (now + timedelta(days=7)).isoformat(
        sep=" ", timespec="seconds"
    )

    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET points=points-18, vip_until=? WHERE user_id=?",
        (expiry, user_id)
    )
    con.commit()
    con.close()

    await update.message.reply_text(
        f"兑换成功！已开通7天VIP。\n到期时间：{expiry}"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"你的 Telegram 用户ID：\n{update.effective_user.id}"
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    row = get_user(user.id)

    username = f"@{user.username}" if user.username else "未设置"

    await update.message.reply_text(
        f"👤 个人资料\n\n"
        f"姓名：{user.first_name}\n"
        f"用户名：{username}\n"
        f"用户ID：{user.id}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}"
    )


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("7天 VIP · 1 USDT", callback_data="plan_7天"),
        ],
        [
            InlineKeyboardButton("30天 VIP · 2 USDT", callback_data="plan_30天"),
        ],
        [
            InlineKeyboardButton("90天 VIP · 5 USDT", callback_data="plan_90天"),
        ],
        [
            InlineKeyboardButton("365天 VIP · 10 USDT", callback_data="plan_365天"),
        ],
    ]

    await update.message.reply_text(
        "💎 请选择 VIP 套餐：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data.startswith("plan_"):
        plan_name = data.replace("plan_", "")
        amount, days = PLANS[plan_name]

        context.user_data["selected_plan"] = (
            plan_name, amount, days
        )
        context.user_data.pop("payment_method", None)
        context.user_data.pop("need_proof", None)

        keyboard = [
            [
                InlineKeyboardButton(
                    "TRC20",
                    callback_data="method_TRC20"
                )
            ],
            [
                InlineKeyboardButton(
                    "OKPay",
                    callback_data="method_OKPay"
                )
            ],
        ]

        await query.edit_message_text(
            f"你选择了 {plan_name} VIP。\n金额：{amount} USDT\n\n请选择支付方式：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("method_"):
        method = data.replace("method_", "")
        plan = context.user_data.get("selected_plan")

        if not plan:
            await query.edit_message_text("请重新选择 VIP 套餐。")
            return

        plan_name, amount, days = plan
        context.user_data["payment_method"] = method
        payment_waiting.add(user_id)

        if method == "OKPay":
            link = OKPAY_LINKS[plan_name]

            keyboard = [
                [InlineKeyboardButton(
                    "🟢 OKPay 支付",
                    url=link
                )],
                [InlineKeyboardButton(
                    "切换到 TRC20",
                    callback_data="method_TRC20"
                )],
                [InlineKeyboardButton(
                    "✅ 我已支付",
                    callback_data="paid"
                )],
            ]

            await query.edit_message_text(
                f"💎 {plan_name} VIP\n"
                f"金额：{amount} USDT\n"
                "支付方式：OKPay\n\n"
                "点击下方按钮前往支付，完成后点击「我已支付」。",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        else:
            keyboard = [
                [InlineKeyboardButton(
                    "🟢 查看 TRC20 收款地址",
                    callback_data="show_address"
                )],
                [InlineKeyboardButton(
                    "切换到 OKPay",
                    callback_data="method_OKPay"
                )],
                [InlineKeyboardButton(
                    "✅ 我已支付",
                    callback_data="paid"
                )],
            ]

            await query.edit_message_text(
                f"💎 {plan_name} VIP\n"
                f"金额：{amount} USDT\n"
                "支付方式：TRC20\n\n"
                "请按上方金额转账，完成后点击「我已支付」。",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    if data == "show_address":
        await query.message.reply_text(
            "TRC20 收款地址（请复制）：\n\n"
            f"`{USDT_ADDRESS}`\n\n"
            "请确认使用 TRON（TRC20）网络。",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "paid":
        if not context.user_data.get("selected_plan"):
            await query.message.reply_text("请先选择套餐。")
            return

        context.user_data["need_proof"] = True

        await query.message.reply_text(
            "请发送交易 TXID 或付款凭证图片。\n"
            "收到后会提交管理员人工审核。"
        )
        return


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.message.reply_text("用法：/approve 用户ID 天数")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("用户ID或天数格式错误。")
        return

    row = get_user(target_id)
    if not row:
        await update.message.reply_text("找不到这个用户。")
        return

    now = datetime.now()
    if row[5]:
        try:
            old = datetime.fromisoformat(row[5])
            if old > now:
                now = old
        except ValueError:
            pass

    expiry = (now + timedelta(days=days)).isoformat(
        sep=" ", timespec="seconds"
    )

    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (expiry, target_id)
    )
    con.commit()
    con.close()

    await context.bot.send_message(
        target_id,
        f"🎉 VIP开通成功！有效期增加{days}天。\n到期时间：{expiry}"
    )
    await update.message.reply_text("VIP开通成功。")


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("无权使用此指令。")
        return

    await update.message.reply_text(
        "管理员指令：\n"
        "/stats - 用户统计\n"
        "/user 用户ID - 查询用户\n"
        "/approve 用户ID 天数 - 开通VIP\n"
        "/addpoints 用户ID 积分 - 增加积分\n"
        "/delpoints 用户ID 积分 - 扣除积分"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    cur.execute("SELECT SUM(points) FROM users")
    total_points = cur.fetchone()[0] or 0
    con.close()

    await update.message.reply_text(
        f"用户总数：{total}\n积分总数：{total_points}"
    )


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 1:
        await update.message.reply_text("用法：/user 用户ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID格式错误。")
        return

    row = get_user(target_id)
    if not row:
        await update.message.reply_text("用户不存在。")
        return

    await update.message.reply_text(
        f"用户ID：{row[0]}\n"
        f"用户名：{row[1]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '无'}"
    )


async def change_points(update, context, amount):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.message.reply_text("用法：/addpoints 用户ID 积分")
        return

    try:
        target_id = int(context.args[0])
        number = int(context.args[1])
        if number <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("参数格式错误。")
        return

    row = get_user(target_id)
    if not row:
        await update.message.reply_text("用户不存在。")
        return

    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET points=MAX(0, points+?) WHERE user_id=?",
        (amount * number, target_id)
    )
    con.commit()
    con.close()

    await update.message.reply_text("积分修改成功。")


async def addpoints(update, context):
    await change_points(update, context, 1)


async def delpoints(update, context):
    await change_points(update, context, -1)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.message
    user = update.effective_user

    if not message or not user:
        return

    # 管理员回复用户消息
    if user.id == ADMIN_ID and message.reply_to_message:
        replied_id = message.reply_to_message.message_id

        con = db()
        cur = con.cursor()
        cur.execute(
            "SELECT user_id FROM replies WHERE admin_message_id=?",
            (replied_id,)
        )
        result = cur.fetchone()
        con.close()

        if result:
            try:
                await context.bot.copy_message(
                    chat_id=result[0],
                    from_chat_id=ADMIN_ID,
                    message_id=message.message_id
                )
                await message.reply_text("已回复用户。")
            except Exception:
                await message.reply_text("发送失败，用户可能已屏蔽机器人。")
            return

    if user.id == ADMIN_ID:
        return

    register_user(user)
    text = (message.text or "").strip()

    menu_items = [
        "💬 双向联系", "👤 我的资料", "🆔 查询用户ID",
        "💎 VIP会员", "🖼 NFT查询", "📱 +888号码查询",
        "👤 收藏用户名查询", "ℹ️ 免费功能", "🎁 每日签到",
        "👨‍💻 联系管理员", "❌ 退出菜单", "❌ 退出联系"
    ]

    if text in menu_items:
        clear_mode(user.id)
        context.user_data.clear()

        if text in ("❌ 退出菜单", "❌ 退出联系"):
            await message.reply_text(
                "已退出当前功能。",
                reply_markup=main_keyboard(user.id)
            )
            return

        user_modes[user.id] = text

        if text == "💬 双向联系":
            contact_waiting.add(user.id)
            await message.reply_text(
                "双向联系已开启。你发送的消息会转发给管理员。\n"
                "点击「❌ 退出联系」即可停止。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "👤 我的资料":
            await profile(update, context)
            await message.reply_text(
                "当前功能：我的资料。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "🆔 查询用户ID":
            await message.reply_text(
                "请把你想查询的那个人发过的 Telegram 消息转发给我。\n"
                "我会尝试读取对方的用户ID。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "💎 VIP会员":
            await vip(update, context)
            await message.reply_text(
                "请选择上方套餐。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text in ("🖼 NFT查询", "📱 +888号码查询", "👤 收藏用户名查询"):
            await message.reply_text(
                "此查询功能还需要接入 TON 数据接口，目前暂未开放真实查询。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "ℹ️ 免费功能":
            await message.reply_text(
                "免费功能：个人资料、查询别人ID、积分和每日签到。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "🎁 每日签到":
            await checkin(update, context)
            await message.reply_text(
                "每日签到完成。",
                reply_markup=main_keyboard(user.id)
            )
            return

        if text == "👨‍💻 联系管理员":
            await message.reply_text(
                "联系管理员：@kpilx\nhttps://t.me/kpilx",
                reply_markup=main_keyboard(user.id)
            )
            return

    # 查询别人ID：读取转发消息来源
    if user_modes.get(user.id) == "🆔 查询用户ID":
        origin = getattr(message, "forward_origin", None)

        if origin and getattr(origin, "type", None) == "user":
            forwarded_user = origin.sender_user

            await message.reply_text(
                f"查询成功！\n"
                f"姓名：{forwarded_user.full_name}\n"
                f"用户名：@{forwarded_user.username if forwarded_user.username else '未设置'}\n"
                f"Telegram用户ID：{forwarded_user.id}",
                reply_markup=main_keyboard(user.id)
            )
        else:
            await message.reply_text(
                "无法读取这条消息的发送者ID。\n"
                "请转发一条没有隐藏发送者身份的消息。"
            )
        return

    # VIP付款凭证
    if context.user_data.get("need_proof"):
        plan = context.user_data.get("selected_plan")
        method = context.user_data.get("payment_method", "未选择")

        if plan:
            caption = (
                "💰 VIP付款申请\n"
                f"套餐：{plan[0]}\n"
                f"金额：{plan[1]} USDT\n"
                f"支付方式：{method}\n"
                f"用户ID：{user.id}\n"
                f"用户名：@{user.username or '未设置'}"
            )

            await context.bot.send_message(ADMIN_ID, caption)

            try:
                await context.bot.copy_message(
                    chat_id=ADMIN_ID,
                    from_chat_id=user.id,
                    message_id=message.message_id
                )
            except Exception:
                pass

            await message.reply_text(
                "付款资料已提交，请等待管理员核实到账。\n"
                "核实成功后才会开通VIP。"
            )

            context.user_data["need_proof"] = False
            payment_waiting.discard(user.id)
            return

    # 双向联系
    if user.id in contact_waiting:
        username = f"@{user.username}" if user.username else "未设置"

        await context.bot.send_message(
            ADMIN_ID,
            f"📩 用户发来消息\n"
            f"用户ID：{user.id}\n"
            f"用户名：{username}\n"
            "直接回复下面转发的消息即可回复用户。"
        )

        copied = await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=user.id,
            message_id=message.message_id
        )

        con = db()
        cur = con.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO replies VALUES (?, ?)",
            (copied.message_id, user.id)
        )
        con.commit()
        con.close()

        await message.reply_text("消息已发送给管理员。")
        return


async def post_init(application):
    user_commands = [
        BotCommand("start", "打开主菜单"),
        BotCommand("qd", "每日签到"),
        BotCommand("jf", "查询积分"),
        BotCommand("dh", "积分兑换会员"),
        BotCommand("me", "我的资料"),
        BotCommand("myid", "查询自己的ID"),
        BotCommand("help", "查看帮助"),
    ]

    admin_commands = [
        BotCommand("admin", "管理员菜单"),
        BotCommand("stats", "用户统计"),
        BotCommand("user", "查询用户"),
        BotCommand("approve", "开通VIP"),
        BotCommand("addpoints", "增加积分"),
        BotCommand("delpoints", "扣除积分"),
    ]

    await application.bot.set_my_commands(user_commands)

    await application.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )


async def help_command(update, context):
    await update.message.reply_text(
        "普通用户指令：\n"
        "/start - 主菜单\n"
        "/qd - 每日签到\n"
        "/jf - 查询积分\n"
        "/dh - 兑换会员\n"
        "/me - 我的资料\n"
        "/myid - 查询自己的ID"
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("没有设置 BOT_TOKEN 环境变量")

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("qd", checkin))
    app.add_handler(CommandHandler("jf", points))
    app.add_handler(CommandHandler("dh", redeem))
    app.add_handler(CommandHandler("me", profile))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("user", user_info))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("addpoints", addpoints))
    app.add_handler(CommandHandler("delpoints", delpoints))

    app.add_handler(
        CallbackQueryHandler(payment_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
