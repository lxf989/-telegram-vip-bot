# app_v5.py
# Python 3.10+
# Dependency: python-telegram-bot==21.6

import os
import re
import sqlite3
import logging
import asyncio
import hashlib
import secrets

from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeChat,
    MessageEntity,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================================================
# 基础配置
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8957110767"))
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

CN_TZ = ZoneInfo("Asia/Shanghai")

USDT_ADDRESS = os.getenv(
    "USDT_ADDRESS",
    "THSbZXqnnLUAzfgYkv9AavBD4YdHTTzNxz",
)

VIP_FLAG_IMAGE = (
    "https://raw.githubusercontent.com/lxf989/"
    "-telegram-vip-bot/main/"
    "1E6D4C30-CAB6-448E-9D9A-2265307D5075.png"
)

VIP_EMOJI_ID = "5235630047959727475"
USER_EMOJI_ID = "6210879471474447673"
CONTACT_EMOJI_ID = "5443038326535759644"
ADMIN_EMOJI_ID = "5330237710655306682"
POINTS_EMOJI_ID = "5408869739982246969"
CHECKIN_EMOJI_ID = "5449800250032143374"
VIP_FEATURE_EMOJI_ID = "5255907722435501318"
NICKNAME_EMOJI_ID = "6235662197276544197"
PROFILE_ID_EMOJI_ID = "6235699516247379290"
USDT_EMOJI_ID = "6213220344614882714"

VIP_MARK = "⭐"
USER_MARK = "⭐"
CONTACT_MARK = "⭐"
ADMIN_MARK = "⭐"
POINTS_MARK = "⭐"
CHECKIN_MARK = "⭐"
VIP_FEATURE_MARK = "⭐"
NICKNAME_MARK = "⭐"
PROFILE_ID_MARK = "⭐"
USDT_MARK = "⭐"

INVITE_REWARD = 1000
WITHDRAW_FEE = 100
MIN_WITHDRAW = 1000

TRON_ADDRESS_PATTERN = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")

PASSWORD_ITERATIONS = 200_000
PASSWORD_LENGTH = 6
PASSWORD_MAX_FAILURES = 5
PASSWORD_LOCK_SECONDS = 15 * 60

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

MENU = [
    ["💬 双向联系", "👤 我的资料"],
    ["🆔 查询用户ID", "💎 VIP会员"],
    ["✈️ VIP功能"],
    ["💰 我的余额", "🔗 邀请好友"],
    ["💸 申请提现"],
    ["🖼 NFT查询", "📱 +888号码查询"],
    ["👤 收藏用户名查询"],
    ["🎁 每日签到", "👨‍💻 联系管理员"],
    ["🔐 设置交易密码"],
    ["❌ 退出菜单"],
]

USER_PAGE_SIZE = 20

user_modes = {}
contact_waiting = set()
payment_waiting = set()
admin_message_targets = {}


# ==================================================
# 时间与金额
# ==================================================

def now_cn():
    return datetime.now(CN_TZ)


def timestamp_cn():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")


def today_cn():
    return now_cn().date().isoformat()


def parse_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ)
    except (ValueError, TypeError):
        return None


def format_usdt(amount):
    return f"{Decimal(amount) / Decimal(100):.2f}"


def parse_usdt(value):
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number <= 0:
            return None
        if number.as_tuple().exponent < -2:
            return None
        cents = int(number * 100)
        if Decimal(cents) != number * 100:
            return None
        return cents
    except (InvalidOperation, ValueError, AttributeError):
        return None


# ==================================================
# 自定义 Emoji
# ==================================================

def custom_entity(text, marker, emoji_id):
    index = text.find(marker)
    if index < 0:
        return []

    offset = len(text[:index].encode("utf-16-le")) // 2
    length = len(marker.encode("utf-16-le")) // 2

    return [
        MessageEntity(
            type=MessageEntity.CUSTOM_EMOJI,
            offset=offset,
            length=length,
            custom_emoji_id=emoji_id,
        )
    ]


def custom_entities_all(text, marker, emoji_id):
    entities = []
    start = 0

    while True:
        index = text.find(marker, start)
        if index < 0:
            break

        offset = len(text[:index].encode("utf-16-le")) // 2
        length = len(marker.encode("utf-16-le")) // 2

        entities.append(
            MessageEntity(
                type=MessageEntity.CUSTOM_EMOJI,
                offset=offset,
                length=length,
                custom_emoji_id=emoji_id,
            )
        )
        start = index + len(marker)

    return entities


async def reply_custom(message, text, marker, emoji_id, **kwargs):
    return await message.reply_text(
        text,
        entities=custom_entity(text, marker, emoji_id),
        **kwargs,
    )


async def send_custom(bot, chat_id, text, marker, emoji_id, **kwargs):
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        entities=custom_entities_all(text, marker, emoji_id),
        **kwargs,
    )


def user_id_line(user_id):
    return f"{USER_MARK} 用户ID：{user_id}"


def usdt_entities(text):
    return custom_entities_all(text, USDT_MARK, USDT_EMOJI_ID)


# ==================================================
# 数据库
# ==================================================

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    con = db()
    cur = con.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT,
        points INTEGER DEFAULT 0,
        vip_until TEXT
    );

    CREATE TABLE IF NOT EXISTS checkins (
        user_id INTEGER,
        check_date TEXT,
        UNIQUE(user_id, check_date)
    );

    CREATE TABLE IF NOT EXISTS replies (
        admin_message_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS balances (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS referrals (
        invited_id INTEGER PRIMARY KEY,
        inviter_id INTEGER NOT NULL,
        reward INTEGER NOT NULL DEFAULT 1000,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        address TEXT NOT NULL,
        amount INTEGER NOT NULL,
        fee INTEGER NOT NULL,
        payout INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT,
        admin_id INTEGER,
        created_at TEXT NOT NULL,
        reviewed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_withdrawals_user
    ON withdrawals(user_id);

    CREATE INDEX IF NOT EXISTS idx_referrals_inviter
    ON referrals(inviter_id);

    CREATE TABLE IF NOT EXISTS trade_passwords (
        user_id INTEGER PRIMARY KEY,
        salt BLOB NOT NULL,
        password_hash BLOB NOT NULL,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT
    );

    CREATE TABLE IF NOT EXISTS vip_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan TEXT NOT NULL,
        days INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        method TEXT NOT NULL,
        proof_message_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        admin_id INTEGER
    );
    """)

    cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        SELECT user_id, 0 FROM users
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
        timestamp_cn(),
    ))

    cur.execute("""
        UPDATE users
        SET username=?, first_name=?
        WHERE user_id=?
    """, (
        user.username or "",
        user.first_name or "",
        user.id,
    ))

    cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
    """, (user.id,))

    con.commit()
    con.close()


def get_user(user_id):
    con = db()
    row = con.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    con.close()
    return row


def get_balance(user_id):
    con = db()
    row = con.execute(
        "SELECT balance FROM balances WHERE user_id=?",
        (user_id,),
    ).fetchone()
    con.close()
    return row[0] if row else 0


def get_invite_count(user_id):
    con = db()
    row = con.execute(
        "SELECT COUNT(*) FROM referrals WHERE inviter_id=?",
        (user_id,),
    ).fetchone()
    con.close()
    return row[0]


def save_reply_message(admin_message_id, user_id):
    con = db()
    con.execute("""
        INSERT OR REPLACE INTO replies(admin_message_id, user_id)
        VALUES (?, ?)
    """, (admin_message_id, user_id))
    con.commit()
    con.close()


def get_reply_user(admin_message_id):
    con = db()
    row = con.execute(
        "SELECT user_id FROM replies WHERE admin_message_id=?",
        (admin_message_id,),
    ).fetchone()
    con.close()
    return row[0] if row else None


# ==================================================
# 交易密码
# ==================================================

def hash_trade_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )


def get_trade_password(user_id):
    con = db()
    row = con.execute("""
        SELECT salt, password_hash, failed_attempts, locked_until
        FROM trade_passwords WHERE user_id=?
    """, (user_id,)).fetchone()
    con.close()
    return row


def has_trade_password(user_id):
    return get_trade_password(user_id) is not None


def set_trade_password(user_id, password):
    salt = secrets.token_bytes(16)
    password_hash = hash_trade_password(password, salt)

    con = db()
    con.execute("""
        INSERT INTO trade_passwords
        (user_id, salt, password_hash, failed_attempts, locked_until)
        VALUES (?, ?, ?, 0, NULL)
        ON CONFLICT(user_id) DO UPDATE SET
            salt=excluded.salt,
            password_hash=excluded.password_hash,
            failed_attempts=0,
            locked_until=NULL
    """, (user_id, salt, password_hash))
    con.commit()
    con.close()


def verify_trade_password(user_id, password):
    row = get_trade_password(user_id)
    if not row:
        return "not_set"

    salt, saved_hash, attempts, locked_until = row
    now = now_cn()

    if locked_until:
        locked_time = parse_datetime(locked_until)
        if locked_time and locked_time > now:
            remaining = int((locked_time - now).total_seconds())
            return ("locked", remaining)

    candidate = hash_trade_password(password, salt)

    if secrets.compare_digest(candidate, saved_hash):
        con = db()
        con.execute("""
            UPDATE trade_passwords
            SET failed_attempts=0, locked_until=NULL
            WHERE user_id=?
        """, (user_id,))
        con.commit()
        con.close()
        return "ok"

    attempts += 1

    if attempts >= PASSWORD_MAX_FAILURES:
        lock_time = now + timedelta(seconds=PASSWORD_LOCK_SECONDS)
        con = db()
        con.execute("""
            UPDATE trade_passwords
            SET failed_attempts=?, locked_until=?
            WHERE user_id=?
        """, (
            attempts,
            lock_time.isoformat(sep=" ", timespec="seconds"),
            user_id,
        ))
        con.commit()
        con.close()
        return ("locked", PASSWORD_LOCK_SECONDS)

    con = db()
    con.execute("""
        UPDATE trade_passwords
        SET failed_attempts=?, locked_until=NULL
        WHERE user_id=?
    """, (attempts, user_id))
    con.commit()
    con.close()

    return ("wrong", PASSWORD_MAX_FAILURES - attempts)


def trade_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="tp_num_1"),
            InlineKeyboardButton("2", callback_data="tp_num_2"),
            InlineKeyboardButton("3", callback_data="tp_num_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="tp_num_4"),
            InlineKeyboardButton("5", callback_data="tp_num_5"),
            InlineKeyboardButton("6", callback_data="tp_num_6"),
        ],
        [
            InlineKeyboardButton("7", callback_data="tp_num_7"),
            InlineKeyboardButton("8", callback_data="tp_num_8"),
            InlineKeyboardButton("9", callback_data="tp_num_9"),
        ],
        [
            InlineKeyboardButton("取消", callback_data="tp_cancel"),
            InlineKeyboardButton("0", callback_data="tp_num_0"),
            InlineKeyboardButton("删除", callback_data="tp_delete"),
        ],
    ])


async def show_trade_prompt(query, context, title=None):
    state = context.user_data.get("trade_state")
    value = context.user_data.get("trade_input", "")

    if not title:
        title = {
            "set_first": "请设置6位数字交易密码",
            "set_confirm": "请再次输入6位数字交易密码",
            "change_old": "请输入当前交易密码",
            "change_new": "请输入新的6位数字交易密码",
            "change_confirm": "请再次输入新的交易密码",
            "withdraw_verify": "请输入交易密码以继续提现",
        }.get(state, "请输入交易密码")

    display = "● " * len(value)
    text = f"🔐 {title}\n\n{display or '尚未输入'}\n\n输入满6位后自动提交。"

    await query.edit_message_text(
        text,
        reply_markup=trade_keyboard(),
    )


async def start_trade_flow(update, context, state):
    context.user_data["trade_state"] = state
    context.user_data["trade_input"] = ""

    await update.effective_message.reply_text(
        "🔐 请使用下面的数字键盘输入。",
        reply_markup=trade_keyboard(),
    )


async def trade_password_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    state = context.user_data.get("trade_state")
    if not state:
        return

    if data == "tp_cancel":
        context.user_data.pop("trade_state", None)
        context.user_data.pop("trade_input", None)
        await query.edit_message_text("已取消交易密码操作。")
        return

    if data == "tp_delete":
        value = context.user_data.get("trade_input", "")
        context.user_data["trade_input"] = value[:-1]
        await show_trade_prompt(query, context)
        return

    if not data.startswith("tp_num_"):
        return

    digit = data.rsplit("_", 1)[1]
    if digit not in "0123456789":
        return

    value = context.user_data.get("trade_input", "")
    if len(value) >= 6:
        return

    value += digit
    context.user_data["trade_input"] = value

    if len(value) < 6:
        await show_trade_prompt(query, context)
        return

    context.user_data["trade_input"] = ""

    if state == "set_first":
        context.user_data["trade_new_password"] = value
        context.user_data["trade_state"] = "set_confirm"
        await show_trade_prompt(query, context)
        return

    if state == "set_confirm":
        first = context.user_data.get("trade_new_password")
        if value != first:
            context.user_data.pop("trade_new_password", None)
            context.user_data["trade_state"] = "set_first"
            await query.edit_message_text(
                "两次输入不一致，请重新设置。",
                reply_markup=trade_keyboard(),
            )
            return

        set_trade_password(user_id, value)
        context.user_data.pop("trade_new_password", None)
        context.user_data.pop("trade_state", None)

        await query.edit_message_text("✅ 交易密码设置成功。")
        return

    if state == "change_old":
        result = verify_trade_password(user_id, value)
        if result == "ok":
            context.user_data["trade_state"] = "change_new"
            await show_trade_prompt(query, context)
        else:
            await handle_password_failure(query, result, context)
        return

    if state == "change_new":
        context.user_data["trade_new_password"] = value
        context.user_data["trade_state"] = "change_confirm"
        await show_trade_prompt(query, context)
        return

    if state == "change_confirm":
        if value != context.user_data.get("trade_new_password"):
            context.user_data.pop("trade_new_password", None)
            context.user_data["trade_state"] = "change_new"
            await query.edit_message_text(
                "两次输入不一致，请重新输入新密码。",
                reply_markup=trade_keyboard(),
            )
            return

        set_trade_password(user_id, value)
        context.user_data.pop("trade_new_password", None)
        context.user_data.pop("trade_state", None)
        await query.edit_message_text("✅ 交易密码修改成功。")
        return

    if state == "withdraw_verify":
        result = verify_trade_password(user_id, value)
        if result == "ok":
            context.user_data.pop("trade_state", None)
            await query.edit_message_text("✅ 验证成功。")
            context.user_data["withdraw_state"] = "address"
            await context.bot.send_message(
                chat_id=user_id,
                text="请发送你的 TRC20 USDT 收款地址。",
            )
        else:
            await handle_password_failure(query, result, context)


