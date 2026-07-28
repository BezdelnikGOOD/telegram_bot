#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import datetime
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ========== КОНФИГ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
TOKEN = os.getenv("BOT_TOKEN")  # Обязательно задать в Railway!
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "6573154279").split(",") if x.strip().isdigit()]
CURRENCY = os.getenv("CURRENCY", "⭐")
BONUS_DAILY = int(os.getenv("BONUS_DAILY", "15"))

# ========== БАЗА ДАННЫХ – ПУТЬ ЧЕРЕЗ VOLUME ==========
# Если Volume смонтирован в /app/data – то БД будет там
DB_PATH = os.getenv("DB_PATH", "/app/data/balance.db")
# Для локального теста можно оставить просто "balance.db"

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
async def init_db():
    # Создаём папку, если её нет
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                daily_last DATE,
                reg_date TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER,
                to_id INTEGER,
                amount INTEGER,
                comment TEXT,
                timestamp TEXT
            )
        ''')
        await db.commit()

# ========== ВСЕ ОСТАЛЬНЫЕ ФУНКЦИИ (БЕЗ ИЗМЕНЕНИЙ) ==========
async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT balance, daily_last FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        if row:
            return {"balance": row[0], "daily_last": row[1]}
        else:
            now = datetime.date.today().isoformat()
            await db.execute('INSERT INTO users (user_id, balance, daily_last, reg_date) VALUES (?, 0, ?, ?)',
                             (user_id, now, datetime.datetime.now().isoformat()))
            await db.commit()
            return {"balance": 0, "daily_last": now}

async def set_balance(user_id: int, new_balance: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, user_id))
        await db.commit()

async def set_daily(user_id: int, date_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET daily_last = ? WHERE user_id = ?', (date_str, user_id))
        await db.commit()

async def add_transaction(from_id, to_id, amount, comment=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO transactions (from_id, to_id, amount, comment, timestamp) VALUES (?, ?, ?, ?, ?)',
            (from_id, to_id, amount, comment, datetime.datetime.now().isoformat())
        )
        await db.commit()

async def get_history(user_id: int, limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT from_id, to_id, amount, comment, timestamp FROM transactions '
            'WHERE from_id = ? OR to_id = ? ORDER BY timestamp DESC LIMIT ?',
            (user_id, user_id, limit)
        )
        return await cursor.fetchall()

async def get_all_users_balance():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT user_id, balance FROM users ORDER BY balance DESC')
        return await cursor.fetchall()

async def get_user_by_username_or_id(bot, identifier: str):
    if identifier.isdigit():
        uid = int(identifier)
        await bot.get_chat(uid)
        return uid
    else:
        clean = identifier.replace("@", "")
        chat = await bot.get_chat(f"@{clean}")
        return chat.id

# ========== КЛАВИАТУРЫ ==========
def main_menu(user_id: int = None):
    kb = [
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="📤 Перевод", callback_data="transfer")],
        [InlineKeyboardButton(text="📜 История", callback_data="history")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="daily")],
    ]
    if user_id in ADMIN_IDS:
        kb.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Топ-10", callback_data="admin_top")],
        [InlineKeyboardButton(text="➕ Выдать баланс", callback_data="admin_add")],
        [InlineKeyboardButton(text="➖ Списать баланс", callback_data="admin_sub")],
        [InlineKeyboardButton(text="👤 Просмотр пользователя", callback_data="admin_view")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

def back_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

# ========== FSM ==========
class TransferState(StatesGroup):
    waiting_target = State()
    waiting_amount = State()

class AdminAddState(StatesGroup):
    waiting_target = State()
    waiting_amount = State()

class AdminSubState(StatesGroup):
    waiting_target = State()
    waiting_amount = State()

class AdminViewState(StatesGroup):
    waiting_target = State()

# ========== ОБРАБОТЧИКИ ==========
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await get_user(user_id)
    await message.answer(
        f"👋 Добро пожаловать!\nВаш баланс: { (await get_user(user_id))['balance'] } {CURRENCY}\n"
        "Используйте меню:",
        reply_markup=main_menu(user_id)
    )

async def cmd_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = await get_user(user_id)
    await callback.message.edit_text(
        f"💰 Ваш баланс: {data['balance']} {CURRENCY}",
        reply_markup=main_menu(user_id)
    )
    await callback.answer()

async def cmd_daily(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = await get_user(user_id)
    today = datetime.date.today().isoformat()
    if data['daily_last'] == today:
        await callback.answer("🎁 Вы уже получили бонус сегодня!", show_alert=True)
        return
    new_bal = data['balance'] + BONUS_DAILY
    await set_balance(user_id, new_bal)
    await set_daily(user_id, today)
    await add_transaction(0, user_id, BONUS_DAILY, "Ежедневный бонус")
    await callback.message.edit_text(
        f"🎁 Получено {BONUS_DAILY} {CURRENCY}!\nТеперь баланс: {new_bal} {CURRENCY}",
        reply_markup=main_menu(user_id)
    )
    await callback.answer()

async def cmd_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    rows = await get_history(user_id, 20)
    if not rows:
        await callback.message.edit_text("📜 История пуста.", reply_markup=main_menu(user_id))
        await callback.answer()
        return
    text = "📜 Последние 20 операций:\n\n"
    for from_id, to_id, amount, comment, ts in rows:
        if from_id == user_id:
            direction = "➡️ исх." if amount > 0 else "⬅️ вх."
        else:
            direction = "⬅️ вх." if amount > 0 else "➡️ исх."
        text += f"{ts[:16]} {direction} {abs(amount)} {CURRENCY} {comment}\n"
    await callback.message.edit_text(text[:4000], reply_markup=main_menu(user_id))
    await callback.answer()

async def cmd_transfer(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📤 Введите @username или ID получателя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel_transfer")]
        ])
    )
    await state.set_state(TransferState.waiting_target)
    await callback.answer()

async def transfer_target(message: types.Message, state: FSMContext):
    target = message.text.strip()
    await state.update_data(target=target)
    await message.answer("Введите сумму (целое число > 0):")
    await state.set_state(TransferState.waiting_amount)

async def transfer_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введите целое положительное число!")
        return
    data = await state.get_data()
    target_username = data['target']
    from_id = message.from_user.id
    try:
        to_id = await get_user_by_username_or_id(message.bot, target_username)
    except:
        await message.answer("❌ Пользователь не найден. Попробуйте снова /start и выберите перевод")
        await state.clear()
        return
    if to_id == from_id:
        await message.answer("❌ Нельзя перевести самому себе!")
        await state.clear()
        return
    bal_data = await get_user(from_id)
    if bal_data['balance'] < amount:
        await message.answer(f"❌ Недостаточно средств! У вас {bal_data['balance']} {CURRENCY}")
        await state.clear()
        return
    from_bal = bal_data['balance'] - amount
    to_data = await get_user(to_id)
    to_bal = to_data['balance'] + amount
    await set_balance(from_id, from_bal)
    await set_balance(to_id, to_bal)
    await add_transaction(from_id, to_id, amount, f"Перевод от {message.from_user.username or message.from_user.first_name}")
    await message.answer(f"✅ Переведено {amount} {CURRENCY} пользователю @{target_username}")
    try:
        await message.bot.send_message(to_id, f"📥 Вам поступил перевод {amount} {CURRENCY} от {message.from_user.username or message.from_user.first_name}")
    except:
        pass
    await state.clear()

async def cancel_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Операция отменена.", reply_markup=main_menu(callback.from_user.id))
    await callback.answer()

async def cmd_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("🔧 Админ-панель:", reply_markup=admin_keyboard())
    await callback.answer()

async def admin_top(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    rows = await get_all_users_balance()
    top = rows[:10]
    if not top:
        await callback.message.edit_text("📊 Нет пользователей.", reply_markup=admin_keyboard())
        await callback.answer()
        return
    text = "📊 Топ-10 по балансу:\n\n"
    for i, (uid, bal) in enumerate(top, 1):
        try:
            chat = await callback.bot.get_chat(uid)
            name = chat.username or chat.first_name or str(uid)
        except:
            name = str(uid)
        text += f"{i}. @{name} — {bal} {CURRENCY}\n"
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "Введите @username или ID пользователя для выдачи баланса:",
        reply_markup=back_button()
    )
    await state.set_state(AdminAddState.waiting_target)
    await callback.answer()

async def admin_add_target(message: types.Message, state: FSMContext):
    target = message.text.strip()
    await state.update_data(target=target)
    await message.answer("Введите сумму для выдачи (целое > 0):")
    await state.set_state(AdminAddState.waiting_amount)

async def admin_add_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введите целое положительное число!")
        return
    data = await state.get_data()
    target = data['target']
    try:
        uid = await get_user_by_username_or_id(message.bot, target)
    except:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    user_data = await get_user(uid)
    new_bal = user_data['balance'] + amount
    await set_balance(uid, new_bal)
    await add_transaction(0, uid, amount, f"Админ выдал {amount} от {message.from_user.id}")
    await message.answer(f"✅ Выдано {amount} {CURRENCY} пользователю. Новый баланс: {new_bal} {CURRENCY}")
    await state.clear()

async def admin_sub_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "Введите @username или ID пользователя для списания баланса:",
        reply_markup=back_button()
    )
    await state.set_state(AdminSubState.waiting_target)
    await callback.answer()

async def admin_sub_target(message: types.Message, state: FSMContext):
    target = message.text.strip()
    await state.update_data(target=target)
    await message.answer("Введите сумму для списания (целое > 0):")
    await state.set_state(AdminSubState.waiting_amount)

async def admin_sub_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введите целое положительное число!")
        return
    data = await state.get_data()
    target = data['target']
    try:
        uid = await get_user_by_username_or_id(message.bot, target)
    except:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    user_data = await get_user(uid)
    if user_data['balance'] < amount:
        await message.answer(f"❌ Недостаточно средств! Баланс: {user_data['balance']} {CURRENCY}")
        await state.clear()
        return
    new_bal = user_data['balance'] - amount
    await set_balance(uid, new_bal)
    await add_transaction(0, uid, -amount, f"Админ списал {amount} от {message.from_user.id}")
    await message.answer(f"✅ Списано {amount} {CURRENCY} у пользователя. Новый баланс: {new_bal} {CURRENCY}")
    await state.clear()

async def admin_view_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "Введите @username или ID пользователя для просмотра:",
        reply_markup=back_button()
    )
    await state.set_state(AdminViewState.waiting_target)
    await callback.answer()

async def admin_view_target(message: types.Message, state: FSMContext):
    target = message.text.strip()
    try:
        uid = await get_user_by_username_or_id(message.bot, target)
    except:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    data = await get_user(uid)
    history = await get_history(uid, 5)
    hist_text = ""
    if history:
        for from_id, to_id, amount, comment, ts in history[:5]:
            hist_text += f"{ts[:16]} {abs(amount)} {CURRENCY} {comment}\n"
    else:
        hist_text = "нет операций"
    await message.answer(
        f"👤 Информация о пользователе:\n"
        f"ID: {uid}\n"
        f"💰 Баланс: {data['balance']} {CURRENCY}\n"
        f"📅 Последний бонус: {data['daily_last'] or 'никогда'}\n"
        f"📋 Последние 5 операций:\n{hist_text}"
    )
    await state.clear()

async def back_main(callback: CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    user_id = callback.from_user.id
    await callback.message.edit_text("🏠 Главное меню:", reply_markup=main_menu(user_id))
    await callback.answer()

# ========== HTTP-ХЕЛСЧЕК ДЛЯ RAILWAY ==========
from aiohttp import web

async def health(request):
    return web.Response(text="OK")

async def start_http():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("✅ HTTP сервер для healthcheck запущен на порту 8080")

# ========== ЗАПУСК ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    storage = MemoryStorage()
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=storage)

    dp.message.register(cmd_start, Command("start"))
    dp.callback_query.register(cmd_balance, F.data == "balance")
    dp.callback_query.register(cmd_daily, F.data == "daily")
    dp.callback_query.register(cmd_history, F.data == "history")
    dp.callback_query.register(cmd_transfer, F.data == "transfer")
    dp.callback_query.register(cancel_transfer, F.data == "cancel_transfer")
    dp.callback_query.register(cmd_admin_panel, F.data == "admin_panel")
    dp.callback_query.register(admin_top, F.data == "admin_top")
    dp.callback_query.register(admin_add_start, F.data == "admin_add")
    dp.callback_query.register(admin_sub_start, F.data == "admin_sub")
    dp.callback_query.register(admin_view_start, F.data == "admin_view")
    dp.callback_query.register(back_main, F.data == "back_main")

    dp.message.register(transfer_target, TransferState.waiting_target)
    dp.message.register(transfer_amount, TransferState.waiting_amount)
    dp.message.register(admin_add_target, AdminAddState.waiting_target)
    dp.message.register(admin_add_amount, AdminAddState.waiting_amount)
    dp.message.register(admin_sub_target, AdminSubState.waiting_target)
    dp.message.register(admin_sub_amount, AdminSubState.waiting_amount)
    dp.message.register(admin_view_target, AdminViewState.waiting_target)

    @dp.message(Command("cancel"))
    async def cancel_cmd(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=main_menu(message.from_user.id))

    # Запускаем HTTP-сервер для Railway
    loop = asyncio.get_event_loop()
    loop.create_task(start_http())

    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())