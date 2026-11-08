import os
import re
import sqlite3
import logging
import asyncio
import hashlib
import hmac
import secrets
import html

from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
    BotCommand,
    BotCommandScopeChat,
    MessageEntity,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
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

VIP_MARK = USER_MARK = CONTACT_MARK = ADMIN_MARK = "⭐"
POINTS_MARK = CHECKIN_MARK = VIP_FEATURE_MARK = "⭐"
NICKNAME_MARK = PROFILE_ID_MARK = "⭐"
USDT_MARK = "💰"

INVITE_REWARD = 1000

# 1 USDT = 100 cents
WITHDRAW_FEE = 100
MIN_WITHDRAW = 1000

USER_PAGE_SIZE = 20

TRON_ADDRESS_PATTERN = re.compile(
    r"^T[1-9A-HJ-NP-Za-km-z]{33}$"
)

TRANSFER_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d{1,2})?)\s*u\s*$",
    re.IGNORECASE
)

MENU = [
    ["💬 双向联系", "👤 我的资料"],
    ["🆔 查询用户ID", "💎 VIP会员"],
    ["✈️ VIP功能"],
    ["💰 我的余额", "🔗 邀请好友"],
    ["💸 申请提现"],
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
admin_message_targets = {}


# ==================================================
# 基础工具
# ==================================================

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    return con


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

    except (InvalidOperation, ValueError, AttributeError):
        return None


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
    result = []
    start = 0

    while True:
        index = text.find(marker, start)

        if index < 0:
            break

        offset = len(text[:index].encode("utf-16-le")) // 2
        length = len(marker.encode("utf-16-le")) // 2

        result.append(
            MessageEntity(
                type="custom_emoji",
                offset=offset,
                length=length,
                custom_emoji_id=emoji_id
            )
        )

        start = index + len(marker)

    return result


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
    return f"{USER_MARK} 用户：{user_id}"


def usdt_entities(text):
    return custom_entities_all(text, USDT_MARK, USDT_EMOJI_ID)


# ==================================================
# 数据库初始化
# ==================================================

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

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_withdrawals_user
    ON withdrawals(user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_referrals_inviter
    ON referrals(inviter_id)
    """)

    # 交易密码：只保存随机盐值和 PBKDF2 哈希
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_passwords (
        user_id INTEGER PRIMARY KEY,
        salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT
    )
    """)

    # Inline USDT 转账
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER,
        amount INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        claimed_at TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_transfers_receiver
    ON transfers(receiver_id)
    """)

    cur.execute("""
    INSERT OR IGNORE INTO balances(user_id, balance)
    SELECT user_id, 0 FROM users
    """)

    con.commit()
    con.close()


# ==================================================
# 用户与余额
# ==================================================

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
    UPDATE users SET username=?, first_name=?
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

    row = con.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    con.close()
    return row


def get_balance(user_id):
    con = db()

    row = con.execute(
        "SELECT balance FROM balances WHERE user_id=?",
        (user_id,)
    ).fetchone()

    con.close()
    return row[0] if row else 0


def get_invite_count(user_id):
    con = db()

    count = con.execute(
        "SELECT COUNT(*) FROM referrals WHERE inviter_id=?",
        (user_id,)
    ).fetchone()[0]

    con.close()
    return count


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
        (admin_message_id,)
    ).fetchone()

    con.close()
    return row[0] if row else None


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

        if not cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (invited_id,)
        ).fetchone():
            con.rollback()
            return False

        if cur.execute(
            "SELECT 1 FROM referrals WHERE invited_id=?",
            (invited_id,)
        ).fetchone():
            con.rollback()
            return False

        if not cur.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (inviter_id,)
        ).fetchone():
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
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
        """, (inviter_id,))

        cur.execute("""
        UPDATE balances SET balance=balance+?
        WHERE user_id=?
        """, (INVITE_REWARD, inviter_id))

        con.commit()
        return True

    except Exception:
        con.rollback()
        logger.exception("邀请奖励发放失败")
        return False

    finally:
        con.close()


# ==================================================
# 交易密码功能
# ==================================================

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


def hash_trade_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        300000
    ).hex()


