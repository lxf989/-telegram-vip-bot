import os
import re
import sqlite3
import logging
import asyncio
from decimal import Decimal, InvalidOperation
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
USDT_MARK = "💰"

INVITE_REWARD = 1000       # 10 USDT，单位为分
WITHDRAW_FEE = 100         # 1 USDT
MIN_WITHDRAW = 1000        # 10 USDT

TRON_ADDRESS_PATTERN = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")

# ==================================================
# 菜单
# ==================================================

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

# ==================================================
# 状态变量
# ==================================================

user_modes = {}
contact_waiting = set()
payment_waiting = set()
admin_message_targets = {}

USER_PAGE_SIZE = 20

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
    return f"{USER_MARK} 用户：{user_id}"

def usdt_entities(text):
    return custom_entities_all(text, USDT_MARK, USDT_EMOJI_ID)

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

# ==================================================
# 数据库
# ==================================================

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
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

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_withdrawals_user
    ON withdrawals(user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_referrals_inviter
    ON referrals(inviter_id)
    """)

    # 为原有用户建立余额记录，不修改原有积分和VIP
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
# 邀请奖励
# ==================================================

def bind_inviter(invited_id, inviter_id):
    if invited_id == inviter_id:
        return False

    con = db()

    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        # 被邀请用户必须是新用户
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
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    user_id = update.effective_user.id

    register_user(update.effective_user)

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

    context.user_data["withdraw_state"] = "address"

    await update.effective_message.reply_text(
        "💸 请发送你的 TRC20 USDT 收款地址。\n\n"
        "请确认使用 TRON/TRC20 网络，地址通常以 T 开头。\n"
        "发送后机器人会将地址回显给你核对。"
    )

async def withdraw_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data == "withdraw_cancel":
        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_address", None)

        await query.edit_message_text("已取消本次提现申请。")
        return

    if data.startswith("withdraw_approve_"):
        if user_id != ADMIN_ID:
            await query.answer("你没有权限操作。", show_alert=True)
            return

        try:
            withdrawal_id = int(data.rsplit("_", 1)[1])
        except ValueError:
            return

        con = db()

        try:
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")

            cur.execute("""
            SELECT user_id, amount, fee, payout, status
            FROM withdrawals WHERE id=?
            """, (withdrawal_id,))

            row = cur.fetchone()

            if not row:
                con.rollback()
                await query.answer("提现申请不存在。", show_alert=True)
                return

            target_id, amount, fee, payout, status = row

            if status != "pending":
                con.rollback()
                await query.answer("该申请已经处理过。", show_alert=True)
                return

            cur.execute("""
            UPDATE withdrawals
            SET status='approved',
                admin_id=?,
                reviewed_at=?
            WHERE id=? AND status='pending'
            """, (
                ADMIN_ID,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                withdrawal_id
            ))

            if cur.rowcount != 1:
                con.rollback()
                await query.answer("申请状态已改变。", show_alert=True)
                return

            con.commit()

        except Exception:
            con.rollback()
            logger.exception("提现审核通过失败")
            await query.answer("处理失败，请重试。", show_alert=True)
            return

        finally:
            con.close()

        await query.edit_message_text(
            query.message.text + "\n\n✅ 审核已通过，等待管理员手动转账。",
            reply_markup=None
        )

        text = (
            "✅ 你的提现申请已经审核通过。\n"
            f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
            f"{USDT_MARK} 手续费：{format_usdt(fee)} USDT\n"
            f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n\n"
            "请等待管理员完成链上转账。"
        )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=text,
                entities=usdt_entities(text)
            )
        except Exception:
            logger.exception("提现通过通知用户失败")

        return

    if data.startswith("withdraw_reject_"):
        if user_id != ADMIN_ID:
            await query.answer("你没有权限操作。", show_alert=True)
            return

        try:
            withdrawal_id = int(data.rsplit("_", 1)[1])
        except ValueError:
            return

        con = db()
        cur = con.cursor()

        cur.execute(
            "SELECT status FROM withdrawals WHERE id=?",
            (withdrawal_id,)
        )

        row = cur.fetchone()
        con.close()

        if not row or row[0] != "pending":
            await query.answer("该申请不存在或已处理。", show_alert=True)
            return

        context.user_data["reject_withdraw_id"] = withdrawal_id

        await query.edit_message_text(
            query.message.text
            + "\n\n请直接发送拒绝理由。"
            "\n发送 /cancelmsg 可取消拒绝操作。"
        )

        await query.answer("请发送拒绝理由。")
        return

async def finish_withdraw_reject(update, context):
    withdrawal_id = context.user_data.get("reject_withdraw_id")

    if not withdrawal_id:
        return False

    if update.effective_user.id != ADMIN_ID:
        return False

    reason = (update.effective_message.text or "").strip()

    if not reason:
        await update.effective_message.reply_text("拒绝理由不能为空。")
        return True

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
            await update.effective_message.reply_text("提现申请不存在。")
            context.user_data.pop("reject_withdraw_id", None)
            return True

        target_id, amount, status = row

        if status != "pending":
            con.rollback()
            await update.effective_message.reply_text("该申请已经处理。")
            context.user_data.pop("reject_withdraw_id", None)
            return True

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
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            withdrawal_id
        ))

        if cur.rowcount != 1:
            con.rollback()
            await update.effective_message.reply_text("处理失败，申请状态已改变。")
            context.user_data.pop("reject_withdraw_id", None)
            return True

        # 退回原先冻结的申请金额
        cur.execute("""
        UPDATE balances
        SET balance=balance+?
        WHERE user_id=?
        """, (amount, target_id))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("提现拒绝退款失败")
        await update.effective_message.reply_text("退款处理失败，请检查数据库。")
        return True

    finally:
        con.close()

    context.user_data.pop("reject_withdraw_id", None)

    await update.effective_message.reply_text(
        f"已拒绝提现申请 #{withdrawal_id}，并退回 {format_usdt(amount)} USDT。"
    )

    text = (
        "❌ 你的提现申请未通过。\n"
        f"拒绝理由：{reason}\n\n"
        f"{USDT_MARK} 已退回余额：{format_usdt(amount)} USDT"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=text,
            entities=usdt_entities(text)
        )
    except Exception:
        logger.exception("提现拒绝通知用户失败")

    return True

async def create_withdrawal(update, context):
    user = update.effective_user
    user_id = user.id

    address = context.user_data.get("withdraw_address")

    if not address:
        await update.effective_message.reply_text("提现地址已失效，请重新申请。")
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
        "💸 新的 USDT 提现申请\n\n"
        f"申请编号：#{withdrawal_id}\n"
        f"用户：{user.first_name or '未设置'}\n"
        f"用户名：@{user.username if user.username else '未设置'}\n"
        f"用户ID：{user_id}\n\n"
        f"TRC20地址：{address}\n\n"
        f"{USDT_MARK} 申请金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 手续费：{format_usdt(WITHDRAW_FEE)} USDT\n"
        f"{USDT_MARK} 实际到账：{format_usdt(payout)} USDT\n\n"
        f"申请时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
            entities=usdt_entities(text),
            reply_markup=keyboard
        )
    except Exception:
        logger.exception("发送提现申请给管理员失败")

        # 管理员通知发送失败时撤销申请并退款
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
            "提现通知管理员失败，申请已撤销，请稍后重新申请。"
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
# 管理员余额调整
# ==================================================

async def change_balance(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "用法：\n"
            "/addbalance 用户ID 金额\n"
            "/delbalance 用户ID 金额"
        )
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
            cur.execute("""
            UPDATE balances
            SET balance=balance+?
            WHERE user_id=?
            """, (amount, target_id))

            operation = "增加"

        else:
            cur.execute("""
            UPDATE balances
            SET balance=balance-?
            WHERE user_id=? AND balance>=?
            """, (amount, target_id, amount))

            if cur.rowcount != 1:
                con.rollback()
                await update.message.reply_text("用户余额不足，无法扣除。")
                return

            operation = "扣除"

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("管理员余额调整失败")
        await update.message.reply_text("余额调整失败。")
        return

    finally:
        con.close()

    balance = get_balance(target_id)

    await update.message.reply_text(
        f"已为用户 {target_id}{operation} {format_usdt(amount)} USDT。\n"
        f"当前余额：{format_usdt(balance)} USDT"
    )

    text = (
        f"💰 管理员已{operation}你的 USDT 余额。\n"
        f"{USDT_MARK} 调整金额：{format_usdt(amount)} USDT\n"
        f"{USDT_MARK} 当前余额：{format_usdt(balance)} USDT"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=text,
            entities=usdt_entities(text)
        )
    except Exception:
        logger.exception("通知用户余额调整失败")

# ==================================================
# 菜单
# ==================================================

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

# ==================================================
# 开始
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    old_user = get_user(user.id)

    register_user(user)
    clear_mode(user.id)

    context.user_data.pop("withdraw_state", None)
    context.user_data.pop("withdraw_address", None)

    # 仅首次注册时处理邀请
    if not old_user and context.args:
        payload = context.args[0]

        if payload.startswith("ref_"):
            try:
                inviter_id = int(payload[4:])

                if bind_inviter(user.id, inviter_id):
                    try:
                        text = (
                            "🎉 邀请成功！\n"
                            "你邀请的新用户已经加入。\n"
                            f"{USDT_MARK} 获得奖励：10 USDT"
                        )

                        await context.bot.send_message(
                            chat_id=inviter_id,
                            text=text,
                            entities=usdt_entities(text)
                        )

                    except Exception:
                        logger.exception("邀请人通知失败")

                    await update.message.reply_text(
                        "🎉 你已通过好友邀请加入机器人！"
                    )

            except ValueError:
                pass

    await update.message.reply_text(
        "欢迎使用 VIP 机器人！\n请选择底部功能菜单。",
        reply_markup=main_keyboard(user.id)
    )

# ==================================================
# 签到
# ==================================================

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

# ==================================================
# 积分
# ==================================================

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await update.message.reply_text(text, entities=entities)

# ==================================================
# 积分兑换 VIP
# ==================================================

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

            if old.tzinfo is not None:
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

# ==================================================
# 查询自己的 ID
# ==================================================

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "你的 Telegram 用户ID：\n"
        f"{user_id_line(update.effective_user.id)}"
    )

    await reply_custom(
        update.message,
        text,
        USER_MARK,
        USER_EMOJI_ID
    )

# ==================================================
# 个人资料
# ==================================================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        update.message,
        text,
        USER_MARK,
        USER_EMOJI_ID
    )

# ==================================================
# VIP 套餐
# ==================================================

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

# ==================================================
# VIP 功能图片
# ==================================================

async def vip_feature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    row = get_user(user_id)

    if not row or not row[5]:
        await update.effective_message.reply_text(
            "❌ 此功能仅限 VIP 会员使用，请先开通 VIP。"
        )
        return

    try:
        vip_until = datetime.fromisoformat(row[5])

        if vip_until.tzinfo is not None:
            now = datetime.now(vip_until.tzinfo)
        else:
            now = datetime.now()

        if vip_until <= now:
            await update.effective_message.reply_text(
                "❌ 你的 VIP 已到期，请续费后使用此功能。"
            )
            return

    except (ValueError, TypeError):
        await update.effective_message.reply_text(
            "❌ VIP信息异常，请联系管理员处理。"
        )
        return

    caption = (
        f"{VIP_FEATURE_MARK} "
        "你好 欢迎开通VIP 谢谢支持！操你妈妈👵还开VIP人傻钱多"
    )

    entities = custom_entity(
        caption,
        VIP_FEATURE_MARK,
        VIP_FEATURE_EMOJI_ID
    )

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=VIP_FLAG_IMAGE,
            caption=caption,
            caption_entities=entities
        )

    except Exception:
        logger.exception("VIP图片发送失败")

        await update.effective_message.reply_text(
            "VIP图片发送失败，请稍后再试。"
        )

# ==================================================
# VIP 支付流程
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
        [InlineKeyboardButton("🔙 返回上一级", callback_data="pay_back_plan")],
    ])

def vip_order_keyboard(plan, method):
    keyboard = []

    if method == "OKPay":
        keyboard.append([
            InlineKeyboardButton(
                "前往 OKPay 支付",
                url=OKPAY_LINKS[plan]
            )
        ])

    keyboard.append([
        InlineKeyboardButton("📋 我已支付", callback_data="paid")
    ])

    keyboard.append([
        InlineKeyboardButton("🔙 返回上一级", callback_data="pay_back_method")
    ])

    keyboard.append([
        InlineKeyboardButton("❌ 取消支付", callback_data="pay_cancel")
    ])

    return InlineKeyboardMarkup(keyboard)

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

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data.startswith("plan_"):
        plan = data.replace("plan_", "")

        if plan not in PLANS:
            return

        clear_payment_state(user_id, context)
        context.user_data["selected_plan"] = plan

        await query.edit_message_text(
            f"已选择 {plan} VIP。\n请选择支付方式：",
            reply_markup=vip_method_keyboard()
        )
        return

    if data == "pay_back_plan":
        clear_payment_state(user_id, context)

        text = f"{VIP_MARK} 请选择 VIP 套餐："

        await query.edit_message_text(
            text,
            entities=custom_entity(text, VIP_MARK, VIP_EMOJI_ID),
            reply_markup=vip_plan_keyboard()
        )
        return

    if data == "pay_back_method":
        payment_waiting.discard(user_id)

        context.user_data.pop("payment_method", None)
        context.user_data.pop("payment_order_active", None)
        context.user_data.pop("payment_menu_message_id", None)
        context.user_data.pop("payment_menu_chat_id", None)

        plan = context.user_data.get("selected_plan")

        if not plan:
            text = f"{VIP_MARK} 请选择 VIP 套餐："

            await query.edit_message_text(
                text,
                entities=custom_entity(text, VIP_MARK, VIP_EMOJI_ID),
                reply_markup=vip_plan_keyboard()
            )
            return

        await query.edit_message_text(
            f"已选择 {plan} VIP。\n请选择支付方式：",
            reply_markup=vip_method_keyboard()
        )
        return

    if data == "pay_cancel":
        clear_payment_state(user_id, context)

        text = f"{VIP_MARK} 已取消本次支付。\n\n请选择 VIP 套餐："

        await query.edit_message_text(
            text,
            entities=custom_entity(text, VIP_MARK, VIP_EMOJI_ID),
            reply_markup=vip_plan_keyboard()
        )
        return

    if data == "pay_trc20":
        plan = context.user_data.get("selected_plan")

        if not plan or plan not in PLANS:
            await query.edit_message_text(
                "套餐信息已失效，请重新选择 VIP 套餐。",
                reply_markup=vip_plan_keyboard()
            )
            return

        amount, days = PLANS[plan]

        context.user_data["payment_method"] = "TRC20"
        context.user_data["payment_order_active"] = True
        context.user_data["payment_menu_message_id"] = query.message.message_id
        context.user_data["payment_menu_chat_id"] = query.message.chat_id

        text = (
            f"{VIP_MARK} 套餐：{plan} VIP\n"
            f"金额：{amount} USDT\n\n"
            "请使用 TRC20 网络转账至以下地址：\n"
            f"{USDT_ADDRESS}\n\n"
            "转账完成后，点击“我已支付”，提交交易凭证。"
        )

        await query.edit_message_text(
            text,
            entities=custom_entities_all(text, VIP_MARK, VIP_EMOJI_ID),
            reply_markup=vip_order_keyboard(plan, "TRC20")
        )
        return

    if data == "pay_okpay":
        plan = context.user_data.get("selected_plan")

        if not plan or plan not in PLANS:
            await query.edit_message_text(
                "套餐信息已失效，请重新选择 VIP 套餐。",
                reply_markup=vip_plan_keyboard()
            )
            return

        context.user_data["payment_method"] = "OKPay"
        context.user_data["payment_order_active"] = True
        context.user_data["payment_menu_message_id"] = query.message.message_id
        context.user_data["payment_menu_chat_id"] = query.message.chat_id

        text = (
            f"{VIP_MARK} 套餐：{plan} VIP\n"
            f"金额：{PLANS[plan][0]} USDT\n\n"
            "点击下方按钮前往 OKPay。支付完成后，请回来提交凭证。"
        )

        await query.edit_message_text(
            text,
            entities=custom_entities_all(text, VIP_MARK, VIP_EMOJI_ID),
            reply_markup=vip_order_keyboard(plan, "OKPay")
        )
        return

    if data == "paid":
        plan = context.user_data.get("selected_plan")

        if not plan or not context.user_data.get("payment_order_active"):
            await query.answer(
                "支付订单不存在，请重新选择支付方式。",
                show_alert=True
            )
            return

        payment_waiting.add(user_id)

        await query.edit_message_text(
            f"请发送你的 {plan} VIP 支付凭证或交易 TXID。\n"
            "管理员核实到账后会为你开通 VIP。",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ 取消支付",
                        callback_data="pay_cancel"
                    )
                ]
            ])
        )
        return

# ==================================================
# 管理员菜单
# ==================================================

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
        "/reducevip 用户ID 天数 - 减少VIP天数\n"
        "/clearvip 用户ID - 清空VIP\n"
        "/addpoints 用户ID 积分 - 增加积分\n"
        "/delpoints 用户ID 积分 - 扣除积分\n"
        "/addbalance 用户ID 金额 - 增加USDT余额\n"
        "/delbalance 用户ID 金额 - 扣除USDT余额\n"
        "/sendusers - 分页选择用户并主动发送消息\n"
        "/cancelmsg - 取消主动发送消息"
    )

# ==================================================
# 统计
# ==================================================

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

    cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
    pending = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(balance),0) FROM balances")
    total_balance = cur.fetchone()[0]

    con.close()

    await update.message.reply_text(
        f"📊 机器人统计\n"
        f"用户总数：{total}\n"
        f"已开通过VIP：{vip_total}\n"
        f"待审核提现：{pending}\n"
        f"用户USDT余额总计：{format_usdt(total_balance)} USDT"
    )

# ==================================================
# 用户名单
# ==================================================

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

# ==================================================
# 查看用户资料
# ==================================================

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
    balance = get_balance(target_id)

    text = (
        f"{user_id_line(row[0])}\n"
        f"用户名：{username}\n"
        f"姓名：{row[2] or '未设置'}\n"
        f"首次使用：{row[3]}\n"
        f"积分：{row[4]}\n"
        f"VIP到期：{row[5] or '暂未开通'}\n"
        f"{USDT_MARK} USDT余额：{format_usdt(balance)}"
    )

    entities = (
        custom_entity(text, USER_MARK, USER_EMOJI_ID)
        + custom_entity(text, USDT_MARK, USDT_EMOJI_ID)
    )

    await update.message.reply_text(text, entities=entities)

# ==================================================
# 管理员开通 VIP
# ==================================================

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

        if target_id <= 0 or days <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text("用户ID和天数必须是有效正整数。")
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    now = datetime.now()

    if row[5]:
        try:
            old = datetime.fromisoformat(row[5])

            if old.tzinfo is not None:
                now = datetime.now(old.tzinfo)

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

    await update.message.reply_text(
        f"✅ 已为用户 {target_id} 开通 {days} 天 VIP。\n"
        f"到期时间：{expiry}"
    )

    try:
        await send_custom(
            context.bot,
            target_id,
            f"{VIP_MARK} 管理员已为你开通 {days} 天 VIP！\n到期时间：{expiry}",
            VIP_MARK,
            VIP_EMOJI_ID
        )

    except Exception:
        logger.exception("通知用户VIP开通失败")

# ==================================================
# 管理员减少 VIP 天数
# ==================================================

async def reduce_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "用法：/reducevip 用户ID 天数"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])

        if target_id <= 0 or days <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "用户ID和天数必须是正整数。"
        )
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    if not row[5]:
        await update.message.reply_text("该用户目前没有VIP。")
        return

    try:
        expiry = datetime.fromisoformat(row[5])

        if expiry.tzinfo is not None:
            now = datetime.now(expiry.tzinfo)
        else:
            now = datetime.now()

        new_expiry = expiry - timedelta(days=days)

        if new_expiry <= now:
            new_value = None
            result = "VIP已到期，已清空VIP。"
        else:
            new_value = new_expiry.isoformat(
                sep=" ",
                timespec="seconds"
            )
            result = f"新的VIP到期时间：{new_value}"

    except (ValueError, TypeError):
        await update.message.reply_text("VIP到期时间格式异常。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (new_value, target_id)
    )

    con.commit()
    con.close()

    await update.message.reply_text(
        f"✅ 已扣除用户 {target_id} 的 {days} 天VIP。\n{result}"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"管理员已调整你的VIP，扣除 {days} 天。\n{result}"
        )
    except Exception:
        logger.exception("通知用户VIP调整失败")

# ==================================================
# 管理员清空 VIP
# ==================================================

async def clear_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if not context.args:
        await update.message.reply_text(
            "用法：/clearvip 用户ID"
        )
        return

    try:
        target_id = int(context.args[0])

        if target_id <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text("用户ID必须是正整数。")
        return

    row = get_user(target_id)

    if not row:
        await update.message.reply_text("没有找到这个用户。")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=NULL WHERE user_id=?",
        (target_id,)
    )

    con.commit()
    con.close()

    await update.message.reply_text(
        f"✅ 已清空用户 {target_id} 的VIP。"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="管理员已清空你的VIP会员资格。"
        )
    except Exception:
        logger.exception("通知用户VIP清空失败")

# ==================================================
# 管理员调整积分
# ==================================================

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

        if target_id <= 0 or amount <= 0:
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

        message_text = f"已为用户 {target_id} 增加 {amount} 积分。"

    else:
        cur.execute(
            "UPDATE users SET points=MAX(0, points-?) WHERE user_id=?",
            (amount, target_id)
        )

        message_text = f"已为用户 {target_id} 扣除 {amount} 积分。"

    con.commit()
    con.close()

    await update.message.reply_text(message_text)

# ==================================================
# 管理员分页选择用户
# ==================================================

def get_users_page(page):
    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    offset = page * USER_PAGE_SIZE

    cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        ORDER BY first_seen ASC, user_id ASC
        LIMIT ? OFFSET ?
    """, (USER_PAGE_SIZE, offset))

    rows = cur.fetchall()
    con.close()

    return rows, total

