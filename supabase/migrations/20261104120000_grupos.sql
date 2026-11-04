-- =====================================================================
-- Resumo do empreendimento: grupos de unidades parecidas, estáveis.
-- Regra do Moisés (4/nov/2026). Só leitura do que foi coletado: nada em
-- im_unidades, im_unidade_estados ou im_vendas muda.
--
-- 1. Semelhança: mesmos dormitórios, a mesma quantidade de vagas (com e sem
--    vaga nunca se misturam) e a mesma marca de programa social do Órulo
--    (a do empreendimento).
-- 2. Área: começa pela área mais frequente (planta-tipo; no empate, a mais
--    perto da mediana de todas, depois a menor) como mediana, junta as
--    unidades a até ±area_mediana_pct dela, recalcula a mediana e repete até
--    o grupo parar de mudar; tira o grupo e recomeça com as que sobraram.
-- 3. R$/m² pelo preco_primeiro (o primeiro preço visto, que não muda):
--    no mesmo andar, salto acima de salto_preco_mesmo_andar_pct divide o andar
--    em faixas; as faixas, ordenadas pelo R$/m² típico (mediana), abrem grupo
--    novo num salto acima de salto_preco_andar_pct; duas faixas do mesmo andar
--    nunca ficam no mesmo grupo.
-- 4. Sem preço: grupo do mesmo andar no mesmo bloco de área (o maior), senão
--    o maior do bloco. Área nula fica fora. Grupo com menos de
--    min_unidades_grupo unidades só junta a um grupo que difere dele apenas na
--    área (outro bloco de área, R$/m² a até salto_preco_andar_pct), o de área
--    mais próxima; sem nenhum, fica separado. Nos dois casos fica pequeno.
-- 5. Estabilidade: unidade já agrupada não muda de grupo. Unidade nova entra
--    no grupo aberto que a comporta (mesma semelhança, área a até
--    ±area_mediana_pct da mediana do grupo e R$/m² dentro da faixa do grupo
--    alargada por salto_preco_andar_pct); as que nenhum comporta passam pela
--    regra só entre elas. im_reagrupar encerra os grupos e refaz tudo; a
--    ligação antiga fica com desde/ate, e a venda guarda o grupo da época
--    (o vigente na data da coleta que a gravou).
--
-- Tabelas novas: im_mercado_config, im_grupos, im_unidade_grupos (RLS como
-- as outras: select para authenticated, escrita só pelas funções).
-- Funções: im_agrupar, im_reagrupar, im_salvar_mercado_config (service_role)
-- e _im_agrupar_emp (interna).
-- =====================================================================

create table public.im_mercado_config (
  unico                       boolean primary key default true check (unico),
  area_mediana_pct            numeric not null default 3 check (area_mediana_pct > 0 and area_mediana_pct < 50),
  salto_preco_andar_pct       numeric not null default 3 check (salto_preco_andar_pct >= 0),
  salto_preco_mesmo_andar_pct numeric not null default 2 check (salto_preco_mesmo_andar_pct >= 0),
  min_unidades_grupo          int not null default 3 check (min_unidades_grupo >= 1),
  atualizado_em               timestamptz not null default now()
);
comment on table public.im_mercado_config is 'Limites do agrupamento das unidades (resumo do empreendimento). Uma linha. Mudar não reagrupa: vale para as unidades novas e para o próximo im_reagrupar.';
insert into public.im_mercado_config (unico) values (true);

create table public.im_grupos (
  id                uuid primary key default gen_random_uuid(),
  empreendimento_id uuid not null references public.im_empreendimentos(id) on delete cascade,
  dormitorios       smallint,
  vagas             smallint,
  programa_social   text not null check (programa_social in ('sim','nao','nao_informado')),
  area_mediana      numeric not null check (area_mediana > 0),
  area_min          numeric not null,
  area_max          numeric not null,
  -- R$/m² do preco_primeiro das unidades com preço quando o grupo nasceu; nulo sem nenhuma
  m2_min            numeric,
  m2_max            numeric,
  m2_mediana        numeric,
  pequeno           boolean not null default false,
  criado_em         timestamptz not null default clock_timestamp(),
  encerrado_em      timestamptz
);
create index im_grupos_emp on public.im_grupos (empreendimento_id) where encerrado_em is null;
comment on table public.im_grupos is 'Grupo de unidades parecidas de um empreendimento (resumo do empreendimento). Faixas fixas desde a criação; encerrado pelo im_reagrupar.';

