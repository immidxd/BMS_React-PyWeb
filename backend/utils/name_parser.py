"""
Розумний парсер імен клієнтів для BMS.

Визначає:
- first_name (ім'я)
- last_name (прізвище)
- middle_name (по батькові) — не парситься автоматично, лише вручну
- nickname (нікнейм) — якщо текст не є реальним ім'ям/прізвищем
- gender_id — вгадується за ім'ям та прізвищем

Логіка:
1. Якщо обидва слова ідентичні ("Alla Alla") → нікнейм
2. Розпізнаємо кожне слово: ім'я, прізвище, або невідоме
3. Якщо є розпізнане ім'я і прізвище → розставляємо правильно
4. Якщо жодне слово не розпізнане → весь рядок = нікнейм
5. Стать визначається за ім'ям (надійніше) або за прізвищем

Gender IDs (з таблиці genders):
  0 = Невідомо
  1 = унісекс
  2 = жіноча
  3 = чоловіча
"""
import re
from typing import Optional, NamedTuple

GENDER_UNKNOWN = 0
GENDER_UNISEX = 1
GENDER_FEMALE = 2
GENDER_MALE = 3


class ParsedName(NamedTuple):
    first_name: Optional[str]
    last_name: Optional[str]
    nickname: Optional[str]
    gender_id: int


# ── Бази імен ─────────────────────────────────────────────────────────────────

# Жіночі імена (українські, слов'янські, міжнародні транслітеровані)
_FEMALE_FIRST_NAMES = frozenset({
    # Українські
    "алла", "аліна", "аліса", "альона", "алёна", "альбіна", "анастасія", "настя",
    "ангеліна", "анжела", "анжеліка", "анна", "аня", "антоніна", "валентина",
    "валерія", "вероніка", "вікторія", "віолетта", "віра", "віта", "влада",
    "галина", "галя", "ганна", "дарина", "дар'я", "даша", "діана", "евеліна",
    "елеонора", "елла", "емілія", "євгенія", "женя", "жанна", "зінаїда",
    "злата", "зоя", "зоряна", "іванна", "інга", "інна", "ірина", "іра",
    "карина", "катерина", "катя", "кіра", "клавдія", "крістіна", "ксенія",
    "лада", "лариса", "леся", "ліана", "лідія", "ліза", "лілія", "ліля",
    "лілія", "ліна", "любов", "люба", "людмила", "люда", "маргарита", "рита",
    "марина", "марія", "маша", "марта", "мар'яна", "мілана", "мирослава",
    "надія", "надя", "наталія", "наталя", "наташа", "неля", "ніна", "оксана",
    "олена", "олеся", "ольга", "оля", "поліна", "раїса", "регіна", "роксолана",
    "руслана", "світлана", "свєтлана", "софія", "соня", "соломія", "стефанія",
    "тамара", "тетяна", "таня", "уляна", "юлія", "юля", "яна", "ярина",
    "ярослава",
    # Слов'янські (рос. транслітерація, часто зустрічаються в Google Sheets)
    "алена", "алина", "анастасия", "вероника", "виктория", "дарья", "евгения",
    "екатерина", "елена", "ирина", "кристина", "ксения", "любовь", "людмила",
    "маргарита", "мария", "наталья", "наталия", "нина", "оксана", "ольга",
    "полина", "светлана", "татьяна", "юлия",
    # Латинізовані (Facebook-імена)
    "alina", "alla", "anastasia", "anna", "daria", "daryna", "diana", "elena",
    "galina", "galyna", "hanna", "halyna", "halina", "inna", "iryna", "irina",
    "ivanna", "julia", "juliya", "karina", "kateryna", "katerina", "khrystyna",
    "kristina", "ksenia", "larysa", "lesia", "lesya", "lidiia", "liliya",
    "liudmyla", "liubov", "luba", "ludmyla", "marina", "mariya", "maria",
    "marta", "maryana", "myroslava", "nadiya", "nadia", "nataliya", "natalia",
    "nataliia", "natasha", "nina", "oksana", "olena", "olga", "olha", "polina",
    "roksolana", "ruslana", "sofiya", "sophia", "solomiia", "svitlana",
    "svetlana", "tamara", "tetiana", "tatiana", "uliana", "ulyana",
    "valentyna", "valeriia", "veronika", "viktoriya", "viktoriia", "vira",
    "vita", "vlada", "yulia", "yuliya", "yana", "yaryna", "yaroslava",
    "zhanna", "zoriana", "zoryana", "zoya",
    # Зменшувальні / неформальні
    "galka", "tanya", "tania", "sveta", "lena", "vika", "dasha",
    "masha", "sasha", "zhenya", "tonya", "lyuda", "lyuba", "liza",
    "valya", "nadya", "katya", "sonya", "rita", "alyona", "nastenka",
    "olenka", "irynka", "ira", "olia", "tania",
})

