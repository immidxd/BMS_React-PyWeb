-- Стилі зі словника Prom («Стиль» у категоріях взуття) → у довідник styles BMS,
-- щоб власник міг обирати їх у картці товару, а експорт на Prom мапив 1:1.
-- Наявні вже: Класичний, Повсякденний, Спортивний. Додаємо ті, яких немає.
insert into public.styles (stylename)
select v.stylename
from (values ('Діловий'), ('Молодіжний'), ('Святковий'), ('Етнічний')) as v(stylename)
where not exists (
    select 1 from public.styles s where lower(s.stylename) = lower(v.stylename)
);
