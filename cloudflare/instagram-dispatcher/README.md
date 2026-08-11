# BMS Instagram Dispatcher

Окремий Cloudflare Worker для OAuth Meta та майбутньої захищеної черги Instagram.

## Поточний безпечний етап

- Створює одноразовий OAuth `state` і приймає callback Facebook Login for Business.
- Знаходить пов’язаний професійний Instagram-акаунт і шифрує Page token у D1 ключем із Cloudflare Secret.
- Приймає лише підписані Meta webhook-події.
- Валідує майбутні чернетки: 1–10 HTTPS JPEG/відео, підпис до 2200 символів, розклад до 365 днів.
- **Не має publish/job endpoint**, тому цей етап фізично не може створити Instagram-пост.

Перший живий endpoint додається окремо лише після підключення професійного тестового акаунта та явного підтвердження користувача. Meta API не дозволяє BMS безпечно видалити тестовий опублікований media.

## Cloudflare bindings

- `DB` — D1 `bms-instagram-dispatcher`.
- `BMS_DISPATCHER_KEY` — авторизація BMS → Worker.
- `META_APP_SECRET` — App Secret застосунку Meta, лише Cloudflare Secret.
- `TOKEN_ENCRYPTION_KEY` — 32 випадкові байти у hex для AES-GCM, лише Cloudflare Secret.
- `META_LOGIN_CONFIG_ID` — configuration ID Facebook Login for Business.
- `META_WEBHOOK_VERIFY_TOKEN` — випадковий verify token для Meta webhook.

App ID не є секретом і зберігається у `wrangler.toml`. Access token ніколи не повертається через API й не потрапляє у Git або журнал Worker.

## Маршрути

- `GET /health` — публічний health-check без конфіденційних даних.
- `GET /oauth/callback` — точний OAuth redirect URI для Meta.
- `GET|POST /webhooks/instagram` — verification і підписані webhook-події.
- `GET /v1/status` — стан підключення, лише з Bearer-ключем BMS.
- `POST /v1/oauth/start` — одноразове посилання авторизації, лише з Bearer-ключем BMS.
- `POST /v1/validate-draft` — валідація без публікації.

## Розгортання

1. Скопіювати `wrangler.example.toml` у ignored `wrangler.toml`, внести реальний D1 ID і workers.dev subdomain.
2. Застосувати `schema.sql` до remote D1.
3. Додати secrets тільки через `wrangler secret put`.
4. Розгорнути Worker і внести `/oauth/callback` як exact valid OAuth redirect URI в Meta.
5. Створити Facebook Login for Business configuration і додати її ID як secret.

Не додавайте App Secret, access token чи dispatcher key у `.env.example`, Git, скриншоти або чат.
