import os
import re
import sqlite3
import logging
import asyncio
import hashlib
import secrets

from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8957110767"))

USDT_ADDRESS = os.getenv(
    "USDT_ADDRESS",
    "THSbZXqnnLUAzfgYkv9AavBD4YdHTTzNxz"
)

DB_PATH = os.getenv("DB_PATH", "bot_data.db")

VIP_FLAG_IMAGE = (
    "https://raw.githubusercontent.com/lxf989/"
    "-telegram-vip-bot/main/"
    "1E6D4C30-CAB6-448E-9D9A-2265307D5075.png"
)

SG_TZ = ZoneInfo("Asia/Shanghai")

INVITE_REWARD = 1000
WITHDRAW_FEE = 100
MIN_WITHDRAW = 1000

TRON_ADDRESS_PATTERN = re.compile(
    r"^T[1-9A-HJ-NP-Za-km-z]{33}$"
)

PASSWORD_ITERATIONS = 200000
PASSWORD_LENGTH = 6
PASSWORD_MAX_FAILURES = 5
PASSWORD_LOCK_MINUTES = 15

USER_PAGE_SIZE = 20

# ==================================================
# 高级表情
# ==================================================

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

# ==================================================
# 菜单
# ==================================================

MENU = [
    ["💬 双向联系", "👤 我的资料"],
    ["🆔 查询用户ID", "💎 VIP会员"],
    ["✈️ VIP功能"],
    ["💰 我的余额", "🔗 邀请好友"],
    ["💸 申请提现"],
    ["🔐 设置交易密码"],
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

# ==================================================
# 状态变量
# ==================================================

user_modes = {}
contact_waiting = set()
payment_waiting = set()
admin_message_targets = {}


# ==================================================
# 新加坡时间工具
# ==================================================

def now_sg():
    return datetime.now(SG_TZ)


def sg_timestamp():
    return now_sg().isoformat(sep=" ", timespec="seconds")


def format_sg_time(value):
    """
    新数据保存为带 +08:00 的新加坡时间。
    旧数据如果没有时区信息，按原始时间显示，
    不擅自增加或减少八小时，避免历史时间被错误修改。
    """
    if not value:
        return "未知"

    try:
        parsed = datetime.fromisoformat(str(value))

        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")

        return parsed.astimezone(SG_TZ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except (ValueError, TypeError):
        return str(value)


def parse_sg_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SG_TZ)
        else:
            parsed = parsed.astimezone(SG_TZ)

        return parsed

    except (ValueError, TypeError):
        return None


# ==================================================
# 高级表情工具
# ==================================================

def custom_entity(text, marker, emoji_id):
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
                type="custom_emoji",
                offset=offset,
                length=length,
                custom_emoji_id=emoji_id
            )
        )

        start = index + len(marker)

    return entities


async def reply_custom(message, text, marker, emoji_id, **kwargs):
    return await message.reply_text(
        text,
        entities=custom_entity(text, marker, emoji_id),
        **kwargs
    )


async def send_custom(bot, chat_id, text, marker, emoji_id, **kwargs):
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        entities=custom_entities_all(text, marker, emoji_id),
        **kwargs
    )


def user_id_line(user_id):
    return f"{USER_MARK} 用户ID：{user_id}"


def usdt_entities(text):
    return custom_entities_all(
        text,
        USDT_MARK,
        USDT_EMOJI_ID
    )


def format_usdt(amount):
    return f"{Decimal(amount) / Decimal(100):.2f}"


def parse_usdt(value):
    try:
        number = Decimal(value.strip())

        if not number.is_finite() or number <= 0:
            return None

        if number.as_tuple().exponent < -2:
            return None

        cents = int(number * 100)

        if Decimal(cents) != number * 100:
            return None

        return cents

    except (
        InvalidOperation,
        ValueError,
        AttributeError
    ):
        return None


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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        invited_id INTEGER PRIMARY KEY,
        inviter_id INTEGER NOT NULL,
        reward INTEGER NOT NULL DEFAULT 1000,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
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
    )
    """)

    # 交易密码独立保存，绝不保存明文密码
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_passwords (
        user_id INTEGER PRIMARY KEY,
        salt BLOB NOT NULL,
        password_hash TEXT NOT NULL,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_withdrawals_user
    ON withdrawals(user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_referrals_inviter
    ON referrals(inviter_id)
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
        sg_timestamp()
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

    cur.execute("""
    INSERT OR IGNORE INTO balances(user_id, balance)
    VALUES (?, 0)
    """, (user.id,))

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


def save_reply_message(admin_message_id, user_id):
    con = db()
    cur = con.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO replies
    (admin_message_id, user_id)
    VALUES (?, ?)
    """, (admin_message_id, user_id))

    con.commit()
    con.close()


def get_reply_user(admin_message_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT user_id FROM replies WHERE admin_message_id=?",
        (admin_message_id,)
    )

    result = cur.fetchone()
    con.close()

    return result[0] if result else None


# ==================================================
# 交易密码工具
# ==================================================

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS
    ).hex()


def has_trade_password(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT 1 FROM trade_passwords WHERE user_id=?",
        (user_id,)
    )

    result = cur.fetchone()
    con.close()

    return bool(result)


def save_trade_password(user_id, password):
    salt = secrets.token_bytes(16)
    hashed = password_hash(password, salt)

    con = db()

    cur = con.cursor()

    cur.execute("""
    INSERT INTO trade_passwords
    (user_id, salt, password_hash, failed_attempts, locked_until)
    VALUES (?, ?, ?, 0, NULL)
    ON CONFLICT(user_id) DO UPDATE SET
        salt=excluded.salt,
        password_hash=excluded.password_hash,
        failed_attempts=0,
        locked_until=NULL
    """, (
        user_id,
        salt,
        hashed
    ))

    con.commit()
    con.close()


def get_password_lock(user_id):
    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT failed_attempts, locked_until
    FROM trade_passwords
    WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()
    con.close()

    if not row:
        return 0, None

    attempts, locked_until = row
    lock_time = parse_sg_datetime(locked_until)

    if lock_time and lock_time > now_sg():
        return attempts, lock_time

    return attempts, None