# Чоловічі імена (українські, слов'янські, міжнародні транслітеровані)
_MALE_FIRST_NAMES = frozenset({
    # Українські
    "адам", "андрій", "антон", "артем", "артур", "богдан", "борис",
    "вадим", "валентин", "валерій", "василь", "віктор", "віталій", "владислав",
    "влад", "володимир", "в'ячеслав", "геннадій", "григорій", "данило",
    "денис", "дмитро", "євген", "євгеній", "захар", "іван", "ігор",
    "кирило", "костянтин", "леонід", "максим", "марк", "микола",
    "михайло", "назар", "олег", "олександр", "остап", "павло",
    "петро", "роман", "ростислав", "руслан", "святослав", "сергій",
    "степан", "тарас", "тимофій", "юрій", "ярослав",
    # Рос. варіанти
    "александр", "алексей", "андрей", "артём", "виктор", "виталий",
    "владимир", "вячеслав", "геннадий", "григорий", "дмитрий", "евгений",
    "игорь", "кирилл", "константин", "леонид", "михаил", "николай",
    "олег", "павел", "роман", "сергей", "юрий",
    # Латинізовані
    "adam", "andrii", "andriy", "andrey", "anton", "artem", "artur",
    "bohdan", "bogdan", "borys", "boris", "danylo", "denys", "denis",
    "dmytro", "dmitry", "eugene", "evgen", "hryhorii", "ihor", "igor",
    "ivan", "kostiantyn", "leonid", "maksym", "maxim", "mark", "mykhailo",
    "mykola", "nazar", "oleg", "oleh", "oleksandr", "olexander", "ostap",
    "pavlo", "petro", "roman", "rostyslav", "ruslan", "serhii", "serhiy",
    "sergiy", "sergei", "stepan", "sviatoslav", "taras", "tymofii",
    "vadym", "valentyn", "valerii", "vasyl", "viktor", "vitalii", "vitaliy",
    "vlad", "vladyslav", "volodymyr", "yaroslav", "yurii", "yuriy",
    "zakhar",
    # Зменшувальні / неформальні
    "sasha", "zhenya", "kolya", "vova", "dima", "tolya", "vitya",
    "petya", "seryozha", "lyosha", "misha", "pasha", "grisha",
})

# ── Патерни прізвищ ───────────────────────────────────────────────────────────

# Типові закінчення українських/слов'янських прізвищ (від специфічних до загальних)
_SURNAME_ENDINGS_FEMALE = (
    "ська", "зька", "цька",        # -ська (Ковальська)
    "ова", "ева", "єва", "ьова",   # -ова (Іванова)
    "іна",                          # -іна (Петрівна — але це по батькові, тут рідко)
)

_SURNAME_ENDINGS_MALE = (
    "ський", "зький", "цький",     # -ський (Ковальський)
    "ов", "ев", "єв", "ьов",       # -ов (Іванов)
    "ін",                           # -ін (Калінін)
)

_SURNAME_ENDINGS_NEUTRAL = (
    "енко", "єнко",                 # -енко (Шевченко) — найпоширеніше укр. прізвище
    "ко",                           # -ко (Бойко)
    "ук", "юк", "чук",             # -ук (Костюк, Мельничук)
    "ак", "як",                     # -ак (Поляк)
    "ець", "єць",                   # -ець (Кравець)
    "ич", "іч",                     # -ич (Петрович) — може бути по батькові
    "ій", "ий",                     # -ій (Добрий, Синій)
    "ів", "їв",                     # -ів (Петрів)
    "ар", "яр",                     # -ар (Бондар)
    "ач",                           # -ач (Ткач)
    "аш",                           # -аш (Лукаш)
    "ей",                           # -ей (Андрусей)
    "ай",                           # -ай (Бабай)
)


def _is_known_first_name(word: str) -> Optional[int]:
    """Повертає gender_id якщо слово є відомим ім'ям, інакше None."""
    w = word.lower().strip()
    if w in _FEMALE_FIRST_NAMES:
        return GENDER_FEMALE
    if w in _MALE_FIRST_NAMES:
        return GENDER_MALE
    return None


