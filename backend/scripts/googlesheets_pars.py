import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import psycopg2
import logging
import time
import re
import subprocess
import sys
from datetime import datetime
import pycountry
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

GOOGLE_SHEETS_JSON_KEY = os.getenv("GOOGLE_SHEETS_JSON_KEY")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_SHEETS_CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, GOOGLE_SHEETS_JSON_KEY)
SPREADSHEET_NAME = os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME")


def get_google_sheet_client():
   """Повертає авторизований клієнт Google Sheets."""
   try:
       creds = ServiceAccountCredentials.from_json_keyfile_name(
           GOOGLE_SHEETS_CREDENTIALS_FILE,
           ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
       )
       return gspread.authorize(creds)
   except Exception as e:
       logger.error(f"Помилка підключення до Google Sheets: {e}")
       return None


def connect_to_db():
   """Повертає з'єднання з PostgreSQL або None, якщо виникла помилка."""
   try:
       return psycopg2.connect(
           host=DB_HOST,
           port=DB_PORT,
           database=DB_NAME,
           user=DB_USER,
           password=DB_PASSWORD
       )
   except psycopg2.Error as e:
       logger.error(f"Помилка підключення до бази даних: {e}")
       return None


def validate_text(value, max_length=None):
   if value is None:
       return None
   text = str(value).strip()
   if max_length and len(text) > max_length:
       text = text[:max_length]
   return text or None


def validate_integer(value):
   try:
       return int(float(str(value).strip()))
   except (ValueError, TypeError):
       return None


def validate_decimal(value):
   try:
       return float(str(value).replace(',', '.').strip())
   except (ValueError, TypeError):
       return None


def get_country_code_by_name(country_name):
   """Повертає двобуквенний ISO-код країни (кидає LookupError, якщо не знайдено)."""
   country = pycountry.countries.search_fuzzy(country_name)[0]
   return country.alpha_2


def is_valid_country_name(country_name):
   try:
       pycountry.countries.search_fuzzy(country_name)
       return True
   except LookupError:
       return False


def parse_sheet_name(sheet_name):
   """
   Якщо аркуш «Валізи(Андрій)» – фіксовано дата 01.01.2024,
   інакше шукаємо дату дд.мм.рррр у назві та решту як ім'я.
   """
   if sheet_name.strip() == "Валізи(Андрій)":
       fixed_date = datetime.strptime("01.01.2024", "%d.%m.%Y").date()
       match = re.search(r'\((.*?)\)', sheet_name)
       if match:
           deliv_name = match.group(1).strip()
       else:
           deliv_name = sheet_name
       return fixed_date, deliv_name

   date_match = re.search(r'\d{1,2}\.\d{1,2}\.\d{2,4}', sheet_name)
   if date_match:
       date_str = date_match.group(0)
       if len(date_str.split('.')[-1]) == 2:
           date_format = '%d.%m.%y'
       else:
           date_format = '%d.%m.%Y'
       try:
           delivery_date = datetime.strptime(date_str, date_format).date()
       except ValueError:
           logger.warning(f"Невірний формат дати в: {sheet_name}")
           delivery_date = None
       sheet_name_without_date = sheet_name.replace(date_str, '').strip()
   else:
       logger.warning(f"Дата не знайдена у '{sheet_name}'")
       delivery_date = None
       sheet_name_without_date = sheet_name

   name_match = re.search(r'\((.*?)\)', sheet_name_without_date)
   if name_match:
       deliv_name = name_match.group(1).strip()
   else:
       deliv_name = sheet_name_without_date

   return delivery_date, deliv_name


