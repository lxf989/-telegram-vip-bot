import os
import sqlite3
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# 基础配置
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8957110767"))

DB_PATH = os.getenv("DB_PATH", "bot_data.db")

USER_PAGE_SIZE = 20

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# 管理员当前选择的发消息目标
admin_message_targets = {}

# 用户双向联系状态
contact_waiting = set()


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
        (user_id, username, first_name, first_seen, points, vip_until)
        VALUES (?, ?, ?, ?, 0, NULL)
    """, (
        user.id,
        user.username,
        user.first_name,
        datetime.now().isoformat(sep=" ", timespec="seconds"),
    ))

    cur.execute("""
        UPDATE users
        SET username=?, first_name=?
        WHERE user_id=?
    """, (user.username, user.first_name, user.id))

    con.commit()
    con.close()


def get_user(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    )

    row = cur.fetchone()
    con.close()

    return row


def get_all_users():
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        ORDER BY first_seen DESC
    """)

    rows = cur.fetchall()
    con.close()

    return rows


def get_user_count():
    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]

    con.close()
    return count


# =========================
# 用户菜单
# =========================

def main_keyboard(user_id):
    keyboard = [
        ["👤 个人中心", "⭐ 积分查询"],
        ["📅 每日签到", "💎 VIP套餐"],
        ["🎁 积分兑换VIP", "📞 联系管理员"],
    ]

    if user_id == ADMIN_ID:
        keyboard.append(["📨 给用户发消息", "⚙️ 管理员面板"])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# =========================
# VIP状态
# =========================

def get_vip_status(user_id):
    row = get_user(user_id)

    if not row or not row[5]:
        return False, None

    try:
        expiry = datetime.fromisoformat(row[5])
    except (ValueError, TypeError):
        return False, None

    if expiry <= datetime.now():
        return False, expiry

    return True, expiry


def add_vip_days(user_id, days):
    row = get_user(user_id)

    if not row:
        return False

    now = datetime.now()

    if row[5]:
        try:
            expiry = datetime.fromisoformat(row[5])
        except (ValueError, TypeError):
            expiry = now
    else:
        expiry = now

    if expiry < now:
        expiry = now

    new_expiry = expiry + timedelta(days=days)

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET vip_until=? WHERE user_id=?",
        (
            new_expiry.isoformat(sep=" ", timespec="seconds"),
            user_id,
        )
    )

    con.commit()
    con.close()

    return True


# =========================
# 通知用户
# =========================

async def notify_user(bot, user_id, message):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=message
        )
        return True

    except Exception:
        logger.exception("通知用户失败：%s", user_id)
        return False
        
        # =========================
# /start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    await update.message.reply_text(
        f"你好，{user.first_name}！欢迎使用VIP机器人。",
        reply_markup=main_keyboard(user.id)
    )


# =========================
# 个人中心
# =========================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(update.effective_user)

    row = get_user(user_id)

    if not row:
        await update.message.reply_text("用户信息不存在。")
        return

    is_vip, expiry = get_vip_status(user_id)

    vip_text = (
        f"有效，到期时间：{expiry.strftime('%Y-%m-%d %H:%M:%S')}"
        if is_vip else "未开通或已到期"
    )

    await update.message.reply_text(
        f"👤 个人中心\n\n"
        f"用户ID：{user_id}\n"
        f"用户名：@{row[1] or '无'}\n"
        f"积分：{row[4]}\n"
        f"VIP状态：{vip_text}"
    )


# =========================
# 积分查询
# =========================

async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    row = get_user(update.effective_user.id)

    await update.message.reply_text(
        f"⭐ 你的积分：{row[4]}"
    )


# =========================
# 每日签到
# =========================

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    today = datetime.now().strftime("%Y-%m-%d")

    con = db()
    cur = con.cursor()

    try:
        cur.execute(
            "INSERT INTO checkins (user_id, check_date) VALUES (?, ?)",
            (user.id, today)
        )

        cur.execute(
            "UPDATE users SET points=points+1 WHERE user_id=?",
            (user.id,)
        )

        con.commit()

        row = get_user(user.id)

        await update.message.reply_text(
            f"✅ 签到成功！获得1积分。\n当前积分：{row[4]}"
        )

    except sqlite3.IntegrityError:
        await update.message.reply_text("今天已经签到过了，明天再来吧！")

    finally:
        con.close()


# =========================
# VIP套餐
# =========================

