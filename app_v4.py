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
    MessageEntity,
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

# 高级表情 ID
VIP_EMOJI_ID = "5235630047959727475"
USER_EMOJI_ID = "6210879471474447673"
CONTACT_EMOJI_ID = "5443038326535759644"
ADMIN_EMOJI_ID = "5330237710655306682"
POINTS_EMOJI_ID = "5408869739982246969"
CHECKIN_EMOJI_ID = "5449800250032143374"

# 用作高级表情占位符
VIP_MARK = "⭐"
USER_MARK = "⭐"
CONTACT_MARK = "⭐"
ADMIN_MARK = "⭐"
POINTS_MARK = "⭐"
CHECKIN_MARK = "⭐"

# 底部菜单
MENU = [
    ["💬 双向联系", "👤 我的资料"],
    ["🆔 查询用户ID", "VIP会员"],
    ["🖼 NFT查询", "📱 +888号码查询"],
    ["👤 收藏用户名查询"],
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


# =========================
# 高级表情发送工具
# =========================

def custom_entity(text, marker, emoji_id):
    """计算占位符在 UTF-16 中的位置，生成高级表情实体。"""
    index = text.find(marker)

    if index < 0:
        return []

    offset = len(text[:index].encode("utf-16-le")) // 2
    length = len(marker.encode("utf-16-le")) // 2

    return [
        MessageEntity(
            type="custom_emoji",
            offset=offset,
            length=length,
            custom_emoji_id=emoji_id
        )
    ]


async def reply_custom(
    message,
    text,
    marker,
    emoji_id,
    **kwargs
):
    entities = custom_entity(text, marker, emoji_id)

    return await message.reply_text(
        text,
        entities=entities,
        **kwargs
    )


async def send_custom(
    bot,
    chat_id,
    text,
    marker,
    emoji_id,
    **kwargs
):
    entities = custom_entity(text, marker, emoji_id)

    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        entities=entities,
        **kwargs
    )


def user_id_line(user_id):
    return f"{USER_MARK} 用户🆔：{user_id}"


