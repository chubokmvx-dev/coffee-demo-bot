"""Мінімальне сховище в JSON-файлі + демо-база умовних клієнтів і журнал візитів."""
import json, os, random, secrets, time
from datetime import datetime
from zoneinfo import ZoneInfo

PATH = os.getenv("DATA_PATH", "/tmp/coffee_demo.json")
DAY = 86400
DEFAULT_SETTINGS = {
    "shop": os.getenv("SHOP_NAME", "Кав'ярня"),
    "stamps_goal": 8,
    "passport_days": 7,
    "points": ["Кав'ярня", "Будка №1", "Будка №2"],
    "drinks": [["Еспресо", 45], ["Американо", 55], ["Капучино", 70], ["Лате", 75]],
    "promos": [
        "☕ Друга кава за пів ціни до 14:00! Покажіть це повідомлення бариста.",
        "🥐 Лате + круасан за 99 грн, тільки з 11:00 до 14:00.",
        "☔ Дощ за вікном — у нас тепло. Капучино −20% до 14:00.",
    ],
    "maps_url": os.getenv("MAPS_URL", "https://maps.google.com"),
    "quiet_from": 11,
    "quiet_to": 14,
    "promo_time": "10:30",
    "weekly_on": True,
    "welcome_on": True,
}
TZ = ZoneInfo("Europe/Kyiv")
DEFAULT_REWARDS = {
    "welcome": "Безкоштовний напій до першої кави",
    "stamps": "Безкоштовна кава «як завжди» ({usual})",
    "passport": "Круасан",
}
WD = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]
WD_ACC = ["понеділок", "вівторок", "середу", "четвер", "п'ятницю", "суботу", "неділю"]


def _empty():
    return {"users": {}, "orders": {}, "next_order": 1, "seeded": False, "promos": 0, "reviews": [],
            "log": [], "scheduled": [], "weekly_sent": "", "seeded_log": False,
            "rewards": dict(DEFAULT_REWARDS), "settings": {}, "invites": {}}