def verify_trade_password(user_id, password):
    con = db()
    cur = con.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT salt, password_hash, failed_attempts, locked_until
        FROM trade_passwords
        WHERE user_id=?
        """, (user_id,))

        row = cur.fetchone()

        if not row:
            con.rollback()
            return "missing", None

        salt, saved_hash, attempts, locked_until = row

        lock_time = parse_sg_datetime(locked_until)

        if lock_time and lock_time > now_sg():
            con.rollback()
            return "locked", lock_time

        calculated = password_hash(password, salt)

        if secrets.compare_digest(calculated, saved_hash):
            cur.execute("""
            UPDATE trade_passwords
            SET failed_attempts=0, locked_until=NULL
            WHERE user_id=?
            """, (user_id,))

            con.commit()
            return "success", None

        attempts += 1

        if attempts >= PASSWORD_MAX_FAILURES:
            lock_time = now_sg() + timedelta(
                minutes=PASSWORD_LOCK_MINUTES
            )

            cur.execute("""
            UPDATE trade_passwords
            SET failed_attempts=?, locked_until=?
            WHERE user_id=?
            """, (
                attempts,
                lock_time.isoformat(sep=" ", timespec="seconds"),
                user_id
            ))

            con.commit()
            return "locked", lock_time

        cur.execute("""
        UPDATE trade_passwords
        SET failed_attempts=?, locked_until=NULL
        WHERE user_id=?
        """, (attempts, user_id))

        con.commit()

        return "wrong", PASSWORD_MAX_FAILURES - attempts

    except Exception:
        con.rollback()
        logger.exception("交易密码验证失败")
        return "error", None

    finally:
        con.close()


def trade_password_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="trade_digit_1"),
            InlineKeyboardButton("2", callback_data="trade_digit_2"),
            InlineKeyboardButton("3", callback_data="trade_digit_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="trade_digit_4"),
            InlineKeyboardButton("5", callback_data="trade_digit_5"),
            InlineKeyboardButton("6", callback_data="trade_digit_6"),
        ],
        [
            InlineKeyboardButton("7", callback_data="trade_digit_7"),
            InlineKeyboardButton("8", callback_data="trade_digit_8"),
            InlineKeyboardButton("9", callback_data="trade_digit_9"),
        ],
        [
            InlineKeyboardButton("取消", callback_data="trade_cancel"),
            InlineKeyboardButton("0", callback_data="trade_digit_0"),
            InlineKeyboardButton("删除", callback_data="trade_delete"),
        ],
    ])


def trade_progress(length):
    return "●" * length + "○" * (PASSWORD_LENGTH - length)


def trade_prompt(flow, length=0, extra=""):
    if flow == "set_first":
        title = "请设置六位数字交易密码"
    elif flow == "set_confirm":
        title = "请再次输入六位数字交易密码进行确认"
    elif flow == "change_verify":
        title = "请输入当前交易密码"
    elif flow == "withdraw_verify":
        title = "提现前请验证交易密码"
    else:
        title = "请输入交易密码"

    text = (
        f"🔐 {title}\n\n"
        f"{trade_progress(length)}\n\n"
        "请使用下方数字按钮输入。"
    )

    if extra:
        text += f"\n\n{extra}"

    return text


async def start_trade_flow(
    update,
    context,
    flow,
    after_setup=None
):
    user_id = update.effective_user.id

    attempts, lock_time = get_password_lock(user_id)

    if lock_time:
        await update.effective_message.reply_text(
            "交易密码已暂时锁定。\n"
            f"请在 {lock_time.strftime('%Y-%m-%d %H:%M:%S')} 后重试。"
        )
        return

    context.user_data["trade_flow"] = flow
    context.user_data["trade_digits"] = ""
    context.user_data.pop("trade_first", None)

    if after_setup:
        context.user_data["after_trade_setup"] = after_setup

    sent = await update.effective_message.reply_text(
        trade_prompt(flow),
        reply_markup=trade_password_keyboard()
    )

    context.user_data["trade_message_id"] = sent.message_id
    context.user_data["trade_chat_id"] = sent.chat_id


async def trade_password_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    flow = context.user_data.get("trade_flow")
    buffer = context.user_data.get("trade_digits", "")

    if not flow:
        await query.answer("密码输入状态已失效。", show_alert=True)
        return

    if query.message.chat_id != context.user_data.get("trade_chat_id"):
        await query.answer("输入消息无效。", show_alert=True)
        return

    if query.message.message_id != context.user_data.get("trade_message_id"):
        await query.answer("输入消息已失效。", show_alert=True)
        return

    if data == "trade_cancel":
        await query.answer()

        for key in (
            "trade_flow",
            "trade_digits",
            "trade_first",
            "trade_message_id",
            "trade_chat_id",
            "after_trade_setup"
        ):
            context.user_data.pop(key, None)

        try:
            await query.message.delete()
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="已取消交易密码操作。"
        )
        return

    if data == "trade_delete":
        await query.answer()

        buffer = buffer[:-1]

        context.user_data["trade_digits"] = buffer

        await query.edit_message_text(
            trade_prompt(flow, len(buffer)),
            reply_markup=trade_password_keyboard()
        )
        return

    if data.startswith("trade_digit_"):
        digit = data.rsplit("_", 1)[1]

        if digit not in "0123456789":
            await query.answer("无效数字。")
            return

        if len(buffer) >= PASSWORD_LENGTH:
            await query.answer()
            return

        await query.answer()

        buffer += digit
        context.user_data["trade_digits"] = buffer

        if len(buffer) < PASSWORD_LENGTH:
            await query.edit_message_text(
                trade_prompt(flow, len(buffer)),
                reply_markup=trade_password_keyboard()
            )
            return

        # 输入满六位，自动处理
        if flow == "set_first":
            context.user_data["trade_first"] = buffer
            context.user_data["trade_digits"] = ""
            context.user_data["trade_flow"] = "set_confirm"

            await query.edit_message_text(
                trade_prompt("set_confirm", 0),
                reply_markup=trade_password_keyboard()
            )
            return

        if flow == "set_confirm":
            first = context.user_data.get("trade_first")

            if not first or not secrets.compare_digest(first, buffer):
                context.user_data["trade_first"] = None
                context.user_data["trade_digits"] = ""
                context.user_data["trade_flow"] = "set_first"

                await query.edit_message_text(
                    trade_prompt(
                        "set_first",
                        0,
                        "两次输入不一致，请重新设置。"
                    ),
                    reply_markup=trade_password_keyboard()
                )
                return

            save_trade_password(user_id, buffer)

            after_setup = context.user_data.pop(
                "after_trade_setup",
                None
            )

            for key in (
                "trade_flow",
                "trade_digits",
                "trade_first",
                "trade_message_id",
                "trade_chat_id"
            ):
                context.user_data.pop(key, None)

            try:
                await query.message.delete()
            except Exception:
                pass

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ 交易密码设置成功，密码已安全保存。"
            )

            if after_setup == "withdraw":
                context.user_data["withdraw_state"] = "address"

                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        "请发送你的 TRC20 USDT 收款地址。\n\n"
                        "请确认使用 TRON/TRC20 网络，地址通常以 T 开头。"
                    )
                )

            return

        if flow == "change_verify":
            status, detail = verify_trade_password(user_id, buffer)

            if status == "success":
                context.user_data["trade_flow"] = "set_first"
                context.user_data["trade_digits"] = ""
                context.user_data.pop("trade_first", None)

                await query.edit_message_text(
                    trade_prompt(
                        "set_first",
                        0,
                        "验证成功，请输入新的交易密码。"
                    ),
                    reply_markup=trade_password_keyboard()
                )
                return

            context.user_data["trade_digits"] = ""

            if status == "wrong":
                extra = f"交易密码错误，还可尝试 {detail} 次。"
            elif status == "locked":
                extra = (
                    "输入错误次数过多，已锁定至："
                    f"{detail.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                extra = "验证失败，请稍后重试。"

            if status == "locked":
                for key in (
                    "trade_flow",
                    "trade_digits",
                    "trade_message_id",
                    "trade_chat_id"
                ):
                    context.user_data.pop(key, None)

                await query.edit_message_text(extra)
                return

            await query.edit_message_text(
                trade_prompt("change_verify", 0, extra),
                reply_markup=trade_password_keyboard()
            )
            return

        if flow == "withdraw_verify":
            status, detail = verify_trade_password(user_id, buffer)

            if status == "success":
                context.user_data.pop("trade_flow", None)
                context.user_data.pop("trade_digits", None)
                context.user_data.pop("trade_message_id", None)
                context.user_data.pop("trade_chat_id", None)

                context.user_data["withdraw_state"] = "address"

                await query.edit_message_text(
                    "✅ 交易密码验证成功。\n\n"
                    "请发送你的 TRC20 USDT 收款地址。"
                )
                return

            context.user_data["trade_digits"] = ""

            if status == "wrong":
                extra = f"交易密码错误，还可尝试 {detail} 次。"
            elif status == "locked":
                extra = (
                    "输错次数过多，已锁定15分钟。\n"
                    f"解锁时间：{detail.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                extra = "验证失败，请稍后重试。"

            if status == "locked":
                for key in (
                    "trade_flow",
                    "trade_digits",
                    "trade_message_id",
                    "trade_chat_id"
                ):
                    context.user_data.pop(key, None)

                await query.edit_message_text(extra)
                return

            await query.edit_message_text(
                trade_prompt("withdraw_verify", 0, extra),
                reply_markup=trade_password_keyboard()
            )
            return


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
            "SELECT first_seen FROM users WHERE user_id=?",
            (invited_id,)
        )

        if not cur.fetchone():
            con.rollback()
            return False

        cur.execute(
            "SELECT 1 FROM referrals WHERE invited_id=?",
            (invited_id,)
        )

        if cur.fetchone():
            con.rollback()
            return False

        cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (inviter_id,)
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
            sg_timestamp()
        ))

        cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
        """, (inviter_id,))

        cur.execute("""
        UPDATE balances
        SET balance=balance+?
        WHERE user_id=?
        """, (INVITE_REWARD, inviter_id))

        con.commit()
        return True

    except sqlite3.IntegrityError:
        con.rollback()
        return False

    except Exception:
        con.rollback()
        logger.exception("邀请奖励发放失败")
        return False

    finally:
        con.close()