# =========================
# 数据库
# =========================

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
    """, (
        user.username or "",
        user.first_name or "",
        user.id
    ))

    con.commit()
    con.close()


def get_user(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    )

    result = cur.fetchone()
    con.close()

    return result


# =========================
# 菜单和状态
# =========================

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

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True
    )


def clear_mode(user_id):
    user_modes.pop(user_id, None)
    contact_waiting.discard(user_id)
    payment_waiting.discard(user_id)


# =========================
# 开始
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    register_user(user)
    clear_mode(user.id)
    context.user_data.clear()

    await update.message.reply_text(
        "欢迎使用 VIP 机器人！\n请选择底部功能菜单。",
        reply_markup=main_keyboard(user.id)
    )


# =========================
# 签到
# =========================

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
        await update.message.reply_text(
            "今天已经签到过了，明天再来吧！"
        )

    finally:
        con.close()


# =========================
# 积分
# =========================

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    row = get_user(update.effective_user.id)

    text = (
        f"{POINTS_MARK} 当前积分：{row[4]}\n"
        f"{CHECKIN_MARK} 每日签到可获得1积分。\n"
        f"{VIP_MARK} 满18积分可兑换30天VIP。"
    )

    entities = []
    entities += custom_entity(text, POINTS_MARK, POINTS_EMOJI_ID)

    offset = len(text[:text.find(CHECKIN_MARK)].encode("utf-16-le")) // 2
    entities.append(MessageEntity(
        type="custom_emoji",
        offset=offset,
        length=2,
        custom_emoji_id=CHECKIN_EMOJI_ID
    ))

    offset = len(text[:text.find(VIP_MARK)].encode("utf-16-le")) // 2
    entities.append(MessageEntity(
        type="custom_emoji",
        offset=offset,
        length=2,
        custom_emoji_id=VIP_EMOJI_ID
    ))

    await update.message.reply_text(
        text,
        entities=entities
    )


# =========================
# 积分兑换VIP：18积分兑换30天
# =========================

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

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

    expiry = (now + timedelta(days=30)).isoformat(
        sep=" ",
        timespec="seconds"
    )

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET points=points-18, vip_until=? WHERE user_id=?",
        (expiry, user_id)
    )

    con.commit()
    con.close()

    await reply_custom(
        update.message,
        f"{VIP_MARK} 兑换成功！已开通30天VIP。\n到期时间：{expiry}",
        VIP_MARK,
        VIP_EMOJI_ID
    )


# =========================
# 查询自己的ID
# =========================

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"你的 Telegram 用户ID：\n{user_id_line(update.effective_user.id)}"

    await reply_custom(
        update.message,
        text,
        USER_MARK,
        USER_EMOJI_ID
    )


# =========================
# 个人资料
# =========================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    register_user(user)

    row = get_user(user.id)

    username = f"@{user.username}" if user.username else "未设置"

    text = (
        f"👤 个人资料\n\n"
        f"姓名：{user.first_name or '未设置'}\n"
        f"用户名：{username}\n"
        f"{user_id_line(user.id)}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}"
    )

    await reply_custom(
        update.message,
        text,
        USER_MARK,
        USER_EMOJI_ID
    )


# =========================
# VIP套餐
# =========================

async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("7天 VIP · 1 USDT", callback_data="plan_7天")],
        [InlineKeyboardButton("30天 VIP · 2 USDT", callback_data="plan_30天")],
        [InlineKeyboardButton("90天 VIP · 5 USDT", callback_data="plan_90天")],
        [InlineKeyboardButton("365天 VIP · 10 USDT", callback_data="plan_365天")],
    ]

    await reply_custom(
        update.message,
        f"{VIP_MARK} 请选择 VIP 套餐：",
        VIP_MARK,
        VIP_EMOJI_ID,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# 支付流程
# =========================

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data.startswith("plan_"):
        plan = data.replace("plan_", "")

        if plan not in PLANS:
            return

        context.user_data["selected_plan"] = plan

        keyboard = [
            [InlineKeyboardButton("TRC20（USDT）", callback_data="pay_trc20")],
            [InlineKeyboardButton("OKPay", callback_data="pay_okpay")],
        ]

        await query.message.reply_text(
            f"已选择 {plan} VIP。\n请选择支付方式：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "pay_trc20":
        plan = context.user_data.get("selected_plan")

        if not plan:
            await query.message.reply_text(
                "请重新进入 VIP 会员页面选择套餐。"
            )
            return

        amount, days = PLANS[plan]

        context.user_data["payment_method"] = "TRC20"

        keyboard = [
            [InlineKeyboardButton("📋 我已支付", callback_data="paid")]
        ]

        text = (
            f"{VIP_MARK} 套餐：{plan} VIP\n"
            f"金额：{amount} USDT\n\n"
            f"请使用 TRC20 网络转账至以下地址：\n"
            f"`{USDT_ADDRESS}`\n\n"
            "转账完成后，点击“我已支付”，提交交易凭证。"
        )

        await send_custom(
            context.bot,
            query.message.chat_id,
            text,
            VIP_MARK,
            VIP_EMOJI_ID,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "pay_okpay":
        plan = context.user_data.get("selected_plan")

        if not plan:
            await query.message.reply_text(
                "请重新进入 VIP 会员页面选择套餐。"
            )
            return

        context.user_data["payment_method"] = "OKPay"

        keyboard = [
            [InlineKeyboardButton(
                "前往 OKPay 支付",
                url=OKPAY_LINKS[plan]
            )],
            [InlineKeyboardButton("📋 我已支付", callback_data="paid")]
        ]

        text = (
            f"{VIP_MARK} 套餐：{plan} VIP\n"
            f"金额：{PLANS[plan][0]} USDT\n\n"
            "点击下方按钮前往 OKPay。支付完成后，请回来提交凭证。"
        )

        await send_custom(
            context.bot,
            query.message.chat_id,
            text,
            VIP_MARK,
            VIP_EMOJI_ID,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "paid":
        plan = context.user_data.get("selected_plan")

        if not plan:
            await query.message.reply_text("请先选择 VIP 套餐。")
            return

        payment_waiting.add(user_id)

        await query.message.reply_text(
            f"请发送你的 {plan} VIP 支付凭证或交易 TXID。\n"
            "管理员核实到账后会为你开通 VIP。"
        )


# =========================
# 管理员菜单
# =========================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    await update.message.reply_text(
        "管理员命令：\n"
        "/stats - 查看统计\n"
        "/users - 查看所有用户\n"
        "/user 用户ID - 查看用户资料\n"
        "/approve 用户ID 天数 - 开通VIP\n"
        "/addpoints 用户ID 积分 - 增加积分\n"
        "/delpoints 用户ID 积分 - 扣除积分"
    )


# =========================
# 统计
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE vip_until IS NOT NULL")
    vip_total = cur.fetchone()[0]

    con.close()

    await update.message.reply_text(
        f"📊 机器人统计\n用户总数：{total}\n已开通过VIP：{vip_total}"
    )


# =========================
# 用户名单
# =========================

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限查看用户名单。")
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        ORDER BY first_seen ASC, user_id ASC
    """)

    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("目前还没有用户记录。")
        return

    messages = []
    current = ""

    for index, row in enumerate(rows, start=1):
        user_id, username, first_name = row

        username_text = f"@{username}" if username else "未设置"
        name_text = first_name or "未设置"

        item = (
            f"{index}. 用户名：{username_text}\n"
            f"   姓名：{name_text}\n"
            f"   {user_id_line(user_id)}\n\n"
        )

        if len(current) + len(item) > 3500:
            messages.append(current)
            current = ""

        current += item

    if current:
        messages.append(current)

    for part in messages:
        await send_custom(
            context.bot,
            update.effective_chat.id,
            part,
            USER_MARK,
            USER_EMOJI_ID
        )

    await update.message.reply_text(
        f"✅ 用户名单结束\n用户总人数：{len(rows)}"
    )


