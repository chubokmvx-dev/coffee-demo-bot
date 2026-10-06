"""Демо-бот для кав'ярні: клієнтський режим + режим власника в одному боті."""
import asyncio, html, io, logging, os, re, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)

from store import WD, WD_ACC, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coffee")

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DEMO_CODE = os.getenv("DEMO_CODE", "")          # якщо задано: /owner <код> дає режим власника
TZ = ZoneInfo("Europe/Kyiv")

st = Store()
router = Router()


# ---------- налаштування (змінює власник) ----------
def goal_n(): return st.cfg("stamps_goal")
def pdays(): return st.cfg("passport_days")
def points(): return st.cfg("points")
def drinks(): return [tuple(d) for d in st.cfg("drinks")]
def promos(): return st.cfg("promos")
def shop(): return st.cfg("shop")
def maps_url(): return st.cfg("maps_url")


def drink(i):
    ds = drinks()
    return ds[i] if 0 <= i < len(ds) else ds[0]


def point_at(k):
    ps = points()
    return ps[k] if 0 <= k < len(ps) else ps[0]


# ---------- кнопки ----------
B_STAMPS, B_GIFTS, B_ORDER = "☕ Мої штампи", "🎁 Подарунки", "⏱ Замовити наперед"
B_POINTS, B_REVIEW, B_CODE = "📍 Наші точки", "⭐ Відгук", "📲 Мій код"   # B_CODE лишився лише для старих клавіатур
B_USUAL, B_PREFS, B_PASS = "🔁 Як завжди", "✍️ Вподобання", "🧭 Паспорт і QR"   # B_USUAL/B_PREFS/B_ORDER/B_POINTS/B_REVIEW лишились для старих клавіатур
B_MENU_ORDER, B_INFO = "☕ Замовити", "📍 Точки й відгук"
B_TO_OWNER, B_TO_CLIENT = "🔐 Режим власника", "🔄 Режим клієнта"
O_PROMO, O_BACK, O_STAMP = "📣 Акція", "💤 Повернути зниклих", "➕ Штамп за кодом"
O_PROMO_OLD = "📣 Акція 11–14"          # для старих клавіатур
O_REDEEM, O_REPORT = "🎁 Погасити подарунок", "📊 Звіт"
O_WEEK, O_POINT, O_REWARDS = "🗓 Розбір тижня", "📍 Змінити точку", "🎁 Нагороди"
O_SETTINGS, O_STAFF, B_ORDERS = "⚙️ Налаштування", "👥 Персонал", "🧾 Замовлення"
B_TO_BARISTA = "👨‍🍳 Режим бариста"
O_AVAIL = "📦 Наявність"
O_MORE, O_LESS = "⋯ Більше", "⬅️ Назад"
ALL_BTNS = {B_STAMPS, B_GIFTS, B_ORDER, B_POINTS, B_REVIEW, B_CODE, B_TO_OWNER, B_TO_CLIENT,
            B_USUAL, B_PREFS, B_PASS, O_PROMO, O_BACK, O_STAMP, O_REDEEM, O_REPORT, O_WEEK, O_POINT,
            B_MENU_ORDER, B_INFO, O_REWARDS, O_PROMO_OLD, O_SETTINGS, O_STAFF, B_ORDERS, B_TO_BARISTA, O_AVAIL, O_MORE, O_LESS}


def rk(rows):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def client_kb(uid):
    rows = [[B_MENU_ORDER, B_PASS], [B_STAMPS, B_GIFTS]]
    if can_owner(uid):
        rows.append([B_INFO, B_TO_OWNER])
    elif is_barista(uid):
        rows.append([B_INFO, B_TO_BARISTA])
    else:
        rows.append([B_INFO])
    return rk(rows)


def owner_kb():
    return rk([[O_STAMP, O_REDEEM], [B_ORDERS, O_AVAIL], [O_MORE, B_TO_CLIENT]])


def owner_more_kb():
    return rk([[O_PROMO, O_BACK], [O_WEEK, O_REPORT], [O_REWARDS, O_SETTINGS], [O_STAFF, O_POINT], [O_LESS]])


def barista_kb():
    return rk([[O_STAMP, O_REDEEM], [B_ORDERS, O_AVAIL], [O_POINT, B_TO_CLIENT]])


def staff_kb(uid):
    return owner_kb() if can_owner(uid) else barista_kb()


def can_owner(uid):
    u = st.user(uid)
    return uid in ADMIN_IDS or bool(u and u.get("owner_ok"))


def is_barista(uid):
    u = st.user(uid)
    return bool(u and u.get("barista"))


def can_staff(uid):
    return can_owner(uid) or is_barista(uid)


def order_recipients(point):
    """Власники + бариста тієї точки (якщо на точці нікого немає — усі бариста)."""
    baristas = [i for i, u in st.real() if u.get("barista")]
    here = [i for i in baristas if (st.user(i) or {}).get("point") == point]
    return set(owners()) | set(here or baristas)


def owners():
    ids = set(ADMIN_IDS)
    ids |= {i for i, u in st.real() if u.get("owner_ok")}
    return ids


def esc(s):
    return html.escape(str(s), quote=False)


def bar(n):
    return "●" * n + "○" * (goal_n() - n)


async def safe_send(bot, chat_id, text, **kw):
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except TelegramAPIError as e:
        log.warning("send to %s failed: %s", chat_id, e)
        return False


BOT_USERNAME = ""
_BOT = None


def bot_ref():
    return _BOT


def qr_png(data: str) -> bytes:
    import segno
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=10, border=3)
    return buf.getvalue()


def client_link(u):
    return f"https://t.me/{BOT_USERNAME}?start=c_{st.token(u)}"


def touch(uid):
    u = st.user(uid)
    if u:
        u["last_visit"] = time.time()
        st.save()


# ---------- FSM ----------
class Review(StatesGroup):
    comment = State()


class Promo(StatesGroup):
    custom = State()


class Stamp(StatesGroup):
    code = State()


class Redeem(StatesGroup):
    code = State()


class Prefs(StatesGroup):
    text = State()


class Reward(StatesGroup):
    text = State()


# ---------- старт ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    uid = m.from_user.id
    arg = (command.args or "").strip()
    if arg.startswith("c_") and can_staff(uid):
        k, target = st.by_token(arg[2:])
        if target:
            st.ensure(uid, m.from_user.full_name)[0]["mode"] = "owner" if can_owner(uid) else "barista"
            await m.answer("🧾 Картка клієнта", reply_markup=staff_kb(uid))
            await show_client_card(m, k, target)
            return
        await m.answer("Цей QR не розпізнано.")
        return
    if arg.startswith("b_"):
        ts = st.d["invites"].pop(arg[2:], None)
        if ts and time.time() - ts < 48 * 3600:
            u, _ = st.ensure(uid, m.from_user.full_name)
            u["barista"], u["mode"] = True, "barista"
            st.save()
            for o in owners():
                await safe_send(bot_ref(), o, f"👨‍🍳 {esc(m.from_user.full_name)} тепер бариста.")
            await m.answer("👨‍🍳 Вас додано як бариста. Скануйте QR клієнтів, ставте штампи й видавайте подарунки. "
                           "Натисніть «📍 Змінити точку», щоб обрати, де ви працюєте.", reply_markup=barista_kb())
            return
        st.save()
        await m.answer("Це запрошення вже використане або прострочене. Попросіть власника створити нове.")
        return
    u, new = st.ensure(uid, m.from_user.full_name)
    u["mode"] = "client"
    st.save()
    if new:
        await m.answer(
            f"Привіт, {esc(m.from_user.first_name)}! ☕\n"
            f"Це бот «{esc(shop())}». Ми вже додали вам <b>подарунок</b>: {esc(st.reward_title('welcome'))}.\n\n"
            f"Збирайте штампи: кожен {goal_n()}-й напій у подарунок. "
            f"Штампи працюють у кав'ярні та на всіх будках.",
            reply_markup=client_kb(uid))
        await m.answer(
            "Поділіться номером, щоб бонуси були на всіх точках і не губились, якщо змінете телефон 👇",
            reply_markup=ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="📱 Поділитися номером", request_contact=True)],
                [KeyboardButton(text="Пізніше")]], resize_keyboard=True, one_time_keyboard=True))
    else:
        touch(uid)
        await m.answer(f"З поверненням, {esc(m.from_user.first_name)}! Оберіть дію 👇", reply_markup=client_kb(uid))


