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
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
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
PROFILE_ID_EMOJI_ID = USER_EMOJI_ID
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
    ["🆔 查询用户ID", "⭐ VIP会员"],
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
        entities=custom_entities_all(text, marker, emoji_id),
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

    # 兼容旧数据库：新增钱包冻结字段，不删除原有数据
    columns = {
        row[1]
        for row in cur.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    }

    if "wallet_frozen" not in columns:
        cur.execute("""
        ALTER TABLE users
        ADD COLUMN wallet_frozen INTEGER NOT NULL DEFAULT 0
        """)

    if "wallet_frozen_at" not in columns:
        cur.execute("""
        ALTER TABLE users
        ADD COLUMN wallet_frozen_at TEXT
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


def is_wallet_frozen(user_id):
    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT wallet_frozen
    FROM users
    WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()
    con.close()

    return bool(row and row[0])


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
# 钱包冻结及解冻
# ==================================================

def freeze_wallet_db(user_id):
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        UPDATE users
        SET wallet_frozen=1,
            wallet_frozen_at=?
        WHERE user_id=?
        """, (sg_timestamp(), user_id))

        changed = cur.rowcount == 1
        con.commit()
        return changed

    except Exception:
        con.rollback()
        logger.exception("冻结钱包失败")
        return False

    finally:
        con.close()


def unfreeze_wallet_db(user_id):
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        UPDATE users
        SET wallet_frozen=0,
            wallet_frozen_at=NULL
        WHERE user_id=?
        """, (user_id,))

        changed = cur.rowcount == 1

        if changed:
            cur.execute("""
            UPDATE trade_passwords
            SET failed_attempts=0,
                locked_until=NULL
            WHERE user_id=?
            """, (user_id,))

        con.commit()
        return changed

    except Exception:
        con.rollback()
        logger.exception("解冻钱包失败")
        return False

    finally:
        con.close()


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
    if is_wallet_frozen(user_id):
        return PASSWORD_MAX_FAILURES, "frozen"

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

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT wallet_frozen
        FROM users
        WHERE user_id=?
        """, (user_id,))

        user_row = cur.fetchone()

        if not user_row:
            con.rollback()
            return "missing", None

        if user_row[0]:
            con.rollback()
            return "frozen", None

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
            SET failed_attempts=0,
                locked_until=NULL
            WHERE user_id=?
            """, (user_id,))

            con.commit()
            return "success", None

        attempts += 1

        if attempts >= PASSWORD_MAX_FAILURES:
            cur.execute("""
            UPDATE trade_passwords
            SET failed_attempts=?,
                locked_until=NULL
            WHERE user_id=?
            """, (attempts, user_id))

            cur.execute("""
            UPDATE users
            SET wallet_frozen=1,
                wallet_frozen_at=?
            WHERE user_id=?
            """, (sg_timestamp(), user_id))

            con.commit()
            return "frozen", None

        cur.execute("""
        UPDATE trade_passwords
        SET failed_attempts=?,
            locked_until=NULL
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

    if is_wallet_frozen(user_id):
        await update.effective_message.reply_text(
            "🔒 你的钱包目前已被冻结，无法进行余额交易或提现，请联系管理员处理。"
        )
        return

    attempts, lock_time = get_password_lock(user_id)

    if lock_time == "frozen":
        await update.effective_message.reply_text(
            "🔒 钱包已永久冻结，请联系管理员解冻。"
        )
        return

    if lock_time:
        await update.effective_message.reply_text(
            "交易密码暂时锁定。\n"
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

    if not data.startswith("trade_digit_"):
        await query.answer("无效操作。", show_alert=True)
        return

    digit = data.rsplit("_", 1)[1]

    if digit not in "0123456789":
        await query.answer("无效数字。", show_alert=True)
        return

    if len(buffer) >= PASSWORD_LENGTH:
        await query.answer()
        return

    buffer += digit
    context.user_data["trade_digits"] = buffer

    if len(buffer) < PASSWORD_LENGTH:
        await query.answer()
        await query.edit_message_text(
            trade_prompt(flow, len(buffer)),
            reply_markup=trade_password_keyboard()
        )
        return

    if flow == "set_first":
        await query.answer()

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
            await query.answer(
                "两次输入不一致，请重新输入。",
                show_alert=True
            )

            context.user_data["trade_first"] = None
            context.user_data["trade_digits"] = ""
            context.user_data["trade_flow"] = "set_first"

            await query.edit_message_text(
                trade_prompt(
                    "set_first",
                    0,
                    "两次输入不一致，请重新设置交易密码。"
                ),
                reply_markup=trade_password_keyboard()
            )
            return

        await query.answer()
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

    if flow in ("change_verify", "withdraw_verify"):
        status, detail = verify_trade_password(user_id, buffer)

        if status == "success":
            await query.answer()

            if flow == "change_verify":
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

            for key in (
                "trade_flow",
                "trade_digits",
                "trade_message_id",
                "trade_chat_id"
            ):
                context.user_data.pop(key, None)

            context.user_data["withdraw_state"] = "address"

            await query.edit_message_text(
                "✅ 交易密码验证成功。\n\n"
                "请发送你的 TRC20 USDT 收款地址。"
            )
            return

        context.user_data["trade_digits"] = ""

        if status == "wrong":
            await query.answer(
                f"交易密码错误，还可尝试 {detail} 次。",
                show_alert=True
            )

            await query.edit_message_text(
                trade_prompt(flow, 0, f"交易密码错误，还可尝试 {detail} 次。"),
                reply_markup=trade_password_keyboard()
            )
            return

        if status == "frozen":
            await query.answer(
                "交易密码连续输错5次，钱包已永久冻结，请联系管理员。",
                show_alert=True
            )

            for key in (
                "trade_flow",
                "trade_digits",
                "trade_first",
                "trade_message_id",
                "trade_chat_id",
                "after_trade_setup",
                "withdraw_state",
                "withdraw_address"
            ):
                context.user_data.pop(key, None)

            await query.edit_message_text(
                "🔒 钱包已永久冻结。\n\n"
                "原因：交易密码连续输错5次。\n"
                "请联系管理员解冻。"
            )

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⚠️ 用户钱包已自动冻结\n\n"
                        f"{user_id_line(user_id)}\n"
                        "原因：交易密码连续输错5次。"
                    ),
                    entities=custom_entities_all(
                        user_id_line(user_id),
                        USER_MARK,
                        USER_EMOJI_ID
                    )
                )
            except Exception:
                logger.exception("通知管理员钱包冻结失败")

            return

        if status == "locked":
            await query.answer(
                "交易密码暂时锁定，请稍后再试。",
                show_alert=True
            )

            await query.edit_message_text(
                "交易密码暂时锁定。\n"
                f"解锁时间：{detail.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        await query.answer(
            "验证失败，请稍后重试。",
            show_alert=True
        )

        await query.edit_message_text(
            trade_prompt(flow, 0, "验证失败，请稍后重试。"),
            reply_markup=trade_password_keyboard()
        )
        return
        
        # ==================================================