async def handle_password_failure(query, result, context):
    if isinstance(result, tuple) and result[0] == "locked":
        seconds = result[1]
        await query.edit_message_text(
            f"❌ 交易密码错误次数过多，已锁定。\n"
            f"请在 {seconds // 60 + 1} 分钟后重试。"
        )
    elif isinstance(result, tuple) and result[0] == "wrong":
        await query.edit_message_text(
            f"❌ 交易密码错误。\n剩余尝试次数：{result[1]}",
            reply_markup=trade_keyboard(),
        )
    elif result == "not_set":
        await query.edit_message_text("你还没有设置交易密码。")


# ==================================================
# 邀请奖励
# ==================================================

def bind_inviter(invited_id, inviter_id):
    if invited_id == inviter_id:
        return False

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (invited_id,),
        )
        if not cur.fetchone():
            con.rollback()
            return False

        cur.execute(
            "SELECT 1 FROM referrals WHERE invited_id=?",
            (invited_id,),
        )
        if cur.fetchone():
            con.rollback()
            return False

        cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (inviter_id,),
        )
        if not cur.fetchone():
            con.rollback()
            return False

        cur.execute("""
            INSERT INTO referrals
            (invited_id, inviter_id, reward, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            invited_id,
            inviter_id,
            INVITE_REWARD,
            timestamp_cn(),
        ))

        cur.execute("""
            UPDATE balances
            SET balance=balance+?
            WHERE user_id=?
        """, (INVITE_REWARD, inviter_id))

        con.commit()
        return True

    except Exception:
        con.rollback()
        logger.exception("邀请奖励处理失败")
        return False
    finally:
        con.close()


# ==================================================
# 菜单与状态
# ==================================================

def main_keyboard(user_id):
    current = user_modes.get(user_id)
    rows = []

    for row in MENU:
        new_row = []
        for button in row:
            new_row.append(button + " ✅" if button == current else button)
        rows.append(new_row)

    if current == "💬 双向联系":
        rows.insert(1, ["❌ 退出联系"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def clear_mode(user_id):
    user_modes.pop(user_id, None)
    contact_waiting.discard(user_id)
    payment_waiting.discard(user_id)


def clear_trade_state(context):
    for key in (
        "trade_state",
        "trade_input",
        "trade_new_password",
    ):
        context.user_data.pop(key, None)


def clear_withdraw_state(context):
    for key in (
        "withdraw_state",
        "withdraw_address",
    ):
        context.user_data.pop(key, None)


# ==================================================
# 余额与邀请
# ==================================================

async def show_balance(update, context):
    user = update.effective_user
    register_user(user)

    balance = get_balance(user.id)
    invited = get_invite_count(user.id)

    text = (
        f"{NICKNAME_MARK} 昵称：{user.first_name or '未设置'}\n"
        f"{USER_MARK} 用户ID：{user.id}\n"
        f"{USDT_MARK} USDT余额：{format_usdt(balance)} USDT\n\n"
        f"成功邀请人数：{invited}"
    )

    entities = (
        custom_entity(text, NICKNAME_MARK, NICKNAME_EMOJI_ID)
        + custom_entity(text, USER_MARK, USER_EMOJI_ID)
        + custom_entity(text, USDT_MARK, USDT_EMOJI_ID)
    )

    await update.effective_message.reply_text(text, entities=entities)


async def show_invite(update, context):
    user = update.effective_user
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{user.id}"

    text = (
        "🔗 专属邀请链接\n\n"
        f"{link}\n\n"
        "每成功邀请一位新用户，可获得10 USDT。\n"
        f"当前成功邀请人数：{get_invite_count(user.id)}\n"
        "奖励仅发放一次，不可重复领取。"
    )
    await update.effective_message.reply_text(text)


# ==================================================
# 提现
# ==================================================

def withdraw_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ 取消提现", callback_data="withdraw_cancel")]
    ])


async def begin_withdraw(update, context):
    user = update.effective_user
    register_user(user)

    if get_balance(user.id) < MIN_WITHDRAW:
        text = (
            f"{USDT_MARK} 当前余额：{format_usdt(get_balance(user.id))} USDT\n"
            "最低提现金额为10 USDT。"
        )
        await update.effective_message.reply_text(
            text,
            entities=usdt_entities(text),
        )
        return

    if not has_trade_password(user.id):
        await update.effective_message.reply_text(
            "你还没有设置交易密码，请先设置交易密码。"
        )
        await start_trade_flow(update, context, "set_first")
        context.user_data["after_trade_setup"] = "withdraw"
        return

    await start_trade_flow(update, context, "withdraw_verify")


async def create_withdrawal(update, context):
    user = update.effective_user
    address = context.user_data.get("withdraw_address")
    amount = parse_usdt(update.effective_message.text or "")

    if not address:
        clear_withdraw_state(context)
        await update.effective_message.reply_text("提现地址已失效，请重新申请。")
        return

    if amount is None or amount < MIN_WITHDRAW:
        await update.effective_message.reply_text(
            "金额格式不正确或低于最低提现金额10 USDT，请重新输入。"
        )
        return

    payout = amount - WITHDRAW_FEE
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
            UPDATE balances
            SET balance=balance-?
            WHERE user_id=? AND balance>=?
        """, (amount, user.id, amount))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text(
                f"余额不足，目前余额：{format_usdt(get_balance(user.id))} USDT。"
            )
            return

        cur.execute("""
            INSERT INTO withdrawals
            (user_id, address, amount, fee, payout, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user.id,
            address,
            amount,
            WITHDRAW_FEE,
            payout,
            timestamp_cn(),
        ))

        withdrawal_id = cur.lastrowid
        con.commit()

    except Exception:
        con.rollback()
        logger.exception("创建提现申请失败")
        await update.effective_message.reply_text("提现申请失败，请稍后重试。")
        return
    finally:
        con.close()

    clear_withdraw_state(context)

    text = (
        "💸 新的USDT提现申请\n\n"
        f"申请编号：#{withdrawal_id}\n"
        f"用户：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username or '未设置'}\n"
        f"用户ID：{user.id}\n\n"
        f"TRC20地址：{address}\n"
        f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n"
        f"申请时间：{timestamp_cn()}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 同意提现", callback_data=f"withdraw_approve_{withdrawal_id}")],
        [InlineKeyboardButton("❌ 拒绝提现", callback_data=f"withdraw_reject_{withdrawal_id}")],
    ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            entities=usdt_entities(text),
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("通知管理员提现失败")

        con = db()
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            UPDATE withdrawals SET status='notification_failed'
            WHERE id=? AND status='pending'
        """, (withdrawal_id,))
        if cur.rowcount == 1:
            cur.execute(
                "UPDATE balances SET balance=balance+? WHERE user_id=?",
                (amount, user.id),
            )
        con.commit()
        con.close()

        await update.effective_message.reply_text(
            "通知管理员失败，申请已撤销并退回余额。"
        )
        return

    text = (
        "✅ 提现申请已提交，等待管理员审核。\n\n"
        f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n\n"
        "审核期间申请金额已暂时冻结。"
    )

    await update.effective_message.reply_text(text, entities=usdt_entities(text))