async def show_admin_user_page(message, page):
    rows, total = get_users_page(page)

    if total == 0:
        await message.reply_text("目前没有用户。")
        return

    keyboard = []

    for user_id, username, first_name in rows:
        name = first_name or "未设置"
        username_text = f"@{username}" if username else "无用户名"

        keyboard.append([
            InlineKeyboardButton(
                f"{name} | {username_text} | {user_id}",
                callback_data=f"msg_user_{user_id}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ 上一页",
                callback_data=f"msg_page_{page - 1}"
            )
        )

    if (page + 1) * USER_PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(
                "下一页 ➡️",
                callback_data=f"msg_page_{page + 1}"
            )
        )

    if navigation:
        keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton(
            "❌ 退出",
            callback_data="msg_exit"
        )
    ])

    await message.reply_text(
        f"请选择要发送消息的用户。\n"
        f"用户总数：{total}\n"
        f"当前页：{page + 1}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def open_admin_user_list(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此功能。")
        return

    await show_admin_user_page(update.message, 0)

async def admin_send_callback(update, context):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("你没有权限操作。", show_alert=True)
        return

    await query.answer()

    data = query.data

    if data == "msg_exit":
        admin_message_targets.pop(ADMIN_ID, None)

        await query.edit_message_text(
            "已退出管理员主动发送消息。"
        )
        return

    if data.startswith("msg_page_"):
        try:
            page = int(data.replace("msg_page_", ""))
        except ValueError:
            return

        await query.edit_message_text(
            f"正在切换到第 {page + 1} 页..."
        )

        await show_admin_user_page(query.message, page)
        return

    if data.startswith("msg_user_"):
        try:
            target_id = int(data.replace("msg_user_", ""))
        except ValueError:
            return

        if not get_user(target_id):
            await query.message.reply_text("该用户不存在。")
            return

        admin_message_targets[ADMIN_ID] = target_id

        await query.message.reply_text(
            f"已选择用户：{target_id}\n\n"
            "现在直接发送文字、图片、视频或文件，"
            "机器人会将消息发送给该用户。\n\n"
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
            message_id=message.message_id
        )

        await message.reply_text(
            f"✅ 消息已发送给用户 {target_id}。"
        )

    except Exception:
        logger.exception("管理员主动发送消息失败")

        await message.reply_text(
            "发送失败，可能是用户屏蔽了机器人或无法接收消息。"
        )

    return True