# 邀请系统
# ==================================================

def bind_inviter(user_id, inviter_id):
    if user_id == inviter_id:
        return False

    con = db()

    try:
        cur = con.cursor()

        cur.execute(
            "SELECT 1 FROM referrals WHERE invited_id=?",
            (user_id,)
        )

        if cur.fetchone():
            return False

        cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (inviter_id,)
        )

        if not cur.fetchone():
            return False

        cur.execute("""
        INSERT INTO referrals
        (invited_id, inviter_id, reward, created_at)
        VALUES (?, ?, ?, ?)
        """, (
            user_id,
            inviter_id,
            INVITE_REWARD,
            sg_timestamp()
        ))

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
        logger.exception("绑定邀请关系失败")
        return False

    finally:
        con.close()


# ==================================================
# 余额
# ==================================================

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


async def show_balance(update, context):
    user_id = update.effective_user.id
    register_user(update.effective_user)

    balance = get_balance(user_id)

    text = (
        f"{USDT_MARK} 我的余额\n\n"
        f"可用余额：{format_usdt(balance)} USDT\n\n"
    )

    if is_wallet_frozen(user_id):
        text += (
            "🔒 钱包状态：已冻结\n"
            "当前无法使用余额或申请提现。\n"
            "如需解冻，请联系管理员。"
        )
    else:
        text += "钱包状态：正常"

    await update.effective_message.reply_text(
        text,
        entities=usdt_entities(text)
    )


async def show_invite(update, context):
    user = update.effective_user
    register_user(user)

    count = get_invite_count(user.id)
    balance = get_balance(user.id)

    bot = await context.bot.get_me()

    invite_link = (
        f"https://t.me/{bot.username}?start={user.id}"
    )

    text = (
        "🔗 邀请好友\n\n"
        f"邀请人数：{count}\n"
        f"累计奖励：{format_usdt(count * INVITE_REWARD)} USDT\n"
        f"当前余额：{format_usdt(balance)} USDT\n\n"
        "每成功邀请一位新用户，可获得 10 USDT 奖励。\n\n"
        f"你的专属邀请链接：\n{invite_link}"
    )

    await update.effective_message.reply_text(
        text,
        entities=usdt_entities(text)
    )


# ==================================================
# 提现
# ==================================================

def withdraw_cancel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "取消提现",
                callback_data="withdraw_cancel"
            )
        ]
    ])


def withdraw_confirm_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "确认提现",
                callback_data="withdraw_confirm"
            ),
            InlineKeyboardButton(
                "取消提现",
                callback_data="withdraw_cancel"
            )
        ]
    ])


async def begin_withdraw(update, context):
    user = update.effective_user
    register_user(user)

    if is_wallet_frozen(user.id):
        await update.effective_message.reply_text(
            "🔒 钱包已被冻结，暂时无法申请提现。\n"
            "请联系管理员处理。"
        )
        return

    balance = get_balance(user.id)

    if balance < MIN_WITHDRAW + WITHDRAW_FEE:
        await update.effective_message.reply_text(
            "余额不足，暂时无法提现。\n\n"
            f"最低提现金额：{format_usdt(MIN_WITHDRAW)} USDT\n"
            f"提现手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
            f"当前余额：{format_usdt(balance)} USDT"
        )
        return

    context.user_data.pop("withdraw_address", None)
    context.user_data.pop("withdraw_amount", None)

    if not has_trade_password(user.id):
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


async def create_withdrawal(
    user_id,
    address,
    amount,
    context,
    chat_id
):
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT wallet_frozen
        FROM users
        WHERE user_id=?
        """, (user_id,))

        user_row = cur.fetchone()

        if not user_row:
            con.rollback()
            return False, "用户不存在"

        if user_row[0]:
            con.rollback()
            return False, "钱包已冻结，无法提现"

        cur.execute("""
        SELECT balance
        FROM balances
        WHERE user_id=?
        """, (user_id,))

        row = cur.fetchone()
        balance = row[0] if row else 0

        total = amount + WITHDRAW_FEE

        if amount < MIN_WITHDRAW:
            con.rollback()
            return False, "提现金额低于最低提现金额"

        if balance < total:
            con.rollback()
            return False, "余额不足"

        cur.execute("""
        UPDATE balances
        SET balance=balance-?
        WHERE user_id=?
          AND balance>=?
        """, (total, user_id, total))

        if cur.rowcount != 1:
            con.rollback()
            return False, "余额扣除失败，请重试"

        payout = amount

        cur.execute("""
        INSERT INTO withdrawals
        (
            user_id,
            address,
            amount,
            fee,
            payout,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user_id,
            address,
            amount,
            WITHDRAW_FEE,
            payout,
            sg_timestamp()
        ))

        withdrawal_id = cur.lastrowid

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("创建提现订单失败")
        return False, "提现申请失败，请稍后重试"

    finally:
        con.close()

    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)
    context.user_data.pop("withdraw_amount", None)

    text = (
        "✅ 提现申请已提交\n\n"
        f"订单编号：#{withdrawal_id}\n"
        f"提现金额：{format_usdt(amount)} USDT\n"
        f"手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"到账金额：{format_usdt(payout)} USDT\n"
        f"收款地址：{address}\n\n"
        "请等待管理员审核。"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text
    )

    admin_text = (
        "🔔 新提现申请\n\n"
        f"{USER_MARK} 用户ID：{user_id}\n"
        f"订单编号：#{withdrawal_id}\n"
        f"提现金额：{format_usdt(amount)} USDT\n"
        f"手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"到账金额：{format_usdt(payout)} USDT\n"
        f"收款地址：{address}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "通过",
                callback_data=f"withdraw_approve_{withdrawal_id}"
            ),
            InlineKeyboardButton(
                "拒绝",
                callback_data=f"withdraw_reject_{withdrawal_id}"
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        entities=custom_entities_all(
            admin_text,
            USER_MARK,
            USER_EMOJI_ID
        ),
        reply_markup=keyboard
    )

    return True, withdrawal_id


async def withdraw_address_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if is_wallet_frozen(user_id):
        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)
        context.user_data.pop("withdraw_amount", None)

        await query.answer(
            "钱包已冻结，无法继续提现。",
            show_alert=True
        )

        try:
            await query.edit_message_text(
                "🔒 钱包已冻结，提现操作已终止。"
            )
        except Exception:
            pass

        return

    data = query.data

    if data == "withdraw_address_confirm":
        address = context.user_data.get("withdraw_address")

        if not address or not TRON_ADDRESS_PATTERN.fullmatch(address):
            await query.answer(
                "收款地址无效，请重新输入。",
                show_alert=True
            )
            return

        context.user_data["withdraw_state"] = "amount"

        await query.answer()

        await query.edit_message_text(
            "请发送提现金额。\n\n"
            f"最低提现：{format_usdt(MIN_WITHDRAW)} USDT\n"
            f"手续费：{format_usdt(WITHDRAW_FEE)} USDT"
        )
        return

    if data == "withdraw_address_cancel":
        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)

        await query.answer("已取消提现。")

        await query.edit_message_text(
            "已取消提现。"
        )
        return


