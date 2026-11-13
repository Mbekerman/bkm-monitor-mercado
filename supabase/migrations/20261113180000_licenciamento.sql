-- Processos de licenciamento de São Paulo e a conferência das unidades do Órulo.
-- Pedido do Moisés, 13/nov/2026.
--
-- Fonte: extratos semestrais do Aprova Digital (dados abertos da prefeitura, conjunto "ad").
-- São PROCESSOS PROTOCOLADOS, com as unidades pedidas, não alvarás emitidos: a base aberta de
-- alvarás aprovados foi descontinuada em 2019 e o site da SMUL recusa acesso automatizado.
-- Guardamos só os de edificação nova (os que trazem unidades). tools/licenciamento/processos.py
-- gera a carga; a rotina do monitor a refaz a cada extrato novo.
--
-- Conferência (regra do Moisés): Órulo acima do processo = número do Órulo provavelmente errado
-- (suspeito, sai do VSO); abaixo = pode ser só uma fase do projeto. Folga de 5% para os dois lados.
-- Só em São Paulo.
--
-- Como acha o processo: o lote fiscal que contém o ponto do empreendimento (ou o mais próximo a
-- até 30 m), consultado no GeoSampa (tools/licenciamento/lotes.py). Prédio pronto vira condomínio
-- (lote 0000) e o processo continua com os lotes antigos; por isso a busca vai à quadra:
--   1. com o lote exato num processo, valem os processos ligados a ele;
--   2. senão, os processos com lote na mesma quadra, autuados até a entrega do empreendimento.
-- Processos que dividem algum lote são o mesmo prédio (aprovação, execução, modificativo) e valem
-- pelo mais recente. Mais de um prédio candidato na quadra fica "ambiguo", sem escolher pelo número
-- de unidades (escolher pelo número tornaria a conferência circular).

create or replace function public._im_quadras(p_lotes text[])
returns text[] language sql immutable parallel safe
set search_path = '' as $$
  select coalesce(array_agg(distinct left(x, 6)), '{}') from unnest(p_lotes) x
$$;

create table public.im_licenciamento_processos (
  processo           text primary key,
  protocolo          text,
  relacionado        text,
  data_autuacao      date,
  assunto            text not null,
  endereco           text,
  categoria          text,
  subcategoria       text,
  his                boolean not null default false,
  hmp                boolean not null default false,
  r2v                boolean not null default false,
  social_no_assunto  boolean not null default false,
  modificativo       boolean not null default false,
  unidades           integer check (unidades >= 0),
  area_terreno       numeric,
  area_construir     numeric,
  blocos             integer,
  pavimentos         integer,
  lotes              text[] not null default '{}',   -- setor+quadra+lote, 10 dígitos, sem o dígito verificador
  origem             text,                           -- extrato de onde veio
  carregado_em       timestamptz not null default now()
);
create index im_lic_lotes on public.im_licenciamento_processos using gin (lotes);
create index im_lic_quadras on public.im_licenciamento_processos using gin (public._im_quadras(lotes));
comment on table public.im_licenciamento_processos is
  'Processos de edificação nova do Aprova Digital (SP), com as unidades pedidas. Não são alvarás emitidos.';

-- Lote fiscal do ponto de cada empreendimento (pelo número do Órulo), consultado no GeoSampa.
create table public.im_empreendimento_lotes (
  orulo_id      integer primary key,
  lote          text check (lote ~ '^\d{10}$'),     -- nulo: nenhum lote a até 30 m
  distancia_m   numeric,                           -- 0 = o ponto cai dentro do lote
  consultado_em timestamptz not null default now()
);
comment on table public.im_empreendimento_lotes is
  'Lote fiscal (GeoSampa) do ponto de cada empreendimento de São Paulo, pelo número do Órulo.';

-- Resultado da conferência, refeito por im_conferir_unidades().
create table public.im_conferencia_unidades (
  orulo_id          integer primary key,
  situacao          text not null check (situacao in ('conferido','suspeito','fase','sem_processo','ambiguo','sem_lote')),
  unidades_orulo    integer,
  unidades_processo integer,
  processo          text,            -- o mais recente do prédio
  processos         text[],          -- todos os do prédio (aprovação, execução, modificativos)
  data_processo     date,
  por_lote_exato    boolean,         -- true: o lote do ponto está no processo; false: achado pela quadra
  his               boolean,
  hmp               boolean,
  r2v               boolean,
  conferido_em      timestamptz not null default now()
);
comment on table public.im_conferencia_unidades is
  'Unidades do Órulo × processo de licenciamento (SP). suspeito = Órulo acima do processo + 5%; fase = abaixo − 5%.';

-- Unidades do Órulo: as do monitor quando o empreendimento é coletado, senão as do histórico.
create or replace function public._im_unidades_orulo(p_orulo integer)
returns integer language sql stable
set search_path = '' as $$
  select coalesce(
    (select e.unidades from public.im_ids_externos x join public.im_empreendimentos e on e.id = x.entidade_id
      where x.entidade = 'empreendimento' and x.fonte like 'orulo%' and x.id_externo = p_orulo::text
      order by e.unidades desc nulls last limit 1),
    (select round(h.unidades)::integer from public.im_historico_orulo_empreendimentos h where h.orulo_id = p_orulo))
$$;

-- Entrega prevista ou real, para não casar com processo de outro prédio autuado depois.
create or replace function public._im_entrega_orulo(p_orulo integer)
returns date language sql stable
set search_path = '' as $$
  select coalesce(
    (select max(e.previsao_entrega) from public.im_ids_externos x join public.im_empreendimentos e on e.id = x.entidade_id
      where x.entidade = 'empreendimento' and x.fonte like 'orulo%' and x.id_externo = p_orulo::text),
    (select h.entrega from public.im_historico_orulo_empreendimentos h where h.orulo_id = p_orulo))