async def withdraw_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data != "withdraw_cancel" and user_id != ADMIN_ID:
        await query.answer("你没有权限操作。", show_alert=True)
        return

    await query.answer()

    if data == "withdraw_cancel":
        clear_withdraw_state(context)
        await query.edit_message_text("已取消本次提现申请。")
        return

    try:
        withdrawal_id = int(data.rsplit("_", 1)[1])
    except ValueError:
        return

    con = db()
    cur = con.cursor()

    if data.startswith("withdraw_approve_"):
        try:
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute("""
                SELECT user_id, amount, fee, payout, status
                FROM withdrawals WHERE id=?
            """, (withdrawal_id,)).fetchone()

            if not row or row[4] != "pending":
                con.rollback()
                await query.answer("申请不存在或已经处理。", show_alert=True)
                return

            target_id, amount, fee, payout, _ = row

            cur.execute("""
                UPDATE withdrawals
                SET status='approved', admin_id=?, reviewed_at=?
                WHERE id=? AND status='pending'
            """, (ADMIN_ID, timestamp_cn(), withdrawal_id))

            if cur.rowcount != 1:
                con.rollback()
                return

            con.commit()

        except Exception:
            con.rollback()
            logger.exception("提现审核失败")
            return
        finally:
            con.close()

        await query.edit_message_text(
            query.message.text + "\n\n✅ 审核通过，等待管理员手动转账。",
            reply_markup=None,
        )

        text = (
            "✅ 你的提现申请已经审核通过。\n"
            f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
            f"{USDT_MARK} 手续费：{format_usdt(fee)} USDT\n"
            f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n"
            "请等待管理员完成链上转账。"
        )
        try:
            await context.bot.send_message(
                target_id,
                text,
                entities=usdt_entities(text),
            )
        except Exception:
            logger.exception("提现通过通知失败")
        return

    if data.startswith("withdraw_reject_"):
        row = cur.execute(
            "SELECT status FROM withdrawals WHERE id=?",
            (withdrawal_id,),
        ).fetchone()
        con.close()

        if not row or row[0] != "pending":
            await query.answer("申请不存在或已经处理。", show_alert=True)
            return

        context.user_data["reject_withdraw_id"] = withdrawal_id
        await query.edit_message_text(
            query.message.text + "\n\n请直接发送拒绝理由。\n发送 /cancelmsg 取消。"
        )