# =========================
# 查看用户资料
# =========================

async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if not context.args:
        await update.message.reply_text("用法：/user 用户ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID必须是数字。")
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    username = f"@{row[1]}" if row[1] else "未设置"

    text = (
        f"{user_id_line(row[0])}\n"
        f"用户名：{username}\n"
        f"姓名：{row[2] or '未设置'}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}"
    )

    await reply_custom(
        update.message,
        text,
        USER_MARK,
        USER_EMOJI_ID
    )


# =========================
# 管理员开通VIP
# =========================

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.message.reply_text("用法：/approve 用户ID 天数")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])

        if days <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text("用户ID和天数必须是有效数字。")
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
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
        sep=" ",
        timespec="seconds"
    )

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (expiry, target_id)
    )

    con.commit()
    con.close()

    text = (
        f"✅ 已为用户 {target_id} 开通 {days} 天 VIP。\n"
        f"到期时间：{expiry}"
    )

    await update.message.reply_text(text)

    try:
        await send_custom(
            context.bot,
            target_id,
            f"{VIP_MARK} 管理员已为你开通 {days} 天 VIP！\n到期时间：{expiry}",
            VIP_MARK,
            VIP_EMOJI_ID
        )

    except Exception:
        logging.exception("通知用户VIP开通失败")


# =========================
# 管理员调整积分
# =========================

async def change_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "用法：/addpoints 或 /delpoints 用户ID 积分"
        )
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])

        if amount <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "用户ID和积分必须是正整数。"
        )
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    command = update.message.text.split()[0].split("@")[0]

    con = db()
    cur = con.cursor()

    if command == "/addpoints":
        cur.execute(
            "UPDATE users SET points=points+? WHERE user_id=?",
            (amount, target_id)
        )

        message = f"已为用户 {target_id} 增加 {amount} 积分。"

    else:
        cur.execute(
            "UPDATE users SET points=MAX(0, points-?) WHERE user_id=?",
            (amount, target_id)
        )

        message = f"已为用户 {target_id} 扣除 {amount} 积分。"

    con.commit()
    con.close()

    await update.message.reply_text(message)


