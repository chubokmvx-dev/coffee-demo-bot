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
    "sizes": ["S", "M", "L"],                       # розміри кави; ціна напою може бути одна або по розмірах: 55/65/75
    "drinks": [["Еспресо", 45], ["Американо", [55, 65, 75]], ["Капучино", [70, 85, 100]], ["Лате", [75, 90, 105]], ["Допіо", 60], ["Флет-вайт", [85, 100, 115]]],
    # доплати / ціни нижче — демо-значення, власник змінює їх у налаштуваннях
    "milks": [["Бананове", [20, 25, 30]], ["Кокосове", [15, 20, 25]], ["Мигдалеве", [10, 15, 20]]],
    "milk_over": {},                                # окрема доплата для пари «молоко|напій»: {"Бананове|Лате": [10, 15, 20]}
    "syrups": [["Карамель", 10], ["Ваніль", 10], ["Фундук", 10]],
    "desserts": [["Круасан", 55], ["Чізкейк", 85], ["Брауні", 60]],
    "lemonades": [["Лимон-м'ята", 65], ["Маракуя", 70], ["Ягідний", 70]],        # смак; лід питаємо окремим кроком
    "cocktails": [["Апероль шприц", 150], ["Мохіто", 140], ["Негроні", 160]],     # алкогольні: потрібне підтвердження 18+
    "mocktails": [["Віргін мохіто", 90], ["Пінк грейпфрут", 95]],                 # безалкогольні
    "promos": [
        "☕ Друга кава за пів ціни до 14:00! Покажіть це повідомлення бариста.",
        "🥐 Лате + круасан за 99 грн, тільки з 11:00 до 14:00.",
        "☔ Дощ за вікном — у нас тепло. Капучино −20% до 14:00.",
    ],
    "maps_url": os.getenv("MAPS_URL", "https://maps.google.com"),
    "maps": {},          # відгуки по точках: {"Назва точки": "посилання"}; без запису діє maps_url
    "quiet_from": 11,
    "quiet_to": 14,
    "promo_time": "10:30",
    "weekly_on": True,
    "welcome_on": True,
    "promo_photo": True,
    "gift_valid_days": 60,      # скільки днів діє подарунок (0 — безстроково)
    "promo_valid_days": 7,      # скільки днів діє купон з акції / повернення
    "backup_on": True,          # щодобовий файл-копія бази власнику в Telegram
    "order_max_active": 1,      # скільки незабраних замовлень одночасно може мати клієнт
    "noshow_limit": 3,          # після скількох «не забрали» за 30 днів передзамовлення вимикаються (0 — не обмежувати)
    "alert_stamps_hour": 12,    # сповіщення власнику, якщо один бариста поставив стільки штампів за годину (0 — вимкнено)
    "alert_gifts_day": 5,       # ... або видав стільки подарунків за добу
    "stamp_confirm": True,      # штамп лише після підтвердження покупки від ціни найдешевшого напою
    "stamp_cooldown": 30,       # хвилин між двома штампами одному клієнту (власника не стосується)
    "stamp_daily_max": 3,       # штампів на день одному клієнту
    "ref_on": True,             # запрошення друга
    "ref_max": 5,               # скільки подарунків за друзів може отримати один клієнт
    "birthday_on": True,
    "birthday_min_days": 14,    # скільки днів клієнт має бути в боті, щоб отримати подарунок на ДН
}
TZ = ZoneInfo("Europe/Kyiv")
DEFAULT_REWARDS = {
    "welcome": "Безкоштовний напій до першої кави",
    "stamps": "Безкоштовна кава «як завжди» ({usual})",
    "passport": "Круасан",
    "referral": "Безкоштовна кава за запрошеного друга",
    "birthday": "Напій у подарунок на день народження",
}
WD = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]
WD_ACC = ["понеділок", "вівторок", "середу", "четвер", "п'ятницю", "суботу", "неділю"]


