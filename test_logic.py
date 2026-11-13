from datetime import datetime

from logic import DB, detect_category, month_report, parse

cases = ["-600 еда", "минус 40к свадьба зал", "450 такси", "+50000 зарплата", "плюс 40 тыс проценты", "5 кофе",
         "зарплата 50 000", "куртка 6000 р", "130 000", "-1 000,5 ₽ аптека", "привет"]
for c in cases:
    p = parse(c)
    print(repr(c), p, detect_category(p[1]) if p else None)
db = DB(":memory:")
db.set_start(1, 130000)
db.add(1, "a", -600, "Еда", "еда")
db.add(1, "a", 40000, "Зарплата", "зп")
print(db.balance(1))
n = datetime.now()
print(month_report(db, 1, n.year, n.month))
