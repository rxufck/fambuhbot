"""Логика бота: разбор сообщений, категории, база данных (SQLite). Без зависимости от Telegram."""
import re
import sqlite3
from datetime import datetime

# ---------- КАТЕГОРИИ (можно редактировать) ----------
# категория -> ключевые слова (достаточно начала слова, регистр не важен)
EXPENSE = {
    "Еда": ["еда", "продукт", "магазин", "пятероч", "магнит", "перекрест", "ашан", "лента", "кафе", "ресторан",
            "обед", "ужин", "завтрак", "кофе", "пицц", "суши", "доставк", "вкусвилл", "самокат"],
    "Одежда": ["одежд", "куртк", "обув", "кроссов", "джинс", "платье", "шапк", "сумк", "футболк", "свитер"],
    "Красота": ["красот", "волос", "маникюр", "парикмахер", "стрижк", "косметик", "салон", "ногти", "брови",
                "ресниц", "массаж"],
    "Свадьба": ["свадьб", "зал", "ведущ", "фотограф", "видеограф", "кольц", "банкет", "декор", "торт", "кортеж"],
    "Транспорт": ["такси", "бензин", "метро", "автобус", "проезд", "парковк", "заправк", "убер", "каршеринг"],
    "Жильё и ЖКХ": ["жкх", "аренд", "квартплат", "коммунал", "свет", "интернет", "вода", "газ"],
    "Праздники и подарки": ["подар", "день рождени", "новый год", "новогод", "цветы", "праздник"],
    "Здоровье": ["аптек", "врач", "лекарств", "стомат", "зуб", "анализ", "клиник", "здоровь"],
    "Путешествия": ["билет", "отель", "отпуск", "путешеств", "авиа", "самолёт", "самолет", "поезд", "виза"],
    "Развлечения": ["кино", "театр", "концерт", "игр", "подписк", "боулинг", "клуб"],
    "Жизнь / прочее": ["жизнь", "прочее", "разное"],
}
INCOME = {
    "Зарплата": ["зарплат", "зп", "аванс", "премия", "оклад"],
    "Проценты": ["процент", "кэшбэк", "кешбэк", "дивиденд", "вклад"],
    "Прочий доход": ["доход", "подработк", "возврат"],
}
OTHER_EXPENSE = "Жизнь / прочее"
OTHER_INCOME = "Прочий доход"

_NUM = r"(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_SUF = r"(?:(к|k|тыс\w*|т\.?р\.?)(?!\w))?"
_RUB = r"(?:₽|(?:руб\w*|р)(?!\w)\.?)?"
_LEAD = re.compile(rf"^\s*([+-])?\s*{_NUM}\s*{_SUF}\s*{_RUB}\s*(.*)$", re.I | re.S)
_TRAIL = re.compile(rf"^(.*?)\s*([+-])?\s*{_NUM}\s*{_SUF}\s*{_RUB}\s*$", re.I | re.S)


def _to_amount(num, suf):
    v = float(num.replace(" ", "").replace(" ", "").replace(",", "."))
    if suf:
        v *= 1000
    return v


def parse(text):
    """Возвращает (сумма > 0, описание, знак '+', '-' или None) либо None, если это не операция."""
    t = text.strip().lower()
    t = re.sub(r"^\s*минус\b", "-", t)
    t = re.sub(r"^\s*плюс\b", "+", t)
    t = t.replace("−", "-").replace("–", "-").replace("—", "-")
    m = _LEAD.match(t)
    if m:
        sign, num, suf, note = m.group(1), m.group(2), m.group(3), m.group(4)
    else:
        m = _TRAIL.match(t)
        if not m:
            return None
        note, sign, num, suf = m.group(1), m.group(2), m.group(3), m.group(4)
        if not note.strip():
            return None
    amount = _to_amount(num, suf)
    if amount <= 0:
        return None
    return amount, note.strip(" ,.-"), sign


def detect_category(note, learned=None):
    """Возвращает (категория, это_доход) или (None, None), если определить не удалось."""
    n = note.lower().strip()
    if learned and n in learned:
        cat = learned[n]
        return cat, cat in INCOME
    for table, inc in ((EXPENSE, False), (INCOME, True)):
        for cat, words in table.items():
            if n == cat.lower():
                return cat, inc
            for w in words:
                if w in n:
                    return cat, inc
    return None, None


def money(x, sign=False):
    s = f"{abs(x):,.0f}".replace(",", " ")
    if sign:
        return ("+" if x >= 0 else "−") + s + " ₽"
    return ("−" if x < 0 else "") + s + " ₽"


