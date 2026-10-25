-- =====================================================================
-- Monitor de mercado · NR (não residencial) e correção de andar/vagas
--
-- Aditiva: não edita a migration do monitor. Só objetos im_*.
--
-- NR. No Órulo o "NR" quase nunca vem no tipo da unidade (Apartamento,
-- Studio...). Vem no nome do empreendimento ("Fort Ipiranga - NR") ou na
-- torre da unidade ("TORRE A NR", "NÃO RESIDENCIAL"). Passa a valer:
--   im_e_nr(texto)              acha NR, N.R. ou "não residencial(is)" no texto;
--   im_empreendimentos.nr       nome do empreendimento é NR (coluna gerada);
--   im_unidades.studio_nr       agora quer dizer "unidade NR", de qualquer tipo
--                               residencial (o nome ficou pelo histórico): nome
--                               do empreendimento, torre, número ou tipo NR;
--   im_unidades.categoria       residencial | residencial_nr | nao_residencial.
-- residencial (boolean) continua sendo o que conta para o filtro e segue a
-- config nr_conta_como_residencial para as NR. O filtro do empreendimento
-- não muda: empreendimento "X - NR" com apartamento/studio continua entrando.
-- Um gatilho em im_unidades calcula studio_nr, residencial e categoria, então
-- vale também para carga de coletor antigo (que só manda nome e torre).
--
-- Andar/vagas. im_importar atualiza andar e vagas com coalesce(novo, antigo):
-- mandar null não limpa valor errado. im_corrigir_andar_vagas grava os
-- valores corrigidos (inclusive null) a partir do bruto, só para service_role.
-- =====================================================================

create or replace function public.im_e_nr(p text)
returns boolean
language sql immutable parallel safe set search_path = '' as $$
  select coalesce(lower(translate(p,
           'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
           'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN'))
         ~ '(^|[^a-z0-9])(nr|n\.r\.?|nao[ -]residencia(l|is))($|[^a-z0-9])', false)
$$;
comment on function public.im_e_nr(text) is 'Texto marca NR (não residencial): NR, N.R. ou "não residencial".';

alter table public.im_empreendimentos
  add column if not exists nr boolean generated always as (public.im_e_nr(nome)) stored;
comment on column public.im_empreendimentos.nr is 'Nome do empreendimento marca NR (não residencial).';

alter table public.im_unidades
  add column if not exists categoria text not null default 'nao_residencial'
  check (categoria in ('residencial', 'residencial_nr', 'nao_residencial'));
comment on column public.im_unidades.categoria is
  'residencial; residencial_nr (tipo residencial em empreendimento, torre, número ou tipo NR); nao_residencial (loja, sala, vaga, tipo desconhecido).';
comment on column public.im_unidades.studio_nr is
  'Unidade NR (não residencial), de qualquer tipo: nome do empreendimento, torre, número ou tipo NR. Nome histórico.';

-- Tipo residencial pelo cadastro. NR residencial obedece a config.
create or replace function public._im_residencial(p_codigo text, p_studio_nr boolean)
returns boolean
language plpgsql stable security definer set search_path = '' as $$
begin
  if p_codigo is null or p_codigo = 'nao_informado' then return false; end if;
  if not coalesce((select residencial from public.im_tipos_unidade where codigo = p_codigo and ativo), false) then
    return false;
  end if;
  if p_studio_nr then
    return coalesce((select nr_conta_como_residencial from public.im_config), true);
  end if;
  return true;
end $$;

create or replace function public._im_categoria(p_codigo text, p_nr boolean)
returns text
language plpgsql stable security definer set search_path = '' as $$
begin
  if p_codigo is null or p_codigo = 'nao_informado'
     or not coalesce((select residencial from public.im_tipos_unidade where codigo = p_codigo and ativo), false) then
    return 'nao_residencial';
  end if;
  return case when p_nr then 'residencial_nr' else 'residencial' end;
end $$;

create or replace function public._im_unidade_nr()
returns trigger
language plpgsql security definer set search_path = '' as $$
declare v_nr boolean;
begin
  v_nr := coalesce((select e.nr from public.im_empreendimentos e where e.id = new.empreendimento_id), false)
          or public.im_e_nr(new.torre) or public.im_e_nr(new.numero) or public.im_e_nr(new.tipo_fonte)
          or public.im_e_nr(new.tipo_unidade);
  new.studio_nr := v_nr;
  new.residencial := public._im_residencial(new.tipo_unidade, v_nr);
  new.categoria := public._im_categoria(new.tipo_unidade, v_nr);
  return new;
end $$;

drop trigger if exists im_unidades_nr on public.im_unidades;
create trigger im_unidades_nr
  before insert or update of empreendimento_id, torre, numero, tipo_unidade, tipo_fonte, studio_nr, residencial, categoria
  on public.im_unidades
  for each row execute function public._im_unidade_nr();