def save_trade_password(user_id, password):
    salt = secrets.token_hex(16)
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
        return False, "你还没有设置交易密码。"

    salt, saved_hash, failed, locked_until = row
    now = datetime.now()

    if locked_until:
        try:
            lock_time = datetime.fromisoformat(locked_until)

            if now < lock_time:
                minutes = max(
                    1,
                    int((lock_time - now).total_seconds() + 59) // 60
                )

                return False, f"密码已锁定，请 {minutes} 分钟后再试。"

        except ValueError:
            pass

    candidate = hash_trade_password(password, salt)

    if hmac.compare_digest(candidate, saved_hash):
        con = db()

        con.execute("""
        UPDATE trade_passwords
        SET failed_attempts=0, locked_until=NULL
        WHERE user_id=?
        """, (user_id,))

        con.commit()
        con.close()

        return True, "验证成功"

    failed += 1
    con = db()

    if failed >= 5:
        locked = (
            now + timedelta(minutes=15)
        ).isoformat(sep=" ", timespec="seconds")

        con.execute("""
        UPDATE trade_passwords
        SET failed_attempts=0, locked_until=?
        WHERE user_id=?
        """, (locked, user_id))

        con.commit()
        con.close()

        return False, "密码错误次数过多，已锁定15分钟。"

    con.execute("""
    UPDATE trade_passwords SET failed_attempts=?
    WHERE user_id=?
    """, (failed, user_id))

    con.commit()
    con.close()

    return False, f"密码错误，还可以尝试 {5 - failed} 次。"


def trade_password_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="trade_1"),
            InlineKeyboardButton("2", callback_data="trade_2"),
            InlineKeyboardButton("3", callback_data="trade_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="trade_4"),
            InlineKeyboardButton("5", callback_data="trade_5"),
            InlineKeyboardButton("6", callback_data="trade_6"),
        ],
        [
            InlineKeyboardButton("7", callback_data="trade_7"),
            InlineKeyboardButton("8", callback_data="trade_8"),
            InlineKeyboardButton("9", callback_data="trade_9"),
        ],
        [
            InlineKeyboardButton("清除", callback_data="trade_clear"),
            InlineKeyboardButton("0", callback_data="trade_0"),
            InlineKeyboardButton("取消", callback_data="trade_cancel"),
        ],
    ])


def trade_prompt(mode, digits):
    labels = {
        "setup": "请输入6位交易密码",
        "old": "请输入原交易密码",
        "new": "请输入新的6位交易密码",
    }

    dots = " ".join(
        "●" if i < len(digits) else "○"
        for i in range(6)
    )

    return f"{labels[mode]}\n\n{dots}"


async def begin_trade_password(update, context, mode):
    user_id = update.effective_user.id

    context.user_data["trade_state"] = {
        "mode": mode,
        "digits": "",
        "owner": user_id
    }

    await update.effective_message.reply_text(
        trade_prompt(mode, ""),
        reply_markup=trade_password_keyboard()
    )


async def trade_password_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    state = context.user_data.get("trade_state")

    if not state or state.get("owner") != user_id:
        await query.answer(
            "操作已失效，请重新开始。",
            show_alert=True
        )
        return

    await query.answer()
    data = query.data

    if data == "trade_cancel":
        context.user_data.pop("trade_state", None)
        await query.edit_message_text("已取消交易密码操作。")
        return

    digits = state["digits"]

    if data == "trade_clear":
        digits = ""

    elif data.startswith("trade_"):
        digit = data[6:]

        if (
            digit.isdigit()
            and len(digit) == 1
            and len(digits) < 6
        ):
            digits += digit

    state["digits"] = digits
    mode = state["mode"]

    if len(digits) < 6:
        await query.edit_message_text(
            trade_prompt(mode, digits),
            reply_markup=trade_password_keyboard()
        )
        return

    context.user_data.pop("trade_state", None)

    if mode == "setup":
        if has_trade_password(user_id):
            await query.edit_message_text(
                "你已经设置过交易密码。"
            )
            return

        save_trade_password(user_id, digits)

        await query.edit_message_text(
            "✅ 交易密码设置成功。"
        )
        return

    if mode == "old":
        valid, result = verify_trade_password(
            user_id,
            digits
        )

        if not valid:
            await query.edit_message_text(result)
            return

        context.user_data["trade_state"] = {
            "mode": "new",
            "digits": "",
            "owner": user_id
        }

        await query.edit_message_text(
            trade_prompt("new", ""),
            reply_markup=trade_password_keyboard()
        )
        return

    if mode == "new":
        save_trade_password(user_id, digits)

        await query.edit_message_text(
            "✅ 交易密码修改成功。"
        )


async def change_password_command(update, context):
    if not has_trade_password(update.effective_user.id):
        await update.message.reply_text(
            "你还没有设置交易密码，请先设置。"
        )
        return

    await begin_trade_password(
        update,
        context,
        "old"
    )
    
    # ==================================================
# Inline USDT 转账
# ==================================================

def parse_transfer_amount(query):
    match = TRANSFER_PATTERN.fullmatch(query or "")
    if not match:
        return None
    return parse_usdt(match.group(1))


