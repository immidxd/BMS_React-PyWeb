# BMS automatic collection drafts

This Worker runs every five minutes and may create only `awaiting_review`
Top-9 snapshots in Neon. It has no social-platform tokens, R2 credentials,
publisher imports or publication endpoints. Viber/Facebook delivery remains a
separate manual action in BMS.

Required Cloudflare secret:

- `DATABASE_URL` — the existing BMS catalog Neon connection string.

Both platform schedules are disabled by default. Enabling one in BMS is an
explicit user action and is mirrored to Neon by the local draft sync service.

`tools/deploy.py` sends the Neon URL through Wrangler's temporary
`--secrets-file`, deletes that file in `finally`, and marks the ignored BMS
`.env` with `AUTO_COLLECTION_DRAFT_WORKER_URL` only after Wrangler reports a
successful deployment. This keeps the UI's “24/7” indicator honest.
