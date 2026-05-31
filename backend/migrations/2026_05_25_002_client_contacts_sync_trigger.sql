-- Двосторонній синк client_contacts ↔ clients.*:
--   1) Коли в client_contacts зʼявляється/міняється рядок з is_primary=TRUE,
--      дзеркалимо value/normalized у відповідну колонку clients.
--   2) Коли користувач напряму редагує clients.{phone_number|facebook|...},
--      upsert у client_contacts (is_primary=TRUE).
-- Це звільняє код від ручного синку у кожному PUT-ендпоінті.

-- ── 1) client_contacts → clients (primary view) ──────────────────────────
create or replace function public.sync_primary_to_client() returns trigger as $$
declare
    target_col text;
    norm_col   text;
begin
    if NEW.is_primary is not true then
        return NEW;
    end if;
    target_col := case NEW.kind
        when 'phone'     then 'phone_number'
        when 'facebook'  then 'facebook'
        when 'telegram'  then 'telegram'
        when 'instagram' then 'instagram'
        when 'email'     then 'email'
        when 'viber'     then 'viber'
        when 'olx'       then 'olx'
        when 'tiktok'    then 'tiktok'
        when 'messenger' then 'messenger'
        else null
    end;
    if target_col is null then return NEW; end if;
    execute format(
        'update clients set %I = $1 where id = $2 and (%I is distinct from $1)',
        target_col, target_col
    ) using NEW.value, NEW.client_id;

    norm_col := case NEW.kind
        when 'phone'     then 'phone_normalized'
        when 'facebook'  then 'facebook_normalized'
        when 'telegram'  then 'telegram_normalized'
        when 'instagram' then 'instagram_normalized'
        else null
    end;
    if norm_col is not null then
        execute format(
            'update clients set %I = $1 where id = $2 and (%I is distinct from $1)',
            norm_col, norm_col
        ) using NEW.normalized, NEW.client_id;
    end if;
    return NEW;
end;
$$ language plpgsql;

drop trigger if exists trg_client_contacts_sync_primary on public.client_contacts;
create trigger trg_client_contacts_sync_primary
    after insert or update on public.client_contacts
    for each row execute function public.sync_primary_to_client();

-- ── 2) clients → client_contacts (upsert on manual edit) ─────────────────
create or replace function public.sync_client_to_contacts() returns trigger as $$
declare
    rec record;
    kinds text[][] := ARRAY[
        ARRAY['phone',     'phone_number'],
        ARRAY['facebook',  'facebook'],
        ARRAY['telegram',  'telegram'],
        ARRAY['instagram', 'instagram'],
        ARRAY['email',     'email'],
        ARRAY['viber',     'viber'],
        ARRAY['olx',       'olx'],
        ARRAY['tiktok',    'tiktok'],
        ARRAY['messenger', 'messenger']
    ];
    k_name text;
    c_name text;
    old_val text;
    new_val text;
    i int;
begin
    for i in 1 .. array_length(kinds, 1) loop
        k_name := kinds[i][1];
        c_name := kinds[i][2];
        execute format('select ($1).%I, ($2).%I', c_name, c_name)
            into old_val, new_val using OLD, NEW;
        if new_val is distinct from old_val then
            if new_val is null or btrim(new_val) = '' then
                -- юзер очистив поле — гасимо primary, secondary лишаємо як історію
                update public.client_contacts
                   set is_primary = FALSE
                 where client_id = NEW.id and client_contacts.kind = k_name and is_primary;
            else
                -- upsert: спершу пробуємо UPDATE існуючого primary, інакше INSERT
                update public.client_contacts
                   set value = new_val,
                       last_seen_at = NOW(),
                       source = COALESCE(source, 'manual')
                 where client_id = NEW.id and client_contacts.kind = k_name and is_primary;
                if not found then
                    insert into public.client_contacts
                        (client_id, kind, value, normalized, is_primary, source)
                    values (NEW.id, k_name, new_val, lower(btrim(new_val)), TRUE, 'manual')
                    on conflict do nothing;
                end if;
            end if;
        end if;
    end loop;
    return NEW;
end;
$$ language plpgsql;

drop trigger if exists trg_clients_sync_to_contacts on public.clients;
create trigger trg_clients_sync_to_contacts
    after update of phone_number, facebook, telegram, instagram, email,
                    viber, olx, tiktok, messenger on public.clients
    for each row execute function public.sync_client_to_contacts();