async def finish_withdraw_reject(update, context):
    withdrawal_id = context.user_data.get("reject_withdraw_id")
    if not withdrawal_id:
        return False

    reason = (update.effective_message.text or "").strip()
    if not reason:
        await update.effective_message.reply_text("拒绝理由不能为空。")
        return True

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        row = cur.execute("""
            SELECT user_id, amount, status
            FROM withdrawals WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row or row[2] != "pending":
            con.rollback()
            await update.effective_message.reply_text("申请不存在或已处理。")
            return True

        target_id, amount, _ = row

        cur.execute("""
            UPDATE withdrawals
            SET status='rejected', reason=?, admin_id=?, reviewed_at=?
            WHERE id=? AND status='pending'
        """, (reason, ADMIN_ID, timestamp_cn(), withdrawal_id))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text("申请状态已改变。")
            return True

        cur.execute("""
            UPDATE balances SET balance=balance+?
            WHERE user_id=?
        """, (amount, target_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("提现拒绝退款失败")
        await update.effective_message.reply_text("退款处理失败。")
        return True
    finally:
        con.close()

    context.user_data.pop("reject_withdraw_id", None)

    await update.effective_message.reply_text(
        f"已拒绝提现申请 #{withdrawal_id}，并退回 {format_usdt(amount)} USDT。"
    )

    text = (
        "❌ 你的提现申请未通过。\n"
        f"拒绝理由：{reason}\n"
        f"{USDT_MARK} 已退回余额：{format_usdt(amount)} USDT"
    )

    try:
        await context.bot.send_message(
            target_id,
            text,
            entities=usdt_entities(text),
        )
    except Exception:
        logger.exception("通知用户提现拒绝失败")

    return True


async def withdraw_address_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if query.from_user.id != user_id:
        await query.answer()
        return

    await query.answer()

    if query.data == "withdraw_address_ok":
        if context.user_data.get("withdraw_state") != "confirm_address":
            await query.edit_message_text("地址确认状态已失效。")
            return

        context.user_data["withdraw_state"] = "amount"
        balance = get_balance(user_id)

        text = (
            f"{USDT_MARK} 当前余额：{format_usdt(balance)} USDT\n\n"
            "请输入本次提现金额。\n"
            "最低提现金额：10 USDT\n"
            "手续费：1 USDT\n"
            "例如申请20 USDT，实际到账19 USDT。"
        )
        await query.edit_message_text(text, entities=usdt_entities(text))
        return

    if query.data == "withdraw_address_retry":
        context.user_data["withdraw_state"] = "address"
        context.user_data.pop("withdraw_address", None)
        await query.edit_message_text("请重新发送TRC20 USDT收款地址。")


# ==================================================
# 签到、积分、VIP
# ==================================================

async def checkin(update, context):
    user = update.effective_user
    register_user(user)

    con = db()
    try:
        con.execute(
            "INSERT INTO checkins(user_id, check_date) VALUES (?, ?)",
            (user.id, today_cn()),
        )
        con.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?",
            (user.id,),
        )
        con.commit()
        row = get_user(user.id)
        await update.effective_message.reply_text(
            f"🎁 签到成功！获得1积分。\n当前积分：{row[4]}"
        )
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text("今天已经签到过了，明天再来吧！")
    finally:
        con.close()


async def points(update, context):
    register_user(update.effective_user)
    row = get_user(update.effective_user.id)

    text = (
        f"{POINTS_MARK} 当前积分：{row[4]}\n"
        f"{CHECKIN_MARK} 每日签到可获得1积分。\n"
        f"{VIP_MARK} 满18积分可兑换30天VIP。"
    )
    entities = (
        custom_entity(text, POINTS_MARK, POINTS_EMOJI_ID)
        + custom_entity(text, CHECKIN_MARK, CHECKIN_EMOJI_ID)
        + custom_entity(text, VIP_MARK, VIP_EMOJI_ID)
    )
    await update.effective_message.reply_text(text, entities=entities)


def extend_vip(user_id, days):
    row = get_user(user_id)
    now = now_cn()

    if row and row[5]:
        old = parse_datetime(row[5])
        if old and old > now:
            now = old

    expiry = now + timedelta(days=days)
    value = expiry.isoformat(sep=" ", timespec="seconds")

    con = db()
    con.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (value, user_id),
    )
    con.commit()
    con.close()
    return value


async def redeem(update, context):
    user_id = update.effective_user.id
    register_user(update.effective_user)
    row = get_user(user_id)

    if row[4] < 18:
        await update.effective_message.reply_text(
            f"积分不足，目前有{row[4]}积分，兑换需要18积分。"
        )
        return

    con = db()
    cur = con.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute(
        "UPDATE users SET points=points-18 WHERE user_id=? AND points>=18",
        (user_id,),
    )
    if cur.rowcount != 1:
        con.rollback()
        con.close()
        await update.effective_message.reply_text("积分不足，请重试。")
        return
    con.commit()
    con.close()

    expiry = extend_vip(user_id, 30)
    text = f"{VIP_MARK} 兑换成功！已开通30天VIP。\n到期时间：{expiry}"
    await reply_custom(
        update.effective_message,
        text,
        VIP_MARK,
        VIP_EMOJI_ID,
    )


async def profile(update, context):
    user = update.effective_user
    register_user(user)
    row = get_user(user.id)

    username = f"@{user.username}" if user.username else "未设置"
    text = (
        "👤 个人资料\n\n"
        f"姓名：{user.first_name or '未设置'}\n"
        f"用户名：{username}\n"
        f"{user_id_line(user.id)}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}"
    )
    await reply_custom(
        update.effective_message,
        text,
        USER_MARK,
        USER_EMOJI_ID,
    )


async def myid(update, context):
    text = f"你的Telegram用户ID：\n{user_id_line(update.effective_user.id)}"
    await reply_custom(
        update.effective_message,
        text,
        USER_MARK,
        USER_EMOJI_ID,
    )


async def vip(update, context):
    text = f"{VIP_MARK} 请选择VIP套餐："
    await reply_custom(
        update.effective_message,
        text,
        VIP_MARK,
        VIP_EMOJI_ID,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("7天 VIP · 1 USDT", callback_data="plan_7天")],
            [InlineKeyboardButton("30天 VIP · 2 USDT", callback_data="plan_30天")],
            [InlineKeyboardButton("90天 VIP · 5 USDT", callback_data="plan_90天")],
            [InlineKeyboardButton("365天 VIP · 10 USDT", callback_data="plan_365天")],
        ]),
    )


async def vip_feature(update, context):
    row = get_user(update.effective_user.id)

    if not row or not row[5]:
        await update.effective_message.reply_text("❌ 此功能仅限VIP会员使用，请先开通VIP。")
        return

    expiry = parse_datetime(row[5])
    if not expiry or expiry <= now_cn():
        await update.effective_message.reply_text("❌ 你的VIP已到期，请续费后使用。")
        return

    caption = f"{VIP_FEATURE_MARK} 你好，欢迎使用VIP功能，感谢支持！"
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=VIP_FLAG_IMAGE,
        caption=caption,
        caption_entities=custom_entity(
            caption,
            VIP_FEATURE_MARK,
            VIP_FEATURE_EMOJI_ID,
        ),
    )


# ==================================================
# VIP支付
# ==================================================

def vip_plan_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("7天 VIP · 1 USDT", callback_data="plan_7天")],
        [InlineKeyboardButton("30天 VIP · 2 USDT", callback_data="plan_30天")],
        [InlineKeyboardButton("90天 VIP · 5 USDT", callback_data="plan_90天")],
        [InlineKeyboardButton("365天 VIP · 10 USDT", callback_data="plan_365天")],
    ])


def vip_method_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("TRC20（USDT）", callback_data="pay_trc20")],
        [InlineKeyboardButton("OKPay", callback_data="pay_okpay")],
        [InlineKeyboardButton("返回套餐", callback_data="pay_back_plan")],
    ])


def vip_order_keyboard(plan, method):
    rows = []

    if method == "OKPay":
        rows.append([
            InlineKeyboardButton("前往OKPay支付", url=OKPAY_LINKS[plan])
        ])

    rows.extend([
        [InlineKeyboardButton("📋 我已支付", callback_data="paid")],
        [InlineKeyboardButton("返回支付方式", callback_data="pay_back_method")],
        [InlineKeyboardButton("取消支付", callback_data="pay_cancel")],
    ])
    return InlineKeyboardMarkup(rows)


def clear_payment_state(user_id, context):
    payment_waiting.discard(user_id)
    for key in (
        "selected_plan",
        "payment_method",
        "payment_order_active",
        "payment_menu_message_id",
        "payment_menu_chat_id",
    ):
        context.user_data.pop(key, None)


async def payment_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data.startswith("plan_"):
        plan = data.replace("plan_", "", 1)
        if plan not in PLANS:
            return

        clear_payment_state(user_id, context)
        context.user_data["selected_plan"] = plan

        await query.edit_message_text(
            f"已选择{plan} VIP。\n请选择支付方式：",
            reply_markup=vip_method_keyboard(),
        )
        return

    if data == "pay_back_plan":
        clear_payment_state(user_id, context)
        await query.edit_message_text(
            f"{VIP_MARK} 请选择VIP套餐：",
            entities=custom_entity(
                f"{VIP_MARK} 请选择VIP套餐：",
                VIP_MARK,
                VIP_EMOJI_ID,
            ),
            reply_markup=vip_plan_keyboard(),
        )
        return

    if data == "pay_back_method":
        payment_waiting.discard(user_id)
        context.user_data.pop("payment_method", None)
        context.user_data.pop("payment_order_active", None)
        plan = context.user_data.get("selected_plan")

        if not plan:
            await query.edit_message_text(
                "请选择VIP套餐：",
                reply_markup=vip_plan_keyboard(),
            )
            return

        await query.edit_message_text(
            f"已选择{plan} VIP。\n请选择支付方式：",
            reply_markup=vip_method_keyboard(),
        )
        return

    if data == "pay_cancel":
        clear_payment_state(user_id, context)
        await query.edit_message_text(
            "已取消本次支付。\n请选择VIP套餐：",
            reply_markup=vip_plan_keyboard(),
        )
        return

    if data in ("pay_trc20", "pay_okpay"):
        plan = context.user_data.get("selected_plan")
        if plan not in PLANS:
            await query.edit_message_text(
                "套餐信息已失效，请重新选择。",
                reply_markup=vip_plan_keyboard(),
            )
            return

        method = "TRC20" if data == "pay_trc20" else "OKPay"
        context.user_data["payment_method"] = method
        context.user_data["payment_order_active"] = True

        amount, days = PLANS[plan]

        if method == "TRC20":
            text = (
                f"{VIP_MARK} 套餐：{plan} VIP\n"
                f"金额：{amount} USDT\n\n"
                "请使用TRC20网络转账至：\n"
                f"{USDT_ADDRESS}\n\n"
                "支付完成后点击“我已支付”提交凭证。"
            )
        else:
            text = (
                f"{VIP_MARK} 套餐：{plan} VIP\n"
                f"金额：{amount} USDT\n\n"
                "点击下方按钮前往OKPay支付，完成后回来提交凭证。"
            )

        await query.edit_message_text(
            text,
            entities=custom_entities_all(text, VIP_MARK, VIP_EMOJI_ID),
            reply_markup=vip_order_keyboard(plan, method),
        )
        return

    if data == "paid":
        plan = context.user_data.get("selected_plan")
        method = context.user_data.get("payment_method")

        if not plan or not method or not context.user_data.get("payment_order_active"):
            await query.answer("支付订单不存在，请重新选择。", show_alert=True)
            return

        payment_waiting.add(user_id)
        await query.edit_message_text(
            f"请发送你的{plan} VIP支付凭证、截图或交易TXID。\n"
            "管理员核实到账后会为你开通VIP。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("取消支付", callback_data="pay_cancel")]
            ]),
        )


# ==================================================
# 管理员余额管理
# ==================================================

async def change_balance(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/addbalance 用户ID 金额\n/delbalance 用户ID 金额"
        )
        return

    try:
        target_id = int(context.args[0])
        amount = parse_usdt(context.args[1])
        if target_id <= 0 or amount is None:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("用户ID或金额格式不正确。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("没有找到这个用户。")
        return

    command = update.effective_message.text.split()[0].split("@")[0]
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        if command == "/addbalance":
            cur.execute(
                "UPDATE balances SET balance=balance+? WHERE user_id=?",
                (amount, target_id),
            )
            operation = "增加"
        else:
            cur.execute("""
                UPDATE balances SET balance=balance-?
                WHERE user_id=? AND balance>=?
            """, (amount, target_id, amount))

            if cur.rowcount != 1:
                con.rollback()
                await update.effective_message.reply_text("用户余额不足，无法扣除。")
                return
            operation = "扣除"

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("余额调整失败")
        await update.effective_message.reply_text("余额调整失败。")
        return
    finally:
        con.close()

    balance = get_balance(target_id)
    await update.effective_message.reply_text(
        f"已为用户{target_id}{operation}{format_usdt(amount)} USDT。\n"
        f"当前余额：{format_usdt(balance)} USDT"
    )

    text = (
        f"管理员已{operation}你的USDT余额。\n"
        f"{USDT_MARK} 调整金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 当前余额：{format_usdt(balance)} USDT"
    )

    try:
        await context.bot.send_message(
            target_id,
            text,
            entities=usdt_entities(text),
        )
    except Exception:
        logger.exception("通知用户余额调整失败")


# ==================================================
# 管理员命令
# ==================================================

def is_admin(update):
    return update.effective_user and update.effective_user.id == ADMIN_ID


async def admin_command(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限使用此命令。")
        return

    await update.effective_message.reply_text(
        "管理员命令：\n"
        "/stats - 查看统计\n"
        "/users - 查看用户\n"
        "/user 用户ID - 查看用户资料\n"
        "/approve 用户ID 天数 - 开通VIP\n"
        "/reducevip 用户ID 天数 - 减少VIP\n"
        "/clearvip 用户ID - 清空VIP\n"
        "/addpoints 用户ID 积分 - 增加积分\n"
        "/delpoints 用户ID 积分 - 扣除积分\n"
        "/addbalance 用户ID 金额 - 增加余额\n"
        "/delbalance 用户ID 金额 - 扣除余额\n"
        "/sendusers - 主动联系用户\n"
        "/cancelmsg - 取消操作"
    )


async def stats(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    con = db()
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    vip_total = cur.execute(
        "SELECT COUNT(*) FROM users WHERE vip_until IS NOT NULL"
    ).fetchone()[0]
    pending = cur.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE status='pending'"
    ).fetchone()[0]
    balance = cur.execute(
        "SELECT COALESCE(SUM(balance),0) FROM balances"
    ).fetchone()[0]
    con.close()

    await update.effective_message.reply_text(
        f"📊 机器人统计\n用户总数：{total}\n"
        f"已开通VIP：{vip_total}\n待审核提现：{pending}\n"
        f"USDT余额总计：{format_usdt(balance)} USDT"
    )


async def users_list(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    con = db()
    rows = con.execute("""
        SELECT user_id, username, first_name
        FROM users ORDER BY first_seen ASC, user_id ASC
    """).fetchall()
    con.close()

    if not rows:
        await update.effective_message.reply_text("目前没有用户。")
        return

    parts = []
    current = ""

    for index, (user_id, username, first_name) in enumerate(rows, 1):
        item = (
            f"{index}. 用户名：@{username or '未设置'}\n"
            f"姓名：{first_name or '未设置'}\n"
            f"{user_id_line(user_id)}\n\n"
        )
        if len(current) + len(item) > 3500:
            parts.append(current)
            current = ""
        current += item

    if current:
        parts.append(current)

    for part in parts:
        await send_custom(
            context.bot,
            update.effective_chat.id,
            part,
            USER_MARK,
            USER_EMOJI_ID,
        )


async def user_info(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if not context.args:
        await update.effective_message.reply_text("用法：/user 用户ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID必须是数字。")
        return

    row = get_user(target_id)
    if not row:
        await update.effective_message.reply_text("没有找到这个用户。")
        return

    text = (
        f"{user_id_line(row[0])}\n"
        f"用户名：@{row[1] or '未设置'}\n"
        f"姓名：{row[2] or '未设置'}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}\n"
        f"{USDT_MARK} USDT余额：{format_usdt(get_balance(target_id))}"
    )

    entities = (
        custom_entity(text, USER_MARK, USER_EMOJI_ID)
        + custom_entity(text, USDT_MARK, USDT_EMOJI_ID)
    )
    await update.effective_message.reply_text(text, entities=entities)


async def approve(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("用法：/approve 用户ID 天数")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        if target_id <= 0 or days <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("用户ID和天数必须是正整数。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("没有找到这个用户。")
        return

    expiry = extend_vip(target_id, days)
    await update.effective_message.reply_text(
        f"已为用户{target_id}开通{days}天VIP。\n到期时间：{expiry}"
    )

    try:
        text = f"{VIP_MARK} 管理员已为你开通{days}天VIP！\n到期时间：{expiry}"
        await send_custom(
            context.bot,
            target_id,
            text,
            VIP_MARK,
            VIP_EMOJI_ID,
        )
    except Exception:
        logger.exception("通知VIP开通失败")


async def reduce_vip(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("用法：/reducevip 用户ID 天数")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        if target_id <= 0 or days <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("参数必须为正整数。")
        return

    row = get_user(target_id)
    if not row or not row[5]:
        await update.effective_message.reply_text("用户不存在或没有VIP。")
        return

    expiry = parse_datetime(row[5])
    if not expiry:
        await update.effective_message.reply_text("VIP到期时间异常。")
        return

    new_expiry = expiry - timedelta(days=days)
    value = None if new_expiry <= now_cn() else new_expiry.isoformat(
        sep=" ",
        timespec="seconds",
    )

    con = db()
    con.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (value, target_id),
    )
    con.commit()
    con.close()

    result = "VIP已到期并清空。" if value is None else f"新的到期时间：{value}"
    await update.effective_message.reply_text(result)

    try:
        await context.bot.send_message(
            target_id,
            f"管理员已扣除你的VIP {days}天。\n{result}",
        )
    except Exception:
        logger.exception("通知VIP调整失败")


async def clear_vip(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if not context.args:
        await update.effective_message.reply_text("用法：/clearvip 用户ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID必须是数字。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("没有找到这个用户。")
        return

    con = db()
    con.execute("UPDATE users SET vip_until=NULL WHERE user_id=?", (target_id,))
    con.commit()
    con.close()

    await update.effective_message.reply_text("已清空该用户VIP。")
    try:
        await context.bot.send_message(target_id, "管理员已清空你的VIP会员资格。")
    except Exception:
        logger.exception("通知用户清空VIP失败")


async def change_points(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("用法：/addpoints 或 /delpoints 用户ID 积分")
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        if target_id <= 0 or amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("参数必须为正整数。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("没有找到这个用户。")
        return

    command = update.effective_message.text.split()[0].split("@")[0]
    con = db()

    if command == "/addpoints":
        con.execute(
            "UPDATE users SET points=points+? WHERE user_id=?",
            (amount, target_id),
        )
        result = f"已增加{amount}积分。"
    else:
        con.execute(
            "UPDATE users SET points=MAX(0, points-?) WHERE user_id=?",
            (amount, target_id),
        )
        result = f"已扣除{amount}积分。"

    con.commit()
    con.close()
    await update.effective_message.reply_text(result)


# ==================================================
# VIP支付凭证处理
# ==================================================

async def submit_payment_proof(update, context):
    user = update.effective_user
    plan = context.user_data.get("selected_plan")
    method = context.user_data.get("payment_method")

    if not plan or plan not in PLANS or not method:
        payment_waiting.discard(user.id)
        return False

    amount, days = PLANS[plan]
    message = update.effective_message

    try:
        copied = await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )

        con = db()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO vip_payments
            (user_id, plan, days, amount, method, proof_message_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user.id,
            plan,
            days,
            amount * 100,
            method,
            copied.message_id,
            timestamp_cn(),
        ))
        payment_id = cur.lastrowid
        con.commit()
        con.close()

        notice = (
            "💳 VIP支付申请\n\n"
            f"订单编号：#{payment_id}\n"
            f"{user_id_line(user.id)}\n"
            f"姓名：{user.first_name or '未设置'}\n"
            f"用户名：@{user.username or '未设置'}\n"
            f"套餐：{plan}\n"
            f"支付方式：{method}\n"
            f"金额：{amount} USDT\n"
            f"凭证消息ID：{copied.message_id}\n\n"
            f"核实到账后使用：\n/approve {user.id} {days}"
        )

        await send_custom(
            context.bot,
            ADMIN_ID,
            notice,
            USER_MARK,
            USER_EMOJI_ID,
        )

        payment_waiting.discard(user.id)
        context.user_data.pop("payment_order_active", None)

        await message.reply_text("✅ 已提交支付凭证，请等待管理员核实到账。")
        return True

    except Exception:
        logger.exception("提交VIP支付凭证失败")
        await message.reply_text("提交失败，请稍后重试。")
        return True


# ==================================================
# 管理员主动联系用户
# ==================================================

def get_users_page(page):
    con = db()
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    rows = cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        ORDER BY first_seen ASC, user_id ASC
        LIMIT ? OFFSET ?
    """, (USER_PAGE_SIZE, page * USER_PAGE_SIZE)).fetchall()
    con.close()
    return rows, total


