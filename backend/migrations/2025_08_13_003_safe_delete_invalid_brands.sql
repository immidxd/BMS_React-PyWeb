-- 003: Safe delete invalid brand IDs 5..70 if unreferenced

delete from public.brands b
where b.id between 5 and 70
  and not exists (
    select 1 from public.products p where p.brandid = b.id
  );