def _looks_like_surname(word: str) -> Optional[int]:
    """
    Повертає gender_id якщо слово виглядає як прізвище за закінченням.
    Жіночі закінчення перевіряємо першими (бо -ова довше за -ов).
    Повертає None якщо не схоже на прізвище.
    """
    w = word.lower().strip()
    if len(w) < 3:
        return None

    for ending in _SURNAME_ENDINGS_FEMALE:
        if w.endswith(ending):
            return GENDER_FEMALE

    for ending in _SURNAME_ENDINGS_MALE:
        if w.endswith(ending):
            return GENDER_MALE

    for ending in _SURNAME_ENDINGS_NEUTRAL:
        if w.endswith(ending):
            return GENDER_UNISEX  # стать не визначена за прізвищем

    return None


def _is_latin(word: str) -> bool:
    """Чи слово написане латиницею."""
    return bool(re.match(r'^[a-zA-Z\-\']+$', word))


def _is_cyrillic(word: str) -> bool:
    """Чи слово написане кирилицею."""
    return bool(re.match(r'^[а-яА-ЯіІїЇєЄґҐ\'\-]+$', word))


def _looks_like_nickname(word: str) -> bool:
    """
    Слово виглядає як нікнейм, якщо:
    - Містить цифри
    - Містить спецсимволи (крім дефіса та апострофа)
    - Починається з маленької літери (нетипово для імені)
    - Дуже коротке (1-2 символи) без розпізнавання як ім'я
    """
    if re.search(r'[0-9_@#$%^&*+=]', word):
        return True
    if len(word) <= 2 and not _is_known_first_name(word):
        return True
    return False


def parse_client_name(raw_name: str) -> ParsedName:
    """
    Головна функція: парсить рядок імені клієнта з Google Sheets.

    Приклади:
        "Ірина Кравець"      → first="Ірина", last="Кравець", nick=None, gender=2(ж)
        "Кравець Ірина"      → first="Ірина", last="Кравець", nick=None, gender=2(ж)
        "Bagirra Bagirra"    → first=None, last=None, nick="Bagirra Bagirra", gender=0
        "Alla Alla"          → first=None, last=None, nick="Alla Alla", gender=0
        "SweetGirl2000"      → first=None, last=None, nick="SweetGirl2000", gender=0
        "Олег"               → first="Олег", last=None, nick=None, gender=3(ч)
        "Шевченко"           → first=None, last="Шевченко", nick=None, gender=1
        "Bevz Oksana"        → first="Oksana", last="Bevz", nick=None, gender=2(ж)
    """
    if not raw_name or not raw_name.strip():
        return ParsedName(None, None, None, GENDER_UNKNOWN)

    name = raw_name.strip()

    # Прибираємо невидимі символи (ㅤ тощо)
    name = re.sub(r'[\u3164\u2800\u200b\u200c\u200d\ufeff]', '', name).strip()
    if not name:
        return ParsedName(None, None, None, GENDER_UNKNOWN)

    # Спеціальні випадки: "Невідомий клієнт", "Магазин (walk-in)" тощо
    _PLACEHOLDER_NAMES = {"невідомий клієнт", "невідомий", "unknown", "test", "тест"}
    if name.lower().strip() in _PLACEHOLDER_NAMES:
        return ParsedName(None, None, None, GENDER_UNKNOWN)

    parts = name.split()

    # ── Одне слово ──
    if len(parts) == 1:
        word = parts[0]

        if _looks_like_nickname(word):
            return ParsedName(None, None, name, GENDER_UNKNOWN)

        gender = _is_known_first_name(word)
        if gender is not None:
            return ParsedName(word, None, None, gender)

        surname_gender = _looks_like_surname(word)
        if surname_gender is not None:
            return ParsedName(None, word, None, surname_gender)

        # Не розпізнано — вважаємо нікнеймом
        return ParsedName(None, None, name, GENDER_UNKNOWN)

    # ── Два слова ──
    if len(parts) == 2:
        w1, w2 = parts

        # Якщо однакові — нікнейм ("Alla Alla", "Bagirra Bagirra")
        if w1.lower() == w2.lower():
            return ParsedName(None, None, name, GENDER_UNKNOWN)

        # Якщо будь-яке слово виглядає як нікнейм — все нікнейм
        if _looks_like_nickname(w1) or _looks_like_nickname(w2):
            return ParsedName(None, None, name, GENDER_UNKNOWN)

        # Розпізнаємо кожне слово
        w1_first_gender = _is_known_first_name(w1)
        w2_first_gender = _is_known_first_name(w2)
        w1_surname_gender = _looks_like_surname(w1)
        w2_surname_gender = _looks_like_surname(w2)

        first_name = None
        last_name = None
        gender = GENDER_UNKNOWN

        # Випадок 1: w1=ім'я, w2=прізвище (стандартний порядок)
        if w1_first_gender is not None and w2_surname_gender is not None:
            first_name = w1
            last_name = w2
            gender = w1_first_gender  # ім'я надійніше для статі

        # Випадок 2: w1=прізвище, w2=ім'я (зворотний порядок)
        elif w2_first_gender is not None and w1_surname_gender is not None:
            first_name = w2
            last_name = w1
            gender = w2_first_gender

        # Випадок 3: обидва — імена (беремо перше як ім'я)
        elif w1_first_gender is not None and w2_first_gender is not None:
            first_name = w1
            last_name = w2
            gender = w1_first_gender

        # Випадок 4: w1=ім'я, w2 невідоме → w2 вважаємо прізвищем
        elif w1_first_gender is not None:
            first_name = w1
            last_name = w2
            gender = w1_first_gender

        # Випадок 5: w2=ім'я, w1 невідоме → w1 вважаємо прізвищем
        elif w2_first_gender is not None:
            first_name = w2
            last_name = w1
            gender = w2_first_gender

        # Випадок 6: w1=прізвище, w2 невідоме → w2 може бути ім'ям
        elif w1_surname_gender is not None:
            first_name = w2
            last_name = w1
            gender = _guess_gender_by_first_name_heuristic(w2) or w1_surname_gender

        # Випадок 7: w2=прізвище, w1 невідоме → w1 може бути ім'ям
        elif w2_surname_gender is not None:
            first_name = w1
            last_name = w2
            gender = _guess_gender_by_first_name_heuristic(w1) or w2_surname_gender

        # Випадок 8: нічого не розпізнано → нікнейм
        else:
            return ParsedName(None, None, name, GENDER_UNKNOWN)

        if gender == GENDER_UNISEX or gender == GENDER_UNKNOWN:
            # Спробуємо ще раз визначити стать по імені евристично
            heuristic = _guess_gender_by_first_name_heuristic(first_name)
            if heuristic and heuristic != GENDER_UNISEX:
                gender = heuristic

        return ParsedName(first_name, last_name, None, gender)

    # ── Три і більше слів ──
    # Перші два аналізуємо як ім'я/прізвище, решту ігноруємо (можуть бути по батькові)
    result = parse_client_name(f"{parts[0]} {parts[1]}")

    # Якщо перші два слова розпізнано як ім'я/прізвище,
    # а третє схоже на по батькові — зберігаємо окремо (але не ставимо в middle_name)
    # Повертаємо як є — middle_name додаватиметься вручну
    if result.first_name or result.last_name:
        return result

    # Все незрозуміле → нікнейм
    return ParsedName(None, None, name, GENDER_UNKNOWN)