-- Nome do empreendimento mudou de NR para não NR (ou o contrário): recalcula as unidades.
create or replace function public._im_empreendimento_nr()
returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.nr is distinct from old.nr then
    update public.im_unidades set studio_nr = studio_nr where empreendimento_id = new.id;
  end if;
  return null;
end $$;

drop trigger if exists im_empreendimentos_nr on public.im_empreendimentos;
create trigger im_empreendimentos_nr
  after update of nome on public.im_empreendimentos
  for each row execute function public._im_empreendimento_nr();

-- Config e tipos mudam residencial e categoria: o gatilho recalcula os dois.
create or replace function public._im_recalcular_residencial()
returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.im_unidades u
     set residencial = residencial
   where u.residencial is distinct from public._im_residencial(u.tipo_unidade, u.studio_nr)
      or u.categoria is distinct from public._im_categoria(u.tipo_unidade, u.studio_nr);
end $$;

-- Andar e vagas corrigidos a partir do bruto. p_dados: [{"e": id do
-- empreendimento na fonte, "t": torre, "n": número, "a": andar, "v": vagas}].
-- Casa a unidade como im_importar (im_norm_rotulo de torre e número) e grava
-- só o que difere, inclusive null. Não mexe em mais nada.
create or replace function public.im_corrigir_andar_vagas(p_dados jsonb, p_fonte text default 'orulo_login')
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare v_rec int; v_upd int;
begin
  if jsonb_typeof(p_dados) is distinct from 'array' then
    raise exception 'p_dados deve ser uma lista' using errcode = '22023';
  end if;
  with d as (
    select btrim(x->>'e') as e, public.im_norm_rotulo(x->>'t') as tn, public.im_norm_rotulo(x->>'n') as nn,
           nullif(x->>'a', '')::smallint as a, nullif(x->>'v', '')::smallint as v, o
      from jsonb_array_elements(p_dados) with ordinality as j(x, o)
  ), dd as (
    select distinct on (e, tn, nn) e, tn, nn, a, v from d order by e, tn, nn, o desc
  ), alvo as (
    select u.id, dd.a, dd.v
      from dd
      join public.im_ids_externos i
        on i.fonte = p_fonte and i.entidade = 'empreendimento' and i.id_externo = dd.e
      join public.im_unidades u
        on u.empreendimento_id = i.entidade_id and u.torre_norm = dd.tn and u.numero_norm = dd.nn
     where u.andar is distinct from dd.a or u.vagas is distinct from dd.v
  ), upd as (
    update public.im_unidades u set andar = alvo.a, vagas = alvo.v
      from alvo where u.id = alvo.id
    returning 1
  )
  select (select count(*) from d), (select count(*) from upd) into v_rec, v_upd;
  return jsonb_build_object('recebidas', v_rec, 'atualizadas', v_upd);
end $$;

-- Backfill das unidades existentes (o gatilho calcula). Centenas de milhares de linhas.
-- set_config local vale dentro deste bloco: o runner de teste aplica o arquivo
-- fora de uma transação, e SET LOCAL solto só é válido dentro de uma.
do $backfill$
begin
  perform set_config('statement_timeout', '15min', true);
  update public.im_unidades set studio_nr = studio_nr;
end
$backfill$;

-- Loft e triplex são residenciais. Produção já tem as linhas
-- (im_salvar_tipo_unidade) e as unidades já foram reclassificadas: conflito
-- não altera nada. _im_semear passa a semeá-los num banco criado do zero.
insert into public.im_tipos_unidade (codigo, residencial)
values ('loft', true), ('triplex', true)
on conflict (codigo) do nothing;

create or replace function public._im_semear()
returns void
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.im_config (unico, municipios)
  select true, coalesce((
    select array_agg(id order by codigo_ibge)
      from public.im_municipios
     where codigo_ibge in ('3550308','3549904')), '{}')
  where not exists (select 1 from public.im_config);

  insert into public.im_tipos_unidade (codigo, residencial)
  select t.codigo, t.residencial
  from (values
    ('apartamento', true), ('studio', true), ('cobertura', true), ('garden', true), ('duplex', true),
    ('loft', true), ('triplex', true),
    ('loja', false), ('sala', false), ('laje', false), ('vaga', false), ('deposito', false)
  ) as t(codigo, residencial)
  where not exists (select 1 from public.im_tipos_unidade x where x.codigo = t.codigo);
end $$;

revoke all on function public.im_e_nr(text) from public, anon;
grant execute on function public.im_e_nr(text) to authenticated, service_role;

revoke all on function public.im_corrigir_andar_vagas(jsonb, text) from public, anon, authenticated;
grant execute on function public.im_corrigir_andar_vagas(jsonb, text) to service_role;

revoke all on function
  public._im_residencial(text, boolean),
  public._im_categoria(text, boolean),
  public._im_unidade_nr(),
  public._im_empreendimento_nr(),
  public._im_recalcular_residencial(),
  public._im_semear()
from public, anon, authenticated, service_role;