def create_transfer(sender_id, amount):
    token = secrets.token_urlsafe(8)
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT balance FROM balances WHERE user_id=?",
            (sender_id,)
        ).fetchone()

        if not row or row[0] < amount:
            con.rollback()
            return None
        cur.execute("""
        INSERT INTO transfers
        (token, sender_id, amount, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        """, (
            token,
            sender_id,
            amount,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        con.commit()
        return token

    except Exception:
        con.rollback()
        logger.exception("创建转账失败")
        return None
    finally:
        con.close()


async def inline_transfer_query(update, context):
    query = update.inline_query
    sender = query.from_user

    amount = parse_transfer_amount(query.query)

    if amount is None:
        await query.answer([], cache_time=1, is_personal=True)
        return

    register_user(sender)

    if get_balance(sender.id) < amount:
        await query.answer([], cache_time=1, is_personal=True)
        return

    token = create_transfer(sender.id, amount)
    if not token:
        await query.answer([], cache_time=1, is_personal=True)
        return

    sender_name = html.escape(sender.first_name or "用户")

    message_text = (
        f'<a href="tg://user?id={sender.id}">{sender_name}</a>'
        f' 转账给你 <b>{format_usdt(amount)} USDT</b>\n'
        "点击下方按钮领取。"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎁 领取",
            callback_data=f"tr_{token}"
        )
    ]])
    result = InlineQueryResultArticle(
        id=token,
        title=f"发送 {format_usdt(amount)} USDT",
        description="发送一笔站内 USDT 转账",
        input_message_content=InputTextMessageContent(
            message_text,
            parse_mode="HTML"
        ),
        reply_markup=keyboard
    )

    await query.answer(
        [result],
        cache_time=1,
        is_personal=True
    )


def claim_transfer(token, receiver_id):
    con = db()
    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        row = cur.execute("""
        SELECT sender_id, amount, status
        FROM transfers WHERE token=?
        """, (token,)).fetchone()

        if not row:
            con.rollback()
            return False, "转账记录不存在。", 0

        sender_id, amount, status = row

        if sender_id == receiver_id:
            con.rollback()
            return False, "不能领取自己发出的转账。", amount
        if status != "pending":
            con.rollback()
            return False, "这笔转账已经被领取或失效。", amount

        cur.execute("""
        UPDATE balances
        SET balance=balance-?
        WHERE user_id=? AND balance>=?
        """, (amount, sender_id, amount))

        if cur.rowcount != 1:
            con.rollback()
            return False, "转账人余额不足，这笔转账无法领取。", amount
        cur.execute("""
        INSERT OR IGNORE INTO balances(user_id, balance)
        VALUES (?, 0)
        """, (receiver_id,))

        cur.execute("""
        UPDATE balances SET balance=balance+?
        WHERE user_id=?
        """, (amount, receiver_id))
        cur.execute("""
        UPDATE transfers
        SET status='claimed',
            receiver_id=?,
            claimed_at=?
        WHERE token=? AND status='pending'
        """, (
            receiver_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            token
        ))

        if cur.rowcount != 1:
            con.rollback()
            return False, "领取失败，请重试。", amount

        con.commit()
        return True, "领取成功", amount
    except Exception:
        con.rollback()
        logger.exception("领取转账失败")
        return False, "系统处理失败，请稍后重试。", 0
    finally:
        con.close()


def transfer_history(user_id):
    con = db()
    rows = con.execute("""
    SELECT amount, sender_id, claimed_at
    FROM transfers
    WHERE receiver_id=? AND status='claimed'
    ORDER BY id DESC LIMIT 10
    """, (user_id,)).fetchall()
    con.close()
    return rows


async def inline_transfer_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "tr_wallet":
        balance = get_balance(user_id)
        await query.answer(
            f"钱包余额：{format_usdt(balance)} USDT",
            show_alert=True
        )
        return

    if data == "tr_list":
        rows = transfer_history(user_id)

        if not rows:
            await query.answer("暂时没有领取记录。", show_alert=True)
            return
        lines = ["最近领取记录："]
        for amount, sender_id, claimed_at in rows:
            lines.append(
                f"{format_usdt(amount)} USDT · 来自 {sender_id} · {claimed_at}"
            )

        await query.answer("\n".join(lines), show_alert=True)
        return

    if not data.startswith("tr_"):
        return

    token = data[3:]
    success, result, amount = claim_transfer(token, user_id)

    if not success:
        await query.answer(result, show_alert=True)
        return
    await query.answer(
        f"领取成功{format_usdt(amount)}USDT！",
        show_alert=True
    )

    receiver_name = html.escape(query.from_user.first_name or "用户")

    text = (
        f'<a href="tg://user?id={user_id}">{receiver_name}</a>'
        f' 已领取 <b>{format_usdt(amount)} USDT</b> 🎉'
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 钱包", callback_data="tr_wallet"),
            InlineKeyboardButton("📋 领取列表", callback_data="tr_list"),
        ]
    ])
    try:
        if query.inline_message_id:
            await context.bot.edit_message_text(
                inline_message_id=query.inline_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        elif query.message:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
    except Exception:
        logger.exception("更新转账消息失败")


# ==================================================
# 余额和邀请
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
    await update.effective_message.reply_text(text, entities=entities)


async def show_invite(update, context):
    user = update.effective_user
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{user.id}"
    await update.effective_message.reply_text(
        "🔗 专属邀请链接\n\n"
        f"{link}\n\n"
        "每成功邀请一位新用户，可获得10 USDT。\n"
        f"当前成功邀请人数：{get_invite_count(user.id)}"
    )


# ==================================================
# 提现
# ==================================================

def withdraw_cancel_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ 取消提现", callback_data="withdraw_cancel")
    ]])


