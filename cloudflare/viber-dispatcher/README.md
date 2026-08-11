# BMS Viber dispatcher

Окремий Cloudflare Worker для Viber Channel Post API. Він приймає від BMS лише
готовий незмінний JPEG, thumbnail, підпис і час; Viber-токен зберігається тільки
в Cloudflare Secrets. Оригінали товарних фото Worker не отримує і не змінює.

## Що вже гарантує модуль

- один канал і один офіційний `picture`-пост на картку;
- D1-черга та Cron щохвилини — Mac може бути вимкнений;
- унікальний `idempotency_key`, тому повтор запиту не дублює пост;
- атомарне захоплення job, до 5 спроб із паузами та відновлення завислої job;
- послідовна відправка з паузою 1,1 с;
- webhook не журналює тіло запиту;
- API доступне лише за окремим Bearer-ключем BMS.

## Безпечне підключення (виконувати лише після окремого підтвердження)

1. Створити D1, скопіювати `wrangler.example.toml` у локальний
   `wrangler.toml` і вписати лише D1 ID.
2. Застосувати `schema.sql` до D1.
3. Додати через `wrangler secret put` три секрети:
   `VIBER_CHANNEL_TOKEN`, `VIBER_CHANNEL_SENDER_ID`, `BMS_DISPATCHER_KEY`.
4. Розгорнути Worker і викликати захищений `/v1/verify-account`. Відповідь має
   підтвердити, що sender є `superadmin` саме потрібного каналу.
5. Один раз встановити у Viber webhook на
   `https://<worker>/viber/webhook` через офіційний `set_webhook`.
6. Лише після dry-run додати URL і той самий dispatcher key до BMS як
   `VIBER_DISPATCHER_URL` та `VIBER_DISPATCHER_KEY`.
7. Виконати один погоджений тестовий пост, перевірити вигляд, і тільки тоді
   дозволити пакетні дії.

`wrangler.toml`, токени й реальні значення секретів не комітяться.
