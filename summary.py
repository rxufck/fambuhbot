"""Отправка итогов месяца во все чаты. Запускается GitHub Actions каждый день;
реально шлёт только в последний день месяца (или с флагом --force)."""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from logic import DB, MONTHS, month_report

TOKEN = os.environ["BOT_TOKEN"]
now = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")))
if (now + timedelta(days=1)).day != 1 and "--force" not in sys.argv:
    print("Сегодня не последний день месяца — пропускаю.")
    sys.exit(0)
db = DB(url=os.environ["DATABASE_URL"])
for cid in db.chats():
    text = month_report(db, cid, now.year, now.month, f"📊 Итоги месяца: {MONTHS[now.month]} {now.year}")
    data = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data) as r:
        print(cid, json.load(r).get("ok"))