async def begin_withdraw(update, context):
    user = update.effective_user
    register_user(user)

    balance = get_balance(user.id)
    if balance < MIN_WITHDRAW:
        await update.effective_message.reply_text(
            f"当前余额：{format_usdt(balance)} USDT\n"
            "最低提现金额为10 USDT。"
        )
        return

    context.user_data["withdraw_state"] = "address"

    await update.effective_message.reply_text(
        "请发送你的 TRC20 USDT 收款地址。"
    )


async def create_withdrawal(update, context):
    user = update.effective_user
    user_id = user.id
    address = context.user_data.get("withdraw_address")
    if not address:
        await update.effective_message.reply_text("提现地址已失效，请重新申请。")
        context.user_data.pop("withdraw_state", None)
        return

    amount = parse_usdt(update.effective_message.text or "")

    if amount is None or amount < MIN_WITHDRAW:
        await update.effective_message.reply_text(
            "金额格式不正确或低于最低提现金额10 USDT。"
        )
        return

    if amount <= WITHDRAW_FEE:
        await update.effective_message.reply_text("申请金额必须大于手续费。")
        return
    payout = amount - WITHDRAW_FEE
    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
        UPDATE balances SET balance=balance-?
        WHERE user_id=? AND balance>=?
        """, (amount, user_id, amount))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text("余额不足。")
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
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)
    text = (
        f"💸 新的USDT提现申请 #{withdrawal_id}\n"
        f"用户：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username or '未设置'}\n"
        f"用户ID：{user_id}\n"
        f"TRC20地址：{address}\n"
        f"申请金额：{format_usdt(amount)} USDT\n"
        f"手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"实际到账：{format_usdt(payout)} USDT"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ 同意提现",
            callback_data=f"withdraw_approve_{withdrawal_id}"
        )],
        [InlineKeyboardButton(
            "❌ 拒绝提现",
            callback_data=f"withdraw_reject_{withdrawal_id}"
        )]
    ])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            text,
            entities=usdt_entities(text),
            reply_markup=keyboard
        )
    except Exception:
        logger.exception("通知管理员失败")
        con = db()
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
        UPDATE withdrawals SET status='notification_failed'
        WHERE id=? AND status='pending'
        """, (withdrawal_id,))
        if cur.rowcount == 1:
            cur.execute("""
            UPDATE balances SET balance=balance+?
            WHERE user_id=?
            """, (amount, user_id))
        con.commit()
        con.close()
        await update.effective_message.reply_text("通知管理员失败，提现已撤销并退款。")
        return
    await update.effective_message.reply_text(
        "✅ 提现申请已提交，等待管理员审核。\n"
        f"申请金额：{format_usdt(amount)} USDT\n"
        f"手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"预计到账：{format_usdt(payout)} USDT"
    )


async def withdraw_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if data == "withdraw_cancel":
        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)
        await query.answer()
        await query.edit_message_text("已取消提现申请。")
        return

    if user_id != ADMIN_ID:
        await query.answer("你没有权限操作。", show_alert=True)
        return

    await query.answer()

    try:
        withdrawal_id = int(data.rsplit("_", 1)[1])
    except ValueError:
        return
    if data.startswith("withdraw_approve_"):
        con = db()
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        row = cur.execute("""
        SELECT user_id, amount, fee, payout, status
        FROM withdrawals WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row or row[4] != "pending":
            con.rollback()
            con.close()
            await query.answer("申请不存在或已处理。", show_alert=True)
            return
        target_id, amount, fee, payout, _ = row

        cur.execute("""
        UPDATE withdrawals
        SET status='approved', admin_id=?, reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            ADMIN_ID,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            withdrawal_id
        ))

        con.commit()
        con.close()

        await query.edit_message_text(
            query.message.text + "\n\n✅ 审核通过，等待管理员手动转账。"
        )
        await context.bot.send_message(
            target_id,
            "✅ 你的提现申请已审核通过。\n"
            f"申请金额：{format_usdt(amount)} USDT\n"
            f"手续费：{format_usdt(fee)} USDT\n"
            f"实际到账：{format_usdt(payout)} USDT"
        )
        return

    if data.startswith("withdraw_reject_"):
        context.user_data["reject_withdraw_id"] = withdrawal_id
        await query.edit_message_text(
            query.message.text + "\n\n请发送拒绝理由。"
        )


