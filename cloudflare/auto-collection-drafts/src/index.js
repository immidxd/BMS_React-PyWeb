import { neon } from '@neondatabase/serverless';

export const WEIGHTS = Object.freeze({
  unique_viewers: 1,
  active_favorites: 3,
  favorite_adds: 4,
  contact_clicks: 8,
  sold_count: 12,
});

export function popularityScore(row) {
  return Object.entries(WEIGHTS).reduce(
    (total, [key, weight]) => total + Number(row[key] || 0) * weight,
    0,
  );
}

export function rankCandidates(rows, count) {
  const ranked = rows.map((row) => ({ ...row, popularity_score: popularityScore(row) }));
  ranked.sort((a, b) => (
    b.popularity_score - a.popularity_score
    || Number(b.unique_viewers || 0) - Number(a.unique_viewers || 0)
    || Number(b.active_favorites || 0) - Number(a.active_favorites || 0)
    || Number(b.sold_count || 0) - Number(a.sold_count || 0)
    || String(a.productnumber || '').localeCompare(String(b.productnumber || ''), 'uk')
    || Number(a.product_id || 0) - Number(b.product_id || 0)
  ));
  const take = Math.max(2, Math.min(Number(count || 9), 9));
  return {
    selected: ranked.slice(0, take).map((row, index) => ({ ...row, position: index + 1 })),
    reserves: ranked.slice(take, take * 2).map((row, index) => ({ ...row, reserve_position: index + 1 })),
  };
}