# =========================
# 消息处理
# =========================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    register_user(user)

    user_id = user.id
    text = message.text or ""

    # 管理员回复双向联系消息
    if user_id == ADMIN_ID and message.reply_to_message:
        con = db()
        cur = con.cursor()

        cur.execute(
            "SELECT user_id FROM replies WHERE admin_message_id=?",
            (message.reply_to_message.message_id,)
        )

        result = cur.fetchone()
        con.close()

        if result:
            target_id = result[0]

            try:
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id
                )

                await message.reply_text("✅ 已回复用户。")

            except Exception as e:
                await message.reply_text(f"发送失败：{e}")

            return

    # 用户提交支付凭证
    if user_id != ADMIN_ID and user_id in payment_waiting:
        try:
            sent = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            plan = context.user_data.get("selected_plan", "未知套餐")
            method = context.user_data.get("payment_method", "未知方式")

            notice = (
                "💳 VIP支付申请\n"
                f"{user_id_line(user_id)}\n"
                f"姓名：{user.first_name or '未设置'}\n"
                f"用户名：@{user.username if user.username else '未设置'}\n"
                f"套餐：{plan}\n"
                f"支付方式：{method}\n"
                f"凭证消息ID：{sent.message_id}\n\n"
                "核实到账后，请使用：\n"
                f"/approve {user_id} {PLANS.get(plan, (0, 7))[1]}"
            )

            await send_custom(
                context.bot,
                ADMIN_ID,
                notice,
                USER_MARK,
                USER_EMOJI_ID
            )

            payment_waiting.discard(user_id)

            await message.reply_text(
                "✅ 已提交支付凭证，请等待管理员核实到账。"
            )

        except Exception as e:
            await message.reply_text(f"提交失败，请稍后重试：{e}")

        return

    # 用户退出双向联系
    if text == "❌ 退出联系":
        clear_mode(user_id)

        await message.reply_text(
            "已退出双向联系。",
            reply_markup=main_keyboard(user_id)
        )

        return

    # 用户退出菜单
    if text == "❌ 退出菜单":
        clear_mode(user_id)
        context.user_data.clear()

        await message.reply_text(
            "已退出当前功能。",
            reply_markup=main_keyboard(user_id)
        )

        return

    # 切换底部功能
    menu_items = {
        "💬 双向联系",
        "👤 我的资料",
        "🆔 查询用户ID",
        "VIP会员",
        "💎 VIP会员",
        "🖼 NFT查询",
        "📱 +888号码查询",
        "👤 收藏用户名查询",
        "🎁 每日签到",
        "👨‍💻 联系管理员",
    }

    if text in menu_items:
        clear_mode(user_id)
        context.user_data.clear()

        if text == "💬 双向联系":
            user_modes[user_id] = text
            contact_waiting.add(user_id)

            await reply_custom(
                message,
                f"{CONTACT_MARK} 双向联系已开启。\n"
                "你发送的消息会转发给管理员，管理员回复后会转发给你。\n"
                "点击“❌ 退出联系”即可结束。",
                CONTACT_MARK,
                CONTACT_EMOJI_ID
            )

            return

        if text == "👤 我的资料":
            await profile(update, context)
            return

        if text == "🆔 查询用户ID":
            user_modes[user_id] = text

            await message.reply_text(
                "请转发你想查询的 Telegram 用户消息给我。\n"
                "如果对方隐藏了转发来源，可能无法查询。"
            )

            return

        if text in ("VIP会员", "💎 VIP会员"):
            await vip(update, context)
            return

        if text == "🖼 NFT查询":
            await message.reply_text(
                "🖼 NFT查询功能暂未开放真实数据查询。"
            )
            return

        if text == "📱 +888号码查询":
            await message.reply_text(
                "📱 +888号码查询功能暂未开放真实数据查询。"
            )
            return

        if text == "👤 收藏用户名查询":
            await message.reply_text(
                "👤 收藏用户名查询功能暂未开放真实数据查询。"
            )
            return

        if text == "🎁 每日签到":
            await checkin(update, context)
            return

        if text == "👨‍💻 联系管理员":
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "联系管理员",
                        url="https://t.me/kpilx"
                    )
                ]
            ])

            await reply_custom(
                message,
                f"{ADMIN_MARK} 如需帮助，请点击下方按钮联系管理员。",
                ADMIN_MARK,
                ADMIN_EMOJI_ID,
                reply_markup=keyboard
            )

            return

    # 查询转发消息中的用户ID
    if user_modes.get(user_id) == "🆔 查询用户ID":
        origin = message.forward_origin

        if origin and getattr(origin, "type", None) == "user":
            target = origin.sender_user

            result_text = (
                f"✅ 查询成功\n"
                f"姓名：{target.first_name or '未设置'}\n"
                f"用户名：@{target.username if target.username else '未设置'}\n"
                f"{user_id_line(target.id)}"
            )

            await reply_custom(
                message,
                result_text,
                USER_MARK,
                USER_EMOJI_ID
            )

        else:
            await message.reply_text(
                "无法读取这条消息的发送者信息。\n"
                "请尝试转发一条没有隐藏来源的消息。"
            )

        clear_mode(user_id)
        return

    # 双向联系：先发用户身份，再复制原始消息
    if user_id != ADMIN_ID and user_id in contact_waiting:
        try:
            username_text = (
                f"@{user.username}"
                if user.username
                else "未设置"
            )

            header = (
                f"用户名：{username_text}\n"
                f"{USER_MARK} 用户🆔：{user.id}\n"
                f"用户名字：{user.first_name or '未设置'}\n\n"
                "消息内容："
            )

            # 先发送身份资料
            await send_custom(
                context.bot,
                ADMIN_ID,
                header,
                USER_MARK,
                USER_EMOJI_ID
            )

            # 再复制用户原始消息
            sent = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            # 回复映射绑定原始消息副本
            con = db()
            cur = con.cursor()

            cur.execute(
                """
                INSERT OR REPLACE INTO replies
                (admin_message_id, user_id)
                VALUES (?, ?)
                """,
                (sent.message_id, user_id)
            )

            con.commit()
            con.close()

            await message.reply_text("✅ 已发送给管理员。")

        except Exception as e:
            logging.exception("双向联系转发失败")
            await message.reply_text(f"发送失败：{e}")

        return

    # 其他消息
    if user_id != ADMIN_ID:
        await message.reply_text(
            "请选择底部菜单中的功能，或发送 /help 查看帮助。",
            reply_markup=main_keyboard(user_id)
        )