async def finish_withdraw_reject(update, context):
    withdrawal_id = context.user_data.get("reject_withdraw_id")
    if not withdrawal_id or update.effective_user.id != ADMIN_ID:
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
            context.user_data.pop("reject_withdraw_id", None)
            return True

        target_id, amount, _ = row
        cur.execute("""
        UPDATE withdrawals
        SET status='rejected', reason=?, admin_id=?, reviewed_at=?
        WHERE id=? AND status='pending'
        """, (
            reason,
            ADMIN_ID,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            withdrawal_id
        ))

        if cur.rowcount != 1:
            con.rollback()
            return True
        cur.execute("""
        UPDATE balances SET balance=balance+?
        WHERE user_id=?
        """, (amount, target_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("提现退款失败")
        await update.effective_message.reply_text("退款处理失败。")
        return True
    finally:
        con.close()

    context.user_data.pop("reject_withdraw_id", None)

    await update.effective_message.reply_text(
        f"已拒绝申请 #{withdrawal_id}，已退款。"
    )
    try:
        await context.bot.send_message(
            target_id,
            f"❌ 提现申请未通过。\n拒绝理由：{reason}\n"
            f"已退回：{format_usdt(amount)} USDT"
        )
    except Exception:
        logger.exception("提现拒绝通知失败")

    return True


async def withdraw_address_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "withdraw_address_ok":
        if context.user_data.get("withdraw_state") != "confirm_address":
            await query.answer("地址状态已失效。", show_alert=True)
            return

        context.user_data["withdraw_state"] = "amount"
        balance = get_balance(query.from_user.id)

        await query.edit_message_text(
            f"当前余额：{format_usdt(balance)} USDT\n"
            "请输入提现金额。最低10 USDT，手续费1 USDT。"
        )
    elif query.data == "withdraw_address_retry":
        context.user_data["withdraw_state"] = "address"
        context.user_data.pop("withdraw_address", None)
        await query.edit_message_text("请重新发送TRC20收款地址。")
        
        # ==================================================
# 签到、积分、VIP
# ==================================================

async def checkin(update, context):
    user = update.effective_user
    register_user(user)
    con = db()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO checkins VALUES (?, ?)",
            (user.id, date.today().isoformat())
        )
        cur.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?",
            (user.id,)
        )
        con.commit()
        row = get_user(user.id)
        await update.effective_message.reply_text(
            f"🎁 签到成功！获得1积分。\n当前积分：{row[4]}"
        )
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text("今天已经签到过了。")
    finally:
        con.close()


async def points(update, context):
    register_user(update.effective_user)
    row = get_user(update.effective_user.id)
    await update.effective_message.reply_text(
        f"当前积分：{row[4]}\n每日签到获得1积分。\n满18积分可兑换30天VIP。"
    )


async def redeem(update, context):
    user_id = update.effective_user.id
    register_user(update.effective_user)
    row = get_user(user_id)
    if row[4] < 18:
        await update.effective_message.reply_text(
            f"积分不足，目前有{row[4]}积分，兑换需要18积分。"
        )
        return

    now = datetime.now()
    if row[5]:
        try:
            old = datetime.fromisoformat(row[5])
            if old.tzinfo:
                now = datetime.now(old.tzinfo)
            if old > now:
                now = old
        except ValueError:
            pass
    expiry = (now + timedelta(days=30)).isoformat(
        sep=" ",
        timespec="seconds"
    )

    con = db()
    con.execute("""
    UPDATE users SET points=points-18, vip_until=?
    WHERE user_id=?
    """, (expiry, user_id))
    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"⭐ 兑换成功！已开通30天VIP。\n到期时间：{expiry}"
    )


async def myid(update, context):
    await reply_custom(
        update.effective_message,
        f"你的Telegram用户ID：\n{user_id_line(update.effective_user.id)}",
        USER_MARK,
        USER_EMOJI_ID
    )


async def profile(update, context):
    user = update.effective_user
    register_user(user)
    row = get_user(user.id)
    await update.effective_message.reply_text(
        "👤 个人资料\n\n"
        f"姓名：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username or '未设置'}\n"
        f"用户ID：{user.id}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}\n"
        f"USDT余额：{format_usdt(get_balance(user.id))}"
    )


async def vip(update, context):
    user_id = update.effective_user.id
    register_user(update.effective_user)
    row = get_user(user_id)

    vip_until = row[5]
    active = False

    if vip_until:
        try:
            active = datetime.fromisoformat(vip_until) > datetime.now()
        except ValueError:
            pass

    if active:
        await update.effective_message.reply_text(
            f"⭐ 你已开通VIP会员。\n到期时间：{vip_until}"
        )
    else:
        await update.effective_message.reply_text(
            "你目前还没有开通VIP会员。\n"
            "可通过积分兑换或购买VIP套餐开通。"
        )