create table public.im_unidade_grupos (
  id         uuid primary key default gen_random_uuid(),
  unidade_id uuid not null references public.im_unidades(id) on delete cascade,
  grupo_id   uuid not null references public.im_grupos(id) on delete cascade,
  desde      timestamptz not null default clock_timestamp(),
  ate        timestamptz
);
create unique index im_unidade_grupo_vigente on public.im_unidade_grupos (unidade_id) where ate is null;
create index im_unidade_grupos_grupo on public.im_unidade_grupos (grupo_id);
comment on table public.im_unidade_grupos is 'Grupo de cada unidade. Uma linha com ate nulo é a vigente; reagrupar fecha a linha e abre outra.';

do $$
declare t text;
begin
  foreach t in array array['im_mercado_config','im_grupos','im_unidade_grupos'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from public, anon, authenticated', t);
    execute format('grant select on table public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on table public.%I to service_role', t);
    execute format('create policy im_leitura on public.%I for select to authenticated using (true)', t);
  end loop;
end $$;

-- Agrupa as unidades ainda sem grupo de um empreendimento. Devolve quantas entraram em grupo.
create or replace function public._im_agrupar_emp(p_emp uuid)
returns int
language plpgsql security definer set search_path = '' as $$
declare
  cfg public.im_mercado_config;
  v_pct numeric; v_sa numeric; v_sm numeric; v_min int;
  v_n int := 0;
  s record; r record; f record; w record;
  v_b int := 0; v_fx int := 0; v_g int := 0;
  v_geral numeric; v_med numeric;
  v_prev uuid[]; v_cur uuid[];
  v_lt text; v_la int; v_lm numeric;
  v_ult numeric; v_andares text[]; v_key text;
  v_alvo int; v_gq record; v_m numeric;
  v_feitos int[] := '{}'; v_pequenos int[] := '{}';
  v_gid uuid;
begin
  select * into cfg from public.im_mercado_config;
  v_pct := cfg.area_mediana_pct; v_sa := cfg.salto_preco_andar_pct / 100; v_sm := cfg.salto_preco_mesmo_andar_pct / 100;
  v_min := cfg.min_unidades_grupo;

  create temp table if not exists _im_ag (
    id uuid primary key, torre text, andar int, dorm smallint, vagas smallint, prog text,
    area numeric, p1 numeric, m2 numeric, bloco int, faixa int, grp int) on commit drop;
  truncate pg_temp._im_ag;

  insert into pg_temp._im_ag (id, torre, andar, dorm, vagas, prog, area, p1, m2)
  select u.id, u.torre_norm, u.andar, u.dormitorios, u.vagas, e.programa_social, u.area_privativa_m2, u.preco_primeiro,
         u.preco_primeiro / u.area_privativa_m2
    from public.im_unidades u
    join public.im_empreendimentos e on e.id = u.empreendimento_id
   where u.empreendimento_id = p_emp and u.residencial and u.removida_coleta_id is null
     and u.area_privativa_m2 is not null
     and not exists (select 1 from public.im_unidade_grupos ug where ug.unidade_id = u.id and ug.ate is null);
  if not found then return 0; end if;

  -- 5. estabilidade: a unidade nova entra no grupo aberto que a comporta
  insert into public.im_unidade_grupos (unidade_id, grupo_id)
  select a.id, g.id
    from pg_temp._im_ag a
    cross join lateral (
      select g.id from public.im_grupos g
       where g.empreendimento_id = p_emp and g.encerrado_em is null
         and g.dormitorios is not distinct from a.dorm and g.vagas is not distinct from a.vagas
         and g.programa_social = a.prog
         and abs(a.area - g.area_mediana) <= g.area_mediana * v_pct / 100 + 1e-9
         and (a.m2 is null or g.m2_min is null
              or (a.m2 >= g.m2_min / (1 + v_sa) - 1e-9 and a.m2 <= g.m2_max * (1 + v_sa) + 1e-9))
       order by abs(a.area - g.area_mediana), abs(coalesce(a.m2 - g.m2_mediana, 0)),
                (select count(*) from public.im_unidade_grupos x where x.grupo_id = g.id and x.ate is null) desc, g.criado_em
       limit 1) g;
  get diagnostics v_n = row_count;
  delete from pg_temp._im_ag a
   where exists (select 1 from public.im_unidade_grupos ug where ug.unidade_id = a.id and ug.ate is null);

  -- 1. por semelhança
  for s in select distinct dorm, vagas, prog from pg_temp._im_ag loop
    v_feitos := '{}'; v_pequenos := '{}';

    -- 2. blocos de área
    loop
      exit when not exists (select 1 from pg_temp._im_ag where dorm is not distinct from s.dorm
        and vagas is not distinct from s.vagas and prog = s.prog and bloco is null);
      v_b := v_b + 1;
      select percentile_cont(0.5) within group (order by area)::numeric into v_geral from pg_temp._im_ag
       where dorm is not distinct from s.dorm and vagas is not distinct from s.vagas and prog = s.prog and bloco is null;
      select x.area into v_med from (
        select area, count(*) c from pg_temp._im_ag
         where dorm is not distinct from s.dorm and vagas is not distinct from s.vagas and prog = s.prog and bloco is null
         group by area) x
       order by x.c desc, abs(x.area - v_geral), x.area limit 1;
      v_prev := null;
      for i in 1..50 loop
        select array_agg(id order by id) into v_cur from pg_temp._im_ag
         where dorm is not distinct from s.dorm and vagas is not distinct from s.vagas and prog = s.prog and bloco is null
           and abs(area - v_med) <= v_med * v_pct / 100 + 1e-9;
        exit when v_prev is not null and v_cur = v_prev;
        v_prev := v_cur;
        select percentile_cont(0.5) within group (order by area)::numeric into v_med from pg_temp._im_ag where id = any(v_prev);
      end loop;
      update pg_temp._im_ag set bloco = v_b where id = any(v_prev);

      -- 3. faixas de cada andar no bloco (sem andar, cada unidade é uma faixa)
      v_lt := null; v_la := null; v_lm := null;
      for r in select id, torre, andar, m2 from pg_temp._im_ag where bloco = v_b and m2 is not null
                order by torre, andar nulls last, m2, id loop
        if r.andar is null or v_la is null or r.torre is distinct from v_lt or r.andar <> v_la or r.m2 > v_lm * (1 + v_sm) then
          v_fx := v_fx + 1;
        end if;
        update pg_temp._im_ag set faixa = v_fx where id = r.id;
        v_lt := r.torre; v_la := r.andar; v_lm := r.m2;
      end loop;

      -- faixas pelo R$/m² típico: salto entre andares abre grupo; o mesmo andar nunca repete no grupo
      v_ult := null; v_andares := '{}';
      for f in select faixa, percentile_cont(0.5) within group (order by m2)::numeric m, min(torre) t, min(andar) a
                 from pg_temp._im_ag where bloco = v_b and faixa is not null group by faixa
                order by 2, case when min(andar) is null then '~' else min(torre) || '#' || lpad(min(andar)::text, 4, '0') end, faixa loop
        v_key := case when f.a is null then null else f.t || '#' || f.a end;
        if v_ult is null or f.m > v_ult * (1 + v_sa) or (v_key is not null and v_key = any(v_andares)) then
          v_g := v_g + 1; v_andares := '{}';
        end if;
        update pg_temp._im_ag set grp = v_g where bloco = v_b and faixa = f.faixa;
        v_ult := f.m;
        if v_key is not null then v_andares := v_andares || v_key; end if;
      end loop;

      -- 4. sem preço: grupo do mesmo andar (o maior), senão o maior do bloco
      for w in select id, torre, andar from pg_temp._im_ag where bloco = v_b and m2 is null order by torre, andar nulls last, id loop
        select x.grp into v_alvo from pg_temp._im_ag x where x.bloco = v_b and x.grp is not null group by x.grp
         order by bool_or(w.andar is not null and x.m2 is not null and x.torre = w.torre and x.andar = w.andar) desc,
                  count(*) desc, x.grp limit 1;
        if v_alvo is null then v_g := v_g + 1; v_alvo := v_g; end if;
        update pg_temp._im_ag set grp = v_alvo where id = w.id;
        v_alvo := null;
      end loop;
    end loop;

    -- grupo pequeno: só junta a um grupo que difere dele apenas na área
    loop
      select x.grp, min(x.bloco) bloco, count(*) n, percentile_cont(0.5) within group (order by x.area)::numeric am,
             sum(x.p1) filter (where x.p1 is not null) / nullif(sum(x.area) filter (where x.p1 is not null), 0) m
        into v_gq
        from pg_temp._im_ag x
       where x.dorm is not distinct from s.dorm and x.vagas is not distinct from s.vagas and x.prog = s.prog
       group by x.grp
      having count(*) < v_min and not (x.grp = any(v_feitos))
       order by count(*), 4, x.grp limit 1;
      exit when not found;
      v_pequenos := v_pequenos || v_gq.grp;
      select o.grp into v_alvo from (
        select x.grp, min(x.bloco) bloco, count(*) n, percentile_cont(0.5) within group (order by x.area)::numeric am,
               sum(x.p1) filter (where x.p1 is not null) / nullif(sum(x.area) filter (where x.p1 is not null), 0) m
          from pg_temp._im_ag x
         where x.dorm is not distinct from s.dorm and x.vagas is not distinct from s.vagas and x.prog = s.prog
           and x.grp <> v_gq.grp
         group by x.grp) o
       where o.bloco <> v_gq.bloco
         and (v_gq.m is null or o.m is null or greatest(v_gq.m, o.m) <= least(v_gq.m, o.m) * (1 + v_sa) + 1e-9)
       order by abs(o.am - v_gq.am), o.n desc, o.grp limit 1;
      if not found then
        v_feitos := v_feitos || v_gq.grp;
        continue;
      end if;
      update pg_temp._im_ag x set grp = v_alvo,
             bloco = (select min(y.bloco) from pg_temp._im_ag y where y.grp = v_alvo)
       where x.grp = v_gq.grp;
      v_pequenos := v_pequenos || v_alvo;
      v_alvo := null;
    end loop;

    for f in select x.grp from pg_temp._im_ag x
              where x.dorm is not distinct from s.dorm and x.vagas is not distinct from s.vagas and x.prog = s.prog
              group by x.grp order by x.grp loop
      insert into public.im_grupos (empreendimento_id, dormitorios, vagas, programa_social, area_mediana, area_min, area_max,
                                    m2_min, m2_max, m2_mediana, pequeno)
      select p_emp, s.dorm, s.vagas, s.prog, percentile_cont(0.5) within group (order by area)::numeric, min(area), max(area),
             min(m2), max(m2), percentile_cont(0.5) within group (order by m2)::numeric, f.grp = any(v_pequenos)
        from pg_temp._im_ag where grp = f.grp
      returning id into v_gid;
      insert into public.im_unidade_grupos (unidade_id, grupo_id)
      select id, v_gid from pg_temp._im_ag where grp = f.grp;
      v_n := v_n + (select count(*) from pg_temp._im_ag where grp = f.grp);
    end loop;
  end loop;
  return v_n;
end $$;

-- Agrupa as unidades sem grupo de um empreendimento, ou de todos (nulo). O coletor chama ao fim de cada rodada.
create or replace function public.im_agrupar(p_emp uuid default null)
returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '10min' as $$
declare e uuid; v_emps int := 0; v_un int := 0; v int;
begin
  for e in
    select distinct u.empreendimento_id from public.im_unidades u
     where (p_emp is null or u.empreendimento_id = p_emp)
       and u.residencial and u.removida_coleta_id is null and u.area_privativa_m2 is not null
       and not exists (select 1 from public.im_unidade_grupos ug where ug.unidade_id = u.id and ug.ate is null)
  loop
    v := public._im_agrupar_emp(e);
    if v > 0 then v_emps := v_emps + 1; v_un := v_un + v; end if;
  end loop;
  return jsonb_build_object('empreendimentos', v_emps, 'unidades', v_un);
end $$;

-- Reagrupar: encerra os grupos abertos (de um empreendimento ou de todos) e agrupa tudo de novo.
-- A ligação antiga fica (ate preenchido): a venda já gravada continua no grupo da época.
create or replace function public.im_reagrupar(p_emp uuid default null)
returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '10min' as $$
declare v_agora timestamptz := clock_timestamp();
begin
  update public.im_unidade_grupos ug set ate = v_agora
    from public.im_grupos g
   where g.id = ug.grupo_id and ug.ate is null and g.encerrado_em is null
     and (p_emp is null or g.empreendimento_id = p_emp);
  update public.im_grupos set encerrado_em = v_agora
   where encerrado_em is null and (p_emp is null or empreendimento_id = p_emp);
  return public.im_agrupar(p_emp);
end $$;

create or replace function public.im_salvar_mercado_config(p_dados jsonb)
returns public.im_mercado_config
language plpgsql security definer set search_path = '' as $$
declare c public.im_mercado_config;
begin
  update public.im_mercado_config set
    area_mediana_pct            = coalesce((p_dados->>'area_mediana_pct')::numeric, area_mediana_pct),
    salto_preco_andar_pct       = coalesce((p_dados->>'salto_preco_andar_pct')::numeric, salto_preco_andar_pct),
    salto_preco_mesmo_andar_pct = coalesce((p_dados->>'salto_preco_mesmo_andar_pct')::numeric, salto_preco_mesmo_andar_pct),
    min_unidades_grupo          = coalesce((p_dados->>'min_unidades_grupo')::int, min_unidades_grupo),
    atualizado_em = now()
  returning * into c;
  return c;
end $$;

revoke all on function
  public._im_agrupar_emp(uuid),
  public.im_agrupar(uuid),
  public.im_reagrupar(uuid),
  public.im_salvar_mercado_config(jsonb)
from public, anon, authenticated;
grant execute on function
  public.im_agrupar(uuid),
  public.im_reagrupar(uuid),
  public.im_salvar_mercado_config(jsonb)
to service_role;
