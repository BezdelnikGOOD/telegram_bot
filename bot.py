import os
import sys
from datetime import datetime
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import psycopg2

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ Ошибка: DATABASE_URL не найдена.")
    sys.exit(1)

print("✅ Бот запущен...")

# === ПОДКЛЮЧЕНИЕ К БД ===
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            login TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            balance INTEGER DEFAULT 100,
            joined_at TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

# === РАБОТА С БД ===
def get_user(login):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE login = %s", (login,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"login": row[0], "password": row[1], "balance": row[2], "joined_at": row[3]}
    return None

def create_user(login, password):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (login, password) VALUES (%s, %s)", (login, password))
    conn.commit()
    cur.close()
    conn.close()

def update_balance(login, amount):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + %s WHERE login = %s", (amount, login))
    conn.commit()
    cur.close()
    conn.close()

# === КОМАНДЫ ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Зарегистрируйся:\n"
        "/reg логин пароль\n\n"
        "Если уже есть аккаунт:\n"
        "/login логин пароль"
    )

def register(update: Update, context: CallbackContext):
    args = context.args
    if len(args) < 2:
        update.message.reply_text("Используй: /reg логин пароль")
        return
    login, password = args[0], args[1]
    if get_user(login):
        update.message.reply_text("❌ Логин уже занят.")
        return
    create_user(login, password)
    update.message.reply_text(f"✅ Регистрация успешна! Теперь войди: /login {login} {password}")

def login(update: Update, context: CallbackContext):
    args = context.args
    if len(args) < 2:
        update.message.reply_text("Используй: /login логин пароль")
        return
    login, password = args[0], args[1]
    user = get_user(login)
    if not user:
        update.message.reply_text("❌ Логин не найден.")
        return
    if user["password"] != password:
        update.message.reply_text("❌ Неверный пароль.")
        return
    context.user_data["login"] = login
    update.message.reply_text(f"✅ Вход выполнен. Привет, {login}!")

def profile(update: Update, context: CallbackContext):
    login = context.user_data.get("login")
    if not login:
        update.message.reply_text("❌ Сначала войди: /login логин пароль")
        return
    user = get_user(login)
    if not user:
        update.message.reply_text("❌ Пользователь не найден.")
        return
    update.message.reply_text(
        f"📋 Профиль игрока:\n"
        f"👤 Логин: {login}\n"
        f"💰 Баланс: {user['balance']} монет\n"
        f"📅 Дата регистрации: {user['joined_at'].strftime('%d.%m.%Y %H:%M')}"
    )

def logout(update: Update, context: CallbackContext):
    if "login" in context.user_data:
        del context.user_data["login"]
        update.message.reply_text("✅ Вы вышли.")
    else:
        update.message.reply_text("❌ Вы не авторизованы.")

def add_balance(update: Update, context: CallbackContext):
    ADMIN_IDS = [6573154279]
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ Доступ запрещён.")
        return
    if len(context.args) < 2:
        update.message.reply_text("Используй: /add логин сумма")
        return
    login = context.args[0]
    try:
        amount = int(context.args[1])
    except ValueError:
        update.message.reply_text("❌ Введи число.")
        return
    if amount <= 0:
        update.message.reply_text("❌ Сумма должна быть больше 0.")
        return
    user = get_user(login)
    if not user:
        update.message.reply_text("❌ Пользователь не найден.")
        return
    update_balance(login, amount)
    user = get_user(login)
    update.message.reply_text(f"✅ Баланс {login} пополнен на {amount} монет. Текущий баланс: {user['balance']}")

def echo(update: Update, context: CallbackContext):
    if update.message.text:
        update.message.reply_text("❌ Неизвестная команда. Используй /start")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("reg", register))
    dp.add_handler(CommandHandler("login", login))
    dp.add_handler(CommandHandler("profile", profile))
    dp.add_handler(CommandHandler("logout", logout))
    dp.add_handler(CommandHandler("add", add_balance))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
