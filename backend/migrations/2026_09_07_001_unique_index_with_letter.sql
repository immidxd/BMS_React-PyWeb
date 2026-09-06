-- Унікальність товару мусить враховувати БУКВЕНИЙ розмір.
--
-- Індекс uix_products_num_size_color був побудований на
--     (productnumber, COALESCE(sizeeu,''), COALESCE(colorid,0))
-- і для взуття це правильно: розмір там числовий. Для одягу `sizeeu` порожній,
-- розмір живе в `size_letter` — тож два рядки журналу під одним номером, XL і M,
-- стикались на цьому індексі. Парсер ловив IntegrityError, відкочував savepoint
-- і замість другого рядка піднімав `quantity` наявному. Так #Ф4384 (Karl
-- Lagerfeld, завіз 05.09.2026) став «XL ×2», а M зник.
--
-- РОЗШИРЕННЯ ІНДЕКСУ БЕЗПЕЧНЕ: додавання колонки до унікального ключа може лише
-- ДОЗВОЛИТИ рядки, які раніше відхилялись, і не може відхилити жоден наявний —
-- усі вони вже задовольняють вужчий ключ, отже задовольняють і ширший.

DROP INDEX IF EXISTS uix_products_num_size_color;

CREATE UNIQUE INDEX IF NOT EXISTS uix_products_num_size_color_letter
    ON products (
        productnumber,
        COALESCE(sizeeu, ''::character varying),
        COALESCE(size_letter, ''::character varying),
        COALESCE(colorid, 0)
    );