def get_or_create_import(cursor, import_name, import_date, conn):
   if not import_name or not import_date:
       return None
   cursor.execute(
       "SELECT id FROM imports WHERE importname=%s AND importdate=%s",
       (import_name, import_date)
   )
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute("""
           INSERT INTO imports (importname, importdate)
           VALUES (%s, %s) RETURNING id
       """, (import_name, import_date))
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_delivery(cursor, delivery_name, delivery_date, conn):
   if not delivery_name or not delivery_date:
       return None
   cursor.execute(
       "SELECT id FROM deliveries WHERE deliveryname=%s AND deliverydate=%s",
       (delivery_name, delivery_date)
   )
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute("""
           INSERT INTO deliveries (deliveryname, deliverydate)
           VALUES (%s,%s) RETURNING id
       """, (delivery_name, delivery_date))
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_type(cursor, type_name, conn):
   if not type_name:
       return None
   cursor.execute("SELECT id FROM types WHERE typename=%s", (type_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute("INSERT INTO types (typename) VALUES (%s) RETURNING id", (type_name,))
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_subtype(cursor, subtype_name, conn):
   if not subtype_name:
       return None
   cursor.execute("SELECT id FROM subtypes WHERE subtypename=%s", (subtype_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute(
           "INSERT INTO subtypes (subtypename) VALUES (%s) RETURNING id",
           (subtype_name,)
       )
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_brand(cursor, brand_name, conn):
   if not brand_name:
       return None
   cursor.execute("SELECT id FROM brands WHERE brandname=%s", (brand_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute("INSERT INTO brands (brandname) VALUES (%s) RETURNING id", (brand_name,))
       conn.commit()
       return cursor.fetchone()[0]


def validate_gender(gender_value):
    """
    Валідує значення гендера, повертає тільки правильні гендери.
    Якщо значення некоректне або відсутнє - повертає 'Невідомо'.
    """
    if not gender_value:
        return 'Невідомо'
    
    gender_str = str(gender_value).strip().lower()
    
    # Дозволені гендери (в нижньому регістрі для порівняння)
    valid_genders = {
        'жіноча': 'жіноча',
        'женская': 'жіноча', 
        'женский': 'жіноча',
        'female': 'жіноча',
        'woman': 'жіноча',
        'women': 'жіноча',
        'w': 'жіноча',
        'f': 'жіноча',
        
        'чоловіча': 'чоловіча',
        'мужская': 'чоловіча',
        'мужской': 'чоловіча', 
        'male': 'чоловіча',
        'man': 'чоловіча',
        'men': 'чоловіча',
        'm': 'чоловіча',
        
        'унісекс': 'унісекс',
        'unisex': 'унісекс',
        'универсальный': 'унісекс',
        'универсальная': 'унісекс',
        'universal': 'унісекс',
        'u': 'унісекс',
        
        'невідомо': 'Невідомо',
        'неизвестно': 'Невідомо',
        'unknown': 'Невідомо',
        'undefined': 'Невідомо',
        'none': 'Невідомо',
        'null': 'Невідомо',
        '': 'Невідомо'
    }
    
    # Повертаємо правильне значення гендера або 'Невідомо' за замовчуванням
    return valid_genders.get(gender_str, 'Невідомо')


def get_or_create_gender(cursor, gender_name, conn):
     """Створює або отримує ID гендера. Завжди повертає валідний ID."""
     # Валідуємо гендер (тепер завжди повертає валідне значення)
     validated_gender = validate_gender(gender_name)
     
     # Маппування назв гендерів на конкретні ID (0-3)
     gender_id_mapping = {
         'Невідомо': 0,
         'унісекс': 1,
         'жіноча': 2,
         'чоловіча': 3
     }
     
     # Повертаємо конкретний ID без створення нових записів
     gender_id = gender_id_mapping.get(validated_gender, 0)  # За замовчуванням "Невідомо"
     
     # Перевіряємо чи існує запис (для логування)
     cursor.execute("SELECT id FROM genders WHERE id=%s", (gender_id,))
   r = cursor.fetchone()
     if not r:
         # Це не повинно відбуватись, але додаємо для безпеки
         logger.warning(f"Гендер ID {gender_id} ({validated_gender}) не знайдено в БД!")
         return 0  # Повертаємо "Невідомо"
     
     return gender_id


def get_or_create_color(cursor, color_name, conn):
   if not color_name:
       return None
   cursor.execute("SELECT id FROM colors WHERE colorname=%s", (color_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute(
           "INSERT INTO colors (colorname) VALUES (%s) RETURNING id",
           (color_name,)
       )
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_country(cursor, country_name, conn):
   if not country_name or not is_valid_country_name(country_name):
       # Вважаємо Unknown / ZZ
       cursor.execute("SELECT id FROM countries WHERE countrycode='ZZ'")
       r = cursor.fetchone()
       if r:
           return r[0]
       else:
           try:
               cursor.execute("""
                   INSERT INTO countries (countryname,countrycode)
                   VALUES (%s,%s) RETURNING id
               """, ('Unknown', 'ZZ'))
               conn.commit()
               return cursor.fetchone()[0]
           except psycopg2.IntegrityError:
               conn.rollback()
               cursor.execute("SELECT id FROM countries WHERE countrycode='ZZ'")
               return cursor.fetchone()[0]
   else:
       name = validate_text(country_name, 100)
       try:
           cc = get_country_code_by_name(name)
       except:
           logger.warning(f"Некоректна країна: {name}")
           return get_or_create_country(cursor, None, conn)
       cursor.execute("SELECT id FROM countries WHERE countryname=%s", (name,))
       r = cursor.fetchone()
       if r:
           return r[0]
       else:
           cursor.execute("""
               INSERT INTO countries (countryname,countrycode)
               VALUES (%s,%s) RETURNING id
           """, (name, cc))
           conn.commit()
           return cursor.fetchone()[0]


def get_or_create_status(cursor, status_name, conn):
   if not status_name:
       return None
   st_name = status_name.strip().capitalize()
   cursor.execute("SELECT id FROM statuses WHERE LOWER(statusname)=LOWER(%s)", (st_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute("INSERT INTO statuses (statusname) VALUES (%s) RETURNING id", (st_name,))
       conn.commit()
       return cursor.fetchone()[0]


def get_or_create_condition(cursor, condition_name, conn):
   if not condition_name:
       return None
   c_name = condition_name.strip().capitalize()
   cursor.execute("SELECT id FROM conditions WHERE LOWER(conditionname)=LOWER(%s)", (c_name,))
   r = cursor.fetchone()
   if r:
       return r[0]
   else:
       cursor.execute(
           "INSERT INTO conditions (conditionname) VALUES (%s) RETURNING id",
           (c_name,)
       )
       conn.commit()
       return cursor.fetchone()[0]


def sanitize_product_number(num):
   if not num:
       return num
   return re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁіІїЇєЄґҐ\-\.\(\)/_#]+", "", num).strip()


def fully_identical_for_merge(tp1, tp2, st1, st2, br1, br2, gd1, gd2, cl1, cl2,
                            md1, md2, mk1, mk2, yr1, yr2, ds1, ds2, sz1, sz2, ms1, ms2):
   def strify(x):
       return x.strip().lower() if x else ''

   tp1 = tp1 or 0
   tp2 = tp2 or 0
   st1 = st1 or 0
   st2 = st2 or 0
   br1 = br1 or 0
   br2 = br2 or 0
   gd1 = gd1 or 0
   gd2 = gd2 or 0
   cl1 = cl1 or 0
   cl2 = cl2 or 0
   yr1 = yr1 or 0
   yr2 = yr2 or 0

   md1 = strify(md1)
   md2 = strify(md2)
   mk1 = strify(mk1)
   mk2 = strify(mk2)
   ds1 = strify(ds1)
   ds2 = strify(ds2)
   sz1 = strify(sz1)
   sz2 = strify(sz2)
   ms1 = strify(ms1)
   ms2 = strify(ms2)

   if tp1 != tp2:
       return False
   if st1 != st2:
       return False
   if br1 != br2:
       return False
   if gd1 != gd2:
       return False
   if cl1 != cl2:
       return False
   if md1 != md2:
       return False
   if mk1 != mk2:
       return False
   if yr1 != yr2:
       return False
   if ds1 != ds2:
       return False

   if sz1 and sz2 and sz1 != sz2:
       return False
   if ms1 and ms2 and ms1 != ms2:
       return False

   return True


def same_item_check(cursor, p_data):
   pn = p_data['productnumber']

   tp = p_data['typeid'] or 0
   st = p_data['subtypeid'] or 0
   br = p_data['brandid'] or 0
   gd = p_data['genderid'] or 0
   cl = p_data['colorid'] or 0
   md = (p_data['model'] or "").lower()
   mk = (p_data['marking'] or "").lower()
   yr = p_data['year'] or 0
   ds = (p_data['description'] or "").lower()
   sz = (p_data['sizeeu'] or "").lower()
   ms = (p_data['measurementscm'] or "").lower()

   cursor.execute("""
       SELECT
         id,
         COALESCE(typeid,0),
         COALESCE(subtypeid,0),
         COALESCE(brandid,0),
         COALESCE(genderid,0),
         COALESCE(colorid,0),
         LOWER(COALESCE(model,'')),
         LOWER(COALESCE(marking,'')),
         COALESCE(year,0),
         LOWER(COALESCE(description,'')),
         LOWER(COALESCE(sizeeu,'')),
         LOWER(COALESCE(measurementscm,''))
       FROM products
       WHERE productnumber=%s
   """, (pn,))
   rows = cursor.fetchall()
   if not rows:
       return False

   for row in rows:
       (rid, rtp, rst, rbr, rgd, rcl,
        rmd, rmk, ryr, rds, rsz, rms) = row
       if fully_identical_for_merge(
           tp, rtp, st, rst, br, rbr, gd, rgd, cl, rcl,
           md, rmd, mk, rmk, yr, ryr, ds, rds,
           sz, rsz, ms, rms
       ):
           return True
   return False


def migrate_add_quantity_column(conn):
   with conn.cursor() as cur:
       cur.execute("""
           SELECT column_name
           FROM information_schema.columns
           WHERE table_name='products' AND column_name='quantity'
       """)
       exists = cur.fetchone()
       if not exists:
           logger.info("Додаємо колонку quantity у таблицю products...")
           cur.execute("ALTER TABLE products ADD COLUMN quantity integer NOT NULL DEFAULT 1")
           conn.commit()
       else:
           logger.debug("Колонка 'quantity' уже існує, пропускаємо.")


def remove_old_suffix_duplicates(conn):
   """
   Тепер видаляємо записи, де productnumber відповідає шаблону:
     - будь-які пробіли, потім відкрита дужка,
     - всередині — будь-які символи (крім закриваючої дужки), у т.ч. цифри, літери, пробіли,
     - закрита дужка, потім ідуть лише пробіли/кінець рядка.
   НО! Якщо товар використовується у order_details, ми не видаляємо, щоби не лягла цілісність.
   """

   with conn.cursor() as cur:
       logger.info("Шукаємо товари з дужками у productnumber ...")

       # 1) Вибираємо ID всіх таких товарів
       #    (не робимо DELETE відразу, щоб перевірити зв'язки)
       cur.execute("""
           SELECT p.id
           FROM products p
           WHERE p.productnumber ~ E'\\(\\s*[^)]+\\s*\\)\\s*$'
       """)
       candidate_ids = [row[0] for row in cur.fetchall()]

       if not candidate_ids:
           logger.info("Немає жодного кандидата з суфіксом (..).")
           return

       logger.info(f"Знайдено {len(candidate_ids)} товар(ів), які закінчуються на (..). Перевіряємо зв'язки ...")

       # 2) Перевіряємо, чи використовується товар у order_details
       #    Якщо так - пропускаємо видалення
       to_delete = []
       for pid in candidate_ids:
           # Перевірка
           cur.execute("SELECT COUNT(*) FROM order_details WHERE product_id=%s", (pid,))
           ref_count = cur.fetchone()[0]
           if ref_count == 0:
               to_delete.append(pid)

       if not to_delete:
           logger.info("Усі знайдені товари з (..) використовуються у замовленнях => не видаляємо їх.")
           return

       logger.info(f"Видаляємо {len(to_delete)} товар(ів), що не використовуються ...")
       cur.execute("""
           DELETE FROM products
           WHERE id = ANY(%s)
       """, (to_delete,))
       del_count = cur.rowcount
       logger.info(f"Вилучено {del_count} товарів з '(..)' (не використовувались).")
       conn.commit()


def is_rostovka(existing_row, new_data):
   rid, rpn, rbrand, rtype, rsubtype, rmodel, rmarking = existing_row

   nb = (new_data.get('_b_name') or "").strip().lower()
   nt = (new_data.get('_t_name') or "").strip().lower()
   nst = (new_data.get('_st_name') or "").strip().lower()
   nmodel = (new_data.get('model') or "").strip().lower()
   nmark = (new_data.get('marking') or "").strip().lower()

   sim_count = 0
   if rbrand and nb and rbrand.lower() == nb:
       sim_count += 1
   if rtype and nt and rtype.lower() == nt:
       sim_count += 1
   if rsubtype and nst and rsubtype.lower() == nst:
       sim_count += 1
   if rmodel and nmodel and rmodel.lower() == nmodel:
       sim_count += 1
   if rmarking and nmark and rmarking.lower() == nmark:
       sim_count += 1

   return (sim_count >= 3)


def find_or_update_rostovka_product(conn, productnumber, p_data):
   with conn.cursor() as cur:
       cur.execute("""
           SELECT
               p.id,
               p.productnumber,
               b.brandname,
               t.typename,
               st.subtypename,
               p.model,
               p.marking
           FROM products p
           LEFT JOIN brands b ON p.brandid=b.id
           LEFT JOIN types t ON p.typeid=t.id
           LEFT JOIN subtypes st ON p.subtypeid=st.id
           WHERE p.productnumber=%s
       """,(productnumber,))
       rows = cur.fetchall()
       if not rows:
           return None

       for row in rows:
           if is_rostovka(row, p_data):
               rid = row[0]
               cur.execute("""
                   UPDATE products
                      SET quantity=quantity+1,
                          updated_at=now()
                    WHERE id=%s
                   RETURNING quantity
               """,(rid,))
               new_q = cur.fetchone()[0]
               conn.commit()
               logger.info(f"[Ростовка] {productnumber} => товар id={rid}, quantity={new_q}")
               return rid
       return None


def insert_or_update_product(cursor, p_data, conn):
   pnum = p_data['productnumber']

   rost_id = find_or_update_rostovka_product(conn, pnum, p_data)
   if rost_id:
       return

   cursor.execute("SELECT id FROM products WHERE productnumber=%s", (pnum,))
   exist = cursor.fetchall()
   if exist:
       if not same_item_check(cursor, p_data):
           base = re.sub(r"\(\d+\)$", "", pnum).strip()
           sfx = 1
           while True:
               newn = f"{base}({sfx})"
               cursor.execute("SELECT id FROM products WHERE productnumber=%s", (newn,))
               if cursor.fetchone():
                   sfx += 1
               else:
                   p_data['productnumber'] = newn
                   break

   final_pn = p_data['productnumber']
   cursor.execute("SELECT id FROM products WHERE productnumber=%s", (final_pn,))
   row = cursor.fetchone()
   if row:
       sets = ', '.join([f"{k}=%s" for k in p_data if k != 'productnumber'])
       vals = [p_data[k] for k in p_data if k != 'productnumber']
       q = f"UPDATE products SET {sets} WHERE productnumber=%s"
       cursor.execute(q, vals + [final_pn])
       conn.commit()
   else:
       cols = ', '.join(p_data.keys())
       pls = ', '.join(['%s'] * len(p_data))
       q = f"INSERT INTO products ({cols}) VALUES ({pls})"
       vals = tuple(p_data.values())
       cursor.execute(q, vals)
       conn.commit()


def merge_similar_products(conn):
   with conn.cursor() as cursor:
       cursor.execute("""
           SELECT
               id,
               productnumber,
               COALESCE(typeid,0),
               COALESCE(subtypeid,0),
               COALESCE(brandid,0),
               COALESCE(genderid,0),
               COALESCE(colorid,0),
               LOWER(COALESCE(model,'')),
               LOWER(COALESCE(marking,'')),
               COALESCE(year,0),
               LOWER(COALESCE(description,'')),
               LOWER(COALESCE(sizeeu,'')),
               LOWER(COALESCE(measurementscm,'')),
               dateadded
           FROM products
       """)
       rows = cursor.fetchall()

       from collections import defaultdict
       groups = defaultdict(list)
       for r in rows:
           rid, rpn, rtp, rst, rbr, rgd, rcl, rmd, rmk, ryr, rds, rsz, rms, rdate = r
           basepn = re.sub(r"\(\d+\)$", "", rpn).strip()
           groups[basepn].append(r)

       def are_same(i1, i2):
           (_, pn1, tp1, st1, b1, g1, c1, md1, mk1, yr1, ds1, sz1, ms1, _) = i1
           (_, pn2, tp2, st2, b2, g2, c2, md2, mk2, yr2, ds2, sz2, ms2, __) = i2
           return fully_identical_for_merge(
               tp1, tp2, st1, st2, b1, b2, g1, g2, c1, c2,
               md1, md2, mk1, mk2, yr1, yr2, ds1, ds2,
               sz1, sz2, ms1, ms2
           )

       for base, items in groups.items():
           if len(items) < 2:
               it = items[0]
               _id, _pn = it[0], it[1]
               new_ = re.sub(r"\(\d+\)$", "", _pn).strip()
               if new_ != _pn:
                   logger.info(f"[merge] Один => {_pn} => {new_}")
                   cursor.execute("UPDATE products SET productnumber=%s WHERE id=%s", (new_, _id))
               continue

           no_sfx = [xx for xx in items if not re.search(r"\(\d+\)$", xx[1])]
           if no_sfx:
               main_it = no_sfx[0]
           else:
               sfx_sorted = sorted(
                   items,
                   key=lambda x: int(re.search(r"\((\d+)\)$", x[1]).group(1))
                   if re.search(r"\((\d+)\)$", x[1])
                   else 9999
               )
               main_it = sfx_sorted[0]

           mid, mpn = main_it[0], main_it[1]
           for x2 in items:
               if x2[0] == mid:
                   continue
               if are_same(main_it, x2):
                   logger.info(f"[merge] дубль {x2[1]} => {mpn}")
                   cursor.execute("DELETE FROM products WHERE id=%s", (x2[0],))

           new_m = re.sub(r"\(\d+\)$", "", mpn).strip()
           if new_m != mpn:
               logger.info(f"[merge] Головн. {mpn} => {new_m}")
               cursor.execute("UPDATE products SET productnumber=%s WHERE id=%s", (new_m, mid))

   conn.commit()


def rename_different_products_in_date_order(conn):
   with conn.cursor() as cursor:
       cursor.execute("""
           SELECT id, productnumber, dateadded
           FROM products
           ORDER BY productnumber
       """)
       rows = cursor.fetchall()

       from collections import defaultdict
       groups = defaultdict(list)
       for r in rows:
           rid, rpn, rdt = r
           basepn = re.sub(r"\(\d+\)$", "", rpn).strip()
           groups[basepn].append((rid, rpn, rdt))

       for base, items in groups.items():
           if len(items) < 2:
               if len(items) == 1:
                   only_id, only_pn, only_dt = items[0]
                   corr = re.sub(r"\(\d+\)$", "", only_pn).strip()
                   if corr != only_pn:
                       logger.info(f"[rename] один => {only_pn} => {corr}")
                       cursor.execute("UPDATE products SET productnumber=%s WHERE id=%s", (corr, only_id))
               continue

           sorted_by_date = sorted(items, key=lambda x: x[2] or datetime(1970, 1, 1))
           n = len(sorted_by_date)
           for i in range(n):
               rid, rpnum, rdt = sorted_by_date[i]
               if i == (n - 1):
                   new_pn = base
               else:
                   new_pn = f"{base}({i + 1})"

               if new_pn != rpnum:
                   logger.info(f"[rename] {rpnum} => {new_pn} (date={rdt})")
                   cursor.execute("UPDATE products SET productnumber=%s WHERE id=%s", (new_pn, rid))

   conn.commit()


def process_sheet_data(data, wtitle, all_product_numbers):
   """Обробка даних з аркуша."""
   logger.info(f"=== Початок обробки аркуша: {wtitle} ===")
   conn = connect_to_db()
   if not conn:
       logger.error(f"Аркуш '{wtitle}': помилка підключення до бази даних")
       return

   cursor = conn.cursor()

   # Рахуємо загальну кількість рядків
   total_rows = len(data)
   logger.info(f"Аркуш '{wtitle}': всього рядків для обробки: {total_rows}")

    # Перевіряємо, чи це основний аркуш "Data" з товарами
    is_data_sheet = (wtitle == "Data")

   # Аналізуємо назву аркуша для отримання дати і назви доставки
   try:
       delivery_date, deliv_name = parse_sheet_name(wtitle)
       if delivery_date:
           logger.info(f"Аркуш '{wtitle}': дата доставки: {delivery_date}, назва: {deliv_name}")
       else:
            if not is_data_sheet:
           logger.warning(f"Аркуш '{wtitle}': не вдалося розпізнати дату з назви")
   except Exception as e:
       logger.error(f"Аркуш '{wtitle}': помилка парсингу назви: {e}")
       delivery_date, deliv_name = None, wtitle

   # Визначаємо ім'я імпорту (наприклад, 'June 2023')
   import_name = delivery_date.strftime("%B %Y") if delivery_date else None
   import_date = delivery_date

   # Якщо можемо, створюємо запис імпорту та доставки
   imp_id = get_or_create_import(cursor, import_name, import_date, conn) if import_name and import_date else None
   deliv_id = get_or_create_delivery(cursor, deliv_name, delivery_date, conn) if deliv_name and delivery_date else None
   logger.info(f"Аркуш '{wtitle}': ID імпорту: {imp_id}, ID доставки: {deliv_id}")

   # Створюємо список для зберігання даних рядків
   rows_data = []
    valid_products = 0

   # Проходимося по кожному рядку даних, починаючи з 1 рядка (0-й - заголовки)
   for row_index, rowvals in enumerate(data[1:], 1):
       try:
           logger.debug(f"Аркуш '{wtitle}': обробка рядка {row_index} з {total_rows-1}")
           
           # Прогрес обробки
            if row_index % 500 == 0 or row_index == 1 or row_index == total_rows-1:
               progress_percent = int((row_index / (total_rows-1)) * 100)
               logger.info(f"Аркуш '{wtitle}': прогрес обробки {progress_percent}% ({row_index}/{total_rows-1})")

            # Отримуємо номер товару залежно від типу аркуша
            if is_data_sheet:
                # Для аркуша "Data" номер в колонці 13 (індекс 12)
                p_num_ = str(rowvals[12] if len(rowvals) > 12 else '').strip()
                # Додаткові дані для "Data"
                brand = validate_text(rowvals[0] if len(rowvals) > 0 else '')
                clones = validate_text(rowvals[1] if len(rowvals) > 1 else '')
                model = validate_text(rowvals[2] if len(rowvals) > 2 else '')
                marking = validate_text(rowvals[3] if len(rowvals) > 3 else '')
                year = validate_integer(rowvals[4] if len(rowvals) > 4 else '')
                gender = validate_text(rowvals[5] if len(rowvals) > 5 else '')
                color = validate_text(rowvals[6] if len(rowvals) > 6 else '')
                country_producer = validate_text(rowvals[7] if len(rowvals) > 7 else '')
                country_owner = validate_text(rowvals[8] if len(rowvals) > 8 else '')
                size = validate_text(rowvals[9] if len(rowvals) > 9 else '')
                size_cm = validate_text(rowvals[10] if len(rowvals) > 10 else '')
                price = validate_decimal(rowvals[11] if len(rowvals) > 11 else '')
                type_name = validate_text(rowvals[13] if len(rowvals) > 13 else '')
                subtype_name = validate_text(rowvals[14] if len(rowvals) > 14 else '')
                description = None  # Для аркуша Data опис може бути в іншій колонці або відсутній
            else:
                # Для аркушів доставок номер в колонці 1 (індекс 0)
                p_num_ = str(rowvals[0] if len(rowvals) > 0 else '').strip()
                # Розширені дані для аркушів доставок відповідно до структури
                clones = validate_text(rowvals[1] if len(rowvals) > 1 else '')
                type_name = validate_text(rowvals[2] if len(rowvals) > 2 else '')
                subtype_name = validate_text(rowvals[3] if len(rowvals) > 3 else '')
                brand = validate_text(rowvals[4] if len(rowvals) > 4 else '')
                model = validate_text(rowvals[5] if len(rowvals) > 5 else '')
                marking = validate_text(rowvals[6] if len(rowvals) > 6 else '')
                year = validate_integer(rowvals[7] if len(rowvals) > 7 else '')
                gender = validate_text(rowvals[8] if len(rowvals) > 8 else '')
                color = validate_text(rowvals[9] if len(rowvals) > 9 else '')
                # Опис в колонці 11 (індекс 10)
                description = validate_text(rowvals[10] if len(rowvals) > 10 else '')
                country_producer = validate_text(rowvals[11] if len(rowvals) > 11 else '')
                country_owner = validate_text(rowvals[12] if len(rowvals) > 12 else '')
                size = validate_text(rowvals[13] if len(rowvals) > 13 else '')
                size_cm = validate_text(rowvals[14] if len(rowvals) > 14 else '')
                price = validate_decimal(rowvals[15] if len(rowvals) > 15 else '')

            # Очищаємо номер товару
            product_number = sanitize_product_number(p_num_) if p_num_ else '#'
           all_product_numbers.add(product_number)

            # Пропускаємо порожні рядки
            if not product_number or product_number == '#':
               logger.debug(f"Аркуш '{wtitle}': рядок {row_index} пропущено - порожній номер товару")
               continue

            # Створюємо словник даних товару
            product_data = {
                'productnumber': product_number,
                'clonednumbers': clones,
                'model': model,
                'marking': marking,
                'year': year,
                'price': price,
                'sizeeu': size,
                'measurementscm': size_cm,
                'description': description if 'description' in locals() else None,
                'created_at': 'now()',
                'updated_at': 'now()',
                'dateadded': import_date or 'now()',
                'statusid': 2  # Непродано
            }
            
            # Отримуємо або створюємо зв'язані об'єкти
            if brand:
                brand_id = get_or_create_brand(cursor, brand, conn)
                product_data['brandid'] = brand_id
                product_data['_b_name'] = brand
            else:
                # Якщо бренд не вказано, можна створити default або залишити None
                # default_brand_id = get_or_create_brand(cursor, 'Невизначено', conn)
                # product_data['brandid'] = default_brand_id
                pass
            
            if type_name:
                type_id = get_or_create_type(cursor, type_name, conn)
                product_data['typeid'] = type_id
                product_data['_t_name'] = type_name
            else:
                # Якщо тип не вказано, створюємо default тип
                default_type_id = get_or_create_type(cursor, 'Невизначено', conn)
                product_data['typeid'] = default_type_id
                
            if subtype_name:
                subtype_id = get_or_create_subtype(cursor, subtype_name, conn)
                product_data['subtypeid'] = subtype_id
                product_data['_st_name'] = subtype_name
                
            # Обробляємо гендер з валідацією (завжди отримуємо валідний ID)
            gender_id = get_or_create_gender(cursor, gender, conn)
            product_data['genderid'] = gender_id
            
            # Логуємо якщо було передане значення, але воно стало "Невідомо"
            if gender and validate_gender(gender) == 'Невідомо':
                logger.debug(f"Аркуш '{wtitle}': рядок {row_index} невалідний гендер '{gender}' → 'Невідомо'")
                
            if color:
                color_id = get_or_create_color(cursor, color, conn)
                product_data['colorid'] = color_id
                
            if country_producer:
                country_id = get_or_create_country(cursor, country_producer, conn)
                product_data['countryid'] = country_id

            # Додаємо до списку для обробки
            rows_data.append(product_data)
            valid_products += 1

       except Exception as e:
           logger.error(f"Аркуш '{wtitle}': рядок {row_index} помилка обробки: {e}")
           logger.error(f"Дані рядка: {rowvals}")
           conn.rollback()
           continue

   logger.info(f"Аркуш '{wtitle}': зібрано {len(rows_data)} валідних товарів")

    # Проходимо по зібраних даних і створюємо/оновлюємо товари
   processed_items = 0
   for item in rows_data:
       try:
            # Видаляємо тимчасові поля перед збереженням
            clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
            
            # Створюємо або оновлюємо товар
            insert_or_update_product(cursor, clean_item, conn)
           processed_items += 1
            
            if processed_items % 100 == 0 or processed_items == 1 or processed_items == len(rows_data):
               progress_percent = int((processed_items / len(rows_data)) * 100)
               logger.info(f"Аркуш '{wtitle}': обробка товарів {progress_percent}% ({processed_items}/{len(rows_data)})")

       except Exception as e:
           logger.error(f"Аркуш '{wtitle}': помилка оновлення товару '{item['productnumber']}': {e}")
           conn.rollback()
           continue

    logger.info(f"=== Завершено обробку аркуша '{wtitle}': створено/оновлено {processed_items} товарів ===")
   cursor.close()
   conn.close()


def merge_similar_products_and_rename():
   conn = connect_to_db()
   if not conn:
       logger.error("Не вдалося підключитися для merge/rename")
       return

   try:
       merge_similar_products(conn)
       rename_different_products_in_date_order(conn)
   except Exception as e:
       logger.error(f"Помилка merge/rename: {e}")
       conn.rollback()
   finally:
       conn.close()


def import_data():
   """
   1) Додаємо колонку quantity (якщо нема).
   2) Видаляємо (м'яко) товари з суфіксом "...( )", які НЕ використовуються у order_details.
   3) Читаємо всі аркуші, парсимо (process_sheet_data).
   4) Видаляємо товари, яких немає в табличках + '#' з малою кількістю полів.
   5) merge_similar_products_and_rename()
   6) Запускаємо orders_pars.py
   """
   logger.info("=== ПОЧАТОК ОНОВЛЕННЯ ТОВАРІВ ===")
   
   conn_mig = connect_to_db()
   if not conn_mig:
       logger.error("Не вдалося підключитися для міграції quantity")
       return
   try:
       logger.info("Перевірка та додавання колонки quantity...")
       migrate_add_quantity_column(conn_mig)
       conn_mig.close()
       logger.info("Міграція quantity завершена")
   except Exception as e:
       logger.error(f"Помилка міграції (quantity): {e}")
       conn_mig.close()
       return

   conn_rm = connect_to_db()
   if not conn_rm:
       logger.error("Не вдалося підключитися для видалення суфіксів")
       return
   try:
       logger.info("Видалення застарілих дублікатів товарів з суфіксами...")
       remove_old_suffix_duplicates(conn_rm)
       conn_rm.close()
       logger.info("Видалення застарілих дублікатів завершено")
   except Exception as e:
       logger.error(f"Помилка видалення старих суфіксів (n): {e}")
       conn_rm.close()
       return

   client = get_google_sheet_client()
   if not client:
       logger.error("Не вдалося отримати Google Sheets client")
       return

   logger.info(f"Документ: {SPREADSHEET_NAME}")
   try:
       doc = client.open(SPREADSHEET_NAME)
   except Exception as e:
       logger.error(f"Помилка відкриття Google Sheets: {e}")
       return

   ignore_sheets = ['Suppliers', 'Publications', 'New']
   sheet_list = doc.worksheets()
   all_product_numbers = set()
   
   # Розділяємо аркуші на поставки (з датами) та довідковий "Data"
   delivery_sheets = []
   data_sheet = None
   
   for ws in sheet_list:
       if ws.title in ignore_sheets:
           continue
       elif ws.title == 'Data':
           data_sheet = ws
       else:
           delivery_sheets.append(ws)
   
   # Спочатку обробляємо аркуші поставок
   total_sheets = len(delivery_sheets) + (1 if data_sheet else 0)
   processed_sheets = 0
   
   logger.info(f"Отримано {len(delivery_sheets)} аркушів поставок та {'1 довідковий аркуш' if data_sheet else '0 довідкових аркушів'}")

   # КРОК 1: Обробляємо аркуші поставок (з датами)
   logger.info("=== ОБРОБКА АРКУШІВ ПОСТАВОК ===")
   for ws in delivery_sheets:
       wtitle = ws.title
       processed_sheets += 1
       progress_percent = int((processed_sheets / total_sheets) * 100)
       
       logger.info(f"Обробка поставки: {wtitle} ({processed_sheets}/{total_sheets}, {progress_percent}%)")
       try:
           logger.info(f"Отримання даних з аркуша {wtitle}...")
           data = ws.get_all_values()
           logger.info(f"Отримано {len(data)} рядків з аркуша {wtitle}")
       except Exception as e:
           logger.error(f"Помилка get_all_values {wtitle}: {e}")
           continue

       process_sheet_data(data, wtitle, all_product_numbers)
       time.sleep(1)

   # КРОК 2: Обробляємо довідковий аркуш "Data" в кінці
   if data_sheet:
       logger.info("=== ОБРОБКА ДОВІДКОВОГО АРКУША DATA ===")
       wtitle = data_sheet.title
       processed_sheets += 1
       progress_percent = int((processed_sheets / total_sheets) * 100)

       logger.info(f"Обробка довідника: {wtitle} ({processed_sheets}/{total_sheets}, {progress_percent}%)")
       try:
           logger.info(f"Отримання даних з довідкового аркуша {wtitle}...")
           data = data_sheet.get_all_values()
           logger.info(f"Отримано {len(data)} рядків з довідкового аркуша {wtitle}")
           process_sheet_data(data, wtitle, all_product_numbers)
       except Exception as e:
           logger.error(f"Помилка обробки довідкового аркуша {wtitle}: {e}")
       time.sleep(1)

   if all_product_numbers:
       total_products = len(all_product_numbers)
       logger.info(f"Всього оброблено {total_products} унікальних товарів")
       
       logger.info("Видалення товарів, які відсутні в таблицях...")
       conn_del = connect_to_db()
       if not conn_del:
           logger.error("Не вдалося підключитися для видалення зайвих")
           return
       cur = conn_del.cursor()
       try:
           # Видаляємо товари, які не знайдені в жодному з аркушів
           cur.execute("""
               DELETE FROM products
               WHERE productnumber NOT IN %s
           """, (tuple(all_product_numbers),))
           deleted_count = cur.rowcount
           conn_del.commit()
           logger.info(f"Видалено {deleted_count} товарів, які відсутні в таблицях")

           # Видаляємо порожні товари (без основних атрибутів)
           cur.execute("""
               DELETE FROM products
               WHERE (productnumber IS NULL OR productnumber='#')
                 AND (
                   (CASE WHEN brandid IS NOT NULL THEN 1 ELSE 0 END)
                 + (CASE WHEN model IS NOT NULL AND model<>'' THEN 1 ELSE 0 END)
                 + (CASE WHEN marking IS NOT NULL AND marking<>'' THEN 1 ELSE 0 END)
                 + (CASE WHEN description IS NOT NULL AND description<>'' THEN 1 ELSE 0 END)
                 )<2
           """)
           deleted_empty_count = cur.rowcount
           conn_del.commit()
           logger.info(f"Видалено {deleted_empty_count} порожніх товарів з номером '#'")

           cur.close()
           conn_del.close()

           logger.info("Об'єднання схожих товарів...")
           merge_similar_products_and_rename()
           logger.info("Об'єднання товарів завершено")

           # Призначаємо гендер "Невідомо" (ID=0) для товарів без гендера (резервна перевірка)
           logger.info("Призначення гендера 'Невідомо' для товарів без гендера...")
           cur.execute("UPDATE products SET genderid = 0 WHERE genderid IS NULL")
           unknown_assigned = cur.rowcount
           conn_del.commit()
           if unknown_assigned > 0:
               logger.info(f"Призначено гендер 'Невідомо' (ID=0) для {unknown_assigned} товарів")
           else:
               logger.info("Всі товари вже мають призначений гендер")

       except Exception as e:
           logger.error(f"Помилка видалення товарів: {e}")
           conn_del.rollback()
           cur.close()
           conn_del.close()
   else:
       logger.warning("Жодного товару не зчитано (all_product_numbers пустий).")

   logger.info("=== ОНОВЛЕННЯ ТОВАРІВ ЗАВЕРШЕНО ===")
   logger.info("Запускаємо orders_pars.py...")
   try:
       script_path_orders = os.path.join(SCRIPT_DIR, 'orders_pars.py')
       result = subprocess.run(
           [sys.executable, script_path_orders],
           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
       )
       if result.returncode != 0:
           logger.error(f"Помилка виконання orders_pars.py: {result.stderr}")
       else:
           logger.info("orders_pars.py успішно виконано.")
   except Exception as err:
       logger.error(f"Не вдалося виконати orders_pars.py: {err}")


if __name__ == '__main__':
   import_data()