def _empty():
    return {"users": {}, "orders": {}, "next_order": 1, "seeded": False, "promos": 0, "reviews": [],
            "log": [], "scheduled": [], "weekly_sent": "", "seeded_log": False,
            "rewards": dict(DEFAULT_REWARDS), "settings": {}, "invites": {}, "off": {}, "actlog": [], "bday_sent": ""}


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
        if self.d["settings"]["drinks"] == [["Еспресо", 45], ["Американо", 55], ["Капучино", 70], ["Лате", 75]]:
            self.d["settings"]["drinks"] = DEFAULT_SETTINGS["drinks"]      # старе демо-меню → нове
        for k, v in DEFAULT_REWARDS.items():
            self.d["rewards"].setdefault(k, v)
        for u in self.d["users"].values():     # міграція: раніше подарунки були числом
            if isinstance(u.get("gifts"), int):
                u["gifts"] = [{"goal": "welcome"} for _ in range(u["gifts"])]
        for u in self.d["users"].values():     # міграція: кожен подарунок отримує id і термін дії
            for g in u.get("gifts", []):
                if "id" not in g:
                    g["id"] = secrets.token_urlsafe(6)
                    g["exp"] = self._exp(g["goal"])
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
                "passport": f"паспорт: {len(self.cfg('points'))} точки за {self.cfg('passport_days')} днів",
                "referral": "запрошення друга", "birthday": "день народження",
                "promo": "акція", "winback": "повернення"}.get(goal, "подарунок")

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
        u = {"name": name, "phone": None, "demo": False, "stamps": 0, "gifts": [], "mode": "client",
             "joined": time.time(), "last_visit": time.time(), "code": code, "owner_ok": False,
             "point": self.cfg("points")[0], "pass": {}, "last": None, "prefs": "", "token": secrets.token_urlsafe(8)}
        self.d["users"][str(uid)] = u
        if self.cfg("welcome_on"):
            self.grant(u, "welcome")
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
    # --- замовлення: ліміти й неявки ---
    ORDER_LIFE = 3 * 3600       # після цього незакрите замовлення вважається застарілим (без штрафу клієнту)

    def active_orders(self, uid):
        lim = time.time() - self.ORDER_LIFE
        return [(n, o) for n, o in self.d["orders"].items() if o["uid"] == uid and not o["done"] and o["ts"] > lim]

    def noshow_count(self, u):
        lim = time.time() - 30 * DAY
        return sum(1 for ts in u.get("noshows", []) if ts > lim)

    def expire_orders(self):
        lim = time.time() - self.ORDER_LIFE
        n = 0
        for o in self.d["orders"].values():
            if not o["done"] and o["ts"] <= lim:
                o["done"], o["expired"] = True, True
                n += 1
        return n

    # --- журнал дій персоналу та захист від зловживань ---
    def log_action(self, kind, by, client, point, x=""):
        """kind: stamp | redeem | blocked."""
        a = self.d["actlog"]
        a.append({"ts": time.time(), "kind": kind, "by": str(by), "client": str(client), "point": point, "x": x})
        if len(a) > 5000:
            del a[:len(a) - 5000]

    def stamp_block(self, client):
        """Текст причини, якщо клієнту зараз не можна ставити штамп (інакше None)."""
        now = time.time()
        mine = [e for e in self.d["actlog"] if e["kind"] == "stamp" and e["client"] == str(client)]
        cd = self.cfg("stamp_cooldown")
        if cd and mine and now - mine[-1]["ts"] < cd * 60:
            left = int(cd - (now - mine[-1]["ts"]) / 60) + 1
            return f"Цьому клієнту вже ставили штамп менше {cd} хв тому. Спробуйте за {left} хв."
        day = datetime.now(TZ).date()
        today = [e for e in mine if datetime.fromtimestamp(e["ts"], TZ).date() == day]
        mx = self.cfg("stamp_daily_max")
        if mx and len(today) >= mx:
            return f"Сьогодні клієнт уже отримав {mx} штамп(и) — це денний ліміт."
        return None

    # --- наявність (окремо для кожної точки) ---
    def is_on(self, cat, name, point):
        return point not in self.d["off"].get(f"{cat}:{name}", [])

    def toggle(self, cat, name, point):
        """Перемикає наявність позиції на точці. Повертає новий стан (True = є)."""
        key = f"{cat}:{name}"
        off = self.d["off"].setdefault(key, [])
        if point in off:
            off.remove(point)
        else:
            off.append(point)
        if not off:
            self.d["off"].pop(key, None)
        self.save()
        return self.is_on(cat, name, point)

    def add_order(self, uid, drink, price, minutes, point, details=""):
        n = self.d["next_order"]
        self.d["next_order"] = n + 1
        self.d["orders"][str(n)] = {"uid": uid, "drink": drink, "price": price, "min": minutes,
                                    "point": point, "ts": time.time(), "done": False, "details": details}
        self.save()
        return n

    # --- подарунки та нагороди ---
    def reward_title(self, goal, u=None):
        """Поточна нагорода за ціль. {usual} → напій «як завжди» клієнта."""
        text = self.d["rewards"].get(goal, DEFAULT_REWARDS.get(goal, "Подарунок"))
        usual = "напій на вибір"
        last = (u or {}).get("last")
        names = [d[0] for d in self.cfg("drinks")]
        if last and last.get("d"):
            usual = last["d"]
        elif last and isinstance(last.get("drink"), int) and last["drink"] < len(names):
            usual = names[last["drink"]]
        return text.replace("{usual}", usual)

    def _exp(self, goal):
        days = self.cfg("promo_valid_days") if goal in ("promo", "winback") else self.cfg("gift_valid_days")
        return time.time() + days * DAY if days else None

    def grant(self, u, goal, title=None):
        """Новий подарунок з унікальним id (для QR-купона) і терміном дії. Повертає його."""
        g = {"goal": goal, "id": secrets.token_urlsafe(6), "exp": self._exp(goal)}
        if title:
            g["title"] = title
        u["gifts"].append(g)
        return g

    def live(self, u):
        now = time.time()
        return [g for g in u["gifts"] if not g.get("exp") or g["exp"] > now]

    def gift_title(self, g, u=None):
        return g.get("title") or self.reward_title(g["goal"], u)

    def exp_text(self, g):
        return f"до {datetime.fromtimestamp(g['exp'], TZ):%d.%m}" if g.get("exp") else "безстроково"

    def take(self, u, key):
        """Забирає подарунок за id купона або за назвою цілі (перший чинний)."""
        for g in self.live(u):
            if g.get("id") == key or g["goal"] == key:
                u["gifts"].remove(g)
                return g
        return None

    def find_gift(self, gid):
        for k, u in self.d["users"].items():
            for g in self.live(u):
                if g.get("id") == gid:
                    return k, u, g
        return None, None, None

    def sweep_gifts(self):
        """Прибирає прострочені подарунки; повертає (скільки прибрано, [(k, u, g)] що спливають за ≤3 дні, ще без нагадування)."""
        now, soon, gone = time.time(), [], 0
        for k, u in self.d["users"].items():
            keep = []
            for g in u["gifts"]:
                if g.get("exp") and g["exp"] <= now:
                    gone += 1
                    continue
                if g.get("exp") and g["exp"] - now <= 3 * DAY and not g.get("warned") and not u.get("demo"):
                    g["warned"] = True
                    soon.append((k, u, g))
                keep.append(g)
            u["gifts"] = keep
        return gone, soon

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
            self.grant(u, "passport")
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
