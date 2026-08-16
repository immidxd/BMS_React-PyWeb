# BMS Facebook Dispatcher

Окремий Cloudflare Worker для офіційного Facebook Login і захищеної черги публікацій у Сторінку.

## Чим відрізняється від instagram-dispatcher

Meta — одна компанія, але Pages API — інший API, а не той самий з іншим хостом:

| | Instagram | Facebook Page |
|---|---|---|
| Вхід | Instagram Login (`api.instagram.com`) | Facebook Login (`graph.facebook.com/oauth`) |
| Токен | Instagram User token, 60 днів, `ig_refresh_token` | long-lived user token → **Page access token** із `/me/accounts` |
| Дозволи | `instagram_business_basic`, `instagram_business_content_publish` | `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` |
| Пост | media container → `media_publish` | `/photos` (одне фото) або непубліковані `/photos` + `/feed` з `attached_media` (альбом) |
| Story | container `media_type=STORIES` | непубліковане `/photos` → `/photo_stories` |
| Reel | container `media_type=REELS` | `/video_reels` start → `rupload.facebook.com` із заголовком `file_url` → finish |

Розклад тримає **Worker**, а не Meta. У Facebook є власний `scheduled_publish_time`,
але він створює допис у Сторінці одразу (unpublished), і скасувати його можна лише
`DELETE`. Ми лишили інстаграмівську семантику: доки не настав час, у Facebook не
існує нічого, тож cancel/reschedule — локальні та безпечні.

## Безпека й виконання

- Одноразовий OAuth `state`, callback Facebook Login.
- Обирає Сторінку строго за `EXPECTED_FB_PAGE`; кілька Сторінок без явного очікування — помилка, а не «візьмемо першу».
- Шифрує обидва токени в D1 ключем із Cloudflare Secret і перевидає Page token до завершення строку user token.
- Приймає лише підписані Meta webhook-події.
- Валідує Feed/альбоми, Stories та Reels: 1–10 HTTPS JPEG/відео, розклад до 365 днів.
- Idempotent job у D1, до 5 повторів, відновлення завислих job, cancel/reschedule до початку завантаження медіа.
- Консервативний локальний ліміт 30 публікацій / 24 год (за найсуворішим — Reels).
- `FACEBOOK_LIVE_ENABLED=false` фізично блокує створення job.

## Cloudflare bindings

- `DB` — D1 `bms-facebook-dispatcher`.
- `BMS_DISPATCHER_KEY` — авторизація BMS → Worker.
- `FACEBOOK_APP_SECRET` — лише Cloudflare Secret.
- `TOKEN_ENCRYPTION_KEY` — 32 випадкові байти у hex для AES-GCM, лише Cloudflare Secret.
- `META_WEBHOOK_VERIFY_TOKEN` — випадковий verify token для Meta webhook.

## Маршрути

- `GET /health` — публічний health-check.
- `GET /oauth/facebook/callback` — точний OAuth redirect URI.
- `GET|POST /webhooks/facebook` — verification і підписані події.
- `GET /v1/status` — стан підключення (Bearer-ключ BMS).
- `POST /v1/oauth/start` — одноразове посилання авторизації.
- `GET /v1/account-check` — read-only перевірка Сторінки й токена.
- `POST /v1/validate-draft` — валідація без публікації.
- `POST /v1/jobs` — створити idempotent job (лише з увімкненим feature flag).
- `GET|PATCH|DELETE /v1/jobs/:id` — стан, перенесення, скасування.

## Розгортання

1. Скопіювати `wrangler.example.toml` у ignored `wrangler.toml`, внести реальний D1 ID, subdomain і назву Сторінки.
2. Застосувати `schema.sql` до remote D1.
3. Додати secrets тільки через `wrangler secret put`.
4. У Meta App додати продукт **Facebook Login** (або Facebook Login for Business) і внести `/oauth/facebook/callback` як exact redirect URI.
5. Дозволи `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` — саме ці три й не більше. Для живої роботи поза dev-режимом потрібен App Review (Advanced Access).
6. Провести OAuth із `FACEBOOK_LIVE_ENABLED=false`, виконати `account-check` і лише після окремого підтвердження ввімкнути живий режим.

Не додавайте App Secret, access token чи dispatcher key у `.env.example`, Git, скриншоти або чат.
