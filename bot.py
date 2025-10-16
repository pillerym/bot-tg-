"""
Telegram referral bot (aiogram v2) — bot.py

Features:
- Unique referral link for each user: t.me/<bot_username>?start=<telegram_id>
- Stores users, deposits, referral earnings, withdrawals in SQLite
- Main menu with welcome message
- "Моя статистика" opens full stats menu
- Admin command /add_deposit to simulate deposit and credit referrer
- Admin command /admin to view overall statistics
"""

import logging
import sqlite3
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, date
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, executor

# ---- load config ----
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]
REF_PERCENT = Decimal(os.getenv("REF_PERCENT", "1.0"))  # percent

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---- sqlite setup ----
DB_PATH = "refbot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        referred_by INTEGER,
        joined_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_telegram_id INTEGER,
        amount TEXT,
        currency TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ref_earnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_telegram_id INTEGER,
        from_user_telegram_id INTEGER,
        amount TEXT,
        created_at TEXT,
        note TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_telegram_id INTEGER,
        amount TEXT,
        status TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ---- helpers ----
def db_conn():
    return sqlite3.connect(DB_PATH)

def decimal_str(v):
    d = Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return format(d, 'f')

def get_bot_username_sync():
    import asyncio
    loop = asyncio.get_event_loop()
    me = loop.run_until_complete(bot.get_me())
    return me.username

BOT_USERNAME = get_bot_username_sync() or "YourBotUsername"

def create_user_if_not_exists(tg_id:int, username:str=None, first_name:str=None, referred_by:int=None):
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (tg_id,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (telegram_id, username, first_name, referred_by, joined_at) VALUES (?, ?, ?, ?, ?)",
            (tg_id, username, first_name, referred_by, datetime.utcnow().isoformat())
        )
        conn.commit()
    conn.close()

def get_user(tg_id):
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT telegram_id, username, first_name, referred_by, joined_at FROM users WHERE telegram_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "telegram_id": row[0],
        "username": row[1],
        "first_name": row[2],
        "referred_by": row[3],
        "joined_at": row[4]
    }

def count_referred(tg_id):
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (tg_id,))
    r = cur.fetchone()[0]
    conn.close()
    return r

def sum_ref_earnings(tg_id):
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM ref_earnings WHERE referrer_telegram_id = ?", (tg_id,))
    s = Decimal(str(cur.fetchone()[0] or "0"))
    conn.close()
    return s

def sum_ref_earnings_today(tg_id):
    conn = db_conn(); cur = conn.cursor()
    today_iso = date.today().isoformat()
    cur.execute("SELECT amount, created_at FROM ref_earnings WHERE referrer_telegram_id = ?", (tg_id,))
    rows = cur.fetchall()
    total = Decimal("0")
    for amount, created_at in rows:
        if created_at and created_at.startswith(today_iso):
            total += Decimal(str(amount))
    conn.close()
    return total

def pending_withdrawable(tg_id):
    total = sum_ref_earnings(tg_id)
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM withdrawals WHERE user_telegram_id = ? AND status = 'paid'", (tg_id,))
    paid = Decimal(str(cur.fetchone()[0] or "0"))
    conn.close()
    return total - paid

# ---- keyboards ----
def welcome_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("📊 Моя статистика")
    return kb

def stats_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add("👥 Мои рефералы")
    kb.add("💰 Мой доход")
    kb.add("🏧 Вывести реферальные")
    kb.add("◀️ Назад")
    return kb

# ---- handlers ----
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    args = message.get_args()
    referred_by = None
    if args:
        try:
            referred_by = int(args)
        except:
            referred_by = None

    existing = get_user(message.from_user.id)
    if existing is None:
        if referred_by == message.from_user.id:
            referred_by = None
        create_user_if_not_exists(message.from_user.id, message.from_user.username, message.from_user.first_name, referred_by)
    else:
        conn = db_conn(); cur = conn.cursor()
        cur.execute("UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?", (message.from_user.username, message.from_user.first_name, message.from_user.id))
        conn.commit(); conn.close()

    await message.answer(
        "💰 Привет!\n"
        "Это твой личный бот для прокачки капитала 💸 и вайба 🔥\n"
        "Зови друзей — и вместе вы сможете умножать свой уровень! 🚀\n\n"
        "👥 +1 друг = +0.05%\n"
        "👥 +5 друзей = +0.5%\n"
        "👥 +10 друзей = +5%\n\n"
        "Чем больше друзей, тем мощнее ты 💪\n"
        "Добавляй своих и наблюдай, как растёт твой “доход” (и настроение 😏)",
        reply_markup=welcome_keyboard()
    )

@dp.message_handler(lambda message: message.text == "📊 Моя статистика")
async def show_stats_menu(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=stats_keyboard())

@dp.message_handler(lambda message: message.text == "◀️ Назад")
async def go_back(message: types.Message):
    await message.answer("Главное меню", reply_markup=welcome_keyboard())