def get_balance(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT balance FROM balances WHERE user_id=?",
        (user_id,)
    )

    row = cur.fetchone()
    con.close()

    return row[0] if row else 0


def get_invite_count(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM referrals WHERE inviter_id=?",
        (user_id,)
    )

    count = cur.fetchone()[0]
    con.close()

    return count


# ==================================================
# 余额查看
# ==================================================

async def show_balance(update, context):
    user = update.effective_user
    register_user(user)

    balance = get_balance(user.id)
    invited = get_invite_count(user.id)

    text = (
        f"{NICKNAME_MARK} 昵称：{user.first_name or '未设置'}\n"
        f"{PROFILE_ID_MARK} ID：{user.id}\n"
        f"{USDT_MARK} USDT余额：{format_usdt(balance)} USDT\n\n"
        f"成功邀请人数：{invited}"
    )

    entities = (
        custom_entity(text, NICKNAME_MARK, NICKNAME_EMOJI_ID)
        + custom_entity(text, PROFILE_ID_MARK, PROFILE_ID_EMOJI_ID)
        + custom_entity(text, USDT_MARK, USDT_EMOJI_ID)
    )

    await update.effective_message.reply_text(
        text,
        entities=entities
    )


# ==================================================
# 邀请好友
# ==================================================

async def show_invite(update, context):
    user = update.effective_user
    bot = await context.bot.get_me()

    invite_link = f"https://t.me/{bot.username}?start=ref_{user.id}"
    invited = get_invite_count(user.id)

    text = (
        "🔗 专属邀请链接\n\n"
        f"{invite_link}\n\n"
        "每成功邀请一位新用户，可获得 10 USDT。\n"
        f"当前成功邀请人数：{invited}\n\n"
        "奖励仅发放一次，不可重复领取。"
    )

    await update.effective_message.reply_text(text)


# ==================================================
# 提现流程
# ==================================================

def withdraw_cancel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ 取消提现",
                callback_data="withdraw_cancel"
            )
        ]
    ])


async def begin_withdraw(update, context):
    user = update.effective_user
    user_id = user.id

    register_user(user)

    balance = get_balance(user_id)

    if balance < MIN_WITHDRAW:
        text = (
            f"{USDT_MARK} 当前余额：{format_usdt(balance)} USDT\n"
            "最低提现金额为 10 USDT。"
        )

        await update.effective_message.reply_text(
            text,
            entities=usdt_entities(text)
        )
        return

    if not has_trade_password(user_id):
        await start_trade_flow(
            update,
            context,
            "set_first",
            after_setup="withdraw"
        )
        return

    await start_trade_flow(
        update,
        context,
        "withdraw_verify"
    )


async def create_withdrawal(update, context):
    user = update.effective_user
    user_id = user.id

    address = context.user_data.get("withdraw_address")

    if not address:
        await update.effective_message.reply_text(
            "提现地址已失效，请重新申请。"
        )
        context.user_data.pop("withdraw_state", None)
        return

    amount = parse_usdt(update.effective_message.text or "")

    if amount is None:
        await update.effective_message.reply_text(
            "金额格式不正确，请输入有效的 USDT 金额，例如：10 或 20.50。"
        )
        return

    if amount < MIN_WITHDRAW:
        await update.effective_message.reply_text(
            "最低提现金额为 10 USDT，请重新输入。"
        )
        return

    if amount <= WITHDRAW_FEE:
        await update.effective_message.reply_text(
            "申请金额必须大于手续费。"
        )
        return

    payout = amount - WITHDRAW_FEE
    created_at = sg_timestamp()

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        UPDATE balances
        SET balance=balance-?
        WHERE user_id=? AND balance>=?
        """, (amount, user_id, amount))

        if cur.rowcount != 1:
            con.rollback()

            current = get_balance(user_id)

            await update.effective_message.reply_text(
                f"余额不足，目前余额：{format_usdt(current)} USDT。"
            )
            return

        cur.execute("""
        INSERT INTO withdrawals
        (user_id, address, amount, fee, payout, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user_id,
            address,
            amount,
            WITHDRAW_FEE,
            payout,
            created_at
        ))

        withdrawal_id = cur.lastrowid
        con.commit()

    except Exception:
        con.rollback()
        logger.exception("创建提现申请失败")
        await update.effective_message.reply_text(
            "提现申请失败，请稍后重试。"
        )
        return

    finally:
        con.close()

    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)

    text = (
        "💸 新的 USDT 提现申请\n\n"
        f"申请编号：#{withdrawal_id}\n"
        f"用户：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username if user.username else '未设置'}\n"
        f"{user_id_line(user_id)}\n\n"
        f"TRC20地址：{address}\n\n"
        f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n\n"
        f"申请时间：{format_sg_time(created_at)}"
    )

    entities = (
        custom_entities_all(text, USDT_MARK, USDT_EMOJI_ID)
        + custom_entities_all(text, USER_MARK, USER_EMOJI_ID)
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ 同意提现",
                callback_data=f"withdraw_approve_{withdrawal_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 拒绝提现",
                callback_data=f"withdraw_reject_{withdrawal_id}"
            )
        ]
    ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            entities=entities,
            reply_markup=keyboard
        )

    except Exception:
        logger.exception("发送提现申请给管理员失败")

        con = db()

        try:
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")

            cur.execute("""
            UPDATE withdrawals
            SET status='notification_failed'
            WHERE id=? AND status='pending'
            """, (withdrawal_id,))

            if cur.rowcount == 1:
                cur.execute("""
                UPDATE balances
                SET balance=balance+?
                WHERE user_id=?
                """, (amount, user_id))

            con.commit()

        except Exception:
            con.rollback()
            logger.exception("提现通知失败后的退款处理失败")

        finally:
            con.close()

        await update.effective_message.reply_text(
            "提现通知管理员失败，申请已撤销，余额已退回。"
        )
        return

    text = (
        "✅ 提现申请已提交，等待管理员审核。\n\n"
        f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n\n"
        "审核期间申请金额已暂时冻结。"
    )

    await update.effective_message.reply_text(
        text,
        entities=usdt_entities(text)
    )
    
    # ==================================================
