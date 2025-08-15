-- 002: Ensure products.brandid exists and FK to brands

-- Column already exists (brandid). Add FK if missing
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints tc
    where tc.table_schema = 'public'
      and tc.table_name = 'products'
      and tc.constraint_type = 'FOREIGN KEY'
      and tc.constraint_name = 'fk_products_brand'
  ) then
    alter table public.products
      add constraint fk_products_brand
      foreign key (brandid) references public.brands(id)
      on update cascade on delete restrict;
  end if;
end $$;

-- Optional future hardening step (uncomment after backfill is done):
-- alter table public.products alter column brandid set not null;