async def show_admin_user_page(message, page):
    rows, total = get_users_page(page)
    if not total:
        await message.reply_text("目前没有用户。")
        return

    keyboard = []
    for user_id, username, first_name in rows:
        keyboard.append([
            InlineKeyboardButton(
                f"{first_name or '未设置'} | @{username or '无用户名'} | {user_id}",
                callback_data=f"msg_user_{user_id}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"msg_page_{page - 1}"))
    if (page + 1) * USER_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"msg_page_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("❌ 退出", callback_data="msg_exit")])

    await message.reply_text(
        f"请选择要发送消息的用户。\n用户总数：{total}\n当前页：{page + 1}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def open_admin_user_list(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return
    await show_admin_user_page(update.effective_message, 0)


async def admin_send_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("你没有权限。", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "msg_exit":
        admin_message_targets.pop(ADMIN_ID, None)
        await query.edit_message_text("已退出主动发送消息。")
        return

    if data.startswith("msg_page_"):
        try:
            page = int(data.rsplit("_", 1)[1])
        except ValueError:
            return
        await query.edit_message_text("正在切换页面……")
        await show_admin_user_page(query.message, page)
        return

    if data.startswith("msg_user_"):
        try:
            target_id = int(data.rsplit("_", 1)[1])
        except ValueError:
            return

        if not get_user(target_id):
            await query.message.reply_text("该用户不存在。")
            return

        admin_message_targets[ADMIN_ID] = target_id
        await query.message.reply_text(
            f"已选择用户：{target_id}\n"
            "现在直接发送文字、图片、视频或文件。\n"
            "发送 /cancelmsg 取消。"
        )


async def send_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        return False

    target_id = admin_message_targets.get(ADMIN_ID)
    if not target_id:
        return False

    message = update.effective_message

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        await message.reply_text(f"✅ 消息已发送给用户{target_id}。")
    except Exception:
        logger.exception("管理员主动发送消息失败")
        await message.reply_text("发送失败，用户可能无法接收消息。")

    return True


async def cancel_admin_message(update, context):
    if not is_admin(update):
        await update.effective_message.reply_text("你没有权限。")
        return

    if context.user_data.get("reject_withdraw_id"):
        context.user_data.pop("reject_withdraw_id", None)
        await update.effective_message.reply_text("已取消提现拒绝操作。")
        return

    admin_message_targets.pop(ADMIN_ID, None)
    await update.effective_message.reply_text("已取消主动发送消息。")


# ==================================================
# 双向联系
# ==================================================

async def forward_contact_message(update, context):
    message = update.effective_message
    user = update.effective_user

    header = (
        f"用户名：@{user.username or '未设置'}\n"
        f"{USER_MARK} 用户ID：{user.id}\n"
        f"用户名字：{user.first_name or '未设置'}\n\n"
        "消息内容："
    )

    try:
        if message.text:
            combined = header + "\n" + message.text
            sent = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=combined,
                entities=custom_entities_all(
                    combined,
                    USER_MARK,
                    USER_EMOJI_ID,
                ),
            )
            save_reply_message(sent.message_id, user.id)

        elif message.caption:
            combined_caption = header + "\n" + message.caption
            sent = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                caption=combined_caption,
                caption_entities=custom_entities_all(
                    combined_caption,
                    USER_MARK,
                    USER_EMOJI_ID,
                ),
            )
            save_reply_message(sent.message_id, user.id)

        else:
            header_message = await send_custom(
                context.bot,
                ADMIN_ID,
                header,
                USER_MARK,
                USER_EMOJI_ID,
            )
            copied = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                reply_to_message_id=header_message.message_id,
            )
            save_reply_message(header_message.message_id, user.id)
            save_reply_message(copied.message_id, user.id)

        notice = await message.reply_text("✅ 已发送给管理员。")
        await asyncio.sleep(1)
        try:
            await notice.delete()
        except Exception:
            pass

    except Exception:
        logger.exception("双向联系转发失败")
        await message.reply_text("发送失败，请稍后重试。")


async def admin_reply_contact(update, context):
    message = update.effective_message
    if not message.reply_to_message:
        return False

    target_id = get_reply_user(message.reply_to_message.message_id)
    if not target_id:
        return False

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        await message.reply_text("✅ 已回复用户。")
    except Exception:
        logger.exception("管理员回复用户失败")
        await message.reply_text("回复失败，用户可能无法接收消息。")

    return True


# ==================================================
# 开始与帮助
# ==================================================

async def start(update, context):
    user = update.effective_user
    old_user = get_user(user.id)

    register_user(user)
    clear_mode(user.id)
    clear_withdraw_state(context)
    clear_trade_state(context)
    clear_payment_state(user.id, context)

    if not old_user and context.args:
        payload = context.args[0]
        if payload.startswith("ref_"):
            try:
                inviter_id = int(payload[4:])
                if bind_inviter(user.id, inviter_id):
                    text = (
                        "🎉 邀请成功！\n"
                        "你邀请的新用户已经加入。\n"
                        f"{USDT_MARK} 获得奖励：10 USDT"
                    )
                    try:
                        await context.bot.send_message(
                            inviter_id,
                            text,
                            entities=usdt_entities(text),
                        )
                    except Exception:
                        logger.exception("通知邀请人失败")
            except ValueError:
                pass

    await update.effective_message.reply_text(
        "欢迎使用VIP机器人！\n请选择底部功能菜单。",
        reply_markup=main_keyboard(user.id),
    )


async def help_command(update, context):
    await update.effective_message.reply_text(
        "机器人使用说明：\n\n"
        "/start - 打开机器人\n"
        "/qd - 每日签到\n"
        "/jf - 查询积分\n"
        "/dh - 积分兑换VIP\n"
        "/me - 我的资料\n"
        "/myid - 查询自己的用户ID\n"
        "/help - 使用帮助"
    )


# ==================================================
# 统一消息处理
# ==================================================

async def handle_message(update, context):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    register_user(user)
    user_id = user.id
    text = message.text or ""

    # 管理员拒绝提现理由
    if user_id == ADMIN_ID and context.user_data.get("reject_withdraw_id"):
        if text.startswith("/"):
            return
        await finish_withdraw_reject(update, context)
        return

    # 管理员主动发送优先级低于管理员回复
    if user_id == ADMIN_ID and message.reply_to_message:
        if await admin_reply_contact(update, context):
            return

    if user_id == ADMIN_ID and admin_message_targets.get(ADMIN_ID):
        if await send_admin_message(update, context):
            return

    # 用户提交支付凭证
    if user_id != ADMIN_ID and user_id in payment_waiting:
        await submit_payment_proof(update, context)
        return

    # 提现地址输入
    if user_id != ADMIN_ID and context.user_data.get("withdraw_state") == "address":
        address = text.strip()

        if not TRON_ADDRESS_PATTERN.fullmatch(address):
            await message.reply_text(
                "❌ TRC20地址格式不正确，请检查是否为T开头的34位地址。"
            )
            return

        context.user_data["withdraw_address"] = address
        context.user_data["withdraw_state"] = "confirm_address"

        await message.reply_text(
            f"请核对你的TRC20收款地址：\n\n"
            f"<code>{address}</code>\n\n"
            "确认无误后点击下面按钮。",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ 地址正确，继续", callback_data="withdraw_address_ok")],
                [InlineKeyboardButton("🔄 重新输入", callback_data="withdraw_address_retry")],
                [InlineKeyboardButton("❌ 取消提现", callback_data="withdraw_cancel")],
            ]),
        )
        return

    # 提现金额
    if user_id != ADMIN_ID and context.user_data.get("withdraw_state") == "amount":
        await create_withdrawal(update, context)
        return

    # 菜单退出
    if text == "❌ 退出联系":
        clear_mode(user_id)
        await message.reply_text(
            "已退出双向联系。",
            reply_markup=main_keyboard(user_id),
        )
        return

    if text == "❌ 退出菜单":
        clear_mode(user_id)
        clear_withdraw_state(context)
        clear_trade_state(context)
        clear_payment_state(user_id, context)
        await message.reply_text(
            "已退出当前功能。",
            reply_markup=main_keyboard(user_id),
        )
        return

    menu_items = {
        "💬 双向联系",
        "👤 我的资料",
        "🆔 查询用户ID",
        "🆔 查询用户ID ✅",
        "💎 VIP会员",
        "💎 VIP会员 ✅",
        "✈️ VIP功能",
        "✈️ VIP功能 ✅",
        "💰 我的余额",
        "🔗 邀请好友",
        "💸 申请提现",
        "🖼 NFT查询",
        "📱 +888号码查询",
        "👤 收藏用户名查询",
        "🎁 每日签到",
        "👨‍💻 联系管理员",
        "🔐 设置交易密码",
        "🔐 设置交易密码 ✅",
    }

    if text in menu_items:
        clear_mode(user_id)
        clear_withdraw_state(context)
        clear_trade_state(context)
        clear_payment_state(user_id, context)

        if text == "💬 双向联系":
            user_modes[user_id] = "💬 双向联系"
            contact_waiting.add(user_id)

            await reply_custom(
                message,
                f"{CONTACT_MARK} 双向联系已开启。\n"
                "你发送的文字或媒体会转发给管理员，管理员回复后会转发给你。\n"
                "点击“退出联系”结束。",
                CONTACT_MARK,
                CONTACT_EMOJI_ID,
                reply_markup=main_keyboard(user_id),
            )
            return

        if text == "👤 我的资料":
            await profile(update, context)
            return

        if text.startswith("🆔 查询用户ID"):
            user_modes[user_id] = "🆔 查询用户ID"
            await message.reply_text(
                "请转发你想查询的Telegram用户消息给我。"
            )
            return

        if text.startswith("💎 VIP会员"):
            await vip(update, context)
            return

        if text.startswith("✈️ VIP功能"):
            await vip_feature(update, context)
            return

        if text == "💰 我的余额":
            await show_balance(update, context)
            return

        if text == "🔗 邀请好友":
            await show_invite(update, context)
            return

        if text == "💸 申请提现":
            await begin_withdraw(update, context)
            return

        if text == "🖼 NFT查询":
            await message.reply_text("NFT查询功能暂未开放真实数据查询。")
            return

        if text == "📱 +888号码查询":
            await message.reply_text("+888号码查询功能暂未开放真实数据查询。")
            return

        if text == "👤 收藏用户名查询":
            await message.reply_text("收藏用户名查询功能暂未开放真实数据查询。")
            return

        if text == "🎁 每日签到":
            await checkin(update, context)
            return

        if text == "👨‍💻 联系管理员":
            await reply_custom(
                message,
                f"{ADMIN_MARK} 如需帮助，请点击下方按钮联系管理员。",
                ADMIN_MARK,
                ADMIN_EMOJI_ID,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("联系管理员", url="https://t.me/kpilx")]
                ]),
            )
            return

        if text.startswith("🔐 设置交易密码"):
            if has_trade_password(user_id):
                await message.reply_text(
                    "你已经设置过交易密码，请输入当前密码后设置新密码。",
                )
                await start_trade_flow(update, context, "change_old")
            else:
                await start_trade_flow(update, context, "set_first")
            return

    # 设置密码后自动继续提现
    if context.user_data.get("after_trade_setup") == "withdraw":
        return

    # 查询转发来源ID
    if user_modes.get(user_id) == "🆔 查询用户ID":
        origin = message.forward_origin
        if origin and getattr(origin, "type", None) == "user":
            target = origin.sender_user
            result = (
                "✅ 查询成功\n"
                f"姓名：{target.first_name or '未设置'}\n"
                f"用户名：@{target.username or '未设置'}\n"
                f"{user_id_line(target.id)}"
            )
            await reply_custom(
                message,
                result,
                USER_MARK,
                USER_EMOJI_ID,
            )
        else:
            await message.reply_text("无法读取发送者信息，请转发一条未隐藏来源的消息。")
        clear_mode(user_id)
        return

    # 双向联系
    if user_id != ADMIN_ID and user_id in contact_waiting:
        await forward_contact_message(update, context)
        return

    if user_id != ADMIN_ID:
        await message.reply_text(
            "请选择底部菜单中的功能，或发送/help查看帮助。",
            reply_markup=main_keyboard(user_id),
        )