@router.message(F.contact)
async def got_contact(m: Message):
    u = st.user(m.from_user.id)
    if u:
        u["phone"] = m.contact.phone_number
        st.save()
    await m.answer("Дякуємо! Номер збережено ✅", reply_markup=client_kb(m.from_user.id))


@router.message(F.text == "Пізніше")
async def later(m: Message):
    await m.answer("Добре, можна й пізніше.", reply_markup=client_kb(m.from_user.id))


@router.message(Command("owner"))
async def owner_cmd(m: Message, command: CommandObject):
    uid = m.from_user.id
    u, _ = st.ensure(uid, m.from_user.full_name)
    if not can_owner(uid):
        if DEMO_CODE and (command.args or "").strip() == DEMO_CODE:
            u["owner_ok"] = True
            st.save()
        else:
            await m.answer("Недоступно.")
            return
    u["mode"] = "owner"
    st.save()
    await m.answer("🔐 Режим власника. Так бот виглядає для вас.", reply_markup=owner_kb())


@router.message(F.text == B_TO_OWNER)
async def to_owner(m: Message, state: FSMContext):
    await state.clear()
    if not can_owner(m.from_user.id):
        return
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    u["mode"] = "owner"
    st.save()
    await m.answer("🔐 Режим власника.", reply_markup=owner_kb())


@router.message(F.text == B_TO_BARISTA)
async def to_barista(m: Message, state: FSMContext):
    await state.clear()
    if not can_staff(m.from_user.id):
        return
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    u["mode"] = "barista"
    st.save()
    await m.answer("👨‍🍳 Режим бариста.", reply_markup=barista_kb())


@router.message(F.text == B_TO_CLIENT)
async def to_client(m: Message, state: FSMContext):
    await state.clear()
    u = st.user(m.from_user.id)
    if u:
        u["mode"] = "client"
        st.save()
    await m.answer("Режим клієнта 👇", reply_markup=client_kb(m.from_user.id))


