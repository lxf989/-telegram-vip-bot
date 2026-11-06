import os
import sqlite3
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8957110767
USDT_ADDRESS = "THSbZXqnnLUAzfgYkv9AavBD4YdHTTzNxz"

PLANS = {
    "1": (1, 1),
    "2": (2, 30),
    "5": (5, 90),
    "10": (10, 365)
}

db = sqlite3.connect("vip.db", check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY,
    expire TEXT
)
""")
db.commit()


def menu():
    buttons = [
        [InlineKeyboardButton("7天 VIP｜1 USDT", callback_data="1")],
        [InlineKeyboardButton("30天 VIP｜2 USDT", callback_data="2")],
        [InlineKeyboardButton("90天 VIP｜5 USDT", callback_data="5")],
        [InlineKeyboardButton("365天 VIP｜10 USDT", callback_data="10")],
        [InlineKeyboardButton("查询我的会员", callback_data="check")]
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "欢迎来到中文VIP会员机器人！\n\n请选择会员套餐：",
        reply_markup=menu()
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "check":
        row = db.execute(
            "SELECT expire FROM members WHERE user_id=?",
            (q.from_user.id,)
        ).fetchone()

        if row:
            await q.message.reply_text("VIP到期时间：" + row[0])
        else:
            await q.message.reply_text("你目前还不是VIP会员。")
        return

    if q.data in PLANS:
        price, days = PLANS[q.data]
        await q.message.reply_text(
            f"你选择了 {days} 天 VIP。\n"
            f"金额：{price} USDT\n"
            f"网络：TRC20\n\n"
            f"收款地址：\n{USDT_ADDRESS}\n\n"
            "付款后请发送交易哈希（TXID）或付款凭证，"
            "管理员核实后为你开通会员。\n"
            "请勿重复付款。"
        )


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""

    await context.bot.send_message(
        ADMIN_ID,
        f"收到会员付款申请\n"
        f"用户ID：{user.id}\n"
        f"用户名：@{user.username}\n"
        f"内容：{text}\n\n"
        f"核实到账后使用：\n/approve {user.id} 天数"
    )

    await update.message.reply_text("申请已提交，请等待管理员核实到账。")


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id = int(context.args[0])
        days = int(context.args[1])

        row = db.execute(
            "SELECT expire FROM members WHERE user_id=?",
            (user_id,)
        ).fetchone()

        now = datetime.now(timezone.utc)

        if row:
            old = datetime.fromisoformat(row[0])
            if old > now:
                now = old

        expire = (now + timedelta(days=days)).isoformat()

        db.execute(
            "INSERT OR REPLACE INTO members VALUES (?, ?)",
            (user_id, expire)
        )
        db.commit()

        await context.bot.send_message(
            user_id, f"恭喜！VIP已开通，有效期 {days} 天。"
        )
        await update.message.reply_text("开通成功！")

    except Exception:
        await update.message.reply_text(
            "格式错误，请使用：/approve 用户ID 天数"
        )


def main():
    if not TOKEN:
        raise RuntimeError("请先设置 BOT_TOKEN 环境变量")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive))
    app.run_polling()


if __name__ == "__main__":
    main()
