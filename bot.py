"""Демо-бот для кав'ярні: клієнтський режим + режим власника в одному боті."""
import asyncio, html, io, logging, os, time
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

from store import GOAL_NAMES, PASSPORT_DAYS, POINTS, STAMPS_FOR_GIFT, WD, WD_ACC, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coffee")

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DEMO_CODE = os.getenv("DEMO_CODE", "")          # якщо задано: /owner <код> дає режим власника
SHOP = os.getenv("SHOP_NAME", "Кав'ярня")        # назва під клієнта
MAPS_URL = os.getenv("MAPS_URL", "https://maps.google.com")
TZ = ZoneInfo("Europe/Kyiv")

DRINKS = [("Еспресо", 45), ("Американо", 55), ("Капучино", 70), ("Лате", 75)]
PROMOS = [
    "☕ Друга кава за пів ціни до 14:00! Покажіть це повідомлення бариста.",
    "🥐 Лате + круасан за 99 грн, тільки з 11:00 до 14:00.",
    "☔ Дощ за вікном — у нас тепло. Капучино −20% до 14:00.",
]

st = Store()
router = Router()


# ---------- кнопки ----------
B_STAMPS, B_GIFTS, B_ORDER = "☕ Мої штампи", "🎁 Подарунки", "⏱ Замовити наперед"
B_POINTS, B_REVIEW, B_CODE = "📍 Наші точки", "⭐ Відгук", "📲 Мій код"   # B_CODE лишився лише для старих клавіатур
B_USUAL, B_PREFS, B_PASS = "🔁 Як завжди", "✍️ Вподобання", "🧭 Паспорт і QR"   # B_USUAL/B_PREFS/B_ORDER/B_POINTS/B_REVIEW лишились для старих клавіатур
B_MENU_ORDER, B_INFO = "☕ Замовити", "📍 Точки й відгук"
B_TO_OWNER, B_TO_CLIENT = "🔐 Режим власника", "🔄 Режим клієнта"
O_PROMO, O_BACK, O_STAMP = "📣 Акція 11–14", "💤 Повернути зниклих", "➕ Штамп за кодом"
O_REDEEM, O_REPORT = "🎁 Погасити подарунок", "📊 Звіт"
O_WEEK, O_POINT, O_REWARDS = "🗓 Розбір тижня", "📍 Змінити точку", "🎁 Нагороди"
ALL_BTNS = {B_STAMPS, B_GIFTS, B_ORDER, B_POINTS, B_REVIEW, B_CODE, B_TO_OWNER, B_TO_CLIENT,
            B_USUAL, B_PREFS, B_PASS, O_PROMO, O_BACK, O_STAMP, O_REDEEM, O_REPORT, O_WEEK, O_POINT,
            B_MENU_ORDER, B_INFO, O_REWARDS}


def rk(rows):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def client_kb(uid):
    rows = [[B_MENU_ORDER, B_PASS], [B_STAMPS, B_GIFTS], [B_INFO]]
    if can_owner(uid):
        rows.append([B_TO_OWNER])
    return rk(rows)


def owner_kb():
    return rk([[O_PROMO, O_BACK], [O_STAMP, O_REDEEM], [O_WEEK, O_REPORT], [O_REWARDS, O_POINT], [B_TO_CLIENT]])


def can_owner(uid):
    u = st.user(uid)
    return uid in ADMIN_IDS or bool(u and u.get("owner_ok"))


def owners():
    ids = set(ADMIN_IDS)
    ids |= {i for i, u in st.real() if u.get("owner_ok")}
    return ids


def esc(s):
    return html.escape(str(s), quote=False)


def bar(n):
    return "●" * n + "○" * (STAMPS_FOR_GIFT - n)


async def safe_send(bot, chat_id, text, **kw):
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except TelegramAPIError as e:
        log.warning("send to %s failed: %s", chat_id, e)
        return False


