# BMS Viber dispatcher

Окремий Cloudflare Worker для Viber Channel Post API. Він приймає від BMS лише
готовий незмінний JPEG, thumbnail, підпис і час; Viber-токен зберігається тільки
в Cloudflare Secrets. Оригінали товарних фото Worker не отримує і не змінює.

## Поточне production-підключення (2026-08-11)

- Cloudflare account ID: `f8bd7d39bafcc19a8d9e6f7a5b43f804`;
- Worker: `bms-viber-dispatcher`;
- URL: `https://bms-viber-dispatcher.vanya-malashenko-2002.workers.dev`;
- D1: `bms-viber-dispatcher`, ID
  `baa97ba4-882e-4491-968a-ebdffb7e1789`;
- Cron: щохвилини (`* * * * *`);
- D1-схема з `schema.sql` застосована;
- webhook прийнятий Viber зі статусом `0 / ok` і веде на
  `/viber/webhook` цього Worker;
- `VIBER_CHANNEL_TOKEN`, `VIBER_CHANNEL_SENDER_ID` і
  `BMS_DISPATCHER_KEY` існують лише як Cloudflare Secrets;
- sender перевірений через офіційний `get_account_info`: рівно один
  superadmin `Ivan`, його ID збігається з налаштованим sender;
- локальна BMS має лише `VIBER_DISPATCHER_URL` та
  `VIBER_DISPATCHER_KEY` у проігнорованому `.env`;
- повний dry-run картки `#Ф4329` успішний: JPEG 1080×1080 — 114720 байт,
  thumbnail — 19300 байт, зовнішніх записів не створено.

Живий тестовий пост не можна робити повторно «для перевірки». Його фактичний
результат і номер картки треба зафіксувати тут після одного погодженого запуску.

> Безпека: під час первинного підключення токен був вставлений у development-
> чат. У репозиторії та локальному `.env` його немає, але після першої перевірки
> його треба ротувати через `devsupport@viber.com`, а нове значення одразу
> перезаписати командою `wrangler secret put VIBER_CHANNEL_TOKEN`.

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
5. Один раз викликати захищений `POST /v1/configure-webhook`. Worker сам
   встановить у Viber точну адресу `https://<worker>/viber/webhook`, не
   виводячи й не передаючи токен назад у BMS.
6. Лише після dry-run додати URL і той самий dispatcher key до BMS як
   `VIBER_DISPATCHER_URL` та `VIBER_DISPATCHER_KEY`.
7. Виконати один погоджений тестовий пост, перевірити вигляд, і тільки тоді
   дозволити пакетні дії.

`wrangler.toml`, токени й реальні значення секретів не комітяться.

## Відновлення та перевірка

Wrangler потребує Node.js 22 або новіший. На робочому Mac перевірена версія —
`wrangler 4.120.1`. Локальний `wrangler.toml` створюється з
`wrangler.example.toml`, містить account/D1 ID вище й залишається ignored.

```bash
npx --yes wrangler@4.120.1 secret list
npx --yes wrangler@4.120.1 deploy
npx --yes wrangler@4.120.1 deployments status
```

`secret list` показує тільки назви, не значення. Після зміни секрету перевірити
`POST /v1/verify-account`, а після зміни домену — ще раз викликати захищений
`POST /v1/configure-webhook`. Backend читає URL/ключ під час запуску, тому вже
відкриту BMS після зміни `.env` потрібно один раз безпечно перезапустити.
