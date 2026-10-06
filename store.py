"""Мінімальне сховище в JSON-файлі + демо-база умовних клієнтів."""
import json, os, random, tempfile, time

PATH = os.getenv("DATA_PATH", "/tmp/coffee_demo.json")
STAMPS_FOR_GIFT = 8
DAY = 86400


def _empty():
    return {"users": {}, "orders": {}, "next_order": 1, "seeded": False, "promos": 0, "reviews": []}


class Store:
    def __init__(self, path=PATH):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.d = json.load(f)
        except Exception:
            self.d = _empty()
        if not self.d.get("seeded"):
            self._seed()
            self.save()

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
                "stamps": rnd.randint(0, 7), "gifts": 0, "mode": "client",
                "joined": now - rnd.randint(10, 90) * DAY,
                "last_visit": now - (rnd.randint(22, 60) if gone else rnd.randint(0, 14)) * DAY,
                "code": f"9{i:03d}",
            }
        self.d["seeded"] = True

    # --- користувачі ---
    def user(self, uid):
        return self.d["users"].get(str(uid))

    def ensure(self, uid, name):
        u = self.user(uid)
        if u:
            return u, False
        used = {x["code"] for x in self.d["users"].values()}
        code = next(c for c in (f"{random.randint(1000, 8999)}" for _ in range(10_000)) if c not in used)
        u = {"name": name, "phone": None, "demo": False, "stamps": 0, "gifts": 1, "mode": "client",
             "joined": time.time(), "last_visit": time.time(), "code": code, "owner_ok": False}
        self.d["users"][str(uid)] = u
        self.save()
        return u, True

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