# 提现地址确认及回调
# ==================================================

async def withdraw_address_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "withdraw_address_confirm":
        address = context.user_data.get("withdraw_address")

        if not address:
            await query.edit_message_text("提现地址已失效，请重新申请。")
            return

        context.user_data["withdraw_state"] = "amount"

        await query.edit_message_text(
            "✅ 地址确认成功。\n\n"
            f"收款地址：\n{address}\n\n"
            "请输入提现金额（USDT）。\n"
            "最低提现金额：10 USDT。"
        )

    elif query.data == "withdraw_address_retry":
        context.user_data.pop("withdraw_address", None)
        context.user_data["withdraw_state"] = "address"

        await query.edit_message_text(
            "请重新发送你的 TRC20 USDT 收款地址。"
        )


async def withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("无权操作。", show_alert=True)
        return

    data = query.data

    if data == "withdraw_cancel":
        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)
        await query.edit_message_text("已取消提现。")
        return

    if data.startswith("withdraw_approve_"):
        withdrawal_id = int(data.rsplit("_", 1)[1])

        con = db()

        try:
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")

            cur.execute("""
            SELECT user_id, amount, payout, status
            FROM withdrawals
            WHERE id=?
            """, (withdrawal_id,))

            row = cur.fetchone()

            if not row:
                con.rollback()
                await query.edit_message_text("提现记录不存在。")
                return

            user_id, amount, payout, status = row

            if status != "pending":
                con.rollback()
                await query.edit_message_text(
                    f"该提现申请已经处理，当前状态：{status}"
                )
                return

            reviewed_at = sg_timestamp()

            cur.execute("""
            UPDATE withdrawals
            SET status='approved',
                admin_id=?,
                reviewed_at=?
            WHERE id=? AND status='pending'
            """, (
                ADMIN_ID,
                reviewed_at,
                withdrawal_id
            ))

            if cur.rowcount != 1:
                con.rollback()
                await query.edit_message_text("该申请已被处理。")
                return

            con.commit()

        except Exception:
            con.rollback()
            logger.exception("审核提现失败")
            await query.edit_message_text("审核失败，请稍后重试。")
            return

        finally:
            con.close()

        await query.edit_message_text(
            "✅ 提现申请已审核通过。\n\n"
            "注意：审核通过不代表链上转账已经完成，"
            "请管理员人工完成转账。"
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ 你的提现申请已审核通过。\n\n"
                    f"申请编号：#{withdrawal_id}\n"
                    f"申请金额：{format_usdt(amount)} USDT\n"
                    f"预计到账：{format_usdt(payout)} USDT\n\n"
                    "审核通过不代表链上转账已完成，"
                    "请等待实际转账。"
                )
            )
        except Exception:
            logger.exception("通知用户提现审核通过失败")

        return

    if data.startswith("withdraw_reject_"):
        withdrawal_id = int(data.rsplit("_", 1)[1])

        con = db()
        cur = con.cursor()

        cur.execute("""
        SELECT status
        FROM withdrawals
        WHERE id=?
        """, (withdrawal_id,))

        row = cur.fetchone()
        con.close()

        if not row:
            await query.edit_message_text("提现记录不存在。")
            return

        if row[0] != "pending":
            await query.edit_message_text("该申请已经处理。")
            return

        context.user_data["reject_withdrawal_id"] = withdrawal_id

        await query.edit_message_text(
            f"请输入拒绝提现申请 #{withdrawal_id} 的理由："
        )