# ---------- БАЗА ДАННЫХ ----------
# Если задана переменная DATABASE_URL — используется PostgreSQL (для хостинга), иначе локальный SQLite.
class DB:
    def __init__(self, path="budget.db", url=None):
        self.url = url or None
        self.path = path
        self.pg = bool(self.url)
        self._connect()
        id_col = "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        big = "BIGINT" if self.pg else "INTEGER"
        real = "DOUBLE PRECISION" if self.pg else "REAL"
        self._run(f"CREATE TABLE IF NOT EXISTS chats(chat_id {big} PRIMARY KEY, start_balance {real} DEFAULT 0)")
        self._run(f"CREATE TABLE IF NOT EXISTS tx(id {id_col}, chat_id {big}, user_name TEXT, ts TEXT, "
                  f"amount {real}, category TEXT, note TEXT)")
        self._run(f"CREATE TABLE IF NOT EXISTS learned(chat_id {big}, note TEXT, category TEXT, "
                  f"PRIMARY KEY(chat_id, note))")

    def _connect(self):
        if self.pg:
            import psycopg
            self.c = psycopg.connect(self.url, autocommit=True)
        else:
            self.c = sqlite3.connect(self.path, check_same_thread=False)

    def _run(self, sql, params=(), fetch=None):
        if self.pg:
            sql = sql.replace("?", "%s")
        for attempt in (0, 1):
            try:
                cur = self.c.execute(sql, params)
                out = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
                if not self.pg:
                    self.c.commit()
                return out
            except Exception as e:
                if self.pg and attempt == 0 and e.__class__.__name__ in ("OperationalError", "InterfaceError"):
                    self._connect()  # соединение с БД оборвалось — переподключаемся
                    continue
                raise

    def register(self, chat_id):
        self._run("INSERT INTO chats(chat_id) VALUES(?) ON CONFLICT DO NOTHING", (chat_id,))

    def chats(self):
        return [r[0] for r in self._run("SELECT chat_id FROM chats", fetch="all")]

    def set_start(self, chat_id, amount):
        self.register(chat_id)
        self._run("UPDATE chats SET start_balance=? WHERE chat_id=?", (amount, chat_id))

    def balance(self, chat_id):
        self.register(chat_id)
        s = self._run("SELECT start_balance FROM chats WHERE chat_id=?", (chat_id,), "one")[0]
        t = self._run("SELECT COALESCE(SUM(amount),0) FROM tx WHERE chat_id=?", (chat_id,), "one")[0]
        return s + t

    def add(self, chat_id, user, amount, category, note, ts=None):
        self.register(chat_id)
        ts = ts or datetime.now().isoformat(timespec="seconds")
        return self._run("INSERT INTO tx(chat_id,user_name,ts,amount,category,note) VALUES(?,?,?,?,?,?) RETURNING id",
                         (chat_id, user, ts, amount, category, note), "one")[0]

    def set_category(self, chat_id, tx_id, category):
        self._run("UPDATE tx SET category=? WHERE id=? AND chat_id=?", (category, tx_id, chat_id))

    def get(self, chat_id, tx_id):
        return self._run("SELECT id,user_name,ts,amount,category,note FROM tx WHERE id=? AND chat_id=?",
                         (tx_id, chat_id), "one")

    def delete(self, chat_id, tx_id=None):
        if tx_id is None:
            r = self._run("SELECT id FROM tx WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,), "one")
            if not r:
                return None
            tx_id = r[0]
        row = self.get(chat_id, tx_id)
        if row:
            self._run("DELETE FROM tx WHERE id=?", (tx_id,))
        return row

    def recent(self, chat_id, n=10):
        return self._run("SELECT id,user_name,ts,amount,category,note FROM tx WHERE chat_id=? "
                         "ORDER BY id DESC LIMIT ?", (chat_id, n), "all")

    def month(self, chat_id, year, month):
        ym = f"{year:04d}-{month:02d}"
        rows = self._run("SELECT COALESCE(category,'Без категории'), amount FROM tx "
                         "WHERE chat_id=? AND substr(ts,1,7)=?", (chat_id, ym), "all")
        exp, inc = {}, {}
        for cat, a in rows:
            d = inc if a > 0 else exp
            d[cat] = d.get(cat, 0) + abs(a)
        return exp, inc

    def learn(self, chat_id, note, category):
        if note:
            self._run("INSERT INTO learned(chat_id,note,category) VALUES(?,?,?) "
                      "ON CONFLICT(chat_id,note) DO UPDATE SET category=excluded.category",
                      (chat_id, note.lower().strip(), category))

    def learned(self, chat_id):
        return dict(self._run("SELECT note, category FROM learned WHERE chat_id=?", (chat_id,), "all"))


MONTHS = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


def month_report(db, chat_id, year, month, title=None):
    exp, inc = db.month(chat_id, year, month)
    te, ti = sum(exp.values()), sum(inc.values())
    out = [title or f"📊 Сводка за {MONTHS[month]} {year}", ""]
    out.append(f"Доходы: {money(ti)}")
    for c, v in sorted(inc.items(), key=lambda x: -x[1]):
        out.append(f"   • {c}: {money(v)}")
    out.append(f"Расходы: {money(te)}")
    for c, v in sorted(exp.items(), key=lambda x: -x[1]):
        out.append(f"   • {c}: {money(v)} ({v / te * 100:.0f}%)")
    out += ["", f"Накоплено за месяц: {money(ti - te, sign=True)}", f"💰 Общий остаток: {money(db.balance(chat_id))}"]
    return "\n".join(out)