async def withdraw_callback(update, context):
    query = update.callback_query
    data = query.data

    # 取消操作必须先处理，普通用户也可以取消
    if data == "withdraw_cancel":
        await query.answer()

        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)
        context.user_data.pop("withdraw_amount", None)

        try:
            await query.edit_message_text("已取消提现。")
        except Exception:
            pass

        return

    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "无权操作。",
            show_alert=True
        )
        return

    await query.answer()

    match = re.fullmatch(
        r"withdraw_(approve|reject)_(\d+)",
        data
    )

    if not match:
        return

    action = match.group(1)
    withdrawal_id = int(match.group(2))

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT user_id, address, amount, fee, payout, status
    FROM withdrawals
    WHERE id=?
    """, (withdrawal_id,))

    row = cur.fetchone()

    if not row:
        con.close()
        await query.edit_message_text("提现订单不存在。")
        return

    user_id, address, amount, fee, payout, status = row

    if status != "pending":
        con.close()
        await query.edit_message_text("该订单已经处理过。")
        return

    if action == "approve":
        cur.execute("""
        UPDATE withdrawals
        SET status='approved',
            admin_id=?,
            reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            ADMIN_ID,
            sg_timestamp(),
            withdrawal_id
        ))

        con.commit()
        con.close()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ 你的提现申请已通过审核。\n\n"
                f"订单编号：#{withdrawal_id}\n"
                f"到账金额：{format_usdt(payout)} USDT\n"
                f"收款地址：{address}\n\n"
                "请留意钱包到账情况。"
            )
        )

        await query.edit_message_text(
            f"✅ 提现订单 #{withdrawal_id} 已通过。"
        )
        return

    if action == "reject":
        cur.execute("""
        UPDATE withdrawals
        SET status='rejected',
            admin_id=?,
            reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            ADMIN_ID,
            sg_timestamp(),
            withdrawal_id
        ))

        if cur.rowcount != 1:
            con.rollback()
            con.close()

            await query.edit_message_text(
                "订单状态已发生变化，请刷新后重试。"
            )
            return

        cur.execute("""
        UPDATE balances
        SET balance=balance+?
        WHERE user_id=?
        """, (amount + fee, user_id))

        con.commit()
        con.close()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ 你的提现申请未通过。\n\n"
                f"订单编号：#{withdrawal_id}\n"
                "提现金额和手续费已退回余额。"
            )
        )

        await query.edit_message_text(
            f"❌ 提现订单 #{withdrawal_id} 已拒绝，余额已退回。"
        )
        return


async def finish_withdraw_reject(
    withdrawal_id,
    admin_id,
    context,
    reason=""
):
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT user_id, amount, fee, status
        FROM withdrawals
        WHERE id=?
        """, (withdrawal_id,))

        row = cur.fetchone()

        if not row:
            con.rollback()
            return False

        user_id, amount, fee, status = row

        if status != "pending":
            con.rollback()
            return False

        cur.execute("""
        UPDATE withdrawals
        SET status='rejected',
            reason=?,
            admin_id=?,
            reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            reason,
            admin_id,
            sg_timestamp(),
            withdrawal_id
        ))

        if cur.rowcount != 1:
            con.rollback()
            return False

        cur.execute("""
        UPDATE balances
        SET balance=balance+?
        WHERE user_id=?
        """, (amount + fee, user_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("拒绝提现并退款失败")
        return False

    finally:
        con.close()

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ 提现申请已被拒绝。\n\n"
                f"订单编号：#{withdrawal_id}\n"
                f"原因：{reason or '未通过审核'}\n"
                "提现金额及手续费已退回余额。"
            )
        )
    except Exception:
        logger.exception("通知用户提现拒绝失败")

    return True


# ==================================================
# 管理员余额调整
# ==================================================

def change_balance(user_id, amount):
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        # 先检查用户是否真实存在，避免产生孤儿余额记录
        cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (user_id,)
        )

        if not cur.fetchone():
            con.rollback()
            return False, "用户不存在"

        cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
        """, (user_id,))

        cur.execute("""
        SELECT balance
        FROM balances
        WHERE user_id=?
        """, (user_id,))

        row = cur.fetchone()
        current = row[0] if row else 0

        new_balance = current + amount

        if new_balance < 0:
            con.rollback()
            return False, "用户余额不足"

        cur.execute("""
        UPDATE balances
        SET balance=?
        WHERE user_id=?
        """, (new_balance, user_id))

        con.commit()

        return True, new_balance

    except Exception:
        con.rollback()
        logger.exception("管理员余额调整失败")
        return False, "数据库操作失败"

    finally:
        con.close()
        
        # ==================================================
# 管理员钱包冻结 / 解冻命令
# ==================================================

async def freeze_wallet(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/freezewallet 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("用户不存在。")
        return

    if not freeze_wallet_db(target_id):
        await update.effective_message.reply_text("冻结失败，请重试。")
        return

    await update.effective_message.reply_text(
        f"已冻结用户 {target_id} 的钱包。"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="🔒 管理员已冻结你的钱包，暂时无法使用余额或申请提现。请联系管理员。"
        )
    except Exception:
        pass


async def unfreeze_wallet(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/unfreezewallet 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("用户不存在。")
        return

    if not unfreeze_wallet_db(target_id):
        await update.effective_message.reply_text("解冻失败，请重试。")
        return

    await update.effective_message.reply_text(
        f"已解冻用户 {target_id} 的钱包。"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="✅ 管理员已解冻你的钱包，现在可以正常使用余额和提现功能。"
        )
    except Exception:
        pass


# ==================================================
# 主菜单及状态
# ==================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        MENU,
        resize_keyboard=True
    )


def clear_mode(user_id):
    user_modes.pop(user_id, None)
    contact_waiting.discard(user_id)
    payment_waiting.discard(user_id)


async def start(update, context):
    user = update.effective_user
    register_user(user)

    if context.args:
        try:
            inviter_id = int(context.args[0])

            if inviter_id != user.id:
                bind_inviter(user.id, inviter_id)

        except (ValueError, TypeError):
            pass

    text = (
        f"你好，{user.first_name or '用户'}！\n\n"
        "欢迎使用 VIP 会员机器人。\n"
        "请选择下方菜单使用功能。"
    )

    await update.effective_message.reply_text(
        text,
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
    cur = con.cursor()

    try:
        cur.execute("""
        INSERT INTO checkins(user_id, check_date)
        VALUES (?, ?)
        """, (user.id, today))

        cur.execute("""
        UPDATE users
        SET points=points+1
        WHERE user_id=?
        """, (user.id,))

        con.commit()

        await update.effective_message.reply_text(
            "🎁 签到成功！\n\n"
            "获得 1 积分。\n"
            "每天可以签到一次。"
        )

    except sqlite3.IntegrityError:
        await update.effective_message.reply_text(
            "今天已经签到过了，明天再来吧。"
        )

    except Exception:
        con.rollback()
        logger.exception("签到失败")

        await update.effective_message.reply_text(
            "签到失败，请稍后重试。"
        )

    finally:
        con.close()


# ==================================================
# 积分及兑换 VIP
# ==================================================

async def points(update, context):
    user = update.effective_user
    register_user(user)

    row = get_user(user.id)
    total = row[4] if row else 0

    text = (
        f"{POINTS_MARK} 我的积分\n\n"
        f"当前积分：{total}\n\n"
        "18 积分可以兑换 30 天 VIP。"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "18积分兑换30天VIP",
                callback_data="redeem_vip"
            )
        ]
    ])

    await update.effective_message.reply_text(
        text,
        entities=custom_entities_all(
            text,
            POINTS_MARK,
            POINTS_EMOJI_ID
        ),
        reply_markup=keyboard
    )