async def finish_withdraw_reject(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    withdrawal_id = context.user_data.get("reject_withdrawal_id")

    if not withdrawal_id:
        return

    reason = (update.effective_message.text or "").strip()

    if not reason:
        await update.effective_message.reply_text("请填写拒绝理由。")
        return

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT user_id, amount, status
        FROM withdrawals
        WHERE id=?
        """, (withdrawal_id,))

        row = cur.fetchone()

        if not row:
            con.rollback()
            await update.effective_message.reply_text("提现记录不存在。")
            return

        user_id, amount, status = row

        if status != "pending":
            con.rollback()
            await update.effective_message.reply_text("该申请已经处理。")
            return

        reviewed_at = sg_timestamp()

        cur.execute("""
        UPDATE withdrawals
        SET status='rejected',
            reason=?,
            admin_id=?,
            reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            reason,
            ADMIN_ID,
            reviewed_at,
            withdrawal_id
        ))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text("处理失败，该申请可能已被处理。")
            return

        cur.execute("""
        UPDATE balances
        SET balance=balance+?
        WHERE user_id=?
        """, (amount, user_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("拒绝提现失败")
        await update.effective_message.reply_text("处理失败，请稍后重试。")
        return

    finally:
        con.close()

    context.user_data.pop("reject_withdrawal_id", None)

    await update.effective_message.reply_text(
        f"已拒绝提现申请 #{withdrawal_id}，申请金额已退还用户。"
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ 你的提现申请未通过审核。\n\n"
                f"申请编号：#{withdrawal_id}\n"
                f"拒绝理由：{reason}\n\n"
                f"已退还：{format_usdt(amount)} USDT"
            )
        )
    except Exception:
        logger.exception("通知用户提现拒绝失败")


# ==================================================
# 管理员余额调整
# ==================================================

async def change_balance(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    parts = (update.effective_message.text or "").split()

    if len(parts) != 3:
        await update.effective_message.reply_text(
            "用法：\n"
            "/addbalance 用户ID 金额\n"
            "/delbalance 用户ID 金额"
        )
        return

    command = parts[0].split("@")[0].lower()

    try:
        target_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式不正确。")
        return

    amount = parse_usdt(parts[2])

    if amount is None:
        await update.effective_message.reply_text("金额格式不正确。")
        return

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
        """, (target_id,))

        if command == "/addbalance":
            cur.execute("""
            UPDATE balances
            SET balance=balance+?
            WHERE user_id=?
            """, (amount, target_id))

        elif command == "/delbalance":
            cur.execute("""
            UPDATE balances
            SET balance=balance-?
            WHERE user_id=? AND balance>=?
            """, (amount, target_id, amount))

            if cur.rowcount != 1:
                con.rollback()
                await update.effective_message.reply_text(
                    "用户余额不足，无法扣除。"
                )
                return

        else:
            con.rollback()
            return

        cur.execute(
            "SELECT balance FROM balances WHERE user_id=?",
            (target_id,)
        )

        balance = cur.fetchone()[0]

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("管理员调整余额失败")
        await update.effective_message.reply_text("余额调整失败。")
        return

    finally:
        con.close()

    await update.effective_message.reply_text(
        f"余额调整成功。\n用户ID：{target_id}\n"
        f"当前余额：{format_usdt(balance)} USDT"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "你的账户余额发生变动。\n"
                f"当前余额：{format_usdt(balance)} USDT"
            )
        )
    except Exception:
        logger.exception("通知用户余额调整失败")


# ==================================================
# 菜单及状态管理
# ==================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        MENU,
        resize_keyboard=True,
        is_persistent=True
    )


def clear_mode(user_id, context):
    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)
    context.user_data.pop("payment_state", None)

    user_modes.pop(user_id, None)
    payment_waiting.discard(user_id)


# ==================================================
# 开始及注册
# ==================================================

async def start(update, context):
    user = update.effective_user
    register_user(user)

    if context.args:
        argument = context.args[0]

        if argument.startswith("ref_"):
            try:
                inviter_id = int(argument[4:])

                if inviter_id != user.id:
                    if bind_inviter(user.id, inviter_id):
                        await context.bot.send_message(
                            chat_id=inviter_id,
                            text=(
                                "🎉 你成功邀请了一位新用户！\n"
                                "获得奖励：10.00 USDT"
                            )
                        )
            except ValueError:
                pass

    clear_mode(user.id, context)

    await update.effective_message.reply_text(
        "欢迎使用 VIP 会员机器人。\n\n"
        "请选择下方菜单使用功能。",
        reply_markup=main_keyboard()
    )


# ==================================================
# 每日签到
# ==================================================

async def checkin(update, context):
    user = update.effective_user
    register_user(user)

    today = now_sg().date().isoformat()

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        INSERT OR IGNORE INTO checkins(user_id, check_date)
        VALUES (?, ?)
        """, (user.id, today))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text(
                "你今天已经签到过了，明天再来吧。"
            )
            return

        points_reward = 1

        cur.execute("""
        UPDATE users
        SET points=points+?
        WHERE user_id=?
        """, (points_reward, user.id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("签到失败")
        await update.effective_message.reply_text("签到失败，请稍后重试。")
        return

    finally:
        con.close()

    await reply_custom(
        update.effective_message,
        f"{CHECKIN_MARK} 签到成功！获得 {points_reward} 积分。",
        CHECKIN_MARK,
        CHECKIN_EMOJI_ID
    )


# ==================================================
# 积分查询
# ==================================================

async def points(update, context):
    user = update.effective_user
    row = get_user(user.id)

    if not row:
        register_user(user)
        row = get_user(user.id)

    await reply_custom(
        update.effective_message,
        f"{POINTS_MARK} 当前积分：{row[4]}",
        POINTS_MARK,
        POINTS_EMOJI_ID
    )


# ==================================================
# 积分兑换VIP
# ==================================================

async def redeem(update, context):
    user = update.effective_user
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            "SELECT points FROM users WHERE user_id=?",
            (user.id,)
        )

        row = cur.fetchone()

        if not row:
            con.rollback()
            await update.effective_message.reply_text("请先发送 /start。")
            return

        if row[0] < 18:
            con.rollback()
            await update.effective_message.reply_text(
                "积分不足，兑换30天VIP需要18积分。"
            )
            return

        cur.execute("""
        UPDATE users
        SET points=points-18
        WHERE user_id=?
        """, (user.id,))

        cur.execute("""
        SELECT vip_until FROM users WHERE user_id=?
        """, (user.id,))

        vip_until = parse_sg_datetime(cur.fetchone()[0])
        current = now_sg()

        if vip_until and vip_until > current:
            new_until = vip_until + timedelta(days=30)
        else:
            new_until = current + timedelta(days=30)

        cur.execute("""
        UPDATE users
        SET vip_until=?
        WHERE user_id=?
        """, (
            new_until.isoformat(sep=" ", timespec="seconds"),
            user.id
        ))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("积分兑换VIP失败")
        await update.effective_message.reply_text("兑换失败，请稍后重试。")
        return

    finally:
        con.close()

    await update.effective_message.reply_text(
        "🎉 兑换成功！\n"
        "已扣除18积分，获得30天VIP。\n"
        f"VIP到期时间：{format_sg_time(new_until.isoformat())}"
    )


# ==================================================
# 用户ID查询
# ==================================================

async def myid(update, context):
    user = update.effective_user

    text = user_id_line(user.id)

    await update.effective_message.reply_text(
        text,
        entities=custom_entity(
            text,
            USER_MARK,
            USER_EMOJI_ID
        )
    )


# ==================================================
# 用户资料
# ==================================================

async def profile(update, context):
    user = update.effective_user
    register_user(user)

    row = get_user(user.id)

    if not row:
        return

    user_id, username, first_name, first_seen, user_points, vip_until = row

    vip_time = parse_sg_datetime(vip_until)

    if vip_time and vip_time > now_sg():
        vip_status = f"有效至：{format_sg_time(vip_until)}"
    else:
        vip_status = "未开通或已过期"

    text = (
        f"{NICKNAME_MARK} 昵称：{first_name or '未设置'}\n"
        f"{PROFILE_ID_MARK} 用户ID：{user_id}\n"
        f"用户名：@{username if username else '未设置'}\n"
        f"首次使用：{format_sg_time(first_seen)}\n"
        f"{POINTS_MARK} 积分：{user_points}\n"
        f"{VIP_MARK} VIP状态：{vip_status}\n"
        f"{USDT_MARK} USDT余额：{format_usdt(get_balance(user_id))}"
    )

    entities = (
        custom_entity(text, NICKNAME_MARK, NICKNAME_EMOJI_ID)
        + custom_entity(text, PROFILE_ID_MARK, PROFILE_ID_EMOJI_ID)
        + custom_entity(text, POINTS_MARK, POINTS_EMOJI_ID)
        + custom_entity(text, VIP_MARK, VIP_EMOJI_ID)
        + custom_entity(text, USDT_MARK, USDT_EMOJI_ID)
    )

    await update.effective_message.reply_text(
        text,
        entities=entities
    )


# ==================================================
# VIP套餐
# ==================================================

def vip_plan_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("7天 1 USDT", callback_data="vip_plan_7天"),
            InlineKeyboardButton("30天 2 USDT", callback_data="vip_plan_30天"),
        ],
        [
            InlineKeyboardButton("90天 5 USDT", callback_data="vip_plan_90天"),
            InlineKeyboardButton("365天 10 USDT", callback_data="vip_plan_365天"),
        ],
    ])


def vip_method_keyboard(plan):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "TRC20 USDT",
                callback_data=f"vip_method_trc20_{plan}"
            )
        ],
        [
            InlineKeyboardButton(
                "OKPay",
                url=OKPAY_LINKS[plan]
            )
        ],
        [
            InlineKeyboardButton(
                "返回套餐",
                callback_data="vip_back_plans"
            )
        ]
    ])


def vip_order_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "提交付款凭证",
                callback_data="vip_submit_proof"
            )
        ],
        [
            InlineKeyboardButton(
                "取消订单",
                callback_data="vip_cancel"
            )
        ]
    ])


async def vip(update, context):
    await update.effective_message.reply_text(
        "💎 VIP会员套餐\n\n"
        "请选择需要开通的套餐：",
        reply_markup=vip_plan_keyboard()
    )


async def vip_feature(update, context):
    await update.effective_message.reply_text(
        "✈️ VIP专属功能\n\n"
        "VIP会员可使用机器人提供的专属功能。\n"
        "如需开通，请点击 VIP会员。"
    )