async def vip_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("7天VIP - 1 USDT", callback_data="plan_7")],
        [InlineKeyboardButton("30天VIP - 2 USDT", callback_data="plan_30")],
        [InlineKeyboardButton("90天VIP - 5 USDT", callback_data="plan_90")],
        [InlineKeyboardButton("365天VIP - 10 USDT", callback_data="plan_365")],
    ]

    await update.message.reply_text(
        "💎 VIP套餐\n\n请选择你需要的VIP时长：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# 积分兑换VIP
# 18积分兑换30天
# =========================

async def exchange_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(update.effective_user)

    row = get_user(user_id)

    if row[4] < 18:
        await update.message.reply_text(
            f"积分不足！\n当前积分：{row[4]}\n需要积分：18"
        )
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET points=points-18 WHERE user_id=?",
        (user_id,)
    )

    con.commit()
    con.close()

    add_vip_days(user_id, 30)

    row = get_user(user_id)

    await update.message.reply_text(
        f"🎉 兑换成功！\n"
        f"已兑换30天VIP。\n"
        f"剩余积分：{row[4]}"
    )


# =========================
# /myid
# =========================

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"你的用户ID：{update.effective_user.id}"
    )


# =========================
# 管理员：减少VIP
# /reducevip 用户ID 天数
# =========================

async def reduce_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) != 2:
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
    except (ValueError, TypeError):
        await update.message.reply_text("用户VIP到期时间异常。")
        return

    now = datetime.now()

    if expiry.tzinfo is not None:
        now = datetime.now(expiry.tzinfo)

    if expiry <= now:
        await update.message.reply_text("该用户VIP已经到期。")
        return

    new_expiry = expiry - timedelta(days=days)

    if new_expiry <= now:
        new_expiry_text = None
        result = "VIP剩余时间已扣完，已取消该用户VIP。"
    else:
        new_expiry_text = new_expiry.isoformat(
            sep=" ",
            timespec="seconds"
        )

        result = (
            f"已减少用户 {target_id} 的 {days} 天VIP。\n"
            f"新的到期时间：{new_expiry_text}"
        )

    con = db()

    try:
        cur = con.cursor()

        cur.execute(
            "UPDATE users SET vip_until=? WHERE user_id=?",
            (new_expiry_text, target_id)
        )

        con.commit()

    finally:
        con.close()

    await update.message.reply_text("✅ " + result)

    await notify_user(
        context.bot,
        target_id,
        "管理员调整了你的VIP时长。\n" + result
    )


# =========================
# 管理员：清空VIP
# /clearvip 用户ID
# =========================

async def clear_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    if len(context.args) != 1:
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

    try:
        cur = con.cursor()

        cur.execute(
            "UPDATE users SET vip_until=NULL WHERE user_id=?",
            (target_id,)
        )

        con.commit()

    finally:
        con.close()

    await update.message.reply_text(
        f"✅ 已成功清空用户 {target_id} 的VIP。"
    )

    await notify_user(
        context.bot,
        target_id,
        "管理员已取消你的VIP资格。"
    )


# =========================
# 管理员：统计
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM users WHERE vip_until IS NOT NULL"
    )
    total_vip = cur.fetchone()[0]

    cur.execute("SELECT SUM(points) FROM users")
    total_points = cur.fetchone()[0] or 0

    con.close()

    await update.message.reply_text(
        f"📊 机器人统计\n\n"
        f"用户总数：{total_users}\n"
        f"VIP记录数：{total_vip}\n"
        f"积分总数：{total_points}"
    )
    
    # =========================
# 管理员用户分页
# =========================