async function selectionKey(platform, periodDays, cooldownDays, selected) {
  const source = [
    platform,
    periodDays,
    cooldownDays,
    ...selected.map((row) => String(row.productnumber || '').trim().replace(/^#+/, '').toLocaleLowerCase('uk')),
  ].join('|');
  const bytes = new TextEncoder().encode(source);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 24);
}

async function dueConfigs(sql) {
  return sql`
    WITH candidate_slots AS (
      SELECT c.*,
             (
               date_trunc('week', now() AT TIME ZONE c.timezone)
               + c.weekday * interval '1 day'
               + (c.local_time - time '00:00')
             ) AT TIME ZONE c.timezone AS this_week_slot
      FROM auto_collection_configs c
      WHERE c.enabled IS TRUE AND c.enabled_at IS NOT NULL
    ), due AS (
      SELECT candidate_slots.*,
             CASE WHEN this_week_slot > now()
                  THEN this_week_slot - interval '7 days'
                  ELSE this_week_slot END AS scheduled_for
      FROM candidate_slots
    )
    SELECT platform, weekday, local_time, timezone, period_days,
           cooldown_days, item_count, enabled_at, scheduled_for
    FROM due
    WHERE scheduled_for >= enabled_at
      AND NOT EXISTS (
        SELECT 1 FROM auto_collection_drafts d
        WHERE d.platform=due.platform AND d.scheduled_for=due.scheduled_for
      )
    ORDER BY platform
  `;
}

async function candidateRows(sql, config) {
  const periodDays = Number(config.period_days);
  const cooldownDays = Number(config.cooldown_days);
  const scheduledFor = config.scheduled_for;
  return sql`
    WITH engagement AS (
      SELECT ce.productnumber,
             COUNT(*) FILTER (WHERE ce.event_type='product_view')::int AS views,
             COUNT(DISTINCT ce.visitor_key)
                 FILTER (WHERE ce.event_type='product_view')::int AS unique_viewers,
             COUNT(*) FILTER (WHERE ce.event_type='favorite_add')::int AS favorite_adds,
             COUNT(*) FILTER (WHERE ce.event_type='contact_click')::int AS contact_clicks
      FROM catalog_events ce
      WHERE ce.productnumber IS NOT NULL
        AND (${periodDays}=0 OR ce.received_at >= now() - make_interval(days => ${periodDays}))
      GROUP BY ce.productnumber
    ), favorites AS (
      SELECT productnumber, COUNT(*)::int AS active_favorites
      FROM catalog_favorite_state
      GROUP BY productnumber
    ), blocked AS (
      SELECT item.value AS productnumber
      FROM auto_collection_drafts d
      CROSS JOIN LATERAL jsonb_array_elements_text(d.product_numbers) item(value)
      WHERE d.status IN ('awaiting_review','approved')
        AND d.scheduled_for > now() - make_interval(days => ${cooldownDays})
        AND d.scheduled_for <> ${scheduledFor}::timestamptz
      UNION ALL
      SELECT productnumber
      FROM auto_collection_recent_posts
      WHERE occurred_at > now() - make_interval(days => ${cooldownDays})
        AND status NOT IN ('failed','error','cancelled')
    ), candidates AS (
      SELECT s.productnumber, s.product_id, s.brand, s.model, s.type,
             s.price::float8 AS price, s.dateadded, s.available,
             COALESCE(e.views,0)::int AS views,
             COALESCE(e.unique_viewers,0)::int AS unique_viewers,
             COALESCE(f.active_favorites,0)::int AS active_favorites,
             COALESCE(e.favorite_adds,0)::int AS favorite_adds,
             COALESCE(e.contact_clicks,0)::int AS contact_clicks,
             CASE ${periodDays}
               WHEN 7 THEN s.sold_7
               WHEN 30 THEN s.sold_30
               WHEN 90 THEN s.sold_90
               ELSE s.sold_all
             END::int AS sold_count
      FROM auto_collection_product_snapshot s
      JOIN catalog_listings cl
        ON cl.productnumber=s.productnumber AND cl.is_published IS TRUE
      LEFT JOIN engagement e ON e.productnumber=s.productnumber
      LEFT JOIN favorites f ON f.productnumber=s.productnumber
      LEFT JOIN LATERAL (
        SELECT p.official_photos_from
        FROM products p
        WHERE p.productnumber=s.productnumber
        ORDER BY p.id
        LIMIT 1
      ) photo_source ON TRUE
      WHERE s.available > 0
        AND NOT EXISTS (
          SELECT 1 FROM blocked b
          WHERE lower(ltrim(b.productnumber, '#'))=lower(ltrim(s.productnumber, '#'))
        )
        AND EXISTS (
          SELECT 1 FROM catalog_images ci
          WHERE position(lower(
            '/' || regexp_replace(
              COALESCE(NULLIF(photo_source.official_photos_from,''),s.productnumber),
              '^#+',''
            ) || '_'
          ) IN lower(ci.relpath)
          ) > 0
        )
    )
    SELECT * FROM candidates
    ORDER BY (
      unique_viewers * 1
      + active_favorites * 3
      + favorite_adds * 4
      + contact_clicks * 8
      + sold_count * 12
    ) DESC,
    unique_viewers DESC,
    active_favorites DESC,
    sold_count DESC,
    productnumber,
    product_id
    LIMIT 120
  `;
}

async function createDraft(sql, config) {
  const candidates = await candidateRows(sql, config);
  const ranked = rankCandidates(candidates, config.item_count);
  if (ranked.selected.length < 2) {
    throw new Error(`Знайдено лише ${ranked.selected.length} безпечних товарів із фото`);
  }
  const warnings = [];
  if (ranked.selected.length < Number(config.item_count)) {
    warnings.push(`Знайдено лише ${ranked.selected.length} із ${config.item_count} безпечних товарів із фото.`);
  }
  const key = await selectionKey(
    config.platform,
    config.period_days,
    config.cooldown_days,
    ranked.selected,
  );
  const productIds = ranked.selected.map((row) => Number(row.product_id));
  const productNumbers = ranked.selected.map((row) => row.productnumber);
  const policy = {
    count: Number(config.item_count),
    period_days: Number(config.period_days),
    cooldown_days: Number(config.cooldown_days),
    weights: WEIGHTS,
    requires_available_stock: true,
    requires_catalog_publication: true,
    requires_photo: true,
    revalidate_before_publish: true,
  };
  const audit = {
    eligible_pool: candidates.length,
    generated_at: new Date().toISOString(),
    selection_key: key,
    execution: 'cloudflare-neon-draft-only',
  };
  const inserted = await sql`
    INSERT INTO auto_collection_drafts (
      platform, source, status, scheduled_for, selection_key,
      product_ids, product_numbers, selected_json, reserves_json,
      warnings_json, policy_json, audit_json
    ) VALUES (
      ${config.platform}, 'scheduled', 'awaiting_review',
      ${config.scheduled_for}::timestamptz, ${key},
      ${JSON.stringify(productIds)}::jsonb,
      ${JSON.stringify(productNumbers)}::jsonb,
      ${JSON.stringify(ranked.selected)}::jsonb,
      ${JSON.stringify(ranked.reserves)}::jsonb,
      ${JSON.stringify(warnings)}::jsonb,
      ${JSON.stringify(policy)}::jsonb,
      ${JSON.stringify(audit)}::jsonb
    )
    ON CONFLICT (platform, scheduled_for) DO NOTHING
    RETURNING id
  `;
  if (inserted.length) {
    await sql`
      UPDATE auto_collection_configs
      SET last_generated_at=now(), last_error=NULL, last_error_at=NULL
      WHERE platform=${config.platform}
    `;
  }
  return { platform: config.platform, created: inserted.length === 1, count: productIds.length };
}

export async function runDraftCycle(env) {
  if (env.DRAFT_ONLY !== 'true') {
    throw new Error('Draft-only safety lock is not enabled');
  }
  if (!env.DATABASE_URL) {
    throw new Error('DATABASE_URL is not configured');
  }
  const sql = neon(env.DATABASE_URL, { fetchOptions: { signal: AbortSignal.timeout(20000) } });
  const configs = await dueConfigs(sql);
  const results = [];
  for (const config of configs) {
    try {
      results.push(await createDraft(sql, config));
    } catch (error) {
      const message = String(error?.message || error).slice(0, 2000);
      await sql`
        UPDATE auto_collection_configs
        SET last_error=${message}, last_error_at=now()
        WHERE platform=${config.platform}
      `;
      results.push({ platform: config.platform, created: false, error: message });
    }
  }
  return { ok: results.every((row) => !row.error), checked: configs.length, results };
}

async function health(env) {
  const base = {
    ok: true,
    service: 'bms-auto-collection-drafts',
    draft_only: env.DRAFT_ONLY === 'true',
    automatic_publishing: false,
    media_uploads: false,
  };
  if (!env.DATABASE_URL) return { ...base, ok: false, database: 'not_configured' };
  try {
    const sql = neon(env.DATABASE_URL, { fetchOptions: { signal: AbortSignal.timeout(8000) } });
    const [row] = await sql`
      SELECT COUNT(*)::int AS snapshot_products,
             MAX(synced_at) AS snapshot_at
      FROM auto_collection_product_snapshot
    `;
    return { ...base, database: 'connected', ...row };
  } catch (error) {
    return { ...base, ok: false, database: 'unavailable' };
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'GET' && (url.pathname === '/' || url.pathname === '/health')) {
      const result = await health(env);
      return Response.json(result, { status: result.ok ? 200 : 503 });
    }
    return Response.json({ error: 'not_found' }, { status: 404 });
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runDraftCycle(env));
  },
};