def clear_payment_state(context):
    for key in (
        "vip_plan",
        "vip_method",
        "vip_waiting_proof"
    ):
        context.user_data.pop(key, None)


async def payment_callback(update, context):
    query = update.callback_query

    await query.answer()

    data = query.data

    if data.startswith("vip_plan_"):
        plan = data.replace("vip_plan_", "", 1)

        if plan not in PLANS:
            return

        context.user_data["vip_plan"] = plan

        await query.edit_message_text(
            f"你选择的套餐：{plan}\n"
            f"价格：{PLANS[plan][0]} USDT\n\n"
            "请选择付款方式：",
            reply_markup=vip_method_keyboard(plan)
        )
        return

    if data == "vip_back_plans":
        await query.edit_message_text(
            "请选择VIP套餐：",
            reply_markup=vip_plan_keyboard()
        )
        return

    if data.startswith("vip_method_trc20_"):
        plan = data.replace("vip_method_trc20_", "", 1)

        if plan not in PLANS:
            return

        context.user_data["vip_plan"] = plan
        context.user_data["vip_method"] = "TRC20"
        context.user_data["vip_waiting_proof"] = True

        amount = PLANS[plan][0]

        await query.edit_message_text(
            f"套餐：{plan}\n"
            f"金额：{amount} USDT\n\n"
            "请向以下地址转账：\n\n"
            f"{USDT_ADDRESS}\n\n"
            "请使用 TRC20 网络转账。\n"
            "转账完成后，请发送交易截图或交易哈希。"
        )
        return

    if data == "vip_submit_proof":
        context.user_data["vip_waiting_proof"] = True
        await query.edit_message_text(
            "请发送付款截图或交易哈希。"
        )
        return

    if data == "vip_cancel":
        clear_payment_state(context)
        await query.edit_message_text("已取消VIP订单。")
        return


# ==================================================
# 管理员命令
# ==================================================

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.effective_message.reply_text(
        "管理员控制面板\n\n"
        "/stats - 统计数据\n"
        "/users - 用户列表\n"
        "/user 用户ID - 用户详情\n"
        "/approve 用户ID 天数 - 开通VIP\n"
        "/reducevip 用户ID 天数 - 减少VIP\n"
        "/clearvip 用户ID - 清除VIP\n"
        "/addpoints 用户ID 数量 - 增加积分\n"
        "/delpoints 用户ID 数量 - 减少积分\n"
        "/addbalance 用户ID 金额 - 增加余额\n"
        "/delbalance 用户ID 金额 - 减少余额\n"
        "/sendusers - 群发消息\n"
        "/cancelmsg - 取消群发"
    )


async def stats(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM referrals")
    referrals_count = cur.fetchone()[0]

    cur.execute("""
    SELECT COALESCE(SUM(balance), 0)
    FROM balances
    """)
    balance = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM withdrawals
    WHERE status='pending'
    """)
    pending = cur.fetchone()[0]

    con.close()

    await update.effective_message.reply_text(
        "📊 机器人统计\n\n"
        f"用户总数：{users_count}\n"
        f"成功邀请：{referrals_count}\n"
        f"用户总余额：{format_usdt(balance)} USDT\n"
        f"待审核提现：{pending}"
    )


async def users_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT user_id, username, first_name
    FROM users
    ORDER BY first_seen DESC
    LIMIT 50
    """)

    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.effective_message.reply_text("暂无用户。")
        return

    lines = ["最近注册的用户："]

    for user_id, username, first_name in rows:
        lines.append(
            f"\n{first_name or '未设置'} "
            f"@{username or '无用户名'}\n"
            f"{USER_MARK} 用户ID：{user_id}"
        )

    text = "\n".join(lines)

    await update.effective_message.reply_text(
        text,
        entities=custom_entities_all(
            text,
            USER_MARK,
            USER_EMOJI_ID
        )
    )


async def user_info(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/user 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID不正确。")
        return

    row = get_user(target_id)

    if not row:
        await update.effective_message.reply_text("用户不存在。")
        return

    balance = get_balance(target_id)

    text = (
        f"用户资料\n\n"
        f"{USER_MARK} 用户ID：{target_id}\n"
        f"昵称：{row[2] or '未设置'}\n"
        f"用户名：@{row[1] or '无'}\n"
        f"注册时间：{format_sg_time(row[3])}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{format_sg_time(row[5])}\n"
        f"余额：{format_usdt(balance)} USDT\n"
        f"邀请人数：{get_invite_count(target_id)}"
    )

    await update.effective_message.reply_text(
        text,
        entities=custom_entity(text, USER_MARK, USER_EMOJI_ID)
    )


async def approve(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "用法：/approve 用户ID 天数"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("参数格式不正确。")
        return

    if days <= 0:
        await update.effective_message.reply_text("天数必须大于0。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT vip_until FROM users WHERE user_id=?",
        (target_id,)
    )

    row = cur.fetchone()

    if not row:
        con.close()
        await update.effective_message.reply_text("用户不存在。")
        return

    current = now_sg()
    old = parse_sg_datetime(row[0])

    if old and old > current:
        new_until = old + timedelta(days=days)
    else:
        new_until = current + timedelta(days=days)

    cur.execute("""
    UPDATE users
    SET vip_until=?
    WHERE user_id=?
    """, (
        new_until.isoformat(sep=" ", timespec="seconds"),
        target_id
    ))

    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"已为用户 {target_id} 开通 {days} 天VIP。"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 管理员已为你开通 {days} 天VIP。\n"
                f"VIP到期时间：{format_sg_time(new_until.isoformat())}"
            )
        )
    except Exception:
        logger.exception("通知VIP开通失败")


async def reduce_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "用法：/reducevip 用户ID 天数"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("参数不正确。")
        return

    if days <= 0:
        await update.effective_message.reply_text("天数必须大于0。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT vip_until FROM users WHERE user_id=?",
        (target_id,)
    )

    row = cur.fetchone()

    if not row:
        con.close()
        await update.effective_message.reply_text("用户不存在。")
        return

    old = parse_sg_datetime(row[0])

    if not old:
        con.close()
        await update.effective_message.reply_text("该用户没有有效VIP。")
        return

    new_until = old - timedelta(days=days)

    if new_until <= now_sg():
        new_until = None

    cur.execute("""
    UPDATE users SET vip_until=?
    WHERE user_id=?
    """, (
        new_until.isoformat(sep=" ", timespec="seconds")
        if new_until else None,
        target_id
    ))

    con.commit()
    con.close()

    await update.effective_message.reply_text("VIP期限调整成功。")


async def clear_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/clearvip 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID不正确。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=NULL WHERE user_id=?",
        (target_id,)
    )

    con.commit()
    changed = cur.rowcount
    con.close()

    await update.effective_message.reply_text(
        "VIP已清除。" if changed else "用户不存在。"
    )


