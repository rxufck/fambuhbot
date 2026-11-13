"""Telegram-бот семейного бюджета. Запуск: python bot.py (токен в файле .env)."""
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from logic import DB, EXPENSE, MONTHS, OTHER_INCOME, detect_category, money, month_report, parse

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
SUMMARY_HOUR = int(os.getenv("SUMMARY_HOUR", "21"))
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
db = DB(os.getenv("DB_PATH", "budget.db"))
EXP_CATS = list(EXPENSE)
logging.basicConfig(level=logging.INFO)

HELP = (
    "Пишите траты и доходы обычным текстом:\n"
    "  «-600 еда», «минус 40к свадьба зал», «450 такси»\n"
    "  «+50000 зарплата», «плюс 40 тыс проценты»\n"
    "Без знака сумма считается расходом (кроме зарплаты, процентов и т.п.).\n\n"
    "Команды:\n"
    "/balance — остаток и итоги месяца\n"
    "/month — сводка за текущий месяц (или /month 11 — за ноябрь)\n"
    "/last — последние операции\n"
    "/undo — отменить последнюю операцию\n"
    "/delete 12 — удалить операцию №12\n"
    "/setbalance 130000 — задать стартовую сумму\n"
    "В последний день месяца бот сам присылает сводку."
)


def allowed(update):
    return not ALLOWED or (update.effective_user and update.effective_user.id in ALLOWED)


async def start(update: Update, ctx):
    if not allowed(update):
        return
    db.register(update.effective_chat.id)
    await update.message.reply_text("Привет! Я веду семейный бюджет.\n"
                                    "Сначала задайте стартовую сумму: «старт 130000»\n\n" + HELP)


async def help_cmd(update: Update, ctx):
    if allowed(update):
        await update.message.reply_text(HELP)


async def setbalance(update: Update, ctx):
    if not allowed(update):
        return
    p = parse(" ".join(ctx.args)) if ctx.args else None
    if not p:
        await update.message.reply_text("Формат: старт 130000")
        return
    cid = update.effective_chat.id
    db.set_start(cid, p[0])
    await update.message.reply_text(f"Стартовая сумма: {money(p[0])}\nТекущий остаток: {money(db.balance(cid))}")


async def balance(update: Update, ctx):
    if not allowed(update):
        return
    cid = update.effective_chat.id
    n = datetime.now(TZ)
    exp, inc = db.month(cid, n.year, n.month)
    await update.message.reply_text(
        f"💰 Остаток: {money(db.balance(cid))}\n"
        f"В этом месяце: доходы {money(sum(inc.values()))}, расходы {money(sum(exp.values()))}")


async def month(update: Update, ctx):
    if not allowed(update):
        return
    n = datetime.now(TZ)
    m = n.month
    if ctx.args and ctx.args[0].isdigit() and 1 <= int(ctx.args[0]) <= 12:
        m = int(ctx.args[0])
    y = n.year if m <= n.month else n.year - 1
    await update.message.reply_text(month_report(db, update.effective_chat.id, y, m))


async def last(update: Update, ctx):
    if not allowed(update):
        return
    rows = db.recent(update.effective_chat.id, 10)
    if not rows:
        await update.message.reply_text("Операций пока нет.")
        return
    lines = [f"№{i} · {ts[8:10]}.{ts[5:7]} · {money(a, True)} · {c or '—'} · {nt or ''} ({u})"
             for i, u, ts, a, c, nt in rows]
    await update.message.reply_text("\n".join(lines))


async def undo(update: Update, ctx):
    if not allowed(update):
        return
    cid = update.effective_chat.id
    row = db.delete(cid)
    if not row:
        await update.message.reply_text("Нечего отменять.")
        return
    await update.message.reply_text(f"↩️ Удалено: {money(row[3], True)} {row[5] or ''}\n"
                                    f"Остаток: {money(db.balance(cid))}")


async def delete(update: Update, ctx):
    if not allowed(update):
        return
    cid = update.effective_chat.id
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Формат: удалить 12 (номер из списка «последние»)")
        return
    row = db.delete(cid, int(ctx.args[0]))
    if not row:
        await update.message.reply_text("Такой операции нет.")
        return
    await update.message.reply_text(f"🗑 Удалено: {money(row[3], True)} {row[5] or ''}\n"
                                    f"Остаток: {money(db.balance(cid))}")