async def cancel_admin_message(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if context.user_data.get("reject_withdraw_id"):
        context.user_data.pop("reject_withdraw_id", None)
        await update.message.reply_text("已取消提现拒绝操作。")
        return

    admin_message_targets.pop(ADMIN_ID, None)

    await update.message.reply_text(
        "已取消主动发送消息。"
    )

# ==================================================
# 双向联系
# ==================================================

async def forward_contact_message(update, context):
    message = update.effective_message
    user = update.effective_user

    username_text = (
        f"@{user.username}"
        if user.username
        else "未设置"
    )

    header = (
        f"用户名：{username_text}\n"
        f"{USER_MARK} 用户：{user.id}\n"
        f"用户名字：{user.first_name or '未设置'}\n\n"
        "消息内容："
    )

    try:
        if message.text:
            combined_text = header + "\n" + message.text

            sent = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=combined_text,
                entities=custom_entities_all(
                    combined_text,
                    USER_MARK,
                    USER_EMOJI_ID
                )
            )

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
                    USER_EMOJI_ID
                )
            )

        else:
            await send_custom(
                context.bot,
                ADMIN_ID,
                header,
                USER_MARK,
                USER_EMOJI_ID
            )

            sent = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

        save_reply_message(sent.message_id, user.id)

        notice = await message.reply_text("✅ 已发送给管理员。")

        await asyncio.sleep(1)

        try:
            await notice.delete()
        except Exception:
            logger.exception("自动删除发送成功提示失败")

    except Exception:
        logger.exception("双向联系转发失败")

        await message.reply_text(
            "发送失败，请稍后重试。"
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

    # 管理员输入提现拒绝理由
    if user_id == ADMIN_ID and context.user_data.get("reject_withdraw_id"):
        if text.startswith("/"):
            return

        await finish_withdraw_reject(update, context)
        return

    # 提现地址输入
    if user_id != ADMIN_ID and context.user_data.get("withdraw_state") == "address":
        address = text.strip()

        if not TRON_ADDRESS_PATTERN.fullmatch(address):
            await message.reply_text(
                "❌ TRC20 地址格式不正确。\n"
                "请检查地址，通常为 T 开头的 34 位 Base58 地址。"
            )
            return

        context.user_data["withdraw_address"] = address
        context.user_data["withdraw_state"] = "confirm_address"

        await message.reply_text(
            f"请仔细核对你的 TRC20 收款地址：\n\n"
            f"<code>{address}</code>\n\n"
            "确认地址无误后点击下面按钮。",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ 地址正确，继续",
                        callback_data="withdraw_address_ok"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 重新输入",
                        callback_data="withdraw_address_retry"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ 取消提现",
                        callback_data="withdraw_cancel"
                    )
                ]
            ])
        )
        return

    # 提现金额输入
    if user_id != ADMIN_ID and context.user_data.get("withdraw_state") == "amount":
        await create_withdrawal(update, context)
        return

    # 管理员主动发送消息
    if user_id == ADMIN_ID and admin_message_targets.get(ADMIN_ID):
        handled = await send_admin_message(update, context)

        if handled:
            return

    # 管理员回复双向联系消息
    if user_id == ADMIN_ID and message.reply_to_message:
        target_id = get_reply_user(
            message.reply_to_message.message_id
        )

        if target_id:
            try:
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id
                )

                await message.reply_text("✅ 已回复用户。")

            except Exception as e:
                logger.exception("管理员回复用户失败")
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
            logger.exception("提交支付凭证失败")
            await message.reply_text(f"提交失败，请稍后重试：{e}")

        return

    # 退出双向联系
    if text == "❌ 退出联系":
        clear_mode(user_id)

        await message.reply_text(
            "已退出双向联系。",
            reply_markup=main_keyboard(user_id)
        )

        return

    # 退出菜单
    if text == "❌ 退出菜单":
        clear_mode(user_id)

        for key in (
            "withdraw_state",
            "withdraw_address",
            "selected_plan",
            "payment_method",
            "payment_order_active"
        ):
            context.user_data.pop(key, None)

        await message.reply_text(
            "已退出当前功能。",
            reply_markup=main_keyboard(user_id)
        )

        return

    menu_items = {
        "💬 双向联系",
        "👤 我的资料",
        "🆔 查询用户ID",
        "VIP会员",
        "💎 VIP会员",
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
    }

    if text in menu_items:
        clear_mode(user_id)

        for key in (
            "withdraw_state",
            "withdraw_address"
        ):
            context.user_data.pop(key, None)

        if text == "💬 双向联系":
            user_modes[user_id] = "💬 双向联系"
            contact_waiting.add(user_id)

            await reply_custom(
                message,
                f"{CONTACT_MARK} 双向联系已开启。\n"
                "你发送的消息会转发给管理员，管理员回复后会转发给你。\n"
                "点击“❌ 退出联系”即可结束。",
                CONTACT_MARK,
                CONTACT_EMOJI_ID,
                reply_markup=main_keyboard(user_id)
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

        if text in ("✈️ VIP功能", "✈️ VIP功能 ✅"):
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
                "✅ 查询成功\n"
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

    # 双向联系
    if user_id != ADMIN_ID and user_id in contact_waiting:
        await forward_contact_message(update, context)
        return

    if user_id != ADMIN_ID:
        await message.reply_text(
            "请选择底部菜单中的功能，或发送 /help 查看帮助。",
            reply_markup=main_keyboard(user_id)
        )

# ==================================================
# 提现地址按钮回调
# ==================================================

async def withdraw_address_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    await query.answer()

    if query.data == "withdraw_address_ok":
        if context.user_data.get("withdraw_state") != "confirm_address":
            await query.answer("地址状态已失效。", show_alert=True)
            return

        context.user_data["withdraw_state"] = "amount"

        balance = get_balance(user_id)

        text = (
            f"{USDT_MARK} 当前余额：{format_usdt(balance)} USDT\n\n"
            "请输入本次提现金额。\n"
            "最低提现金额：10 USDT\n"
            "手续费：1 USDT\n\n"
            "例如申请20 USDT，实际到账19 USDT。"
        )

        await query.edit_message_text(
            text,
            entities=usdt_entities(text)
        )
        return

    if query.data == "withdraw_address_retry":
        context.user_data["withdraw_state"] = "address"
        context.user_data.pop("withdraw_address", None)

        await query.edit_message_text(
            "请重新发送你的 TRC20 USDT 收款地址。"
        )
        return

# ==================================================
# 帮助
# ==================================================

async def help_command(update, context):
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

# ==================================================
# 命令设置
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
        BotCommand("cancelmsg", "取消主动发送消息"),
    ]

    await application.bot.set_my_commands(user_commands)

    await application.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )

# ==================================================
# 启动
# ==================================================

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

    # 用户命令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("qd", checkin))
    application.add_handler(CommandHandler("jf", points))
    application.add_handler(CommandHandler("dh", redeem))
    application.add_handler(CommandHandler("me", profile))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("help", help_command))

    # 管理员命令
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

    application.add_handler(
        CommandHandler("sendusers", open_admin_user_list)
    )

    application.add_handler(
        CommandHandler("cancelmsg", cancel_admin_message)
    )

    # 管理员用户分页回调
    application.add_handler(
        CallbackQueryHandler(
            admin_send_callback,
            pattern=r"^msg_"
        )
    )

    # 提现审核回调
    application.add_handler(
        CallbackQueryHandler(
            withdraw_callback,
            pattern=r"^withdraw_(?:approve_|reject_|cancel$)"
        )
    )

    # 提现地址确认回调
    application.add_handler(
        CallbackQueryHandler(
            withdraw_address_callback,
            pattern=r"^withdraw_address_(?:ok|retry)$"
        )
    )

    # VIP支付回调
    application.add_handler(
        CallbackQueryHandler(
            payment_callback,
            pattern=r"^(plan_|pay_|paid$)"
        )
    )

    # VIP功能
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^✈️ VIP功能(?: ✅)?$"),
            vip_feature
        )
    )

    # 其他消息统一处理
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    print("机器人启动成功！")
    application.run_polling()

if __name__ == "__main__":
    main()