async def change_points(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    parts = (update.effective_message.text or "").split()

    if len(parts) != 3:
        await update.effective_message.reply_text(
            "用法：/addpoints 用户ID 数量\n"
            "/delpoints 用户ID 数量"
        )
        return

    command = parts[0].split("@")[0].lower()

    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await update.effective_message.reply_text("参数不正确。")
        return

    if amount <= 0:
        await update.effective_message.reply_text("数量必须大于0。")
        return

    con = db()
    cur = con.cursor()

    if command == "/addpoints":
        cur.execute("""
        UPDATE users
        SET points=points+?
        WHERE user_id=?
        """, (amount, target_id))

    elif command == "/delpoints":
        cur.execute("""
        UPDATE users
        SET points=points-?
        WHERE user_id=? AND points>=?
        """, (amount, target_id, amount))

        if cur.rowcount != 1:
            con.close()
            await update.effective_message.reply_text("积分不足或用户不存在。")
            return

    else:
        con.close()
        return

    changed = cur.rowcount
    con.commit()
    con.close()

    if changed:
        await update.effective_message.reply_text("积分调整成功。")
    else:
        await update.effective_message.reply_text("用户不存在。")


# ==================================================
# 管理员群发
# ==================================================

async def get_users_page(page=0):
    con = db()
    cur = con.cursor()

    offset = page * USER_PAGE_SIZE

    cur.execute("""
    SELECT user_id, username, first_name
    FROM users
    ORDER BY first_seen DESC
    LIMIT ? OFFSET ?
    """, (
        USER_PAGE_SIZE,
        offset
    ))

    rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    con.close()

    return rows, total


async def show_admin_user_page(query, page):
    rows, total = await get_users_page(page)

    buttons = []

    for user_id, username, first_name in rows:
        label = (
            f"{first_name or '用户'} "
            f"(@{username})" if username else
            f"{first_name or '用户'}"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{label} | {user_id}",
                callback_data=f"admin_target_{user_id}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ 上一页",
                callback_data=f"admin_page_{page - 1}"
            )
        )

    if (page + 1) * USER_PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(
                "下一页 ➡️",
                callback_data=f"admin_page_{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "取消",
            callback_data="admin_send_cancel"
        )
    ])

    await query.edit_message_text(
        f"请选择要发送消息的用户。\n"
        f"用户总数：{total}\n"
        f"当前页：{page + 1}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def open_admin_user_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.effective_message.reply_text(
        "请选择接收消息的用户："
    )

    rows, total = await get_users_page(0)

    buttons = []

    for user_id, username, first_name in rows:
        label = f"{first_name or '用户'} | {user_id}"

        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"admin_target_{user_id}"
            )
        ])

    navigation = []

    if total > USER_PAGE_SIZE:
        navigation.append(
            InlineKeyboardButton(
                "下一页 ➡️",
                callback_data="admin_page_1"
            )
        )

    buttons.append([
        InlineKeyboardButton(
            "取消",
            callback_data="admin_send_cancel"
        )
    ])

    await update.effective_message.reply_text(
        f"请选择用户，共 {total} 人：",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_send_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data

    if data.startswith("admin_page_"):
        page = int(data.rsplit("_", 1)[1])
        await show_admin_user_page(query, page)
        return

    if data.startswith("admin_target_"):
        target_id = int(data.rsplit("_", 1)[1])

        context.user_data["admin_send_target"] = target_id
        context.user_data["admin_send_waiting"] = True

        await query.edit_message_text(
            f"已选择用户 {target_id}。\n"
            "请发送要转发给该用户的消息。"
        )
        return

    if data == "admin_send_cancel":
        context.user_data.pop("admin_send_target", None)
        context.user_data.pop("admin_send_waiting", None)
        await query.edit_message_text("已取消。")


async def send_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.user_data.get("admin_send_waiting"):
        return

    target_id = context.user_data.get("admin_send_target")

    if not target_id:
        context.user_data.pop("admin_send_waiting", None)
        return

    try:
        sent = await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id
        )

        await update.effective_message.reply_text(
            f"✅ 消息已发送给用户 {target_id}。"
        )

    except Exception:
        logger.exception("管理员主动发送消息失败")
        await update.effective_message.reply_text(
            "发送失败，请检查用户是否已启动机器人。"
        )

    context.user_data.pop("admin_send_waiting", None)
    context.user_data.pop("admin_send_target", None)


async def cancel_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data.pop("admin_send_waiting", None)
    context.user_data.pop("admin_send_target", None)

    await update.effective_message.reply_text("已取消群发/主动发送状态。")


# ==================================================
# 双向联系
# ==================================================

async def forward_contact_message(update, context):
    message = update.effective_message
    user = update.effective_user

    header = (
        "📩 用户发送了新消息\n\n"
        f"昵称：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username or '无'}\n"
        f"{user_id_line(user.id)}\n\n"
        "管理员可以直接回复这条消息或下面的媒体内容。"
    )

    header_sent = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=header,
        entities=custom_entity(
            header,
            USER_MARK,
            USER_EMOJI_ID
        )
    )

    save_reply_message(header_sent.message_id, user.id)

    # 复制用户原消息，支持文本、图片、视频、文件等
    copied = await context.bot.copy_message(
        chat_id=ADMIN_ID,
        from_chat_id=message.chat_id,
        message_id=message.message_id
    )

    save_reply_message(copied.message_id, user.id)

    try:
        notice = await message.reply_text("✅ 消息已发送给管理员。")

        await asyncio.sleep(1)

        await notice.delete()

    except Exception:
        pass


async def admin_reply_contact(update, context):
    message = update.effective_message

    if not message.reply_to_message:
        return False

    target_id = get_reply_user(
        message.reply_to_message.message_id
    )

    if not target_id:
        return False

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        await message.reply_text("✅ 已回复用户。")

    except Exception:
        logger.exception("管理员回复用户失败")
        await message.reply_text(
            "回复失败，用户可能已经屏蔽机器人。"
        )

    return True


# ==================================================
# 安全设置入口
# ==================================================

async def security_settings(update, context):
    user_id = update.effective_user.id

    if has_trade_password(user_id):
        await start_trade_flow(
            update,
            context,
            "change_verify"
        )
    else:
        await start_trade_flow(
            update,
            context,
            "set_first"
        )


# ==================================================
# 普通消息处理
# ==================================================