@dp.message_handler(lambda message: message.text == "👥 Мои рефералы")
async def my_refs(message: types.Message):
    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT telegram_id, username, first_name, joined_at FROM users WHERE referred_by = ? ORDER BY joined_at DESC", (message.from_user.id,))
    rows = cur.fetchall(); conn.close()
    if not rows:
        await message.answer("У тебя ещё нет рефералов.", reply_markup=stats_keyboard())
        return
    lines = []
    for r in rows:
        tid, uname, fname, joined = r
        display = uname or fname or str(tid)
        lines.append(f"- {display} ({tid}) — {joined.split('T')[0]}")
    await message.answer("Твои рефералы:\n" + "\n".join(lines), reply_markup=stats_keyboard())

@dp.message_handler(lambda message: message.text == "💰 Мой доход")
async def my_income(message: types.Message):
    total = sum_ref_earnings(message.from_user.id)
    today = sum_ref_earnings_today(message.from_user.id)
    to_withdraw = pending_withdrawable(message.from_user.id)
    await message.answer(
        f"💰 Мой доход\n\n"
        f"Всего заработано: {decimal_str(total)} USDT\n"
        f"За сегодня: {decimal_str(today)} USDT\n"
        f"К выплате: {decimal_str(to_withdraw)} USDT",
        reply_markup=stats_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🏧 Вывести реферальные")
async def withdraw_request(message: types.Message):
    to_withdraw = pending_withdrawable(message.from_user.id)
    if to_withdraw <= Decimal("0"):
        await message.answer("У тебя нет средств для вывода.", reply_markup=stats_keyboard())
        return
    conn = db_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO withdrawals (user_telegram_id, amount, status, created_at) VALUES (?, ?, ?, ?)",
                (message.from_user.id, decimal_str(to_withdraw), "pending", datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await message.answer(f"Заявка на вывод создана: {decimal_str(to_withdraw)} USDT\nСтатус: ожидает обработки.", reply_markup=stats_keyboard())
    for admin in ADMINS:
        try:
            await bot.send_message(admin, f"📤 Новая заявка на вывод:\nПользователь: @{message.from_user.username or message.from_user.id} ({message.from_user.id})\nСумма: {decimal_str(to_withdraw)} USDT")
        except Exception:
            logging.exception("cannot notify admin")

# ---- admin commands ----
@dp.message_handler(commands=["add_deposit"])
async def admin_add_deposit(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.reply("Только админы."); return
    args = message.get_args().split()
    if len(args) < 2:
        await message.reply("Использование: /add_deposit <user_id> <amount> [currency]"); return
    try:
        target_id = int(args[0])
        amount = Decimal(args[1])
    except Exception:
        await message.reply("Неверные параметры."); return
    currency = args[2] if len(args) >= 3 else "USDT"
    create_user_if_not_exists(target_id)
    conn = db_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO deposits (user_telegram_id, amount, currency, created_at) VALUES (?,?,?,?)",
                (target_id, decimal_str(amount), currency, datetime.utcnow().isoformat()))
    conn.commit()
    user = get_user(target_id)
    if user and user.get("referred_by"):
        referrer = user["referred_by"]
        if referrer != target_id:
            bonus = (amount * REF_PERCENT / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            cur.execute("INSERT INTO ref_earnings (referrer_telegram_id, from_user_telegram_id, amount, created_at, note) VALUES (?,?,?,?,?)",
                        (referrer, target_id, str(bonus), datetime.utcnow().isoformat(), f"Referral bonus {REF_PERCENT}% from {target_id}"))
            conn.commit()
            try:
                await bot.send_message(referrer, f"🎉 Твой реферал @{user.get('username') or user.get('first_name') or target_id} пополнил {decimal_str(amount)} {currency}. Тебе начислено {decimal_str(bonus)} USDT.")
            except Exception:
                pass
    conn.close()
    await message.reply("✅ Депозит добавлен и реферальный бонус начислен при необходимости.")

@dp.message_handler(commands=["admin"])
async def admin_stats(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.reply("Только админы.")
        return

    conn = db_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
    total_referrals = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM deposits")
    total_deposits = Decimal(str(cur.fetchone()[0] or "0"))

    cur.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM ref_earnings")
    total_ref_earnings = Decimal(str(cur.fetchone()[0] or "0"))
    conn.close()

    text = (
        f"📊 Статистика бота\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Всего рефералов: {total_referrals}\n"
        f"Всего пополнено: {decimal_str(total_deposits)} USDT\n"
        f"Всего реферальных начислений: {decimal_str(total_ref_earnings)} USDT"
    )
    await message.reply(text)

# ---- fallback ----
@dp.message_handler()
async def fallback(message: types.Message):
    await message.answer("Не понимаю. Используй меню.", reply_markup=welcome_keyboard())

# ---- start bot ----
if __name__ == "__main__":
    print("Bot is starting...")
    executor.start_polling(dp, skip_updates=True)