async def vip_features(update, context):
    await update.effective_message.reply_text(
        "✈️ VIP会员专属功能\n\n"
        "开通VIP后可使用机器人提供的专属查询功能。"
    )


# ==================================================
# 管理员功能
# ==================================================

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("你没有管理员权限。")
        return

    await update.effective_message.reply_text(
        "管理员功能：\n"
        "/users - 用户统计\n"
        "/broadcast - 群发消息\n"
        "/addbalance 用户ID 金额 - 增加余额\n"
        "/subbalance 用户ID 金额 - 扣除余额"
    )


async def users_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    con = db()
    total = con.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]
    con.close()

    await update.effective_message.reply_text(
        f"当前注册用户总数：{total}"
    )


async def addbalance_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "用法：/addbalance 用户ID 金额"
        )
        return

    try:
        target_id = int(context.args[0])
        amount = parse_usdt(context.args[1])
    except ValueError:
        amount = None

    if amount is None:
        await update.effective_message.reply_text("金额格式不正确。")
        return

    con = db()
    con.execute("""
    INSERT OR IGNORE INTO balances(user_id, balance)
    VALUES (?, 0)
    """, (target_id,))
    con.execute("""
    UPDATE balances SET balance=balance+?
    WHERE user_id=?
    """, (amount, target_id))
    con.commit()
    con.close()

    await update.effective_message.reply_text(
        f"已给用户 {target_id} 增加 {format_usdt(amount)} USDT。"
    )


async def subbalance_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "用法：/subbalance 用户ID 金额"
        )
        return

    try:
        target_id = int(context.args[0])
        amount = parse_usdt(context.args[1])
    except ValueError:
        amount = None

    if amount is None:
        await update.effective_message.reply_text("金额格式不正确。")
        return

    con = db()
    cur = con.cursor()
    cur.execute("""
    UPDATE balances SET balance=balance-?
    WHERE user_id=? AND balance>=?
    """, (amount, target_id, amount))

    changed = cur.rowcount
    con.commit()
    con.close()

    if changed:
        await update.effective_message.reply_text(
            f"已扣除用户 {target_id} 的 {format_usdt(amount)} USDT。"
        )
    else:
        await update.effective_message.reply_text("用户不存在或余额不足。")


async def broadcast_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if not update.effective_message.reply_to_message:
        await update.effective_message.reply_text(
            "请回复一条消息并发送 /broadcast，即可广播该消息。"
        )
        return

    source = update.effective_message.reply_to_message

    con = db()
    rows = con.execute(
        "SELECT user_id FROM users"
    ).fetchall()
    con.close()

    success = 0
    failed = 0

    status = await update.effective_message.reply_text(
        f"开始广播，共 {len(rows)} 位用户。"
    )

    for row in rows:
        target_id = row[0]
        try:
            await source.copy(chat_id=target_id)
            success += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    await status.edit_text(
        "广播完成。\n"
        f"成功：{success}\n"
        f"失败：{failed}"
    )
    
    # ==================================================
# 管理员命令
# ==================================================

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return
    await update.message.reply_text(
        "管理员命令：\n"
        "/stats\n/users\n/user 用户ID\n"
        "/approve 用户ID 天数\n"
        "/reducevip 用户ID 天数\n"
        "/clearvip 用户ID\n"
        "/addpoints 用户ID 积分\n"
        "/delpoints 用户ID 积分\n"
        "/addbalance 用户ID 金额\n"
        "/delbalance 用户ID 金额\n"
        "/sendusers\n/cancelmsg"
    )