# =========================
# 帮助
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "机器人使用说明：\n\n"
        "/start - 打开机器人\n"
        "/qd - 每日签到\n"
        "/jf - 查询积分\n"
        "/dh - 积分兑换VIP\n"
        "/me - 我的资料\n"
        "/myid - 查询自己的用户ID\n"
        "/help - 使用帮助"
    )


# =========================
# 命令设置
# =========================

async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "打开机器人"),
        BotCommand("qd", "每日签到"),
        BotCommand("jf", "查询积分"),
        BotCommand("dh", "积分兑换VIP"),
        BotCommand("me", "我的资料"),
        BotCommand("myid", "查询用户ID"),
        BotCommand("help", "使用帮助"),
    ]

    admin_commands = user_commands + [
        BotCommand("admin", "管理员菜单"),
        BotCommand("stats", "查看统计"),
        BotCommand("users", "查看所有用户"),
        BotCommand("user", "查看用户资料"),
        BotCommand("approve", "开通VIP"),
        BotCommand("addpoints", "增加积分"),
        BotCommand("delpoints", "扣除积分"),
    ]

    await application.bot.set_my_commands(user_commands)

    await application.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )


# =========================
# 启动
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "请先在 FadeHost 设置 BOT_TOKEN 环境变量。"
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("qd", checkin))
    application.add_handler(CommandHandler("jf", points))
    application.add_handler(CommandHandler("dh", redeem))
    application.add_handler(CommandHandler("me", profile))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("users", users_list))
    application.add_handler(CommandHandler("user", user_info))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("addpoints", change_points))
    application.add_handler(CommandHandler("delpoints", change_points))

    application.add_handler(CallbackQueryHandler(payment_callback))

    application.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_message
    ))

    print("机器人启动成功！")

    application.run_polling()


if __name__ == "__main__":
    main()