async def redeem(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if query.data != "redeem_vip":
        await query.answer()
        return

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        SELECT points, vip_until
        FROM users
        WHERE user_id=?
        """, (user_id,))

        row = cur.fetchone()

        if not row:
            con.rollback()
            await query.answer("用户不存在。", show_alert=True)
            return

        current_points, vip_until = row

        if current_points < 18:
            con.rollback()
            await query.answer(
                "积分不足，需要18积分。",
                show_alert=True
            )
            return

        current_time = now_sg()
        old_until = parse_sg_datetime(vip_until)

        base_time = (
            old_until
            if old_until and old_until > current_time
            else current_time
        )

        new_until = (
            base_time + timedelta(days=30)
        ).isoformat(sep=" ", timespec="seconds")

        cur.execute("""
        UPDATE users
        SET points=points-18,
            vip_until=?
        WHERE user_id=?
        """, (new_until, user_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("积分兑换VIP失败")

        await query.answer(
            "兑换失败，请稍后重试。",
            show_alert=True
        )
        return

    finally:
        con.close()

    await query.answer("兑换成功！")

    await query.edit_message_text(
        "✅ VIP兑换成功！\n\n"
        "已扣除18积分。\n"
        "VIP有效期增加30天。\n"
        f"到期时间：{format_sg_time(new_until)}"
    )


# ==================================================
# 用户ID及个人资料
# ==================================================

async def myid(update, context):
    user = update.effective_user
    register_user(user)

    text = user_id_line(user.id)

    await update.effective_message.reply_text(
        text,
        entities=custom_entities_all(
            text,
            USER_MARK,
            USER_EMOJI_ID
        )
    )


async def profile(update, context):
    user = update.effective_user
    register_user(user)

    row = get_user(user.id)

    if not row:
        await update.effective_message.reply_text("用户资料不存在。")
        return

    username = (
        f"@{user.username}"
        if user.username
        else "未设置"
    )

    vip_until = row[5] if len(row) > 5 else None
    vip_time = parse_sg_datetime(vip_until)

    if vip_time and vip_time > now_sg():
        vip_status = f"有效至：{format_sg_time(vip_until)}"
    else:
        vip_status = "未开通"

    text = (
        f"{NICKNAME_MARK} 我的资料\n\n"
        f"昵称：{user.first_name or '未设置'}\n"
        f"用户名：{username}\n"
        f"{PROFILE_ID_MARK} 用户ID：{user.id}\n"
        f"积分：{row[4]}\n"
        f"VIP状态：{vip_status}\n"
        f"钱包状态：{'已冻结' if is_wallet_frozen(user.id) else '正常'}"
    )

    entities = (
        custom_entities_all(
            text,
            NICKNAME_MARK,
            NICKNAME_EMOJI_ID
        )
        + custom_entities_all(
            text,
            PROFILE_ID_MARK,
            PROFILE_ID_EMOJI_ID
        )
    )

    await update.effective_message.reply_text(
        text,
        entities=entities
    )


# ==================================================
# VIP套餐
# ==================================================

async def vip(update, context):
    user = update.effective_user
    register_user(user)

    text = (
        f"{VIP_MARK} VIP会员套餐\n\n"
        "7天 VIP：1 USDT\n"
        "30天 VIP：2 USDT\n"
        "90天 VIP：5 USDT\n"
        "365天 VIP：10 USDT\n\n"
        "请选择需要购买的套餐。"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "7天 VIP - 1 USDT",
                callback_data="vip_7天"
            )
        ],
        [
            InlineKeyboardButton(
                "30天 VIP - 2 USDT",
                callback_data="vip_30天"
            )
        ],
        [
            InlineKeyboardButton(
                "90天 VIP - 5 USDT",
                callback_data="vip_90天"
            )
        ],
        [
            InlineKeyboardButton(
                "365天 VIP - 10 USDT",
                callback_data="vip_365天"
            )
        ],
    ])

    await update.effective_message.reply_text(
        text,
        entities=custom_entities_all(
            text,
            VIP_MARK,
            VIP_EMOJI_ID
        ),
        reply_markup=keyboard
    )


async def vip_feature(update, context):
    user = update.effective_user
    register_user(user)

    row = get_user(user.id)
    vip_until = row[5] if row and len(row) > 5 else None

    parsed = parse_sg_datetime(vip_until)

    if not parsed or parsed <= now_sg():
        await update.effective_message.reply_text(
            "你目前没有有效的VIP会员，请先购买VIP套餐。"
        )
        return

    await update.effective_message.reply_text(
        "⭐ VIP专属功能\n\n"
        "你的VIP会员目前有效。\n"
        f"到期时间：{format_sg_time(vip_until)}"
    )


async def payment_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if not query.data.startswith("vip_"):
        await query.answer()
        return

    plan_name = query.data[4:]

    if plan_name not in PLANS:
        await query.answer("套餐不存在。", show_alert=True)
        return

    amount, days = PLANS[plan_name]
    payment_waiting.add(user_id)

    link = OKPAY_LINKS.get(plan_name)

    text = (
        f"你选择了 {plan_name} VIP。\n\n"
        f"支付金额：{amount} USDT\n"
        f"会员时长：{days}天\n\n"
        "请通过下方链接完成支付。\n"
        "支付完成后，请将支付成功截图发送给管理员核实。"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "前往支付",
                url=link
            )
        ],
        [
            InlineKeyboardButton(
                "取消",
                callback_data="payment_cancel"
            )
        ]
    ])

    await query.answer()

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


# ==================================================
# 管理员命令及统计
# ==================================================

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    text = (
        "管理员命令：\n\n"
        "/stats - 查看统计\n"
        "/users - 用户列表\n"
        "/userinfo 用户ID - 用户资料\n"
        "/addbalance 用户ID 金额 - 增加余额\n"
        "/delbalance 用户ID 金额 - 扣除余额\n"
        "/freezewallet 用户ID - 冻结钱包\n"
        "/unfreezewallet 用户ID - 解冻钱包\n"
        "/approve 提现订单ID - 通过提现\n"
        "/help - 查看帮助"
    )

    await update.effective_message.reply_text(text)


async def stats(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM referrals")
    total_referrals = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM withdrawals
    WHERE status='pending'
    """)
    pending_withdrawals = cur.fetchone()[0]

    cur.execute("""
    SELECT COALESCE(SUM(balance), 0)
    FROM balances
    """)
    total_balance = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM users
    WHERE wallet_frozen=1
    """)
    frozen_count = cur.fetchone()[0]

    con.close()

    text = (
        "📊 机器人统计\n\n"
        f"用户总数：{total_users}\n"
        f"邀请总数：{total_referrals}\n"
        f"待审核提现：{pending_withdrawals}\n"
        f"用户余额总额：{format_usdt(total_balance)} USDT\n"
        f"冻结钱包数量：{frozen_count}"
    )

    await update.effective_message.reply_text(text)


async def users_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT user_id, username, first_name, points, vip_until,
           wallet_frozen
    FROM users
    ORDER BY first_seen DESC
    LIMIT 50
    """)

    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.effective_message.reply_text("暂无用户。")
        return

    lines = ["👥 最近注册用户\n"]

    for row in rows:
        user_id, username, first_name, points_value, vip_until, frozen = row

        lines.append(
            f"{USER_MARK} {user_id} | "
            f"{first_name or '未知'} | "
            f"@{username or '无'} | "
            f"积分：{points_value} | "
            f"{'冻结' if frozen else '正常'}"
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
        await update.effective_message.reply_text("无权操作。")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/userinfo 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    row = get_user(target_id)

    if not row:
        await update.effective_message.reply_text("用户不存在。")
        return

    balance = get_balance(target_id)

    text = (
        f"{USER_MARK} 用户资料\n\n"
        f"用户ID：{target_id}\n"
        f"用户名：{row[1] or '无'}\n"
        f"昵称：{row[2] or '无'}\n"
        f"注册时间：{row[3] or '未知'}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '未开通'}\n"
        f"余额：{format_usdt(balance)} USDT\n"
        f"钱包状态：{'已冻结' if is_wallet_frozen(target_id) else '正常'}"
    )

    await update.effective_message.reply_text(
        text,
        entities=custom_entities_all(
            text,
            USER_MARK,
            USER_EMOJI_ID
        )
    )


# ==================================================
# 管理员余额命令
# ==================================================

async def add_balance_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/addbalance 用户ID 金额\n"
            "例如：/addbalance 123456789 10"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    amount = parse_usdt(context.args[1])

    if amount is None:
        await update.effective_message.reply_text(
            "金额格式错误，请输入大于0且最多两位小数的金额。"
        )
        return

    success, result = change_balance(target_id, amount)

    if not success:
        await update.effective_message.reply_text(result)
        return

    await update.effective_message.reply_text(
        f"✅ 已为用户 {target_id} 增加 {format_usdt(amount)} USDT。\n"
        f"当前余额：{format_usdt(result)} USDT"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "💰 管理员已为你的账户增加余额。\n"
                f"增加金额：{format_usdt(amount)} USDT\n"
                f"当前余额：{format_usdt(result)} USDT"
            )
        )
    except Exception:
        pass