# ==================================================
# 命令注册
# ==================================================

async def post_init(application):
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
        BotCommand("reducevip", "减少VIP天数"),
        BotCommand("clearvip", "清空VIP"),
        BotCommand("addpoints", "增加积分"),
        BotCommand("delpoints", "扣除积分"),
        BotCommand("addbalance", "增加USDT余额"),
        BotCommand("delbalance", "扣除USDT余额"),
        BotCommand("sendusers", "分页选择用户发送消息"),
        BotCommand("cancelmsg", "取消操作"),
    ]

    await application.bot.set_my_commands(user_commands)
    await application.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(chat_id=ADMIN_ID),
    )


# ==================================================
# 启动
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("请先设置BOT_TOKEN环境变量。")

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
    application.add_handler(CommandHandler("reducevip", reduce_vip))
    application.add_handler(CommandHandler("clearvip", clear_vip))
    application.add_handler(CommandHandler("addpoints", change_points))
    application.add_handler(CommandHandler("delpoints", change_points))
    application.add_handler(CommandHandler("addbalance", change_balance))
    application.add_handler(CommandHandler("delbalance", change_balance))
    application.add_handler(CommandHandler("sendusers", open_admin_user_list))
    application.add_handler(CommandHandler("cancelmsg", cancel_admin_message))

    application.add_handler(
        CallbackQueryHandler(admin_send_callback, pattern=r"^msg_")
    )
    application.add_handler(
        CallbackQueryHandler(withdraw_callback, pattern=r"^withdraw_(?:approve_|reject_|cancel$)")
    )
    application.add_handler(
        CallbackQueryHandler(withdraw_address_callback, pattern=r"^withdraw_address_(?:ok|retry)$")
    )
    application.add_handler(
        CallbackQueryHandler(payment_callback, pattern=r"^(?:plan_|pay_|paid$)")
    )
    application.add_handler(
        CallbackQueryHandler(trade_password_callback, pattern=r"^tp_")
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("机器人启动成功")
    application.run_polling()


if __name__ == "__main__":
    main()