BOT_USERNAME = ""


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
    if arg.startswith("c_") and can_owner(uid):
        k, target = st.by_token(arg[2:])
        if target:
            st.ensure(uid, m.from_user.full_name)[0]["mode"] = "owner"
            await m.answer("🔐 Режим власника", reply_markup=owner_kb())
            await show_client_card(m, k, target)
            return
        await m.answer("Цей QR не розпізнано.")
        return
    u, new = st.ensure(uid, m.from_user.full_name)
    u["mode"] = "client"
    st.save()
    if new:
        await m.answer(
            f"Привіт, {esc(m.from_user.first_name)}! ☕\n"
            f"Це бот «{esc(SHOP)}». Ми вже додали вам <b>подарунок</b>: {esc(st.reward_title('welcome'))}.\n\n"
            f"Збирайте штампи: кожен {STAMPS_FOR_GIFT}-й напій у подарунок. "
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
    left = STAMPS_FOR_GIFT - u["stamps"]
    await m.answer(f"<b>Ваші штампи</b>\n{bar(u['stamps'])}  {u['stamps']}/{STAMPS_FOR_GIFT}\n\n"
                   f"До подарунка лишилось: <b>{left}</b>. Покажіть бариста QR із «{B_PASS}».")


def gift_lines(u):
    return "\n".join(f"• <b>{esc(st.reward_title(g['goal'], u))}</b> — {GOAL_NAMES[g['goal']]}" for g in u["gifts"])


@router.message(F.text == B_GIFTS)
async def my_gifts(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    have, _left = st.passport_state(u)
    goals = (f"🎯 <b>Як отримати подарунок</b>\n"
             f"☕ {STAMPS_FOR_GIFT} штампів ({u['stamps']}/{STAMPS_FOR_GIFT}) → {esc(st.reward_title('stamps', u))}\n"
             f"🧭 Паспорт ({len(have)}/{len(POINTS)} точок за {PASSPORT_DAYS} днів) → {esc(st.reward_title('passport', u))}")
    if u["gifts"]:
        await m.answer(f"🎁 <b>Ваші подарунки</b>\n{gift_lines(u)}\n\nПокажіть QR із «{B_PASS}» бариста, щоб забрати.\n\n{goals}")
    else:
        await m.answer(f"Подарунків поки немає.\n\n{goals}")


def points_text():
    return ("📍 <b>Наші точки</b>\n" + "\n".join(f"• {p}" for p in POINTS) +
            "\n\nШтампи й подарунки діють на всіх точках.")


@router.message(F.text == B_POINTS)
async def points(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(points_text())


@router.message(F.text == B_MENU_ORDER)
async def order_menu(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    rows = []
    last = u.get("last")
    if last:
        rows.append([InlineKeyboardButton(text=f"🔁 Як завжди: {DRINKS[last['drink']][0]}", callback_data="mn:usual")])
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
        await show_drinks(c.message)
    elif act == "prefs":
        await ask_prefs(c.message, c.from_user, state)
    elif act == "points":
        await c.message.answer(points_text())
    elif act == "review":
        await show_rating(c.message)
    await c.answer()


# --- предзамовлення ---
async def show_drinks(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{n} — {p} грн", callback_data=f"od:{i}")] for i, (n, p) in enumerate(DRINKS)])
    await msg.answer("Що замовити?", reply_markup=kb)


@router.message(F.text == B_ORDER)
async def order_start(m: Message, state: FSMContext):
    await state.clear()
    await show_drinks(m)


@router.callback_query(F.data.startswith("od:"))
async def order_time(c: CallbackQuery):
    i = int(c.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"через {t} хв", callback_data=f"ot:{i}:{t}") for t in (5, 10, 15)]])
    await c.message.edit_text(f"<b>{DRINKS[i][0]}</b>. Коли забрати?", reply_markup=kb)
    await c.answer()


@router.callback_query(F.data.startswith("ot:"))
async def order_point(c: CallbackQuery):
    _, i, t = c.data.split(":")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p, callback_data=f"op:{i}:{t}:{k}")] for k, p in enumerate(POINTS)])
    await c.message.edit_text("Де забрати?", reply_markup=kb)
    await c.answer()


async def place_order(bot: Bot, user, i: int, t: int, k: int, usual=False):
    """Створює замовлення, зберігає його як «останнє» і сповіщає власника. Повертає текст для клієнта."""
    name, price = DRINKS[i]
    point = POINTS[k]
    u, _ = st.ensure(user.id, user.full_name)
    n = st.add_order(user.id, name, price, t, point)
    u["last"] = {"drink": i, "point": k}
    st.save()
    touch(user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data=f"rd:{n}")]])
    note = ("\n🔁 Постійний клієнт: «як завжди»" if usual else "")
    if u.get("prefs"):
        note += f"\n📝 Вподобання: <b>{esc(u['prefs'])}</b>"
    for o in owners():
        await safe_send(bot, o, f"🆕 <b>Замовлення №{n}</b>\n{name} · {price} грн\n📍 {esc(point)} · через {t} хв\n"
                                f"Клієнт: {esc(user.full_name)}{note}", reply_markup=kb)
    return (f"✅ Замовлення №{n} прийнято\n<b>{name}</b> · {price} грн\n"
            f"📍 {esc(point)} · через {t} хв\nМи напишемо, коли буде готово.")