def _guess_gender_by_first_name_heuristic(name: Optional[str]) -> Optional[int]:
    """
    Евристичне визначення статі за закінченням імені,
    коли ім'я не знайдене в словнику.
    Працює для кирилиці та латиниці.
    """
    if not name:
        return None
    n = name.lower().strip()
    if len(n) < 2:
        return None

    # Кирилиця: жіночі закінчення
    if _is_cyrillic(n):
        if n.endswith(("а", "я", "і")):
            # Виключаємо типові чоловічі імена на -а: Микола, Ілля, Сава
            male_a_exceptions = {"микола", "ілля", "сава", "кузьма", "хома", "лука"}
            if n not in male_a_exceptions:
                return GENDER_FEMALE
        if n.endswith(("ій", "ій", "ей", "ій")):
            return GENDER_MALE
        if n.endswith("о"):  # Петро, Дмитро
            return GENDER_MALE
        return None

    # Латиниця: евристика
    if _is_latin(n):
        if n.endswith(("a", "ya", "ia", "na", "ina", "yna")):
            return GENDER_FEMALE
        if n.endswith(("y", "ii", "iy")):
            return GENDER_MALE
        return None

    return None


def guess_gender(first_name: Optional[str] = None,
                 last_name: Optional[str] = None) -> int:
    """
    Визначити стать за ім'ям та/або прізвищем.
    Пріоритет: ім'я > прізвище.
    """
    # За ім'ям (словник)
    if first_name:
        g = _is_known_first_name(first_name)
        if g is not None:
            return g
        # За ім'ям (евристика)
        g = _guess_gender_by_first_name_heuristic(first_name)
        if g is not None and g not in (GENDER_UNKNOWN, GENDER_UNISEX):
            return g

    # За прізвищем
    if last_name:
        g = _looks_like_surname(last_name)
        if g is not None and g not in (GENDER_UNKNOWN, GENDER_UNISEX):
            return g

    return GENDER_UNKNOWN
