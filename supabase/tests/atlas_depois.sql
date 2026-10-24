-- A foto dos objetos atlas_* tem de ser a mesma de antes das migrations.
do $$
declare
  antes text;
  depois text;
begin
  select foto into antes from public.atlas_fingerprint;
  depois := public._teste_atlas_foto();
  if antes is distinct from depois then
    raise exception E'FALHOU: objetos atlas_* mudaram\n--- antes ---\n%\n--- depois ---\n%', antes, depois;
  end if;
  if public.atlas_soma(2, 3) is distinct from 5 then
    raise exception 'FALHOU: atlas_soma deixou de responder';
  end if;
  if (select segredo from atlas_private.atlas_segredo where id = 1) is distinct from 'sigilo' then
    raise exception 'FALHOU: dado em atlas_private mudou';
  end if;
  raise notice 'ok  objetos atlas_* permanecem iguais depois das migrations';
end $$;