async def del_balance_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/delbalance 用户ID 金额\n"
            "例如：/delbalance 123456789 10"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    amount = parse_usdt(context.args[1])

    if amount is None:
        await update.effective_message.reply_text(
            "金额格式错误，请输入大于0且最多两位小数的金额。"
        )
        return

    success, result = change_balance(target_id, -amount)

    if not success:
        await update.effective_message.reply_text(result)
        return

    await update.effective_message.reply_text(
        f"✅ 已扣除用户 {target_id} 的 {format_usdt(amount)} USDT。\n"
        f"当前余额：{format_usdt(result)} USDT"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "💰 管理员已调整你的账户余额。\n"
                f"扣除金额：{format_usdt(amount)} USDT\n"
                f"当前余额：{format_usdt(result)} USDT"
            )
        )
    except Exception:
        pass
        
        # ==================================================
# VIP 管理
# ==================================================

async def approve(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/approve 用户ID 天数"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("参数格式错误。")
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

    old_until = parse_sg_datetime(row[0])
    base = old_until if old_until and old_until > now_sg() else now_sg()

    new_until = (
        base + timedelta(days=days)
    ).isoformat(sep=" ", timespec="seconds")

    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (new_until, target_id)
    )

    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"已为用户 {target_id} 增加 {days} 天 VIP。\n"
        f"到期时间：{format_sg_time(new_until)}"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "🎉 管理员已为你开通VIP会员。\n"
                f"增加时长：{days}天\n"
                f"到期时间：{format_sg_time(new_until)}"
            )
        )
    except Exception:
        pass


async def reduce_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/reducevip 用户ID 天数"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("参数格式错误。")
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

    old_until = parse_sg_datetime(row[0])

    if not old_until or old_until <= now_sg():
        con.close()
        await update.effective_message.reply_text("用户当前没有有效VIP。")
        return

    new_until = old_until - timedelta(days=days)

    if new_until <= now_sg():
        new_value = None
    else:
        new_value = new_until.isoformat(
            sep=" ",
            timespec="seconds"
        )

    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (new_value, target_id)
    )

    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"已减少用户 {target_id} 的 {days} 天VIP。"
    )


async def clear_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/clearvip 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=NULL WHERE user_id=?",
        (target_id,)
    )

    changed = cur.rowcount
    con.commit()
    con.close()

    await update.effective_message.reply_text(
        "VIP已清除。" if changed else "用户不存在。"
    )


async def change_points(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "用法：/changepoints 用户ID 积分\n"
            "负数代表扣除积分。"
        )
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("参数格式错误。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT points FROM users WHERE user_id=?",
        (target_id,)
    )

    row = cur.fetchone()

    if not row:
        con.close()
        await update.effective_message.reply_text("用户不存在。")
        return

    new_points = row[0] + amount

    if new_points < 0:
        con.close()
        await update.effective_message.reply_text("用户积分不足。")
        return

    cur.execute(
        "UPDATE users SET points=? WHERE user_id=?",
        (new_points, target_id)
    )

    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"积分调整成功，当前积分：{new_points}"
    )


# ==================================================
# 管理员用户分页
# ==================================================

