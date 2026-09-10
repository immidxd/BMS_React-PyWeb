"""Доставити в канал те, що застрягло у форумі — хвилями, з розподілом у часі.

ЗАДАЧА. Канал наповнює сам Telegram із нашого `schedule=`. Коли його відправка
провалюється, товар лишається лише у форумі й ніколи не потрапляє в канал —
мовчки. 26.08.2026 так загубилось 65 товарів. Цей скрипт бере список від
`telegram_publisher.undelivered_to_channel()` і ставить їх у розклад заново.

ЧОМУ ХВИЛЯМИ. Ліміт Telegram — 100 запланованих повідомлень на чат, і КОЖНЕ
фото альбому рахується окремо. Пʼять фото на товар означає ~18 товарів на
хвилю, не більше. Наступну хвилю можна ставити лише коли попередня вийшла і
звільнила місця — тому скрипт ЩОРАЗУ питає Telegram, скільки зайнято, і бере
рівно стільки, скільки влазить.

ЧОМУ КОПІЯ СЕСІЇ. Застосунок користувача тримає оригінал
`backend/.telegram_session/bms.session`, а два клієнти Telethon на одному
SQLite-файлі можуть його пошкодити. Скрипт працює з копією і видаляє її по
завершенні: це дійсні облікові дані.

Джерела, видалені з форуму, пропускаються — форвард без джерела неможливий.

Використання:
    python backend/scripts/telegram_deliver_wave.py            # лише план
    python backend/scripts/telegram_deliver_wave.py --apply    # поставити в розклад
"""
import atexit
import sys, os, asyncio, json, datetime as dt, warnings
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))
from zoneinfo import ZoneInfo
from telethon import TelegramClient
from telethon.tl.functions.messages import GetScheduledHistoryRequest
from telethon.tl.types import PeerChannel

SCRATCH = os.environ.get("TG_WAVE_TMP", "/tmp")
KYIV = ZoneInfo("Europe/Kyiv")
FORUM, CHANNEL = 2373506200, 1201323714
BUDGET = 90            # із 100, лишаємо запас
SLOTS = [9, 12, 15, 18]
DAYS = 5
APPLY = "--apply" in sys.argv


def _session_copy() -> str:
    """Копія файла сесії у тимчасовій теці. Оригінал тримає застосунок."""
    import shutil, tempfile
    try:
        from services.runtime_config import telegram_session_prefix
    except ImportError:  # pragma: no cover
        from backend.services.runtime_config import telegram_session_prefix
    src = telegram_session_prefix() + ".session"
    tmp = tempfile.mkdtemp(prefix="tgwave_")
    dst = os.path.join(tmp, "wave")
    shutil.copy2(src, dst + ".session")
    atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))
    return dst


def _candidates():
    """Недоставлені + головне повідомлення їхнього альбому у форумі."""
    from models.database import SessionLocal
    from services.telegram_publisher import undelivered_to_channel
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = undelivered_to_channel(db, since=os.getenv("TG_WAVE_SINCE", "2026-08-01"))
        out = []
        for r in rows:
            m = db.execute(text("""SELECT min(message_id) FROM telegram_posts
                WHERE product_id = :i AND chat_id = :f"""),
                {"i": r["product_id"], "f": FORUM}).scalar()
            if m:
                out.append({**r, "first_msg": m})
        return out
    finally:
        db.close()


def _record(done):
    """Записати поставлене в telegram_scheduled_posts — памʼятка BMS."""
    from models.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        for when, it in done:
            db.execute(text("""INSERT INTO telegram_scheduled_posts
                (product_id, product_number, chat_id, chat_title, scheduled_at,
                 source_chat_id, source_message_id)
                VALUES (:pid, :pn, :cid, :ct, :at, :scid, :smid)"""),
                {"pid": it["product_id"], "pn": it["productnumber"], "cid": CHANNEL,
                 "ct": "BrandStore 👟 │ Брендове взуття", "at": when,
                 "scid": FORUM, "smid": it["ids"][0]})
        db.commit()
    finally:
        db.close()


async def main():
    cands = _candidates()
    c = TelegramClient(_session_copy(),
                       int(os.getenv("TELEGRAM_API_ID")), os.getenv("TELEGRAM_API_HASH"))
    await c.connect()
    forum = await c.get_entity(PeerChannel(FORUM))
    channel = await c.get_entity(PeerChannel(CHANNEL))

    used = getattr(await c(GetScheduledHistoryRequest(peer=channel, hash=0)), "count", 0)
    print(f"у розкладі каналу вже зайнято: {used} зі 100\n")

    picked, budget = [], BUDGET - int(used or 0)
    for it in cands:
        if len(picked) >= len(SLOTS) * DAYS:
            break
        head = await c.get_messages(forum, ids=int(it["first_msg"]))
        if head is None:
            print(f"  ⨯ {it['productnumber']:<9} джерело видалено — пропускаю")
            continue
        gid = getattr(head, "grouped_id", None)
        album = [head]
        if gid:
            near = await c.get_messages(forum, min_id=int(it["first_msg"]) - 1,
                                        max_id=int(it["first_msg"]) + 12, limit=14)
            album = sorted([m for m in near if getattr(m, "grouped_id", None) == gid],
                           key=lambda m: m.id)
        if len(album) > budget:
            print(f"  ⨯ {it['productnumber']:<9} не влазить у бюджет ({len(album)} > {budget})")
            break
        budget -= len(album)
        picked.append({**it, "ids": [m.id for m in album], "photos": len(album)})

    # розклад: DAYS днів, SLOTS годин, від завтра
    base = dt.datetime.now(KYIV).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    plan = []
    for i, it in enumerate(picked):
        day, slot = divmod(i, len(SLOTS))
        when = base + dt.timedelta(days=day, hours=SLOTS[slot], minutes=(i % 3) * 7)
        plan.append((when, it))

    print(f"{'коли (Київ)':<20}{'товар':<10}{'фото':>5}  бренд / модель")
    print("─" * 74)
    for when, it in plan:
        print(f"{when:%d.%m %H:%M}{'':<8}{it['productnumber']:<10}{it['photos']:>5}  "
              f"{str(it['brand'])[:14]:<15} {str(it['model'] or '')[:22]}")
    print("─" * 74)
    print(f"товарів {len(plan)}, місць у розкладі {sum(i['photos'] for _, i in plan)}, "
          f"лишиться вільно {budget}")

    if not APPLY:
        print("\nСУХИЙ ПРОГІН — нічого не надіслано. Для запуску: --apply")
        await c.disconnect(); return

    print("\n═══ ставлю в розклад ═══")
    done = []
    for when, it in plan:
        try:
            await c.forward_messages(entity=channel, messages=it["ids"],
                                     from_peer=forum, schedule=when, silent=False)
            done.append((when, it))
            print(f"  ✓ {it['productnumber']:<9} на {when:%d.%m %H:%M}")
        except Exception as e:
            print(f"  ✗ {it['productnumber']:<9} {type(e).__name__}: {e}")
        await asyncio.sleep(2)
    _record(done)
    print(f"\nпоставлено: {len(done)} із {len(plan)} (записано в telegram_scheduled_posts)")
    await c.disconnect()

asyncio.run(main())