# ---------- клієнт ----------
@router.message(F.text == B_STAMPS)
async def my_stamps(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    left = goal_n() - u["stamps"]
    await m.answer(f"<b>Ваші штампи</b>\n{bar(u['stamps'])}  {u['stamps']}/{goal_n()}\n\n"
                   f"До подарунка лишилось: <b>{left}</b>. Покажіть бариста QR із «{B_PASS}».")


def gift_lines(u):
    return "\n".join(f"• <b>{esc(st.reward_title(g['goal'], u))}</b> — {st.goal_name(g['goal'])}" for g in u["gifts"])


@router.message(F.text == B_GIFTS)
async def my_gifts(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    have, _left = st.passport_state(u)
    goals = (f"🎯 <b>Як отримати подарунок</b>\n"
             f"☕ {goal_n()} штампів ({u['stamps']}/{goal_n()}) → {esc(st.reward_title('stamps', u))}\n"
             f"🧭 Паспорт ({len(have)}/{len(points())} точок за {pdays()} днів) → {esc(st.reward_title('passport', u))}")
    if u["gifts"]:
        await m.answer(f"🎁 <b>Ваші подарунки</b>\n{gift_lines(u)}\n\nПокажіть QR із «{B_PASS}» бариста, щоб забрати.\n\n{goals}")
    else:
        await m.answer(f"Подарунків поки немає.\n\n{goals}")


def points_text():
    return ("📍 <b>Наші точки</b>\n" + "\n".join(f"• {p}" for p in points()) +
            "\n\nШтампи й подарунки діють на всіх точках.")


@router.message(F.text == B_POINTS)
async def points_handler(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(points_text())


@router.message(F.text == B_MENU_ORDER)
async def order_menu(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    rows = []
    last = u.get("last")
    if last:
        rows.append([InlineKeyboardButton(text=f"🔁 Як завжди: {usual_label(u)}"[:60], callback_data="mn:usual")])
    rows.append([InlineKeyboardButton(text="🆕 Нове замовлення", callback_data="mn:new")])
    rows.append([InlineKeyboardButton(text="✍️ Вподобання" + (" ✓" if u.get("prefs") else ""), callback_data="mn:prefs")])
    await m.answer("☕ Що робимо?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(F.text == B_INFO)
async def info_menu(m: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Наші точки", callback_data="mn:points")],
        [InlineKeyboardButton(text="⭐ Залишити відгук", callback_data="mn:review")]])
    await m.answer("Що показати?", reply_markup=kb)


@router.callback_query(F.data.startswith("mn:"))
async def menu_cb(c: CallbackQuery, state: FSMContext):
    act = c.data[3:]
    if act == "usual":
        await show_usual(c.message, c.from_user)
    elif act == "new":
        await state.clear()
        await start_order(c.message, c.from_user, state)
    elif act == "prefs":
        await ask_prefs(c.message, c.from_user, state)
    elif act == "points":
        await c.message.answer(points_text())
    elif act == "review":
        await show_rating(c.message)
    await c.answer()


# --- конструктор замовлення: точка → напій → молоко → сироп → десерт → час ---
CATS = {"milks": "🥛 Молоко", "syrups": "🍯 Сиропи", "desserts": "🍰 Десерти", "drinks": "☕ Напої"}


def price_of(cat, name):
    for n, p in st.cfg(cat):
        if n == name:
            return p
    return None


def avail(cat, point):
    """Позиції категорії, які є на точці: [(індекс, назва, ціна)]."""
    return [(i, n, p) for i, (n, p) in enumerate(st.cfg(cat)) if st.is_on(cat, n, point)]


def extra(p):
    return f" +{p} грн" if p else ""


def cart_total(cart):
    total = price_of("drinks", cart["dr"]) or 0
    if cart.get("ml"):
        total += price_of("milks", cart["ml"]) or 0
    total += sum(price_of("syrups", x) or 0 for x in cart.get("sy", []))
    total += sum(price_of("desserts", x) or 0 for x in cart.get("ds", []))
    return total


def cart_lines(cart):
    out = [f"☕ <b>{esc(cart['dr'])}</b> · {price_of('drinks', cart['dr'])} грн"]
    if cart.get("ml"):
        out.append(f"🥛 {esc(cart['ml'])}{extra(price_of('milks', cart['ml']))}")
    for x in cart.get("sy", []):
        out.append(f"🍯 {esc(x)}{extra(price_of('syrups', x))}")
    for x in cart.get("ds", []):
        out.append(f"🍰 {esc(x)} · {price_of('desserts', x)} грн")
    return out


def cart_details(cart):
    """Коротко для бариста: усе, крім самого напою."""
    parts = []
    if cart.get("ml"):
        parts.append(f"молоко: {cart['ml']}")
    if cart.get("sy"):
        parts.append("сироп: " + ", ".join(cart["sy"]))
    if cart.get("ds"):
        parts.append("десерт: " + ", ".join(cart["ds"]))
    return "; ".join(parts)


def step_has_options(cart, step):
    if step == "milk":
        return bool(avail("milks", cart["pt"]))
    if step == "syrup":
        return bool(avail("syrups", cart["pt"]))
    if step == "dessert":
        return bool(avail("desserts", cart["pt"]))
    return True


def next_step(cart, cur):
    order = ["drink", "milk", "syrup", "dessert", "time", "confirm"]
    for st_ in order[order.index(cur) + 1:]:
        if step_has_options(cart, st_):
            return st_
    return "confirm"


def kb_rows(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_step(msg: Message, cart: dict, step: str, edit=True):
    pt = cart["pt"]
    head = f"📍 {esc(pt)}\n"
    if cart.get("dr"):
        head += "\n".join(cart_lines(cart)) + "\n"
    head += "\n"
    if step == "drink":
        items = avail("drinks", pt)
        text = f"📍 {esc(pt)}\n\n☕ <b>Що замовити?</b>" if items else f"📍 {esc(pt)}\n\nНа цій точці зараз немає напоїв."
        rows = [[InlineKeyboardButton(text=f"{n} — {p} грн", callback_data=f"o:dr:{i}")] for i, n, p in items]
    elif step == "milk":
        text = head + "🥛 <b>Яке молоко?</b>"
        rows = [[InlineKeyboardButton(text="Звичайне (без доплати)", callback_data="o:ml:x")]]
        rows += [[InlineKeyboardButton(text=f"{n}{extra(p)}", callback_data=f"o:ml:{i}")] for i, n, p in avail("milks", pt)]
    elif step == "syrup":
        text = head + "🍯 <b>Додати сироп?</b> Можна кілька або пропустити."
        rows = [[InlineKeyboardButton(text=f"{'✅' if n in cart['sy'] else '⬜'} {n}{extra(p)}", callback_data=f"o:sy:{i}")]
                for i, n, p in avail("syrups", pt)]
        rows.append([InlineKeyboardButton(text="Далі ➡️", callback_data="o:sy:ok")])
    elif step == "dessert":
        text = head + "🍰 <b>Смачна пара до кави?</b> Можна додати десерт або пропустити."
        rows = [[InlineKeyboardButton(text=f"{'✅' if n in cart['ds'] else '⬜'} {n} — {p} грн", callback_data=f"o:ds:{i}")]
                for i, n, p in avail("desserts", pt)]
        rows.append([InlineKeyboardButton(text="Далі ➡️", callback_data="o:ds:ok")])
    elif step == "time":
        text = head + "⏱ <b>Коли забрати?</b>"
        rows = [[InlineKeyboardButton(text=f"через {t} хв", callback_data=f"o:tm:{t}") for t in (5, 10, 15)]]
    else:
        text = head + f"⏱ через {cart['tm']} хв\n\n💰 <b>Разом: {cart_total(cart)} грн</b>"
        rows = [[InlineKeyboardButton(text="✅ Замовити", callback_data="o:go")],
                [InlineKeyboardButton(text="↩️ Почати спочатку", callback_data="o:back")]]
    if edit:
        await msg.edit_text(text, reply_markup=kb_rows(rows))
    else:
        await msg.answer(text, reply_markup=kb_rows(rows))


async def start_order(msg: Message, user, state: FSMContext):
    """Крок 0: точка (якщо точок кілька). Запам'ятовує останню точку першою."""
    u, _ = st.ensure(user.id, user.full_name)
    ps = points()
    last_pt = (u.get("last") or {}).get("p")
    order = sorted(ps, key=lambda p: p != last_pt)
    if len(ps) == 1:
        cart = {"pt": ps[0], "dr": None, "ml": None, "sy": [], "ds": []}
        await state.update_data(cart=cart)
        await render_step(msg, cart, "drink", edit=False)
        return
    rows = [[InlineKeyboardButton(text=f"📍 {p}", callback_data=f"o:pt:{ps.index(p)}")] for p in order]
    await msg.answer("Де заберете замовлення?", reply_markup=kb_rows(rows))


@router.message(F.text == B_ORDER)
async def order_start(m: Message, state: FSMContext):
    await state.clear()
    await start_order(m, m.from_user, state)


@router.callback_query(F.data.startswith("o:"))
async def order_cb(c: CallbackQuery, state: FSMContext, bot: Bot):
    parts = c.data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    cart = (await state.get_data()).get("cart")
    if act == "pt":
        cart = {"pt": point_at(int(arg)), "dr": None, "ml": None, "sy": [], "ds": []}
        await state.update_data(cart=cart)
        await render_step(c.message, cart, "drink")
        await c.answer()
        return
    if not cart:
        await c.answer("Сесія закінчилась — почніть замовлення спочатку.", show_alert=True)
        return
    pt = cart["pt"]
    try:
        if act == "dr":
            item = next((n for i, n, p in avail("drinks", pt) if i == int(arg)), None)
            if not item:
                await c.answer("Цього напою зараз немає.", show_alert=True)
                return
            cart["dr"] = item
            step = next_step(cart, "drink")
        elif act == "ml":
            cart["ml"] = None if arg == "x" else next((n for i, n, p in avail("milks", pt) if i == int(arg)), None)
            step = next_step(cart, "milk")
        elif act in ("sy", "ds"):
            key, cat, cur = ("sy", "syrups", "syrup") if act == "sy" else ("ds", "desserts", "dessert")
            if arg == "ok":
                step = next_step(cart, cur)
            else:
                name = next((n for i, n, p in avail(cat, pt) if i == int(arg)), None)
                if name:
                    cart[key] = [x for x in cart[key] if x != name] if name in cart[key] else cart[key] + [name]
                step = cur
        elif act == "tm":
            cart["tm"] = int(arg)
            step = "confirm"
        elif act == "back":
            cart.update({"dr": None, "ml": None, "sy": [], "ds": []})
            step = "drink"
        elif act == "go":
            if not cart.get("dr") or not cart.get("tm"):
                await c.answer("Замовлення неповне.", show_alert=True)
                return
            text = await place_order(bot, c.from_user, cart)
            await state.update_data(cart=None)
            await c.message.edit_text(text)
            await c.answer()
            return
        else:
            await c.answer()
            return
    except (ValueError, TypeError):
        await c.answer("Меню змінилось, почніть спочатку.", show_alert=True)
        return
    await state.update_data(cart=cart)
    await render_step(c.message, cart, step)
    await c.answer()


async def place_order(bot: Bot, user, cart: dict, usual=False):
    """Створює замовлення, зберігає його як «як завжди» і сповіщає бариста. Повертає текст клієнту."""
    u, _ = st.ensure(user.id, user.full_name)
    total = cart_total(cart)
    details = cart_details(cart)
    n = st.add_order(user.id, cart["dr"], total, cart["tm"], cart["pt"], details)
    u["last"] = {"d": cart["dr"], "p": cart["pt"], "m": cart.get("ml"), "s": list(cart.get("sy", []))}
    st.save()
    touch(user.id)
    kb = kb_rows([[InlineKeyboardButton(text="✅ Готово", callback_data=f"rd:{n}")]])
    note = ("\n🔁 Постійний клієнт: «як завжди»" if usual else "")
    if u.get("prefs"):
        note += f"\n📝 Вподобання: <b>{esc(u['prefs'])}</b>"
    body = "\n".join(cart_lines(cart))
    for o in order_recipients(cart["pt"]):
        await safe_send(bot, o, f"🆕 <b>Замовлення №{n}</b> · {total} грн\n{body}\n📍 {esc(cart['pt'])} · через {cart['tm']} хв\n"
                                f"Клієнт: {esc(user.full_name)}{note}", reply_markup=kb)
    return (f"✅ Замовлення №{n} прийнято\n{body}\n💰 Разом: <b>{total} грн</b>\n"
            f"📍 {esc(cart['pt'])} · через {cart['tm']} хв\nМи напишемо, коли буде готово.")


# --- «Як завжди» ---
def usual_cart(u):
    """Збережене «як завжди» → кошик. Підтримує старий формат (індекси)."""
    last = u.get("last")
    if not last:
        return None
    if "d" in last:
        return {"pt": last["p"], "dr": last["d"], "ml": last.get("m"), "sy": list(last.get("s", [])), "ds": []}
    ds = drinks()
    if isinstance(last.get("drink"), int) and last["drink"] < len(ds):
        return {"pt": point_at(last.get("point", 0)), "dr": ds[last["drink"]][0], "ml": None, "sy": [], "ds": []}
    return None


def usual_missing(cart):
    """Що з «як завжди» зараз недоступно (прибрано з меню або немає на точці)."""
    bad = []
    if cart["pt"] not in points():
        return [f"точка «{cart['pt']}»"]
    pt = cart["pt"]
    if price_of("drinks", cart["dr"]) is None or not st.is_on("drinks", cart["dr"], pt):
        bad.append(cart["dr"])
    if cart["ml"] and (price_of("milks", cart["ml"]) is None or not st.is_on("milks", cart["ml"], pt)):
        bad.append(cart["ml"])
    for x in cart["sy"]:
        if price_of("syrups", x) is None or not st.is_on("syrups", x, pt):
            bad.append(x)
    return bad


def usual_label(u):
    c = usual_cart(u)
    if not c:
        return None
    return c["dr"] + (f" + {c['ml']}" if c["ml"] else "") + (f" + {', '.join(c['sy'])}" if c["sy"] else "")


@router.message(F.text == B_USUAL)
async def usual_start(m: Message, state: FSMContext):
    await state.clear()
    await show_usual(m, m.from_user)


async def show_usual(m: Message, user):
    u, _ = st.ensure(user.id, user.full_name)
    cart = usual_cart(u)
    if not cart:
        await m.answer(f"Ви ще нічого не замовляли. Зробіть перше замовлення через «{B_MENU_ORDER}» — "
                       f"далі «Як завжди» повторить його одним дотиком.")
        return
    bad = usual_missing(cart)
    if bad:
        kb = kb_rows([[InlineKeyboardButton(text="🆕 Нове замовлення", callback_data="mn:new")]])
        await m.answer(f"На жаль, зараз недоступно: <b>{esc(', '.join(bad))}</b>. Оформіть нове замовлення.", reply_markup=kb)
        return
    kb = kb_rows([[InlineKeyboardButton(text=f"через {t} хв", callback_data=f"uz:{t}") for t in (5, 10, 15)]])
    await m.answer("🔁 <b>Як завжди</b>\n" + "\n".join(cart_lines(cart)) + f"\n📍 {esc(cart['pt'])}\n"
                   f"💰 Разом: <b>{cart_total(cart)} грн</b>\nКоли забрати?", reply_markup=kb)


@router.callback_query(F.data.startswith("uz:"))
async def usual_go(c: CallbackQuery, bot: Bot):
    u, _ = st.ensure(c.from_user.id, c.from_user.full_name)
    cart = usual_cart(u)
    if not cart or usual_missing(cart):
        await c.answer("Меню змінилось — оформіть нове замовлення.", show_alert=True)
        return
    cart["tm"] = int(c.data.split(":")[1])
    text = await place_order(bot, c.from_user, cart, usual=True)
    await c.message.edit_text(text)
    await c.answer()


@router.message(F.text == B_PREFS)
async def prefs_start(m: Message, state: FSMContext):
    await ask_prefs(m, m.from_user, state)


async def ask_prefs(m: Message, user, state: FSMContext):
    u, _ = st.ensure(user.id, user.full_name)
    await state.set_state(Prefs.text)
    cur = f"\nЗараз: <b>{esc(u['prefs'])}</b>" if u.get("prefs") else ""
    await m.answer("✍️ Напишіть, як ви любите каву — бариста побачить це в кожному вашому замовленні.\n"
                   f"Наприклад: <i>вівсяне молоко, без цукру, подвійний шот</i>.{cur}\n"
                   "Щоб очистити, надішліть «-».")


@router.message(Prefs.text, ~F.text.in_(ALL_BTNS))
async def prefs_save(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    t = (m.text or "").strip()
    u["prefs"] = "" if t == "-" else t[:120]
    st.save()
    await m.answer("Збережено ✅" if u["prefs"] else "Вподобання очищено.", reply_markup=client_kb(m.from_user.id))


# --- паспорт ---
@router.message(F.text.in_({B_PASS, B_CODE}))
async def passport(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    have, left = st.passport_state(u)
    rows = "\n".join(f"{'✅' if p in have else '⬜'} {esc(p)}" for p in points())
    tail = (f"Лишилось днів: <b>{left}</b>." if have else f"Відвідайте всі точки за {pdays()} днів.")
    caption = (f"🧭 <b>Кавовий паспорт</b>\nКав'ярня й обидві будки за {pdays()} днів — подарунок: "
               f"<b>{esc(st.reward_title('passport', u))}</b>.\n\n"
               f"{rows}\n{tail}\n\n📲 Покажіть цей QR бариста на касі.\n"
               f"Якщо не зчитується — назвіть код: <code>{u['code']}</code>")
    try:
        png = qr_png(client_link(u))
    except Exception:
        log.exception("qr")
        await m.answer(caption)
        return
    await m.answer_photo(BufferedInputFile(png, filename="passport.png"), caption=caption)


@router.callback_query(F.data.startswith("rd:"))
async def order_ready(c: CallbackQuery, bot: Bot):
    if not can_staff(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    n = c.data.split(":")[1]
    o = st.d["orders"].get(n)
    if not o:
        await c.answer("Не знайдено")
        return
    o["done"] = True
    st.save()
    await c.message.edit_text(c.message.html_text + "\n\n✔️ <b>Видано</b>")
    await safe_send(bot, o["uid"], f"☕ Замовлення №{n} готове! Забирайте на точці «{esc(o['point'])}».")
    await c.answer("Клієнту надіслано")


# --- відгук ---
@router.message(F.text == B_REVIEW)
async def review_start(m: Message, state: FSMContext):
    await state.clear()
    await show_rating(m)


async def show_rating(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{n}⭐", callback_data=f"rv:{n}") for n in range(1, 6)]])
    await m.answer("Як вам візит? Оцініть від 1 до 5 ⭐", reply_markup=kb)


@router.callback_query(F.data.startswith("rv:"))
async def review_rate(c: CallbackQuery, state: FSMContext):
    s = int(c.data.split(":")[1])
    if s >= 4:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Залишити відгук на Google Maps", url=maps_url())]])
        await c.message.edit_text("Дякуємо за оцінку! 💛 Допоможете нам — залиште відгук на карті, це займе хвилину:", reply_markup=kb)
        st.d["reviews"].append({"uid": c.from_user.id, "stars": s, "ts": time.time()})
        st.save()
    else:
        await state.set_state(Review.comment)
        await state.update_data(stars=s)
        await c.message.edit_text("Шкода, що так вийшло 😔 Напишіть, що не сподобалось — це побачить власник, і ми виправимось.")
    await c.answer()


@router.message(Review.comment, ~F.text.in_(ALL_BTNS))
async def review_comment(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    st.d["reviews"].append({"uid": m.from_user.id, "stars": data.get("stars"), "text": m.text, "ts": time.time()})
    st.save()
    for o in owners():
        await safe_send(bot, o, f"⚠️ <b>Низька оцінка {data.get('stars')}/5</b> (до Google не потрапила)\n"
                                f"{esc(m.from_user.full_name)}: {esc(m.text)}")
    await m.answer("Дякуємо, передали власнику. Ми обов'язково розберемось 🙏", reply_markup=client_kb(m.from_user.id))


# ---------- власник ----------
def owner_only(m: Message):
    return can_owner(m.from_user.id)


def staff_only(m: Message):
    return can_staff(m.from_user.id)


@router.message(F.text.in_({O_PROMO, O_PROMO_OLD}), owner_only)
async def promo_start(m: Message, state: FSMContext):
    await state.clear()
    rows = [[InlineKeyboardButton(text=p[:50] + ("…" if len(p) > 50 else ""), callback_data=f"pm:{i}")] for i, p in enumerate(promos())]
    rows.append([InlineKeyboardButton(text="✍️ Свій текст", callback_data="pm:c")])
    await m.answer(f"📣 <b>Акція на тихі години ({quiet_text()})</b>\nОберіть шаблон або напишіть свій:",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("pm:"))
async def promo_pick(c: CallbackQuery, state: FSMContext):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    key = c.data.split(":")[1]
    if key == "c":
        await state.set_state(Promo.custom)
        await c.message.edit_text("Напишіть текст акції одним повідомленням:")
    else:
        await promo_preview(c.message, state, promos()[int(key)])
    await c.answer()


@router.message(Promo.custom, ~F.text.in_(ALL_BTNS))
async def promo_custom(m: Message, state: FSMContext):
    await promo_preview(m, state, m.text)


async def promo_preview(msg: Message, state: FSMContext, text: str):
    await state.set_state(None)
    await state.update_data(promo=text)
    real = len(st.real())
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Надіслати", callback_data="ps"),
                                                InlineKeyboardButton(text="Скасувати", callback_data="px")]])
    await msg.answer(f"Так побачать клієнти:\n\n{esc(text)}\n\nОтримувачів: {real} у боті + {st.demo_count()} у демо-базі.",
                     reply_markup=kb)


@router.callback_query(F.data == "ps")
async def promo_send(c: CallbackQuery, state: FSMContext, bot: Bot):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    text = (await state.get_data()).get("promo")
    if not text:
        await c.answer("Немає тексту, почніть спочатку", show_alert=True)
        return
    sent = 0
    for uid, _ in st.real():
        if await safe_send(bot, uid, text):
            sent += 1
        await asyncio.sleep(0.05)
    st.d["promos"] = st.d.get("promos", 0) + 1
    st.save()
    await state.clear()
    await c.message.edit_text(f"✅ Надіслано: {sent} реальним підписникам (+ {st.demo_count()} у демо-базі умовно).\n"
                              f"Результат дивіться у «{O_REPORT}»: продажі 11–14 до і після.")
    await c.answer()


@router.callback_query(F.data == "px")
async def promo_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Скасовано.")
    await c.answer()


@router.message(F.text == O_BACK, owner_only)
async def win_back(m: Message, state: FSMContext):
    await state.clear()
    gone = st.inactive(21)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📨 Надіслати «Скучили»", callback_data="wb")]])
    await m.answer(f"💤 Не були 3+ тижні: <b>{len(gone)}</b> клієнтів.\nБот напише кожному: "
                   f"«Скучили! Кава за наш рахунок, діє 7 днів».", reply_markup=kb)


@router.callback_query(F.data == "wb")
async def win_back_send(c: CallbackQuery, bot: Bot):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    gone = st.inactive(21)
    real_gone = [(i, u) for i, u in gone if not u.get("demo")]
    for i, _ in real_gone:
        await safe_send(bot, i, "Скучили за вами! ☕ Кава за наш рахунок — діє 7 днів. Покажіть це повідомлення.")
    # приклад для власника: що саме побачить такий клієнт
    await safe_send(bot, c.from_user.id, "👀 Так це побачить клієнт:\n\nСкучили за вами! ☕ Кава за наш рахунок — діє 7 днів. Покажіть це повідомлення.")
    await c.message.edit_text(f"✅ Відправлено: {len(gone)} клієнтам (реальних у боті: {len(real_gone)}, решта — демо-база).")
    await c.answer()


@router.message(F.text == O_STAMP, staff_only)
async def stamp_start(m: Message, state: FSMContext):
    await state.set_state(Stamp.code)
    await m.answer("Найшвидше — відскануйте QR клієнта камерою телефона. Або введіть код клієнта (клієнт бачить його під QR у «🧭 Паспорт і QR»):")


@router.message(F.text == O_MORE, owner_only)
async def more_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⋯", reply_markup=owner_more_kb())


@router.message(F.text == O_LESS, owner_only)
async def less_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("·", reply_markup=owner_kb())


@router.message(F.text == O_POINT, staff_only)
async def change_point(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    cur = u.get("point", points()[0])
    u["point"] = points()[(points().index(cur) + 1) % len(points())] if cur in points() else points()[0]
    st.save()
    await m.answer(f"📍 Ви працюєте на точці: <b>{esc(u['point'])}</b>. Штампи записуватимуться сюди. "
                   f"Натисніть ще раз, щоб змінити.")


async def apply_stamp(bot: Bot, me, k, u):
    """Штамп на точці бариста. Повертає текст для бариста."""
    point = me.get("point")
    if point not in points():
        point = points()[0]
    u["stamps"] += 1
    u["last_visit"] = time.time()
    gift = u["stamps"] >= goal_n()
    if gift:
        u["stamps"] = 0
        st.grant(u, "stamps")
    passed = st.passport_mark(u, point)
    st.log_visit(point)
    st.save()
    g_stamps = esc(st.reward_title("stamps", u))
    g_pass = esc(st.reward_title("passport", u))
    if not u.get("demo"):
        txt = f"☕ +1 штамп ({esc(point)})!\n{bar(u['stamps'])}  {u['stamps']}/{goal_n()}"
        if gift:
            txt = f"🎉 {goal_n()} штампів! Ваш подарунок: <b>{g_stamps}</b>. Покажіть QR бариста."
        if passed:
            txt += f"\n🧭 Паспорт пройдено — подарунок: <b>{g_pass}</b>!"
        else:
            have, _left = st.passport_state(u)
            txt += f"\n🧭 Паспорт: {len(have)}/{len(points())} точок"
        await safe_send(bot, int(k), txt)
    return (f"✅ Штамп додано: {esc(u['name'])} · {esc(point)}\n{bar(u['stamps'])} {u['stamps']}/{goal_n()}"
            + (f"\n🎉 Подарунок за штампи: {g_stamps}" if gift else "")
            + (f"\n🧭 Паспорт пройдено — подарунок: {g_pass}" if passed else ""))


async def apply_redeem(bot: Bot, k, u, goal=None):
    g = st.take(u, goal) if goal else (u["gifts"].pop(0) if u["gifts"] else None)
    if not g:
        return f"У {esc(u['name'])} немає такого подарунка."
    st.save()
    title = esc(st.reward_title(g["goal"], u))
    if not u.get("demo"):
        await safe_send(bot, int(k), f"🎁 Видано: <b>{title}</b>. Смачного! ☕")
    return f"🎁 Видано: {title} → {esc(u['name'])}. Лишилось подарунків: {len(u['gifts'])}."


async def show_client_card(m: Message, k, u):
    """Картка клієнта після скану QR: бариста сам обирає дію, тож повторний скан не дає подвійного штампа."""
    have, _ = st.passport_state(u)
    text = (f"👤 <b>{esc(u['name'])}</b>\n{bar(u['stamps'])} {u['stamps']}/{goal_n()}\n"
            f"🧭 Паспорт: {len(have)}/{len(points())} точок")
    if u["gifts"]:
        text += f"\n\n🎁 <b>Подарунки</b>\n{gift_lines(u)}"
    if u.get("prefs"):
        text += f"\n\n📝 Вподобання: <b>{esc(u['prefs'])}</b>"
    point = st.user(m.from_user.id).get("point")
    if point not in points():
        point = points()[0]
    tok = st.token(u)
    rows = [[InlineKeyboardButton(text=f"➕ Штамп ({point})", callback_data=f"cs:{tok}")]]
    for goal in dict.fromkeys(g["goal"] for g in u["gifts"]):
        rows.append([InlineKeyboardButton(text=f"🎁 Видати: {st.reward_title(goal, u)}"[:60], callback_data=f"cg:{tok}:{goal}")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith(("cs:", "cg:")))
async def card_action(c: CallbackQuery, bot: Bot):
    if not can_staff(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    act, tok, *rest = c.data.split(":")
    k, u = st.by_token(tok)
    if not u:
        await c.answer("Клієнта не знайдено", show_alert=True)
        return
    me, _ = st.ensure(c.from_user.id, c.from_user.full_name)
    res = await (apply_stamp(bot, me, k, u) if act == "cs" else apply_redeem(bot, k, u, rest[0] if rest else None))
    await c.message.edit_text(c.message.html_text + "\n\n" + res)
    await c.answer()


@router.message(Stamp.code, ~F.text.in_(ALL_BTNS))
async def stamp_code(m: Message, state: FSMContext, bot: Bot):
    k, u = st.by_code((m.text or "").strip())
    if not u:
        await m.answer("Такого коду немає. Спробуйте ще раз або натисніть іншу кнопку.")
        return
    await state.clear()
    me, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    await m.answer(await apply_stamp(bot, me, k, u))


@router.message(F.text == O_REDEEM, staff_only)
async def redeem_start(m: Message, state: FSMContext):
    await state.set_state(Redeem.code)
    await m.answer("Відскануйте QR клієнта (там є кнопка «Погасити») або введіть його код:")


@router.message(Redeem.code, ~F.text.in_(ALL_BTNS))
async def redeem_code(m: Message, state: FSMContext, bot: Bot):
    k, u = st.by_code((m.text or "").strip())
    if not u:
        await m.answer("Такого коду немає.")
        return
    await state.clear()
    await show_client_card(m, k, u)


@router.message(F.text == O_REWARDS, owner_only)
async def rewards_menu(m: Message, state: FSMContext):
    await state.clear()
    rows = [[InlineKeyboardButton(text=f"{st.goal_name(g)}: {st.d['rewards'][g]}"[:60], callback_data=f"rw:{g}")]
            for g in ("passport", "stamps", "welcome")]
    await m.answer("🎁 <b>Нагороди за цілі</b>\nОберіть, що змінити. На кнопці — поточна нагорода.",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("rw:"))
async def rewards_pick(c: CallbackQuery, state: FSMContext):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    goal = c.data[3:]
    await state.set_state(Reward.text)
    await state.update_data(goal=goal)
    hint = "\nМожна використати {usual} — підставиться улюблений напій клієнта." if goal == "stamps" else ""
    await c.message.answer(f"Ціль: <b>{st.goal_name(goal)}</b>\nЗараз: <b>{esc(st.d['rewards'][goal])}</b>\n"
                           f"Напишіть нову нагороду одним повідомленням.{hint}")
    await c.answer()


@router.message(Reward.text, ~F.text.in_(ALL_BTNS))
async def rewards_save(m: Message, state: FSMContext):
    goal = (await state.get_data()).get("goal")
    await state.clear()
    text = (m.text or "").strip()[:80]
    if goal not in ("welcome", "stamps", "passport") or not text:
        await m.answer("Не вдалося зберегти, спробуйте ще раз.")
        return
    st.d["rewards"][goal] = text
    st.save()
    await m.answer(f"✅ Нагорода за «{st.goal_name(goal)}»: <b>{esc(text)}</b>\nКлієнти побачать її одразу.")


@router.message(F.text == O_REPORT, owner_only)
async def report(m: Message, state: FSMContext):
    await state.clear()
    users = st.d["users"].values()
    week = time.time() - 7 * 86400
    new7 = sum(1 for u in users if u["joined"] > week)
    active = sum(1 for u in users if u["last_visit"] > time.time() - 14 * 86400)
    orders = len(st.d["orders"])
    await m.answer(
        "📊 <b>Звіт</b>\n"
        f"Клієнтів у базі: <b>{len(st.d['users'])}</b> (з них демо: {st.demo_count()})\n"
        f"Нових за 7 днів: {new7}\n"
        f"Були за 14 днів: {active}\n"
        f"Не були 3+ тижні: {len(st.inactive(21))}\n"
        f"Замовлень через бота: {orders}\n"
        f"Розсилок зроблено: {st.d.get('promos', 0)}\n\n"
        "<b>Приклад звіту по акції (демо-цифри, не реальні)</b>\n"
        f"Чеків {quiet_text()} за день: було 14 → стало 23\n"
        "У реальному запуску ці цифри беремо з вашого Poster.")


# ---------- відкриті замовлення (бариста й власник) ----------
@router.message(F.text == B_ORDERS, staff_only)
async def open_orders(m: Message, state: FSMContext):
    await state.clear()
    me, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    cutoff = time.time() - 6 * 3600
    mine = me.get("point")
    items = [(n, o) for n, o in st.d["orders"].items()
             if not o["done"] and o["ts"] > cutoff and (can_owner(m.from_user.id) or o["point"] == mine or mine not in points())]
    if not items:
        await m.answer("🧾 Відкритих замовлень немає.")
        return
    for n, o in items[-10:]:
        cu = st.user(o["uid"]) or {}
        note = f"\n📝 {esc(cu['prefs'])}" if cu.get("prefs") else ""
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data=f"rd:{n}")]])
        det = f"\n➕ {esc(o['details'])}" if o.get("details") else ""
        await m.answer(f"🧾 <b>№{n}</b> · {esc(o['drink'])} · {o['price']} грн{det}\n📍 {esc(o['point'])} · через {o['min']} хв · "
                       f"{esc(cu.get('name', ''))}{note}", reply_markup=kb)


# ---------- наявність (бариста й власник; окремо для кожної точки) ----------
def avail_point(uid):
    u = st.user(uid) or {}
    p = u.get("point")
    return p if p in points() else points()[0]


def avail_menu_kb():
    rows = [[InlineKeyboardButton(text=label, callback_data=f"av:c:{cat}")] for cat, label in CATS.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def avail_cat_kb(cat, point):
    rows = []
    for i, (n, p) in enumerate(st.cfg(cat)):
        mark = "✅" if st.is_on(cat, n, point) else "❌"
        rows.append([InlineKeyboardButton(text=f"{mark} {n}{extra(p) if cat != 'desserts' and cat != 'drinks' else ' · ' + str(p) + ' грн'}"[:60],
                                          callback_data=f"av:t:{cat}:{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="av:m")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


AVAIL_TXT = "📦 <b>Наявність на точці: {p}</b>\nОберіть розділ. Те, що вимкнено, клієнти не побачать у замовленні. " \
            "Змінити точку — кнопка «📍 Змінити точку»."


@router.message(F.text == O_AVAIL, staff_only)
async def avail_open(m: Message, state: FSMContext):
    await state.clear()
    st.ensure(m.from_user.id, m.from_user.full_name)
    await m.answer(AVAIL_TXT.format(p=esc(avail_point(m.from_user.id))), reply_markup=avail_menu_kb())


@router.callback_query(F.data.startswith("av:"))
async def avail_cb(c: CallbackQuery):
    if not can_staff(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    parts = c.data.split(":")
    pt = avail_point(c.from_user.id)
    if parts[1] == "m":
        await c.message.edit_text(AVAIL_TXT.format(p=esc(pt)), reply_markup=avail_menu_kb())
    elif parts[1] == "c" and parts[2] in CATS:
        cat = parts[2]
        if not st.cfg(cat):
            await c.answer("Список порожній — власник додає позиції в налаштуваннях.", show_alert=True)
            return
        await c.message.edit_text(f"{CATS[cat]} · <b>{esc(pt)}</b>\nНатисніть, щоб вмикати/вимикати. ✅ є, ❌ немає.",
                                  reply_markup=avail_cat_kb(cat, pt))
    elif parts[1] == "t" and parts[2] in CATS:
        cat, i = parts[2], int(parts[3])
        items = st.cfg(cat)
        if i >= len(items):
            await c.answer("Список змінився, відкрийте розділ знову.", show_alert=True)
            return
        st.toggle(cat, items[i][0], pt)
        await c.message.edit_reply_markup(reply_markup=avail_cat_kb(cat, pt))
    await c.answer()


# ---------- персонал ----------
@router.message(F.text == O_STAFF, owner_only)
async def staff_menu(m: Message, state: FSMContext):
    await state.clear()
    rows = [[InlineKeyboardButton(text="➕ Запросити бариста", callback_data="sf:inv")]]
    lines = []
    for i, u in st.real():
        if u.get("barista"):
            lines.append(f"• {esc(u['name'])} · {esc(u.get('point', ''))}")
            rows.append([InlineKeyboardButton(text=f"❌ Прибрати: {u['name']}"[:60], callback_data=f"sf:rm:{i}")])
    txt = "👥 <b>Персонал</b>\n" + ("\n".join(lines) if lines else "Бариста поки немає.") + \
          "\n\nБариста може: скануйте QR, ставити штампи, видавати подарунки, бачити й закривати замовлення. " \
          "Акції, звіти, нагороди й налаштування — тільки власнику."
    await m.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sf:"))
async def staff_cb(c: CallbackQuery):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    _, act, *rest = c.data.split(":")
    if act == "inv":
        import secrets
        tok = secrets.token_urlsafe(6)
        st.d["invites"][tok] = time.time()
        st.save()
        await c.message.answer(f"👨‍🍳 Запрошення для бариста (одноразове, діє 48 годин):\n"
                               f"https://t.me/{BOT_USERNAME}?start=b_{tok}\n\n"
                               f"Надішліть це посилання бариста — він відкриє його в Telegram.")
    elif act == "rm":
        u = st.user(int(rest[0]))
        if u:
            u["barista"] = False
            if u.get("mode") == "barista":
                u["mode"] = "client"
            st.save()
            await safe_send(bot_ref(), int(rest[0]), "Ваш доступ бариста вимкнено.", reply_markup=client_kb(int(rest[0])))
            await c.message.edit_text(c.message.html_text + f"\n\n✅ Прибрано: {esc(u['name'])}")
    await c.answer()


# ---------- налаштування ----------
SETTING_ITEMS = [
    ("shop", "🏷 Назва закладу"), ("stamps_goal", "☕ Штампів до подарунка"),
    ("passport_days", "🧭 Паспорт: днів"), ("points", "📍 Точки"), ("drinks", "🍵 Напої та ціни"),
    ("milks", "🥛 Молоко (доплата)"), ("syrups", "🍯 Сиропи (доплата)"), ("desserts", "🍰 Десерти"),
    ("promos", "📣 Шаблони акцій"), ("quiet", "🕚 Тихі години"), ("promo_time", "⏰ Час розсилки"),
    ("maps_url", "⭐ Google Maps"), ("weekly_on", "🗓 Тижневий розбір"), ("welcome_on", "🎁 Вітальний подарунок"),
]
TOGGLES = {"weekly_on", "welcome_on"}
PRICE_KEYS = ("drinks", "milks", "syrups", "desserts")


class Setting(StatesGroup):
    entry = State()


def setting_value(key):
    if key == "quiet":
        return f"{st.cfg('quiet_from'):02d}:00–{st.cfg('quiet_to'):02d}:00"
    v = st.cfg(key)
    if key == "points":
        return ", ".join(v)
    if key in PRICE_KEYS:
        return "; ".join(f"{n} {p}" for n, p in v) if v else "немає"
    if key == "promos":
        return f"{len(v)} шт."
    if key in TOGGLES:
        return "✅ увімк." if v else "⛔ вимк."
    return str(v)


SETTING_GROUPS = [
    ("menu", "☕ Меню та ціни", ["drinks", "milks", "syrups", "desserts"]),
    ("loyal", "🎁 Лояльність", ["stamps_goal", "passport_days", "welcome_on"]),
    ("shop", "🏷 Заклад", ["shop", "points", "maps_url"]),
    ("mkt", "📣 Маркетинг", ["promos", "quiet", "promo_time", "weekly_on"]),
]


def group_of(key):
    return next((g for g, _, keys in SETTING_GROUPS if key in keys), None)


def btn_value(key):
    if key in PRICE_KEYS or key == "points":
        return str(len(st.cfg(key)))
    if key == "maps_url":
        return "задано"
    return setting_value(key)


def settings_kb(group=None):
    labels = dict(SETTING_ITEMS)
    if group is None:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data=f"stg:g:{g}")] for g, title, _ in SETTING_GROUPS])
    keys = next(k for g, _, k in SETTING_GROUPS if g == group)
    rows = []
    for key in keys:
        cb = f"stg:t:{key}" if key in TOGGLES else f"stg:e:{key}"
        rows.append([InlineKeyboardButton(text=f"{labels[key]} · {btn_value(key)}"[:60], callback_data=cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stg:g:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SETTING_HELP = {
    "shop": "Напишіть назву закладу (до 40 символів).",
    "stamps_goal": "Скільки штампів до подарунка? Число від 2 до 30.",
    "passport_days": "За скільки днів треба обійти всі точки? Число від 1 до 60.",
    "points": "Перелічіть точки через кому (від 2 до 6). Приклад: Кав'ярня, Будка №1, Будка №2.\n"
              "Паспорт вимагає відвідати всі точки зі списку.",
    "drinks": "Напої та ціни, кожен з нового рядка у форматі «Назва - ціна». Приклад:\nЕспресо - 45\nЛате - 75",
    "milks": "Види молока й доплата, кожен з нового рядка: «Назва - доплата». Приклад:\nБананове - 25\nКокосове - 20\n"
             "Щоб прибрати крок молока — надішліть «-». Що є в наявності сьогодні, відмічає бариста кнопкою «📦 Наявність».",
    "syrups": "Сиропи й доплата, кожен з нового рядка: «Назва - доплата». Приклад:\nКарамель - 10\nВаніль - 10\n"
              "Щоб прибрати крок сиропів — надішліть «-».",
    "desserts": "Десерти й ціни, кожен з нового рядка: «Назва - ціна». Приклад:\nКруасан - 55\nЧізкейк - 85\n"
                "Щоб прибрати десерти з замовлення — надішліть «-».",
    "promos": "Шаблони акцій — кожен з нового рядка (від 1 до 6, до 150 символів).",
    "quiet": "Тихі години, коли в закладі мало людей. Формат «11-14» (години від 0 до 23).",
    "promo_time": "О котрій надсилати заплановану акцію? Формат «10:30».",
    "maps_url": "Посилання на сторінку закладу в Google Maps (починається з http).",
}


PRICE_LINE = re.compile(r"^(.*?)[\s\-–—:=]*\+?\s*(\d+)\s*(?:грн)?\s*$")


def parse_setting(key, text):
    """Повертає (ок, значення або текст помилки)."""
    t = text.strip()
    try:
        if key == "shop":
            return (True, t[:40]) if t else (False, "Назва не може бути порожньою.")
        if key in ("stamps_goal", "passport_days"):
            lo, hi = (2, 30) if key == "stamps_goal" else (1, 60)
            n = int(t)
            return (True, n) if lo <= n <= hi else (False, f"Потрібне число від {lo} до {hi}.")
        if key == "points":
            ps = [x.strip()[:30] for x in t.replace("\n", ",").split(",") if x.strip()]
            if len(set(ps)) != len(ps) or not 2 <= len(ps) <= 6:
                return False, "Потрібно від 2 до 6 різних точок."
            return True, ps
        if key in PRICE_KEYS:
            if key != "drinks" and t in ("-", "–", "—", "0"):
                return True, []
            out = []
            for line in t.splitlines():
                if not line.strip():
                    continue
                mt = PRICE_LINE.match(line.strip())
                if not mt or not mt.group(1).strip():
                    return False, f"Не вдалося розібрати рядок: «{line.strip()[:40]}». Формат: Назва - ціна."
                name, price = mt.group(1).strip()[:30], int(mt.group(2))
                if key == "drinks" and price < 1:
                    return False, "Ціна напою має бути більшою за 0."
                if price > 9999:
                    return False, "Задто велика ціна."
                out.append([name, price])
            if len({n for n, _ in out}) != len(out):
                return False, "Назви мають бути різними."
            if not out:
                return False, "Додайте хоча б одну позицію (або «-», щоб прибрати розділ)." if key != "drinks" else "Додайте хоча б один напій."
            return True, out[:12]
        if key == "promos":
            ps = [x.strip()[:150] for x in t.splitlines() if x.strip()]
            return (True, ps[:6]) if ps else (False, "Додайте хоча б один шаблон.")
        if key == "quiet":
            a, b = (int(x) for x in t.replace("–", "-").split("-"))
            return (True, (a, b)) if 0 <= a < b <= 23 else (False, "Приклад: 11-14 (початок менший за кінець).")
        if key == "promo_time":
            h, mi = (int(x) for x in t.split(":"))
            return (True, f"{h:02d}:{mi:02d}") if 0 <= h <= 23 and 0 <= mi <= 59 else (False, "Приклад: 10:30.")
        if key == "maps_url":
            return (True, t) if t.startswith("http") else (False, "Посилання має починатися з http.")
    except (ValueError, IndexError):
        return False, "Не вдалося розібрати. Перевірте формат за прикладом."
    return False, "Невідоме налаштування."


@router.message(F.text == O_SETTINGS, owner_only)
async def settings_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⚙️ <b>Налаштування</b>", reply_markup=settings_kb())


@router.callback_query(F.data.startswith("stg:"))
async def settings_cb(c: CallbackQuery, state: FSMContext):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    _, act, key = c.data.split(":")
    if act == "g":
        await c.message.edit_reply_markup(reply_markup=settings_kb(None if key == "root" else key))
    elif act == "t" and key in TOGGLES:
        st.d["settings"][key] = not st.cfg(key)
        st.save()
        await c.message.edit_reply_markup(reply_markup=settings_kb(group_of(key)))
    elif act == "e" and key in SETTING_HELP:
        await state.set_state(Setting.entry)
        await state.update_data(key=key)
        cur = "\n".join(st.cfg(key)) if key == "promos" else (
            "\n".join(f"{n} - {p}" for n, p in st.cfg(key)) or "-" if key in PRICE_KEYS else setting_value(key))
        await c.message.answer(f"{SETTING_HELP[key]}\n\nЗараз:\n{esc(cur)}")
    await c.answer()


@router.message(Setting.entry, ~F.text.in_(ALL_BTNS))
async def settings_save(m: Message, state: FSMContext):
    key = (await state.get_data()).get("key")
    ok, val = parse_setting(key, m.text or "")
    if not ok:
        await m.answer(f"⚠️ {val}\nСпробуйте ще раз або натисніть будь-яку кнопку меню, щоб скасувати.")
        return
    await state.clear()
    if key == "quiet":
        st.d["settings"]["quiet_from"], st.d["settings"]["quiet_to"] = val
    else:
        st.d["settings"][key] = val
    st.save()
    await m.answer("✅ Збережено.", reply_markup=settings_kb(group_of(key)))


# ---------- тижневий розбір ----------
def quiet_text():
    return f"{st.cfg('quiet_from'):02d}:00–{st.cfg('quiet_to'):02d}:00"


def next_slot(wd: int, hour=None, minute=None):
    if hour is None:
        hour, minute = (int(x) for x in st.cfg("promo_time").split(":"))
    """Найближчий (майбутній) день тижня wd о hour:minute за київським часом."""
    now = datetime.now(TZ)
    d = now + timedelta(days=(wd - now.weekday()) % 7)
    d = d.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if d <= now:
        d += timedelta(days=7)
    return d


def promo_for(wd: int):
    return f"☕ У {WD_ACC[wd]} з {st.cfg('quiet_from'):02d}:00 до {st.cfg('quiet_to'):02d}:00 — друга кава за пів ціни. Покажіть це повідомлення бариста."


def build_review():
    w = st.weak_slot()
    if not w:
        return None
    wd = w["wd"]
    drop = round((1 - w["weak"] / w["other"]) * 100)
    src = "демо-дані" if not w["real"] else f"демо-дані + {w['real']} реальних візитів"
    text = (f"🗓 <b>Розбір тижня</b>\n"
            f"Найслабший час: <b>{WD[wd]} {quiet_text()}</b>.\n"
            f"Візитів на день у середньому: {w['weak']:.0f} проти {w['other']:.0f} в інші дні (−{drop}%).\n"
            f"<i>Основа: журнал візитів за 4 тижні ({src}).</i>\n\n"
            f"Пропоную акцію:\n«{esc(promo_for(wd))}»")
    when = next_slot(wd).strftime("%d.%m о %H:%M")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗓 Запланувати на {when}", callback_data=f"wk:plan:{wd}")],
        [InlineKeyboardButton(text="🚀 Надіслати зараз (для демо)", callback_data=f"wk:now:{wd}")]])
    return text, kb


async def broadcast(bot: Bot, text: str):
    sent = 0
    for uid, _ in st.real():
        if await safe_send(bot, uid, text):
            sent += 1
        await asyncio.sleep(0.05)
    return sent


@router.message(F.text == O_WEEK, owner_only)
async def week_review(m: Message, state: FSMContext):
    await state.clear()
    r = build_review()
    if not r:
        await m.answer("Поки замало даних для розбору.")
        return
    await m.answer(r[0], reply_markup=r[1])


@router.callback_query(F.data.startswith("wk:"))
async def week_action(c: CallbackQuery, bot: Bot):
    if not can_owner(c.from_user.id):
        await c.answer("Недоступно", show_alert=True)
        return
    _, act, wd = c.data.split(":")
    wd = int(wd)
    text = promo_for(wd)
    if act == "plan":
        at = next_slot(wd)
        st.d["scheduled"].append({"at": at.timestamp(), "text": text})
        st.save()
        await c.message.edit_text(c.message.html_text + f"\n\n✅ Заплановано на {at.strftime('%d.%m о %H:%M')}. "
                                                      f"Бот надішле сам, вам нічого робити не треба.")
    else:
        sent = await broadcast(bot, text)
        st.d["promos"] = st.d.get("promos", 0) + 1
        st.save()
        await c.message.edit_text(c.message.html_text + f"\n\n✅ Надіслано зараз: {sent} реальним підписникам.")
    await c.answer()


async def scheduler(bot: Bot):
    """Раз на 30 с: відкладені акції + щопонеділка о 09:00 розбір тижня власнику."""
    while True:
        try:
            now = time.time()
            due = [x for x in st.d["scheduled"] if x["at"] <= now]
            if due:
                st.d["scheduled"] = [x for x in st.d["scheduled"] if x["at"] > now]
                st.save()
                for x in due:
                    log.info("scheduled promo: sent=%s", await broadcast(bot, x["text"]))
            n = datetime.now(TZ)
            key = n.strftime("%Y-%m-%d")
            if st.cfg("weekly_on") and n.weekday() == 0 and n.hour == 9 and st.d.get("weekly_sent") != key:
                st.d["weekly_sent"] = key
                st.save()
                r = build_review()
                if r:
                    for o in owners():
                        await safe_send(bot, o, r[0], reply_markup=r[1])
        except Exception:
            log.exception("scheduler")
        await asyncio.sleep(30)


# ---------- запуск ----------
async def main():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    me = await bot.get_me()
    global BOT_USERNAME, _BOT
    BOT_USERNAME, _BOT = me.username, bot
    try:
        log.info("qr self-test ok: %d bytes", len(qr_png(f"https://t.me/{BOT_USERNAME}?start=c_test")))
    except Exception:
        log.exception("qr self-test FAILED")
    log.info("started as @%s admins=%s", me.username, sorted(ADMIN_IDS))
    bg = asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
