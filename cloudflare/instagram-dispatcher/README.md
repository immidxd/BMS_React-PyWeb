# BMS Instagram Dispatcher

Окремий Cloudflare Worker для офіційного Instagram Login та захищеної черги публікацій.

## Безпека й виконання

- Створює одноразовий OAuth `state` і приймає callback Instagram Business Login.
- Перевіряє точний username, шифрує Instagram User token у D1 ключем із Cloudflare Secret і автоматично оновлює його до завершення 60-денного строку.
- Приймає лише підписані Meta webhook-події.
- Валідує Feed/каруселі, Stories та Reels: 1–10 HTTPS JPEG/відео, підпис до 2200 символів, розклад до 365 днів.
- Зберігає idempotent job у D1, щохвилини створює/poll-ить Meta media containers і після `FINISHED` викликає `media_publish`.
- Має консервативний локальний ліміт 45/24 год, до 5 повторів, відновлення завислих job та cancel/reschedule до початку створення container.
- `INSTAGRAM_LIVE_ENABLED=false` фізично блокує створення job. Після успішного контрольного live-smoke production використовує `true`; Meta API не дозволяє BMS видалити вже опублікований media.

## Cloudflare bindings

- `DB` — D1 `bms-instagram-dispatcher`.
- `BMS_DISPATCHER_KEY` — авторизація BMS → Worker.
- `INSTAGRAM_APP_SECRET` — окремий Instagram App Secret, лише Cloudflare Secret.
- `TOKEN_ENCRYPTION_KEY` — 32 випадкові байти у hex для AES-GCM, лише Cloudflare Secret.
- `META_WEBHOOK_VERIFY_TOKEN` — випадковий verify token для Meta webhook.

Instagram App ID не є секретом і зберігається у `wrangler.toml`. Access token ніколи не повертається через API й не потрапляє у Git або журнал Worker.

## Маршрути

- `GET /health` — публічний health-check без конфіденційних даних.
- `GET /oauth/instagram/callback` — точний OAuth redirect URI для Instagram.
- `GET|POST /webhooks/instagram` — verification і підписані webhook-події.
- `GET /v1/status` — стан підключення, лише з Bearer-ключем BMS.
- `POST /v1/oauth/start` — одноразове посилання авторизації, лише з Bearer-ключем BMS.
- `GET /v1/account-check` — read-only перевірка токена, username, типу акаунта й publishing limit.
- `POST /v1/validate-draft` — валідація без публікації.
- `POST /v1/jobs` — створити idempotent job; працює лише з увімкненим feature flag.
- `GET /v1/jobs/:id` — стан job.
- `PATCH /v1/jobs/:id` — перенести job, якщо container ще не створюється.
- `DELETE /v1/jobs/:id` — скасувати job, якщо container ще не створюється.

## Розгортання

1. Скопіювати `wrangler.example.toml` у ignored `wrangler.toml`, внести реальний D1 ID і workers.dev subdomain.
2. Застосувати `schema.sql` до remote D1.
3. Додати secrets тільки через `wrangler secret put`.
4. У Meta → Instagram API → API setup with Instagram login внести `/oauth/instagram/callback` як exact redirect URI.
5. Додати лише `instagram_business_basic` і `instagram_business_content_publish`; Worker формує OAuth URL сам і не просить доступу до Direct/коментарів.
6. Провести OAuth із `INSTAGRAM_LIVE_ENABLED=false`, виконати `account-check` і лише після окремого фінального підтвердження ввімкнути живий режим та провести контрольний тест.

Не додавайте App Secret, access token чи dispatcher key у `.env.example`, Git, скриншоти або чат.