def after_text(cid, value, cat):
    n = datetime.now(TZ)
    exp, _ = db.month(cid, n.year, n.month)
    s = f"{'➕' if value > 0 else '➖'} {money(abs(value))} · {cat or 'без категории'}\n💰 Остаток: {money(db.balance(cid))}"
    if value < 0 and cat:
        s += f"\n{cat} за месяц: {money(exp.get(cat, 0))}"
    return s


async def text(update: Update, ctx):
    if not allowed(update) or not update.message or not update.message.text:
        return
    cid = update.effective_chat.id
    raw = update.message.text
    words = raw.strip().lower().split()
    alias = ALIASES.get(words[0]) if words else None
    if alias and len(words) <= 2 and (len(words) == 1 or words[1].replace(" ", "").isdigit()):
        ctx.args = words[1:]
        await alias(update, ctx)
        return
    p = parse(raw)
    if not p:
        if update.effective_chat.type == "private":
            await update.message.reply_text("Не понял. Пример: «-600 еда» или «+50000 зарплата». Справка: «помощь»")
        return
    amount, note, sign = p
    user = update.effective_user.first_name or "—"
    cat, is_inc = detect_category(note, db.learned(cid))
    income = bool(is_inc) if sign is None else sign == "+"
    if cat is not None and income != is_inc:
        cat = None  # знак и категория противоречат — спросим
    value = amount if income else -amount
    if cat is None and income:
        cat = OTHER_INCOME
    tx = db.add(cid, user, value, cat, note)
    if cat is None:
        btns = [InlineKeyboardButton(c, callback_data=f"c:{tx}:{i}") for i, c in enumerate(EXP_CATS)]
        kb = InlineKeyboardMarkup([btns[i:i + 2] for i in range(0, len(btns), 2)])
        await update.message.reply_text(
            f"➖ {money(amount)} · {note or 'без описания'}\nК какой категории отнести?\n"
            f"💰 Остаток: {money(db.balance(cid))}", reply_markup=kb)
        return
    await update.message.reply_text(after_text(cid, value, cat))


async def pick_category(update: Update, ctx):
    q = update.callback_query
    if ALLOWED and q.from_user.id not in ALLOWED:
        await q.answer()
        return
    _, tx, i = q.data.split(":")
    cid = q.message.chat_id
    row = db.get(cid, int(tx))
    if not row:
        await q.answer("Операция уже удалена")
        return
    cat = EXP_CATS[int(i)]
    db.set_category(cid, int(tx), cat)
    db.learn(cid, row[5], cat)  # запоминаем: в следующий раз такое описание определится само
    await q.answer("Готово")
    await q.edit_message_text(after_text(cid, row[3], cat))


ALIASES = {
    "баланс": balance, "остаток": balance, "сводка": month, "итоги": month, "месяц": month,
    "последние": last, "операции": last, "отмена": undo, "отменить": undo, "удалить": delete,
    "старт": setbalance, "стартовая": setbalance, "помощь": help_cmd, "справка": help_cmd,
}


async def monthly(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    if (now + timedelta(days=1)).day != 1:
        return  # шлём только в последний день месяца
    for cid in db.chats():
        try:
            await ctx.bot.send_message(cid, month_report(db, cid, now.year, now.month,
                                                         f"📊 Итоги месяца: {MONTHS[now.month]} {now.year}"))
        except Exception:
            logging.exception("summary failed for %s", cid)


async def post_init(app):
    await app.bot.set_my_commands([
        ("balance", "Остаток и итоги месяца"), ("month", "Сводка за месяц"), ("last", "Последние операции"),
        ("undo", "Отменить последнюю операцию"), ("setbalance", "Задать стартовую сумму"),
        ("help", "Справка")])


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    for name, fn in [("start", start), ("help", help_cmd), ("setbalance", setbalance), ("balance", balance),
                     ("month", month), ("last", last), ("undo", undo), ("delete", delete)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(pick_category, pattern=r"^c:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.job_queue.run_daily(monthly, time=time(SUMMARY_HOUR, 0, tzinfo=TZ))
    app.run_polling()


if __name__ == "__main__":
    main()