async def stats(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
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
    total_balance = cur.execute(
        "SELECT COALESCE(SUM(balance),0) FROM balances"
    ).fetchone()[0]
    con.close()
    await update.message.reply_text(
        f"📊 机器人统计\n用户总数：{total}\n"
        f"已开通VIP：{vip_total}\n待审核提现：{pending}\n"
        f"用户余额总计：{format_usdt(total_balance)} USDT"
    )


async def users_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    con = db()
    rows = con.execute("""
    SELECT user_id, username, first_name
    FROM users ORDER BY first_seen ASC, user_id ASC
    """).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("目前没有用户。")
        return

    parts = []
    current = ""

    for index, (user_id, username, first_name) in enumerate(rows, 1):
        item = (
            f"{index}. 用户名：@{username or '未设置'}\n"
            f"姓名：{first_name or '未设置'}\n"
            f"用户ID：{user_id}\n\n"
        )
        if len(current) + len(item) > 3500:
            parts.append(current)
            current = ""
        current += item
    if current:
        parts.append(current)

    for part in parts:
        await update.message.reply_text(part)

    await update.message.reply_text(f"用户名单结束，共{len(rows)}人。")


async def user_info(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    if not context.args:
        await update.message.reply_text("用法：/user 用户ID")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID格式错误。")
        return

    row = get_user(target_id)
    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    await update.message.reply_text(
        f"用户ID：{row[0]}\n用户名：@{row[1] or '未设置'}\n"
        f"姓名：{row[2] or '未设置'}\n首次使用：{row[3]}\n"
        f"积分：{row[4]}\nVIP到期：{row[5] or '暂未开通'}\n"
        f"USDT余额：{format_usdt(get_balance(target_id))}"
    )


async def approve_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
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

    expiry = (now + timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    con = db()
    con.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (expiry, target_id)
    )
    con.commit()
    con.close()

    await update.message.reply_text(
        f"已为用户 {target_id} 开通 {days} 天VIP。\n到期时间：{expiry}"
    )

    try:
        await context.bot.send_message(
            target_id,
            f"🎉 管理员已为你开通 {days} 天VIP。\n到期时间：{expiry}"
        )
    except Exception:
        logger.exception("VIP开通通知失败")


async def reduce_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    if len(context.args) != 2:
        await update.message.reply_text("用法：/reducevip 用户ID 天数")
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
    if not row or not row[5]:
        await update.message.reply_text("用户没有VIP记录。")
        return

    try:
        expiry = datetime.fromisoformat(row[5])
        expiry -= timedelta(days=days)
    except ValueError:
        await update.message.reply_text("VIP到期时间格式异常。")
        return

    con = db()
    con.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (expiry.strftime("%Y-%m-%d %H:%M:%S"), target_id)
    )
    con.commit()
    con.close()

    await update.message.reply_text(
        f"已减少用户 {target_id} 的VIP时长 {days} 天。"
    )


async def clear_vip(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    if not context.args:
        await update.message.reply_text("用法：/clearvip 用户ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID格式错误。")
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

    if changed:
        await update.message.reply_text(f"已清除用户 {target_id} 的VIP。")
    else:
        await update.message.reply_text("没有找到这个用户。")


async def change_points(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    if len(context.args) != 2:
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
        await update.message.reply_text("用户ID或积分格式错误。")
        return

    command = update.message.text.split()[0].split("@")[0]
    operator = "+" if command == "/addpoints" else "-"

    con = db()
    cur = con.cursor()
    if operator == "+":
        cur.execute(
            "UPDATE users SET points=points+? WHERE user_id=?",
            (amount, target_id)
        )
    else:
        cur.execute("""
        UPDATE users SET points=points-?
        WHERE user_id=? AND points>=?
        """, (amount, target_id, amount))

    changed = cur.rowcount
    con.commit()
    con.close()

    if not changed:
        await update.message.reply_text("用户不存在或积分不足。")
        return

    await update.message.reply_text("积分调整成功。")


async def change_balance(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.message.reply_text("用法：/addbalance 用户ID 金额")
        return
    try:
        target_id = int(context.args[0])
        amount = parse_usdt(context.args[1])
        if target_id <= 0 or amount is None:
            raise ValueError
    except ValueError:
        await update.message.reply_text("用户ID或金额格式不正确。")
        return

    if not get_user(target_id):
        await update.message.reply_text("没有找到这个用户。")
        return

    command = update.message.text.split()[0].split("@")[0]
    con = db()
    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        if command == "/addbalance":
            cur.execute(
                "UPDATE balances SET balance=balance+? WHERE user_id=?",
                (amount, target_id)
            )
            operation = "增加"
        else:
            cur.execute("""
            UPDATE balances SET balance=balance-?
            WHERE user_id=? AND balance>=?
            """, (amount, target_id, amount))
            if cur.rowcount != 1:
                con.rollback()
                await update.message.reply_text("用户余额不足。")
                return
            operation = "扣除"
        con.commit()

    except Exception:
        con.rollback()
        logger.exception("余额调整失败")
        await update.message.reply_text("余额调整失败。")
        return
    finally:
        con.close()

    balance = get_balance(target_id)
    await update.message.reply_text(
        f"已为用户{target_id}{operation}{format_usdt(amount)} USDT。\n"
        f"当前余额：{format_usdt(balance)} USDT"
    )
    try:
        await context.bot.send_message(
            target_id,
            f"管理员已{operation}你的余额。\n"
            f"调整金额：{format_usdt(amount)} USDT\n"
            f"当前余额：{format_usdt(balance)} USDT"
        )
    except Exception:
        logger.exception("余额调整通知失败")
        
        # ==================================================
# 管理员分页发送
# ==================================================

def get_users_page(page):
    con = db()
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    rows = cur.execute("""
    SELECT user_id, username, first_name
    FROM users ORDER BY first_seen ASC, user_id ASC
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
        keyboard.append([InlineKeyboardButton(
            f"{first_name or '未设置'} | @{username or '无用户名'} | {user_id}",
            callback_data=f"msg_user_{user_id}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ 上一页",
            callback_data=f"msg_page_{page - 1}"
        ))

    if (page + 1) * USER_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            "下一页 ➡️",
            callback_data=f"msg_page_{page + 1}"
        ))

    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton(
        "❌ 退出",
        callback_data="msg_exit"
    )])

    await message.reply_text(
        f"请选择要发送消息的用户。\n用户总数：{total}\n当前页：{page + 1}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def open_admin_user_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return
    await show_admin_user_page(update.message, 0)


async def admin_send_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("你没有权限。", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "msg_exit":
        admin_message_targets.pop(ADMIN_ID, None)
        await query.edit_message_text("已退出管理员主动发送消息。")
        return

    if data.startswith("msg_page_"):
        page = int(data.replace("msg_page_", ""))
        await query.edit_message_text(f"正在切换到第{page + 1}页...")
        await show_admin_user_page(query.message, page)
        return

    if data.startswith("msg_user_"):
        target_id = int(data.replace("msg_user_", ""))

        if not get_user(target_id):
            await query.message.reply_text("用户不存在。")
            return

        admin_message_targets[ADMIN_ID] = target_id
        await query.message.reply_text(
            f"已选择用户：{target_id}\n"
            "现在直接发送文字、图片、视频或文件。\n"
            "发送/cancelmsg取消。"
        )


async def send_admin_message(update, context):
    target_id = admin_message_targets.get(ADMIN_ID)

    if not target_id:
        return False

    message = update.effective_message

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
        await message.reply_text(f"消息已发送给用户{target_id}。")

    except Exception:
        logger.exception("管理员主动发送失败")
        await message.reply_text("发送失败，用户可能无法接收消息。")

    return True


async def cancel_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限。")
        return

    if context.user_data.get("reject_withdraw_id"):
        context.user_data.pop("reject_withdraw_id", None)
        await update.message.reply_text("已取消提现拒绝操作。")
        return

    admin_message_targets.pop(ADMIN_ID, None)
    await update.message.reply_text("已取消主动发送消息。")


# ==================================================
# 双向联系
# ==================================================

async def forward_contact_message(update, context):
    message = update.effective_message
    user = update.effective_user

    username_text = f"@{user.username}" if user.username else "未设置"

    header = (
        f"用户名：{username_text}\n"
        f"用户：{user.id}\n"
        f"用户名字：{user.first_name or '未设置'}\n\n"
        "消息内容："
    )

    try:
        if message.text:
            sent = await context.bot.send_message(
                ADMIN_ID,
                header + "\n" + message.text
            )
        else:
            await context.bot.send_message(ADMIN_ID, header)
            sent = await context.bot.copy_message(
                ADMIN_ID,
                message.chat_id,
                message.message_id
            )

        save_reply_message(sent.message_id, user.id)
        notice = await message.reply_text("✅ 已发送给管理员。")
        await asyncio.sleep(1)

        try:
            await notice.delete()
        except Exception:
            pass

    except Exception:
        logger.exception("双向联系转发失败")
        await message.reply_text("发送失败，请稍后重试。")


# ==================================================
# 菜单
# ==================================================

def main_keyboard(user_id):
    current = user_modes.get(user_id)
    rows = [list(row) for row in MENU]

    # 没设置密码显示设置按钮；设置后只显示修改按钮
    if has_trade_password(user_id):
        rows.insert(-1, ["🔑 修改交易密码"])
    else:
        rows.insert(-1, ["🔐 设置交易密码"])

    for row in rows:
        for i, button in enumerate(row):
            if button == current:
                row[i] = button + " ✅"

    if current == "💬 双向联系":
        rows.insert(1, ["❌ 退出联系"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def clear_mode(user_id):
    user_modes.pop(user_id, None)
    contact_waiting.discard(user_id)
    payment_waiting.discard(user_id)


# ==================================================
# 开始
# ==================================================

async def start(update, context):
    user = update.effective_user
    old_user = get_user(user.id)

    register_user(user)
    clear_mode(user.id)

    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)

    if not old_user and context.args:
        payload = context.args[0]

        if payload.startswith("ref_"):
            try:
                inviter_id = int(payload[4:])

                if bind_inviter(user.id, inviter_id):
                    try:
                        await context.bot.send_message(
                            inviter_id,
                            "🎉 邀请成功！你获得10 USDT奖励。"
                        )
                    except Exception:
                        logger.exception("邀请通知失败")

                    await update.message.reply_text(
                        "🎉 你已通过好友邀请加入机器人！"
                    )

            except ValueError:
                pass

    await update.message.reply_text(
        "欢迎使用VIP机器人！\n请选择底部功能菜单。",
        reply_markup=main_keyboard(user.id)
    )
    
    