def get_users_page(page=0):
    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    total_pages = max(1, (total + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE)

    page = max(0, min(page, total_pages - 1))

    cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        ORDER BY first_seen DESC
        LIMIT ? OFFSET ?
    """, (USER_PAGE_SIZE, page * USER_PAGE_SIZE))

    rows = cur.fetchall()
    con.close()

    return rows, total, total_pages, page


# =========================
# 显示管理员用户列表
# =========================

async def show_admin_user_page(query, page=0):
    rows, total, total_pages, page = get_users_page(page)

    keyboard = []

    for user_id, username, first_name in rows:
        name = first_name or "未设置姓名"
        username_text = f"@{username}" if username else "无用户名"

        button_text = f"{name} | {username_text} | {user_id}"

        if len(button_text) > 60:
            button_text = button_text[:57] + "..."

        keyboard.append([
            InlineKeyboardButton(
                button_text,
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

    if page < total_pages - 1:
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
            "❌ 退出发消息",
            callback_data="msg_exit"
        )
    ])

    text = (
        f"📨 管理员发消息\n\n"
        f"当前用户总数：{total}\n"
        f"第 {page + 1}/{total_pages} 页\n\n"
        f"请选择要发送消息的用户："
    )

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# 打开用户列表
# =========================

async def open_admin_user_list(update: Update):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此功能。")
        return

    admin_message_targets.pop(user_id, None)

    rows, total, total_pages, page = get_users_page(0)

    keyboard = []

    for target_id, username, first_name in rows:
        name = first_name or "未设置姓名"
        username_text = f"@{username}" if username else "无用户名"

        button_text = f"{name} | {username_text} | {target_id}"

        if len(button_text) > 60:
            button_text = button_text[:57] + "..."

        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"msg_user_{target_id}"
            )
        ])

    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton(
                "下一页 ➡️",
                callback_data="msg_page_1"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "❌ 退出发消息",
            callback_data="msg_exit"
        )
    ])

    await update.message.reply_text(
        f"📨 管理员发消息\n\n"
        f"用户总数：{total}\n"
        f"请选择要发送消息的用户：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# 管理员发消息回调
# =========================

async def admin_send_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("你没有权限使用此功能。", show_alert=True)
        return

    data = query.data

    if data == "msg_exit":
        admin_message_targets.pop(ADMIN_ID, None)

        await query.answer("已退出发消息模式。")

        await query.edit_message_text(
            "❌ 已退出管理员发消息模式。"
        )
        return

    if data == "msg_back":
        admin_message_targets.pop(ADMIN_ID, None)

        await query.answer("已返回用户列表。")
        await show_admin_user_page(query, 0)
        return

    if data.startswith("msg_page_"):
        try:
            page = int(data.replace("msg_page_", ""))
        except ValueError:
            await query.answer("页码错误。", show_alert=True)
            return

        admin_message_targets.pop(ADMIN_ID, None)

        await query.answer()
        await show_admin_user_page(query, page)
        return

    if data.startswith("msg_user_"):
        try:
            target_id = int(data.replace("msg_user_", ""))
        except ValueError:
            await query.answer("用户ID错误。", show_alert=True)
            return

        row = get_user(target_id)

        if not row:
            await query.answer("该用户不存在。", show_alert=True)
            await show_admin_user_page(query, 0)
            return

        admin_message_targets[ADMIN_ID] = target_id

        await query.answer("已选择用户。")

        await query.edit_message_text(
            f"✅ 已选择发送对象\n\n"
            f"用户姓名：{row[2] or '未设置'}\n"
            f"用户ID：{target_id}\n"
            f"用户名：@{row[1] or '无'}\n\n"
            f"现在可以直接发送文字、图片、视频或文件。\n"
            f"发送的内容会转发给该用户。\n\n"
            f"发送成功或失败后，仍然保持当前用户。\n\n"
            f"请选择操作：",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ 返回选择用户",
                        callback_data="msg_back"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ 退出发消息",
                        callback_data="msg_exit"
                    )
                ]
            ])
        )
        return


# =========================
# 管理员主动发送消息
# =========================

async def send_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin_id = update.effective_user.id
    target_id = admin_message_targets.get(admin_id)

    if not target_id:
        return False

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )

        await update.message.reply_text(
            f"✅ 消息已发送给用户 {target_id}。"
        )

    except Exception:
        logger.exception("管理员发送消息失败")

        await update.message.reply_text(
            f"❌ 发送失败，用户ID：{target_id}\n"
            f"请检查用户是否屏蔽机器人。"
        )

    return True


# =========================
# 管理员消息处理
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    user_id = update.effective_user.id

    # 管理员主动发消息模式优先处理
    if user_id == ADMIN_ID and user_id in admin_message_targets:
        await send_admin_message(update, context)
        return

    # 普通用户注册
    if user_id != ADMIN_ID:
        register_user(update.effective_user)

    # 用户点击发消息入口
    if update.message.text == "📨 给用户发消息":
        await open_admin_user_list(update)
        return

    # 用户个人中心
    if update.message.text == "👤 个人中心":
        await profile(update, context)
        return

    # 积分查询
    if update.message.text == "⭐ 积分查询":
        await points(update, context)
        return

    # 每日签到
    if update.message.text == "📅 每日签到":
        await checkin(update, context)
        return

    # VIP套餐
    if update.message.text == "💎 VIP套餐":
        await vip_plans(update, context)
        return

    # 积分兑换
    if update.message.text == "🎁 积分兑换VIP":
        await exchange_vip(update, context)
        return

    # 联系管理员
    if update.message.text == "📞 联系管理员":
        await update.message.reply_text(
            "如需帮助，请联系管理员：\nhttps://t.me/kpilx"
        )
        return

    # 管理员面板
    if update.message.text == "⚙️ 管理员面板":
        if user_id == ADMIN_ID:
            await update.message.reply_text(
                "⚙️ 管理员面板\n\n"
                "/stats - 机器人统计\n"
                "/reducevip 用户ID 天数 - 减少VIP\n"
                "/clearvip 用户ID - 清空VIP\n"
                "/myid - 查看自己的ID"
            )
        return

    # 管理员回复用户的原有功能
    if user_id == ADMIN_ID and update.message.reply_to_message:
        con = db()
        cur = con.cursor()

        cur.execute(
            "SELECT user_id FROM replies WHERE admin_message_id=?",
            (update.message.reply_to_message.message_id,)
        )

        row = cur.fetchone()
        con.close()

        if row:
            target_id = row[0]

            try:
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id
                )

                await update.message.reply_text("✅ 已回复用户。")

            except Exception:
                await update.message.reply_text("❌ 回复失败。")

            return

    # 用户向管理员发送消息
    if user_id != ADMIN_ID:
        try:
            sent = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )

            con = db()
            cur = con.cursor()

            cur.execute(
                "INSERT OR REPLACE INTO replies "
                "(admin_message_id, user_id) VALUES (?, ?)",
                (sent.message_id, user_id)
            )

            con.commit()
            con.close()

            await update.message.reply_text("✅ 消息已发送给管理员。")

        except Exception:
            logger.exception("用户联系管理员失败")
            await update.message.reply_text("❌ 发送失败，请稍后重试。")
            
            # =========================
# 帮助命令
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        text = (
            "📚 管理员命令\n\n"
            "/start - 启动机器人\n"
            "/myid - 查看自己的ID\n"
            "/stats - 统计数据\n"
            "/reducevip 用户ID 天数 - 减少VIP天数\n"
            "/clearvip 用户ID - 清空VIP\n"
        )
    else:
        text = (
            "📚 用户帮助\n\n"
            "/start - 启动机器人\n"
            "/myid - 查看自己的ID\n"
            "/help - 查看帮助\n"
        )

    await update.message.reply_text(text)


# =========================
# 管理员用户列表命令
# =========================

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("你没有权限使用此命令。")
        return

    await open_admin_user_list(update)


# =========================
# VIP套餐回调
# =========================

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    plans = {
        "plan_7": ("7天", "1 USDT"),
        "plan_30": ("30天", "2 USDT"),
        "plan_90": ("90天", "5 USDT"),
        "plan_365": ("365天", "10 USDT"),
    }

    if data not in plans:
        return

    name, price = plans[data]

    await query.edit_message_text(
        f"你选择了：{name} VIP\n"
        f"价格：{price}\n\n"
        "请联系管理员完成付款和开通。"
    )


# =========================
# BotCommand菜单
# =========================

async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "启动机器人"),
        BotCommand("myid", "查看用户ID"),
        BotCommand("help", "帮助"),
    ]

    admin_commands = user_commands + [
        BotCommand("stats", "机器人统计"),
        BotCommand("users", "用户列表"),
        BotCommand("reducevip", "减少VIP天数"),
        BotCommand("clearvip", "清空VIP"),
    ]

    await application.bot.set_my_commands(user_commands)

    await application.bot.set_my_commands(
        admin_commands,
        scope=None
    )


# =========================
# main
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "没有找到 BOT_TOKEN 环境变量，请先设置机器人Token。"
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
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myid", myid))

    # 管理员命令
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("reducevip", reduce_vip))
    application.add_handler(CommandHandler("clearvip", clear_vip))

    # 管理员发消息回调
    application.add_handler(
        CallbackQueryHandler(
            admin_send_callback,
            pattern=r"^msg_"
        )
    )

    # VIP套餐回调
    application.add_handler(
        CallbackQueryHandler(
            payment_callback,
            pattern=r"^plan_"
        )
    )

    # 普通消息和媒体消息
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