$$;

create or replace function public.im_conferir_unidade(p_orulo integer)
returns void language plpgsql security definer
set search_path = '' as $$
declare
  v_lote text; v_exato boolean; v_n_predios int; v_proc record;
  v_uo integer := public._im_unidades_orulo(p_orulo);
  v_entrega date := public._im_entrega_orulo(p_orulo);
  v_sit text; v_procs text[];
begin
  delete from public.im_conferencia_unidades where orulo_id = p_orulo;
  if not exists (select 1 from public.im_empreendimento_lotes where orulo_id = p_orulo) then
    return;  -- lote não consultado (fora de São Paulo)
  end if;
  select lote into v_lote from public.im_empreendimento_lotes where orulo_id = p_orulo;
  if v_lote is null then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo) values (p_orulo, 'sem_lote', v_uo);
    return;
  end if;

  create temp table if not exists _im_cand (processo text primary key, lotes text[], data_autuacao date, unidades int,
    his boolean, hmp boolean, r2v boolean) on commit drop;
  truncate pg_temp._im_cand;
  v_exato := exists (select 1 from public.im_licenciamento_processos p
                      where p.lotes @> array[v_lote] and coalesce(p.unidades, 0) > 0);
  if v_exato then
    insert into pg_temp._im_cand
      select p.processo, p.lotes, p.data_autuacao, p.unidades, p.his, p.hmp, p.r2v
        from public.im_licenciamento_processos p
       where p.lotes && (select coalesce(array_agg(distinct l), '{}') from public.im_licenciamento_processos q, unnest(q.lotes) l
                          where q.lotes @> array[v_lote] and coalesce(q.unidades, 0) > 0)
         and coalesce(p.unidades, 0) > 0;
  else
    insert into pg_temp._im_cand
      select p.processo, p.lotes, p.data_autuacao, p.unidades, p.his, p.hmp, p.r2v
        from public.im_licenciamento_processos p
       where public._im_quadras(p.lotes) @> array[left(v_lote, 6)]
         and coalesce(p.unidades, 0) > 0
         and (v_entrega is null or p.data_autuacao is null or p.data_autuacao <= v_entrega);
  end if;

  -- prédios = grupos de processos ligados por lote em comum
  with recursive arestas as (
    select a.processo a, b.processo b from pg_temp._im_cand a join pg_temp._im_cand b on a.lotes && b.lotes),
  r (p, raiz) as (
    select processo, processo from pg_temp._im_cand
    union
    select arestas.b, r.raiz from r join arestas on arestas.a = r.p)
  select count(distinct raiz) into v_n_predios
    from (select p, min(raiz) raiz from r group by p) g;

  if v_n_predios = 0 then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo, por_lote_exato)
      values (p_orulo, 'sem_processo', v_uo, false);
    return;
  elsif v_n_predios > 1 then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo, processos, por_lote_exato)
      values (p_orulo, 'ambiguo', v_uo, (select array_agg(processo order by data_autuacao) from pg_temp._im_cand), v_exato);
    return;
  end if;

  select * into v_proc from pg_temp._im_cand order by data_autuacao desc nulls last, processo desc limit 1;
  select array_agg(processo order by data_autuacao) into v_procs from pg_temp._im_cand;
  v_sit := case
    when v_uo is null then 'sem_processo'
    when v_uo > v_proc.unidades * 1.05 then 'suspeito'
    when v_uo < v_proc.unidades * 0.95 then 'fase'
    else 'conferido' end;
  insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo, unidades_processo, processo, processos,
                                              data_processo, por_lote_exato, his, hmp, r2v)
    values (p_orulo, v_sit, v_uo, v_proc.unidades, v_proc.processo, v_procs, v_proc.data_autuacao, v_exato,
            v_proc.his, v_proc.hmp, v_proc.r2v);
end $$;

-- Refaz a conferência de todos os empreendimentos com lote consultado.
create or replace function public.im_conferir_unidades()
returns json language plpgsql security definer
set search_path = '' as $$
declare v_id integer;
begin
  for v_id in select orulo_id from public.im_empreendimento_lotes loop
    perform public.im_conferir_unidade(v_id);
  end loop;
  return (select json_object_agg(situacao, n) from
          (select situacao, count(*) n from public.im_conferencia_unidades group by situacao) s);
end $$;

alter table public.im_licenciamento_processos enable row level security;
alter table public.im_empreendimento_lotes enable row level security;
alter table public.im_conferencia_unidades enable row level security;
revoke all on table public.im_licenciamento_processos, public.im_empreendimento_lotes, public.im_conferencia_unidades
  from public, anon, authenticated;
grant select on table public.im_licenciamento_processos, public.im_empreendimento_lotes, public.im_conferencia_unidades
  to authenticated;
grant select, insert, update, delete on table public.im_licenciamento_processos, public.im_empreendimento_lotes,
  public.im_conferencia_unidades to service_role;
create policy im_leitura on public.im_licenciamento_processos for select to authenticated using (true);
create policy im_leitura on public.im_empreendimento_lotes for select to authenticated using (true);
create policy im_leitura on public.im_conferencia_unidades for select to authenticated using (true);

revoke all on function public._im_unidades_orulo(integer), public._im_entrega_orulo(integer),
  public.im_conferir_unidade(integer), public.im_conferir_unidades() from public, anon, authenticated;
grant execute on function public._im_unidades_orulo(integer), public._im_entrega_orulo(integer),
  public.im_conferir_unidade(integer), public.im_conferir_unidades() to service_role;