def get_users_page(page):
    offset = page * USER_PAGE_SIZE

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT user_id, username, first_name, points, vip_until,
           wallet_frozen
    FROM users
    ORDER BY first_seen DESC
    LIMIT ? OFFSET ?
    """, (USER_PAGE_SIZE, offset))

    rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    con.close()

    return rows, total


async def show_admin_user_page(query, page):
    rows, total = get_users_page(page)

    if not rows:
        await query.answer("没有更多用户。", show_alert=True)
        return

    lines = [
        f"👥 用户列表（第 {page + 1} 页）",
        f"总用户数：{total}",
        ""
    ]

    buttons = []

    for row in rows:
        user_id, username, first_name, points_value, vip_until, frozen = row

        lines.append(
            f"{USER_MARK} {user_id} | "
            f"{first_name or '未知'} | "
            f"{'冻结' if frozen else '正常'}"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{first_name or user_id}",
                callback_data=f"admin_user_{user_id}"
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "上一页",
                callback_data=f"admin_users_{page - 1}"
            )
        )

    if (page + 1) * USER_PAGE_SIZE < total:
        nav.append(
            InlineKeyboardButton(
                "下一页",
                callback_data=f"admin_users_{page + 1}"
            )
        )

    if nav:
        buttons.append(nav)

    text = "\n".join(lines)

    await query.edit_message_text(
        text,
        entities=custom_entities_all(
            text,
            USER_MARK,
            USER_EMOJI_ID
        ),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def open_admin_user_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "打开用户列表",
                callback_data="admin_users_0"
            )
        ]
    ])

    await update.effective_message.reply_text(
        "管理员用户管理",
        reply_markup=keyboard
    )


async def admin_send_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("无权操作。", show_alert=True)
        return

    data = query.data

    if data.startswith("admin_users_"):
        try:
            page = int(data.rsplit("_", 1)[1])
        except ValueError:
            await query.answer("页码错误。", show_alert=True)
            return

        await query.answer()
        await show_admin_user_page(query, page)
        return

    if data.startswith("admin_user_"):
        try:
            target_id = int(data.rsplit("_", 1)[1])
        except ValueError:
            await query.answer("用户ID错误。", show_alert=True)
            return

        await query.answer()

        row = get_user(target_id)

        if not row:
            await query.edit_message_text("用户不存在。")
            return

        balance = get_balance(target_id)
        frozen = is_wallet_frozen(target_id)

        text = (
            f"{USER_MARK} 用户管理\n\n"
            f"用户ID：{target_id}\n"
            f"昵称：{row[2] or '无'}\n"
            f"用户名：{row[1] or '无'}\n"
            f"积分：{row[4]}\n"
            f"余额：{format_usdt(balance)} USDT\n"
            f"VIP到期：{row[5] or '无'}\n"
            f"钱包状态：{'已冻结' if frozen else '正常'}"
        )

        freeze_button = (
            InlineKeyboardButton(
                "解冻钱包",
                callback_data=f"admin_unfreeze_{target_id}"
            )
            if frozen else
            InlineKeyboardButton(
                "冻结钱包",
                callback_data=f"admin_freeze_{target_id}"
            )
        )

        keyboard = InlineKeyboardMarkup([
            [freeze_button],
            [
                InlineKeyboardButton(
                    "返回用户列表",
                    callback_data="admin_users_0"
                )
            ]
        ])

        await query.edit_message_text(
            text,
            entities=custom_entities_all(
                text,
                USER_MARK,
                USER_EMOJI_ID
            ),
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_freeze_"):
        target_id = int(data.rsplit("_", 1)[1])

        if not get_user(target_id):
            await query.answer("用户不存在。", show_alert=True)
            return

        freeze_wallet_db(target_id)

        await query.answer("钱包已冻结。", show_alert=True)

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="🔒 管理员已冻结你的钱包，请联系管理员。"
            )
        except Exception:
            pass

        row = get_user(target_id)

        text = (
            f"{USER_MARK} 用户管理\n\n"
            f"用户ID：{target_id}\n"
            f"昵称：{row[2] or '无'}\n"
            "钱包状态：已冻结"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "解冻钱包",
                    callback_data=f"admin_unfreeze_{target_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "返回用户列表",
                    callback_data="admin_users_0"
                )
            ]
        ])

        await query.edit_message_text(
            text,
            entities=custom_entities_all(
                text,
                USER_MARK,
                USER_EMOJI_ID
            ),
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_unfreeze_"):
        target_id = int(data.rsplit("_", 1)[1])

        if not get_user(target_id):
            await query.answer("用户不存在。", show_alert=True)
            return

        unfreeze_wallet_db(target_id)

        await query.answer("钱包已解冻。", show_alert=True)

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ 管理员已解冻你的钱包。"
            )
        except Exception:
            pass

        row = get_user(target_id)

        text = (
            f"{USER_MARK} 用户管理\n\n"
            f"用户ID：{target_id}\n"
            f"昵称：{row[2] or '无'}\n"
            "钱包状态：正常"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "冻结钱包",
                    callback_data=f"admin_freeze_{target_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "返回用户列表",
                    callback_data="admin_users_0"
                )
            ]
        ])

        await query.edit_message_text(
            text,
            entities=custom_entities_all(
                text,
                USER_MARK,
                USER_EMOJI_ID
            ),
            reply_markup=keyboard
        )
        return


# ==================================================
# 管理员主动联系用户
# ==================================================

async def send_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("无权操作。")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "用法：/send 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("用户ID格式错误。")
        return

    if not get_user(target_id):
        await update.effective_message.reply_text("用户不存在。")
        return

    admin_message_targets[ADMIN_ID] = target_id

    await update.effective_message.reply_text(
        f"请发送要发给用户 {target_id} 的消息。\n"
        "发送 /cancel 取消。"
    )


async def cancel_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    admin_message_targets.pop(ADMIN_ID, None)

    await update.effective_message.reply_text("已取消。")


# ==================================================
# 双向联系
# ==================================================

def build_merged_text_entities(header, original_text, original_entities):
    combined = header + "\n\n" + original_text

    entities = custom_entities_all(
        combined,
        USER_MARK,
        USER_EMOJI_ID
    )

    prefix_length = len(
        (header + "\n\n").encode("utf-16-le")
    ) // 2

    for entity in original_entities or []:
        try:
            entities.append(
                MessageEntity(
                    type=entity.type,
                    offset=entity.offset + prefix_length,
                    length=entity.length,
                    url=entity.url,
                    user=entity.user,
                    language=entity.language,
                    custom_emoji_id=entity.custom_emoji_id
                )
            )
        except Exception:
            pass

    return combined, entities


async def forward_contact_message(update, context):
    message = update.effective_message
    user = update.effective_user

    register_user(user)

    username = (
        f"@{user.username}"
        if user.username
        else "无用户名"
    )

    header = (
        "📩 用户联系消息\n\n"
        f"{USER_MARK} 用户ID：{user.id}\n"
        f"昵称：{user.first_name or '未知'}\n"
        f"用户名：{username}"
    )

    # 文字消息：合并为一条
    if message.text:
        combined, entities = build_merged_text_entities(
            header,
            message.text,
            message.entities
        )

        sent = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=combined,
            entities=entities
        )

        save_reply_message(sent.message_id, user.id)

        await message.reply_text("消息已发送给管理员。")
        return

    # 支持 caption 的媒体：尽量合并为一条
    if message.caption:
        combined_caption = header + "\n\n" + message.caption

        if len(combined_caption) <= 1024:
            caption_entities = custom_entities_all(
                combined_caption,
                USER_MARK,
                USER_EMOJI_ID
            )

            media_kwargs = {
                "chat_id": ADMIN_ID,
                "caption": combined_caption,
                "caption_entities": caption_entities
            }

            sent = None

            try:
                if message.photo:
                    sent = await context.bot.send_photo(
                        photo=message.photo[-1].file_id,
                        **media_kwargs
                    )

                elif message.video:
                    sent = await context.bot.send_video(
                        video=message.video.file_id,
                        **media_kwargs
                    )

                elif message.document:
                    sent = await context.bot.send_document(
                        document=message.document.file_id,
                        **media_kwargs
                    )

                elif message.animation:
                    sent = await context.bot.send_animation(
                        animation=message.animation.file_id,
                        **media_kwargs
                    )

                elif message.audio:
                    sent = await context.bot.send_audio(
                        audio=message.audio.file_id,
                        **media_kwargs
                    )

            except Exception:
                logger.exception("合并媒体消息失败")

            if sent:
                save_reply_message(sent.message_id, user.id)
                await message.reply_text("消息已发送给管理员。")
                return

    # 不支持合并的消息：使用两条消息回退
    sent_header = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=header,
        entities=custom_entities_all(
            header,
            USER_MARK,
            USER_EMOJI_ID
        )
    )

    save_reply_message(sent_header.message_id, user.id)

    try:
        copied = await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        save_reply_message(copied.message_id, user.id)

    except Exception:
        logger.exception("转发用户消息失败")

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚠️ 用户消息复制失败，请检查机器人权限。"
        )

    await message.reply_text("消息已发送给管理员。")


async def admin_reply_contact(update, context):
    message = update.effective_message

    if update.effective_user.id != ADMIN_ID:
        return

    if not message.reply_to_message:
        return

    target_id = get_reply_user(
        message.reply_to_message.message_id
    )

    if not target_id:
        return

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        await message.reply_text("回复已发送。")

    except Exception:
        logger.exception("管理员回复用户失败")
        await message.reply_text("发送失败，用户可能已屏蔽机器人。")


# ==================================================
# 安全设置
# ==================================================

async def security_settings(update, context):
    user_id = update.effective_user.id

    frozen = is_wallet_frozen(user_id)
    password_exists = has_trade_password(user_id)

    text = (
        "🔐 安全设置\n\n"
        f"交易密码：{'已设置' if password_exists else '未设置'}\n"
        f"钱包状态：{'已冻结' if frozen else '正常'}\n\n"
        "请勿向任何人透露你的交易密码。"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "设置交易密码" if not password_exists else "修改交易密码",
                callback_data="security_change"
            )
        ]
    ])

    await update.effective_message.reply_text(
        text,
        reply_markup=keyboard
    )


# ==================================================
# 消息处理
# ==================================================

async def handle_message(update, context):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    register_user(user)

    user_id = user.id
    text = message.text or ""

    # 管理员主动给用户发送消息
    if user_id == ADMIN_ID and user_id in admin_message_targets:
        target_id = admin_message_targets[user_id]

        try:
            await context.bot.copy_message(
                chat_id=target_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            await message.reply_text("消息已发送。")

        except Exception:
            logger.exception("管理员发送消息失败")
            await message.reply_text("发送失败。")

        admin_message_targets.pop(user_id, None)
        return

    # 管理员回复用户消息
    if user_id == ADMIN_ID and message.reply_to_message:
        target_id = get_reply_user(
            message.reply_to_message.message_id
        )

        if target_id:
            await admin_reply_contact(update, context)
            return

    if text.startswith("/"):
        return

    if text == "❌ 退出菜单":
        clear_mode(user_id)

        await message.reply_text(
            "已退出当前操作。",
            reply_markup=main_keyboard()
        )
        return

    # 用户提交收款地址
    if context.user_data.get("withdraw_state") == "address":
        if is_wallet_frozen(user_id):
            context.user_data.pop("withdraw_state", None)

            await message.reply_text(
                "🔒 钱包已冻结，无法继续提现。"
            )
            return

        address = text.strip()

        if not TRON_ADDRESS_PATTERN.fullmatch(address):
            await message.reply_text(
                "地址格式不正确，请输入有效的TRC20地址。\n"
                "TRC20地址通常以 T 开头。"
            )
            return

        context.user_data["withdraw_address"] = address
        context.user_data["withdraw_state"] = "address_confirm"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "确认地址",
                    callback_data="withdraw_address_confirm"
                ),
                InlineKeyboardButton(
                    "取消",
                    callback_data="withdraw_address_cancel"
                )
            ]
        ])

        await message.reply_text(
            f"请确认收款地址：\n\n{address}",
            reply_markup=keyboard
        )
        return

    # 用户提交提现金额
    if context.user_data.get("withdraw_state") == "amount":
        if is_wallet_frozen(user_id):
            context.user_data.pop("withdraw_state", None)
            context.user_data.pop("withdraw_address", None)

            await message.reply_text(
                "🔒 钱包已冻结，无法继续提现。"
            )
            return

        amount = parse_usdt(text)

        if amount is None or amount < MIN_WITHDRAW:
            await message.reply_text(
                f"最低提现金额为 {format_usdt(MIN_WITHDRAW)} USDT，"
                "请输入有效金额。"
            )
            return

        context.user_data["withdraw_amount"] = amount

        balance = get_balance(user_id)
        total = amount + WITHDRAW_FEE

        if balance < total:
            await message.reply_text(
                "余额不足。\n\n"
                f"当前余额：{format_usdt(balance)} USDT\n"
                f"提现金额：{format_usdt(amount)} USDT\n"
                f"手续费：{format_usdt(WITHDRAW_FEE)} USDT"
            )
            return

        address = context.user_data.get("withdraw_address")

        if not address:
            context.user_data["withdraw_state"] = "address"
            await message.reply_text("请重新发送收款地址。")
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "确认提现",
                    callback_data="withdraw_final_confirm"
                ),
                InlineKeyboardButton(
                    "取消",
                    callback_data="withdraw_cancel"
                )
            ]
        ])

        await message.reply_text(
            "请确认提现信息：\n\n"
            f"提现金额：{format_usdt(amount)} USDT\n"
            f"手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
            f"总扣除：{format_usdt(total)} USDT\n"
            f"收款地址：{address}",
            reply_markup=keyboard
        )
        context.user_data["withdraw_state"] = "final_confirm"
        return

    # 双向联系开启
    if text == "💬 双向联系":
        contact_waiting.add(user_id)
        await message.reply_text(
            "💬 双向联系已开启。\n"
            "你可以连续发送文字、图片、视频和文件。\n"
            "管理员回复后，消息会转发给你。\n"
            "点击「❌ 退出联系」结束。"
        )
        return

    # 退出双向联系
    if text == "❌ 退出联系":
        contact_waiting.discard(user_id)
        await message.reply_text("❌ 双向联系已关闭。")
        return

    # 联系期间允许连续发送，不自动退出
    if user_id in contact_waiting:
        await forward_contact_message(update, context)
        return

    # 菜单功能
    if text == "👤 我的资料":
        await profile(update, context)
        return

    if text == "🆔 查询用户ID":
        await myid(update, context)
        return

    if text == "⭐ VIP会员":
        await vip(update, context)
        return

    if text == "✈️ VIP功能":
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

    if text == "🔐 设置交易密码":
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
        return

    if text == "🎁 每日签到":
        await checkin(update, context)
        return

    if text == "👨‍💻 联系管理员":
        contact_waiting.add(user_id)

        await message.reply_text(
            "请发送你要联系管理员的消息。"
        )
        return

    if text == "我的积分":
        await points(update, context)
        return

    # 支付截图
    if user_id in payment_waiting:
        payment_waiting.discard(user_id)

        try:
            await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💳 用户提交了支付凭证。\n"
                    f"{USER_MARK} 用户ID：{user_id}"
                ),
                entities=custom_entities_all(
                    f"{USER_MARK} 用户ID：{user_id}",
                    USER_MARK,
                    USER_EMOJI_ID
                )
            )

            await message.reply_text(
                "支付凭证已发送给管理员，请等待核实。"
            )

        except Exception:
            logger.exception("支付凭证转发失败")
            await message.reply_text("发送失败，请稍后重试。")

        return

    # 普通用户其他消息
    if user_id != ADMIN_ID:
        await message.reply_text(
            "请使用下方菜单选择功能。",
            reply_markup=main_keyboard()
        )


# ==================================================
# 回调总处理
# ==================================================

async def callback_router(update, context):
    query = update.callback_query
    data = query.data or ""

    if data.startswith("trade_"):
        await trade_password_callback(update, context)
        return

    if data.startswith("withdraw_"):
        if data in (
            "withdraw_address_confirm",
            "withdraw_address_cancel"
        ):
            await withdraw_address_callback(update, context)
        else:
            await withdraw_callback(update, context)
        return

    if data.startswith("vip_"):
        await payment_callback(update, context)
        return

    if data == "redeem_vip":
        await redeem(update, context)
        return

    if data == "security_change":
        if query.from_user.id != ADMIN_ID and is_wallet_frozen(query.from_user.id):
            await query.answer(
                "钱包已冻结，无法修改交易密码。",
                show_alert=True
            )
            return

        await query.answer()

        if has_trade_password(query.from_user.id):
            context.user_data["trade_flow"] = "change_verify"
            context.user_data["trade_digits"] = ""
            flow = "change_verify"
        else:
            context.user_data["trade_flow"] = "set_first"
            context.user_data["trade_digits"] = ""
            flow = "set_first"

        sent = await query.message.reply_text(
            trade_prompt(flow),
            reply_markup=trade_password_keyboard()
        )

        context.user_data["trade_message_id"] = sent.message_id
        context.user_data["trade_chat_id"] = sent.chat_id
        return

    if data.startswith("admin_"):
        await admin_send_callback(update, context)
        return

    if data == "payment_cancel":
        payment_waiting.discard(query.from_user.id)

        await query.answer("已取消。")
        await query.edit_message_text("已取消支付操作。")
        return

    await query.answer()


# ==================================================
# 帮助
# ==================================================

async def help_command(update, context):
    if update.effective_user.id == ADMIN_ID:
        text = (
            "管理员帮助\n\n"
            "/admin - 管理员菜单\n"
            "/stats - 统计信息\n"
            "/users - 用户列表\n"
            "/userinfo 用户ID - 用户资料\n"
            "/addbalance 用户ID 金额 - 增加余额\n"
            "/delbalance 用户ID 金额 - 扣除余额\n"
            "/freezewallet 用户ID - 冻结钱包\n"
            "/unfreezewallet 用户ID - 解冻钱包\n"
            "/approve 用户ID 天数 - 增加VIP\n"
            "/reducevip 用户ID 天数 - 减少VIP\n"
            "/clearvip 用户ID - 清除VIP\n"
            "/changepoints 用户ID 积分 - 调整积分\n"
            "/send 用户ID - 主动联系用户\n"
            "/cancel - 取消主动联系"
        )
    else:
        text = (
            "用户帮助\n\n"
            "使用菜单可以查看资料、购买VIP、签到、"
            "查看余额、邀请好友及申请提现。\n\n"
            "18积分可以兑换30天VIP。\n"
            "提现需要设置交易密码。"
        )

    await update.effective_message.reply_text(text)


# ==================================================
# Bot 命令菜单
# ==================================================

async def post_init(application):
    user_commands = [
        BotCommand("start", "开始使用"),
        BotCommand("help", "帮助"),
    ]

    await application.bot.set_my_commands(user_commands)

    admin_commands = user_commands + [
        BotCommand("admin", "管理员菜单"),
        BotCommand("stats", "统计信息"),
        BotCommand("users", "用户列表"),
        BotCommand("userinfo", "用户资料"),
        BotCommand("addbalance", "增加余额"),
        BotCommand("delbalance", "扣除余额"),
        BotCommand("freezewallet", "冻结钱包"),
        BotCommand("unfreezewallet", "解冻钱包"),
        BotCommand("approve", "增加VIP"),
        BotCommand("reducevip", "减少VIP"),
        BotCommand("clearvip", "清除VIP"),
        BotCommand("changepoints", "调整积分"),
        BotCommand("send", "主动联系用户"),
        BotCommand("cancel", "取消操作"),
    ]

    try:
        await application.bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=ADMIN_ID)
        )
    except Exception:
        logger.exception("设置管理员命令菜单失败")


# ==================================================
# 程序入口
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "未设置 BOT_TOKEN 环境变量。"
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # 命令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("users", users_list))
    application.add_handler(CommandHandler("userinfo", user_info))

    application.add_handler(
        CommandHandler("addbalance", add_balance_command)
    )

    application.add_handler(
        CommandHandler("delbalance", del_balance_command)
    )

    application.add_handler(
        CommandHandler("freezewallet", freeze_wallet)
    )

    application.add_handler(
        CommandHandler("unfreezewallet", unfreeze_wallet)
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
        CommandHandler("changepoints", change_points)
    )

    application.add_handler(
        CommandHandler("send", send_admin_message)
    )

    application.add_handler(
        CommandHandler("cancel", cancel_admin_message)
    )

    # 回调
    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    # 普通消息及媒体
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info("VIP机器人启动成功。")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()