@router.callback_query(F.data.startswith("op:"))
async def order_done(c: CallbackQuery, bot: Bot):
    _, i, t, k = c.data.split(":")
    text = await place_order(bot, c.from_user, int(i), int(t), int(k))
    await c.message.edit_text(text)
    await c.answer()


# --- «Як завжди» ---
@router.message(F.text == B_USUAL)
async def usual_start(m: Message, state: FSMContext):
    await state.clear()
    await show_usual(m, m.from_user)


async def show_usual(m: Message, user):
    u, _ = st.ensure(user.id, user.full_name)
    last = u.get("last")
    if not last:
        await m.answer(f"Ви ще нічого не замовляли. Зробіть перше замовлення через «{B_ORDER}» — "
                       f"далі «Як завжди» повторить його одним дотиком.")
        return
    name, price = DRINKS[last["drink"]]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"через {t} хв", callback_data=f"uz:{t}") for t in (5, 10, 15)]])
    await m.answer(f"🔁 Як завжди: <b>{name}</b> · {price} грн\n📍 {esc(POINTS[last['point']])}\nКоли забрати?",
                   reply_markup=kb)


@router.callback_query(F.data.startswith("uz:"))
async def usual_go(c: CallbackQuery, bot: Bot):
    u, _ = st.ensure(c.from_user.id, c.from_user.full_name)
    last = u.get("last")
    if not last:
        await c.answer("Немає останнього замовлення", show_alert=True)
        return
    text = await place_order(bot, c.from_user, last["drink"], int(c.data.split(":")[1]), last["point"], usual=True)
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
    rows = "\n".join(f"{'✅' if p in have else '⬜'} {esc(p)}" for p in POINTS)
    tail = (f"Лишилось днів: <b>{left}</b>." if have else f"Відвідайте всі точки за {PASSPORT_DAYS} днів.")
    caption = (f"🧭 <b>Кавовий паспорт</b>\nКав'ярня й обидві будки за {PASSPORT_DAYS} днів — подарунок: "
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
    if not can_owner(c.from_user.id):
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
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Залишити відгук на Google Maps", url=MAPS_URL)]])
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


@router.message(F.text == O_PROMO, owner_only)
async def promo_start(m: Message, state: FSMContext):
    await state.clear()
    rows = [[InlineKeyboardButton(text=p[:50] + ("…" if len(p) > 50 else ""), callback_data=f"pm:{i}")] for i, p in enumerate(PROMOS)]
    rows.append([InlineKeyboardButton(text="✍️ Свій текст", callback_data="pm:c")])
    await m.answer("📣 <b>Акція на тихі години (11:00–14:00)</b>\nОберіть шаблон або напишіть свій:",
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
        await promo_preview(c.message, state, PROMOS[int(key)])
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


@router.message(F.text == O_STAMP, owner_only)
async def stamp_start(m: Message, state: FSMContext):
    await state.set_state(Stamp.code)
    await m.answer("Найшвидше — відскануйте QR клієнта камерою телефона. Або введіть код клієнта (клієнт бачить його під QR у «🧭 Паспорт і QR»):")


@router.message(F.text == O_POINT, owner_only)
async def change_point(m: Message, state: FSMContext):
    await state.clear()
    u, _ = st.ensure(m.from_user.id, m.from_user.full_name)
    cur = u.get("point", POINTS[0])
    u["point"] = POINTS[(POINTS.index(cur) + 1) % len(POINTS)] if cur in POINTS else POINTS[0]
    st.save()
    await m.answer(f"📍 Ви працюєте на точці: <b>{esc(u['point'])}</b>. Штампи записуватимуться сюди. "
                   f"Натисніть ще раз, щоб змінити.")


async def apply_stamp(bot: Bot, me, k, u):
    """Штамп на точці бариста. Повертає текст для бариста."""
    point = me.get("point", POINTS[0])
    u["stamps"] += 1
    u["last_visit"] = time.time()
    gift = u["stamps"] >= STAMPS_FOR_GIFT
    if gift:
        u["stamps"] = 0
        st.grant(u, "stamps")
    passed = st.passport_mark(u, point)
    st.log_visit(point)
    st.save()
    g_stamps = esc(st.reward_title("stamps", u))
    g_pass = esc(st.reward_title("passport", u))
    if not u.get("demo"):
        txt = f"☕ +1 штамп ({esc(point)})!\n{bar(u['stamps'])}  {u['stamps']}/{STAMPS_FOR_GIFT}"
        if gift:
            txt = f"🎉 {STAMPS_FOR_GIFT} штампів! Ваш подарунок: <b>{g_stamps}</b>. Покажіть QR бариста."
        if passed:
            txt += f"\n🧭 Паспорт пройдено — подарунок: <b>{g_pass}</b>!"
        else:
            have, _left = st.passport_state(u)
            txt += f"\n🧭 Паспорт: {len(have)}/{len(POINTS)} точок"
        await safe_send(bot, int(k), txt)
    return (f"✅ Штамп додано: {esc(u['name'])} · {esc(point)}\n{bar(u['stamps'])} {u['stamps']}/{STAMPS_FOR_GIFT}"
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
    text = (f"👤 <b>{esc(u['name'])}</b>\n{bar(u['stamps'])} {u['stamps']}/{STAMPS_FOR_GIFT}\n"
            f"🧭 Паспорт: {len(have)}/{len(POINTS)} точок")
    if u["gifts"]:
        text += f"\n\n🎁 <b>Подарунки</b>\n{gift_lines(u)}"
    if u.get("prefs"):
        text += f"\n\n📝 Вподобання: <b>{esc(u['prefs'])}</b>"
    point = st.user(m.from_user.id).get("point", POINTS[0])
    tok = st.token(u)
    rows = [[InlineKeyboardButton(text=f"➕ Штамп ({point})", callback_data=f"cs:{tok}")]]
    for goal in dict.fromkeys(g["goal"] for g in u["gifts"]):
        rows.append([InlineKeyboardButton(text=f"🎁 Видати: {st.reward_title(goal, u)}"[:60], callback_data=f"cg:{tok}:{goal}")])
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith(("cs:", "cg:")))
async def card_action(c: CallbackQuery, bot: Bot):
    if not can_owner(c.from_user.id):
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


@router.message(F.text == O_REDEEM, owner_only)
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
    rows = [[InlineKeyboardButton(text=f"{GOAL_NAMES[g]}: {st.d['rewards'][g]}"[:60], callback_data=f"rw:{g}")]
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
    await c.message.answer(f"Ціль: <b>{GOAL_NAMES[goal]}</b>\nЗараз: <b>{esc(st.d['rewards'][goal])}</b>\n"
                           f"Напишіть нову нагороду одним повідомленням.{hint}")
    await c.answer()


@router.message(Reward.text, ~F.text.in_(ALL_BTNS))
async def rewards_save(m: Message, state: FSMContext):
    goal = (await state.get_data()).get("goal")
    await state.clear()
    text = (m.text or "").strip()[:80]
    if goal not in GOAL_NAMES or not text:
        await m.answer("Не вдалося зберегти, спробуйте ще раз.")
        return
    st.d["rewards"][goal] = text
    st.save()
    await m.answer(f"✅ Нагорода за «{GOAL_NAMES[goal]}»: <b>{esc(text)}</b>\nКлієнти побачать її одразу.")


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
        "Чеків 11:00–14:00 за день: було 14 → стало 23\n"
        "У реальному запуску ці цифри беремо з вашого Poster.")


# ---------- тижневий розбір ----------
def next_slot(wd: int, hour=10, minute=30):
    """Найближчий (майбутній) день тижня wd о hour:minute за київським часом."""
    now = datetime.now(TZ)
    d = now + timedelta(days=(wd - now.weekday()) % 7)
    d = d.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if d <= now:
        d += timedelta(days=7)
    return d


def promo_for(wd: int):
    return f"☕ У {WD_ACC[wd]} з 11:00 до 14:00 — друга кава за пів ціни. Покажіть це повідомлення бариста."


def build_review():
    w = st.weak_slot()
    if not w:
        return None
    wd = w["wd"]
    drop = round((1 - w["weak"] / w["other"]) * 100)
    src = "демо-дані" if not w["real"] else f"демо-дані + {w['real']} реальних візитів"
    text = (f"🗓 <b>Розбір тижня</b>\n"
            f"Найслабший час: <b>{WD[wd]} 11:00–14:00</b>.\n"
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
            if n.weekday() == 0 and n.hour == 9 and st.d.get("weekly_sent") != key:
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
    global BOT_USERNAME
    BOT_USERNAME = me.username
    try:
        log.info("qr self-test ok: %d bytes", len(qr_png(f"https://t.me/{BOT_USERNAME}?start=c_test")))
    except Exception:
        log.exception("qr self-test FAILED")
    log.info("started as @%s admins=%s", me.username, sorted(ADMIN_IDS))
    bg = asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