class Store:
    def __init__(self, path=PATH):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.d = json.load(f)
        except Exception:
            self.d = _empty()
        for k, v in _empty().items():
            self.d.setdefault(k, v)
        for k, v in DEFAULT_SETTINGS.items():
            self.d["settings"].setdefault(k, v)
        for k, v in DEFAULT_REWARDS.items():
            self.d["rewards"].setdefault(k, v)
        for u in self.d["users"].values():     # міграція: раніше подарунки були числом
            if isinstance(u.get("gifts"), int):
                u["gifts"] = [{"goal": "welcome"} for _ in range(u["gifts"])]
        if not self.d["seeded"]:
            self._seed()
        if not self.d["seeded_log"]:
            self._seed_log()
        self.save()

    def cfg(self, key):
        return self.d["settings"][key]

    def goal_name(self, goal):
        return {"welcome": "вітальний подарунок",
                "stamps": f"{self.cfg('stamps_goal')} штампів",
                "passport": f"паспорт: {len(self.cfg('points'))} точки за {self.cfg('passport_days')} днів"}[goal]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _seed(self):
        """60 умовних клієнтів: щоб звіти й «повернення зниклих» мали що показати."""
        rnd = random.Random(7)
        now = time.time()
        for i in range(60):
            uid = str(-(i + 1))
            gone = rnd.random() < 0.3
            self.d["users"][uid] = {
                "name": f"Демо-клієнт {i + 1}", "phone": None, "demo": True,
                "stamps": rnd.randint(0, 7), "gifts": [], "mode": "client",
                "joined": now - rnd.randint(10, 90) * DAY,
                "last_visit": now - (rnd.randint(22, 60) if gone else rnd.randint(0, 14)) * DAY,
                "code": f"9{i:03d}",
            }
        self.d["seeded"] = True

    def _seed_log(self):
        """Демо-журнал візитів за 4 тижні. Навмисно слабкий вівторок 11–14, щоб розбір мав що знайти."""
        rnd = random.Random(11)
        base = {8: 7, 9: 9, 10: 8, 11: 4, 12: 5, 13: 4, 14: 5, 15: 6, 16: 7, 17: 8, 18: 6}
        today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        for back in range(1, 29):
            day = today.fromtimestamp(today.timestamp() - back * DAY, TZ)
            for hour, n in base.items():
                k = n
                if hour in (11, 12, 13):
                    k = round(n * (0.4 if day.weekday() == 1 else 1.0))
                for _ in range(max(0, k + rnd.randint(-1, 1))):
                    ts = day.replace(hour=hour, minute=rnd.randint(0, 59)).timestamp()
                    self.d["log"].append({"ts": ts, "point": rnd.choice(self.cfg("points")), "demo": True})
        self.d["seeded_log"] = True

    # --- користувачі ---
    def user(self, uid):
        return self.d["users"].get(str(uid))

    def ensure(self, uid, name):
        u = self.user(uid)
        if u:
            return u, False
        used = {x["code"] for x in self.d["users"].values()}
        code = next(c for c in (f"{random.randint(100000, 899999)}" for _ in range(10_000)) if c not in used)
        u = {"name": name, "phone": None, "demo": False, "stamps": 0, "gifts": [{"goal": "welcome"}] if self.cfg("welcome_on") else [], "mode": "client",
             "joined": time.time(), "last_visit": time.time(), "code": code, "owner_ok": False,
             "point": self.cfg("points")[0], "pass": {}, "last": None, "prefs": "", "token": secrets.token_urlsafe(8)}
        self.d["users"][str(uid)] = u
        self.save()
        return u, True

    def token(self, u):
        """Довгий непередбачуваний токен для QR; для старих записів створюється при першому використанні."""
        if not u.get("token"):
            u["token"] = secrets.token_urlsafe(8)
            self.save()
        return u["token"]

    def by_token(self, tok):
        for k, u in self.d["users"].items():
            if u.get("token") == tok:
                return k, u
        return None, None

    def by_code(self, code):
        for k, u in self.d["users"].items():
            if u["code"] == code:
                return k, u
        return None, None

    def real(self):
        return [(int(k), u) for k, u in self.d["users"].items() if not u.get("demo")]

    def demo_count(self):
        return sum(1 for u in self.d["users"].values() if u.get("demo"))

    def inactive(self, days=21):
        lim = time.time() - days * DAY
        return [(int(k), u) for k, u in self.d["users"].items() if u["last_visit"] < lim]

    # --- замовлення ---
    def add_order(self, uid, drink, price, minutes, point):
        n = self.d["next_order"]
        self.d["next_order"] = n + 1
        self.d["orders"][str(n)] = {"uid": uid, "drink": drink, "price": price, "min": minutes,
                                    "point": point, "ts": time.time(), "done": False}
        self.save()
        return n

    # --- подарунки та нагороди ---
    def reward_title(self, goal, u=None):
        """Поточна нагорода за ціль. {usual} → напій «як завжди» клієнта."""
        text = self.d["rewards"].get(goal, DEFAULT_REWARDS.get(goal, "Подарунок"))
        usual = "напій на вибір"
        last = (u or {}).get("last")
        names = [d[0] for d in self.cfg("drinks")]
        if last and last["drink"] < len(names):
            usual = names[last["drink"]]
        return text.replace("{usual}", usual)

    def grant(self, u, goal):
        u["gifts"].append({"goal": goal})

    def take(self, u, goal):
        for i, g in enumerate(u["gifts"]):
            if g["goal"] == goal:
                return u["gifts"].pop(i)
        return None

    # --- паспорт ---
    def passport_state(self, u):
        days = self.cfg("passport_days")
        lim = time.time() - days * DAY
        have = {p: ts for p, ts in (u.get("pass") or {}).items() if ts >= lim and p in self.cfg("points")}
        left = None
        if have:
            left = max(0, int((min(have.values()) + days * DAY - time.time()) // DAY) + 1)
        return have, left

    def passport_mark(self, u, point):
        """Відмітка візиту на точці. True, якщо паспорт щойно пройдено (усі точки за тиждень)."""
        have, _ = self.passport_state(u)
        have[point] = time.time()
        if all(p in have for p in self.cfg("points")):
            u["pass"] = {}
            u["gifts"].append({"goal": "passport"})
            return True
        u["pass"] = have
        return False

    # --- журнал і розбір ---
    def log_visit(self, point):
        self.d["log"].append({"ts": time.time(), "point": point, "demo": False})

    def weak_slot(self):
        """Найслабший день тижня у годинах 11–14 за 4 тижні. None, якщо даних замало."""
        lim = time.time() - 28 * DAY
        sums = [0] * 7
        real = 0
        for e in self.d["log"]:
            if e["ts"] < lim:
                continue
            dt = datetime.fromtimestamp(e["ts"], TZ)
            if self.cfg("quiet_from") <= dt.hour < self.cfg("quiet_to"):
                sums[dt.weekday()] += 1
                real += 0 if e.get("demo") else 1
        avg = [s / 4 for s in sums]            # у вікні 28 днів кожен день тижня зустрічається 4 рази
        if sum(avg) == 0:
            return None
        wd = min(range(7), key=lambda i: avg[i])
        others = [a for i, a in enumerate(avg) if i != wd]
        other = sum(others) / len(others)
        if other <= 0:
            return None
        return {"wd": wd, "weak": avg[wd], "other": other, "real": real}