async def handle_message(update, context):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    user_id = user.id
    text = (message.text or "").strip()

    # 管理员拒绝提现时输入理由
    if (
        user_id == ADMIN_ID
        and context.user_data.get("reject_withdrawal_id")
        and text
        and not text.startswith("/")
    ):
        await finish_withdraw_reject(update, context)
        return

    # 管理员回复用户的优先级高于主动发送模式
    if user_id == ADMIN_ID and message.reply_to_message:
        handled = await admin_reply_contact(update, context)

        if handled:
            return

    # 管理员主动发送模式
    if (
        user_id == ADMIN_ID
        and context.user_data.get("admin_send_waiting")
    ):
        if text in ("取消", "/cancelmsg"):
            await cancel_admin_message(update, context)
            return

        await send_admin_message(update, context)
        return

    # 用户联系管理员状态
    if user_id in contact_waiting:
        if text == "❌ 退出菜单":
            contact_waiting.discard(user_id)
            await message.reply_text(
                "已退出双向联系。",
                reply_markup=main_keyboard()
            )
            return

        if text and text.startswith("/"):
            return

        try:
            await forward_contact_message(update, context)
        except Exception:
            logger.exception("转发用户消息失败")
            await message.reply_text("发送失败，请稍后重试。")

        return

    # 交易密码状态期间，不接收普通文本密码
    if context.user_data.get("trade_flow"):
        await message.reply_text(
            "请使用交易密码输入界面下方的数字按钮操作。"
        )
        return

    # 提现地址输入
    if context.user_data.get("withdraw_state") == "address":
        address = text

        if not TRON_ADDRESS_PATTERN.fullmatch(address):
            await message.reply_text(
                "❌ TRC20地址格式不正确。\n"
                "请重新发送以 T 开头的有效地址。"
            )
            return

        context.user_data["withdraw_address"] = address

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ 确认地址",
                    callback_data="withdraw_address_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 重新输入",
                    callback_data="withdraw_address_retry"
                )
            ]
        ])

        await message.reply_text(
            "请确认你的提现地址：\n\n"
            f"{address}",
            reply_markup=keyboard
        )
        return

    # 提现金额输入
    if context.user_data.get("withdraw_state") == "amount":
        await create_withdrawal(update, context)
        return

    # VIP付款凭证
    if context.user_data.get("vip_waiting_proof"):
        plan = context.user_data.get("vip_plan", "未知")

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "📩 收到VIP付款凭证\n\n"
                    f"用户ID：{user_id}\n"
                    f"昵称：{user.first_name or '未设置'}\n"
                    f"套餐：{plan}\n"
                    "请审核用户提交的凭证。"
                )
            )

            await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            await message.reply_text(
                "✅ 付款凭证已提交，请等待管理员审核。"
            )

            clear_payment_state(context)

        except Exception:
            logger.exception("提交VIP付款凭证失败")
            await message.reply_text("提交失败，请稍后重试。")

        return

    # 退出菜单
    if text == "❌ 退出菜单":
        clear_mode(user_id, context)
        contact_waiting.discard(user_id)

        await message.reply_text(
            "已退出当前功能。",
            reply_markup=main_keyboard()
        )
        return

    # 菜单分发
    if text == "💬 双向联系":
        clear_mode(user_id, context)
        contact_waiting.add(user_id)

        await message.reply_text(
            "💬 已进入双向联系模式。\n\n"
            "请直接发送文字、图片、视频或文件，"
            "管理员收到后可以回复你。\n\n"
            "点击“❌ 退出菜单”即可退出。",
            reply_markup=main_keyboard()
        )
        return

    if text == "👤 我的资料":
        clear_mode(user_id, context)
        await profile(update, context)
        return

    if text == "🆔 查询用户ID":
        clear_mode(user_id, context)
        await myid(update, context)
        return

    if text == "💎 VIP会员":
        clear_mode(user_id, context)
        await vip(update, context)
        return

    if text == "✈️ VIP功能":
        clear_mode(user_id, context)
        await vip_feature(update, context)
        return

    if text == "💰 我的余额":
        clear_mode(user_id, context)
        await show_balance(update, context)
        return

    if text == "🔗 邀请好友":
        clear_mode(user_id, context)
        await show_invite(update, context)
        return

    if text == "💸 申请提现":
        clear_mode(user_id, context)
        await begin_withdraw(update, context)
        return

    if text == "🔐 设置交易密码":
        clear_mode(user_id, context)
        await security_settings(update, context)
        return

    if text == "🎁 每日签到":
        clear_mode(user_id, context)
        await checkin(update, context)
        return

    if text == "👨‍💻 联系管理员":
        clear_mode(user_id, context)
        contact_waiting.add(user_id)

        await message.reply_text(
            "请发送你要联系管理员的内容。"
        )
        return

    if text in ("🖼 NFT查询", "📱 +888号码查询", "👤 收藏用户名查询"):
        await message.reply_text(
            "该功能目前暂未开放，请稍后再试。"
        )
        return

    # 未匹配菜单时保持安静
    return


# ==================================================
# 帮助命令
# ==================================================

async def help_command(update, context):
    await update.effective_message.reply_text(
        "机器人使用说明：\n\n"
        "/start - 开始使用\n"
        "/points - 查询积分\n"
        "/redeem - 使用18积分兑换30天VIP\n\n"
        "用户可以通过菜单查询余额、邀请好友、"
        "申请提现和联系管理员。"
    )


# ==================================================
# 启动初始化
# ==================================================

async def post_init(application):
    commands = [
        BotCommand("start", "开始使用"),
        BotCommand("help", "帮助"),
        BotCommand("points", "查询积分"),
        BotCommand("redeem", "18积分兑换30天VIP"),
    ]

    await application.bot.set_my_commands(commands)

    try:
        await application.bot.set_my_commands(
            [
                BotCommand("admin", "管理员面板"),
                BotCommand("stats", "统计数据"),
                BotCommand("users", "用户列表"),
                BotCommand("user", "用户详情"),
                BotCommand("approve", "开通VIP"),
                BotCommand("reducevip", "减少VIP"),
                BotCommand("clearvip", "清除VIP"),
                BotCommand("addpoints", "增加积分"),
                BotCommand("delpoints", "减少积分"),
                BotCommand("addbalance", "增加余额"),
                BotCommand("delbalance", "减少余额"),
                BotCommand("sendusers", "选择用户发送消息"),
                BotCommand("cancelmsg", "取消发送"),
            ],
            scope=BotCommandScopeChat(ADMIN_ID)
        )

    except Exception:
        logger.exception("设置管理员命令失败")


# ==================================================
# 主程序
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "缺少 BOT_TOKEN 环境变量，请先设置机器人 Token。"
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # 用户命令
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("points", points)
    )

    application.add_handler(
        CommandHandler("redeem", redeem)
    )

    # 管理员命令
    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("stats", stats)
    )

    application.add_handler(
        CommandHandler("users", users_list)
    )

    application.add_handler(
        CommandHandler("user", user_info)
    )

    application.add_handler(
        CommandHandler("approve", approve)
    )

    application.add_handler(
        CommandHandler("reducevip", reduce_vip)
    )

    application.add_handler(
        CommandHandler("clearvip", clear_vip)
    )

    application.add_handler(
        CommandHandler(
            "addpoints",
            change_points
        )
    )

    application.add_handler(
        CommandHandler(
            "delpoints",
            change_points
        )
    )

    application.add_handler(
        CommandHandler(
            "addbalance",
            change_balance
        )
    )

    application.add_handler(
        CommandHandler(
            "delbalance",
            change_balance
        )
    )

    application.add_handler(
        CommandHandler(
            "sendusers",
            open_admin_user_list
        )
    )

    application.add_handler(
        CommandHandler(
            "cancelmsg",
            cancel_admin_message
        )
    )

    # 交易密码按钮
    application.add_handler(
        CallbackQueryHandler(
            trade_password_callback,
            pattern=r"^trade_"
        )
    )

    # 提现按钮
    application.add_handler(
        CallbackQueryHandler(
            withdraw_callback,
            pattern=r"^(withdraw_cancel|withdraw_approve_|withdraw_reject_)"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            withdraw_address_callback,
            pattern=r"^withdraw_address_"
        )
    )

    # VIP按钮
    application.add_handler(
        CallbackQueryHandler(
            payment_callback,
            pattern=r"^vip_"
        )
    )

    # 管理员主动发送按钮
    application.add_handler(
        CallbackQueryHandler(
            admin_send_callback,
            pattern=r"^admin_"
        )
    )

    # 普通消息统一处理
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info("VIP机器人启动成功")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main() 