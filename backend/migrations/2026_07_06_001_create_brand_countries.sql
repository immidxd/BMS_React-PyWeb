-- Довідник «бренд → країна ВЛАСНИКА бренду» (для рядка «Бренд (Країна)» в описі Prom).
-- Раніше був словником у коді (prom_service.BRAND_COUNTRY) — виносимо в БД, щоб власник
-- міг сам правити/додавати без зміни коду. Політика: країна ТЕПЕРІШНЬОГО власника бренду
-- (напр. Fila → Південна Корея, Sorel → США — вже не походження). Спірні кейси власник
-- корегує тут вручну. Країни — українською (у рос-опис мапляться в prom_service._country).
create table if not exists public.brand_countries (
    brand      varchar(120) primary key,          -- ключ у НИЖНЬОМУ регістрі
    country    varchar(80) not null,
    updated_at timestamptz default now()
);
comment on table public.brand_countries is
    'Бренд → країна власника бренду (редаговане джерело для опису Prom). Ключ brand — lower-case.';

-- Сід (ON CONFLICT DO NOTHING — щоб повторний прогін НЕ затирав ручні правки власника).
insert into public.brand_countries (brand, country) values
    ('nike','США'),('adidas','Німеччина'),('reebok','США'),('puma','Німеччина'),
    ('new balance','США'),('asics','Японія'),('hoka','США'),('teva','США'),
    ('merrell','США'),('salomon','Франція'),('columbia','США'),('vans','США'),
    ('converse','США'),('timberland','США'),('crocs','США'),('skechers','США'),
    ('ecco','Данія'),('geox','Італія'),('clarks','Великобританія'),('caprice','Німеччина'),
    ('rieker','Німеччина'),('tamaris','Німеччина'),('gabor','Німеччина'),('lasocki','Польща'),
    ('gino rossi','Польща'),('badura','Польща'),('guess','США'),('tommy hilfiger','США'),
    ('calvin klein','США'),('karl lagerfeld','Німеччина'),('michael kors','США'),
    ('lacoste','Франція'),('fila','Південна Корея'),('kappa','Італія'),('champion','США'),
    ('ugg','США'),('birkenstock','Німеччина'),('dr. martens','Великобританія'),('keen','США'),
    ('jack wolfskin','Німеччина'),('under armour','США'),('mizuno','Японія'),('saucony','США'),
    ('brooks','США'),('diesel','Італія'),('gucci','Італія'),('versace','Італія'),
    ('emporio armani','Італія'),('liu jo','Італія'),('pinko','Італія'),('levis','США'),
    ('wrangler','США'),('mustang','Німеччина'),('s.oliver','Німеччина'),('bugatti','Німеччина'),
    ('marco tozzi','Німеччина'),('legero','Австрія'),('ara','Німеччина'),('remonte','Німеччина'),
    ('josef seibel','Німеччина'),('salamander','Німеччина'),('camper','Іспанія'),
    ('pikolinos','Іспанія'),('aldo','Канада'),('steve madden','США'),('palladium','Франція'),
    ('hey dude','США'),('keds','США'),('sorel','США'),('the north face','США'),
    ('cmp','Італія'),('helly hansen','Норвегія')
on conflict (brand) do nothing;
