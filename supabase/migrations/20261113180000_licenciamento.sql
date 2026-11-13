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
-- até 30 m), consultado no GeoSampa (tools/licenciamento/lotes.py). Só vale o lote exato: o prédio
-- pronto vira condomínio (lote 0000) e a busca pela quadra, testada com os dados de nov/2026, casava
-- prédios errados (fica "sem_processo"). Processos ligados por lote em comum são o mesmo prédio
-- (aprovação, execução, modificativos) e valem pelo de mais unidades (o mais recente às vezes é um
-- modificativo pequeno). O Órulo divide o projeto em "- Residencial", "- NR", "- Lojas", "Fase 1/2":
-- os residenciais do mesmo prédio são somados e os não residenciais ficam de fora, porque o processo
-- conta só unidades residenciais.
--
-- Suspeito NÃO sai do VSO por enquanto (decisão pendente do Moisés): projeto em vários lotes pode
-- ter um processo por lote, e aqui só se vê o do lote do ponto.

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
  situacao          text not null check (situacao in
                      ('conferido','suspeito','fase','sem_processo','sem_lote','nao_residencial')),
  unidades_orulo    integer,         -- residenciais do empreendimento
  unidades_predio   integer,         -- soma dos empreendimentos residenciais do Órulo no mesmo prédio
  unidades_processo integer,
  processo          text,            -- o de mais unidades do prédio
  processos         text[],          -- todos os do prédio (aprovação, execução, modificativos)
  data_processo     date,
  his               boolean,
  hmp               boolean,
  r2v               boolean,
  conferido_em      timestamptz not null default now()
);
comment on table public.im_conferencia_unidades is
  'Unidades residenciais do Órulo × processo de licenciamento (SP), pelo lote do ponto. suspeito = Órulo acima do processo + 5%; fase = abaixo − 5%.';

-- Nome de parte não residencial do projeto no Órulo ("Edifício X - NR", "- Lojas", "- Comercial").
create or replace function public._im_nome_nao_residencial(p_nome text)
returns boolean language sql immutable
set search_path = '' as $$
  select coalesce(p_nome ~* '-\s*(NR|Lojas?|Comercial|Corporate|Office|Mall)\s*$', false)
$$;

-- Unidades residenciais do Órulo: pelo histórico (última situação de cada tipologia residencial);
-- sem histórico, pelas unidades residenciais coletadas pelo monitor; sem as duas, o total do empreendimento.
create or replace function public._im_unidades_orulo(p_orulo integer)
returns integer language sql stable
set search_path = '' as $$
  select coalesce(
    (select round(sum(t.unidades))::integer from (
        select distinct on (orulo_tid) unidades, finalidade from public.im_historico_orulo_tipologias
         where orulo_id = p_orulo order by orulo_tid, mes desc) t
      where t.finalidade = 'Residencial'),
    (select nullif(count(*), 0)::integer from public.im_ids_externos x
       join public.im_unidades u on u.empreendimento_id = x.entidade_id
      where x.entidade = 'empreendimento' and x.fonte like 'orulo%' and x.id_externo = p_orulo::text
        and u.residencial),
    (select round(h.unidades)::integer from public.im_historico_orulo_empreendimentos h where h.orulo_id = p_orulo))
$$;

create or replace function public._im_nome_orulo(p_orulo integer)
returns text language sql stable
set search_path = '' as $$
  select coalesce(
    (select h.nome from public.im_historico_orulo_empreendimentos h where h.orulo_id = p_orulo),
    (select e.nome from public.im_ids_externos x join public.im_empreendimentos e on e.id = x.entidade_id
      where x.entidade = 'empreendimento' and x.fonte like 'orulo%' and x.id_externo = p_orulo::text limit 1))
$$;

-- Primeira passada de um empreendimento: acha o prédio e grava as unidades dele.
create or replace function public._im_conferir_um(p_orulo integer)
returns void language plpgsql security definer
set search_path = '' as $$
declare
  v_lote text; v_proc record; v_procs text[];
  v_uo integer := public._im_unidades_orulo(p_orulo);
begin
  delete from public.im_conferencia_unidades where orulo_id = p_orulo;
  if not exists (select 1 from public.im_empreendimento_lotes where orulo_id = p_orulo) then
    return;  -- lote não consultado (fora de São Paulo)
  end if;
  if public._im_nome_orulo(p_orulo) is not null and public._im_nome_nao_residencial(public._im_nome_orulo(p_orulo))
     or v_uo = 0 then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo) values (p_orulo, 'nao_residencial', v_uo);
    return;
  end if;
  select lote into v_lote from public.im_empreendimento_lotes where orulo_id = p_orulo;
  if v_lote is null then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo) values (p_orulo, 'sem_lote', v_uo);
    return;
  end if;

  -- o prédio: os processos com unidades ligados ao lote, direta ou indiretamente, por lote em comum
  with recursive r (processo, lotes) as (
    select p.processo, p.lotes from public.im_licenciamento_processos p
     where p.lotes @> array[v_lote] and coalesce(p.unidades, 0) > 0
    union
    select p.processo, p.lotes from public.im_licenciamento_processos p join r on p.lotes && r.lotes
     where coalesce(p.unidades, 0) > 0)
  select array_agg(distinct processo order by processo) into v_procs from r;

  if v_procs is null then
    insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo) values (p_orulo, 'sem_processo', v_uo);
    return;
  end if;
  select * into v_proc from public.im_licenciamento_processos
   where processo = any (v_procs) order by unidades desc, data_autuacao desc nulls last limit 1;
  insert into public.im_conferencia_unidades (orulo_id, situacao, unidades_orulo, unidades_processo, processo, processos,
                                              data_processo, his, hmp, r2v)
    values (p_orulo, 'conferido', v_uo, v_proc.unidades, v_proc.processo, v_procs, v_proc.data_autuacao,
            v_proc.his, v_proc.hmp, v_proc.r2v);
end $$;

-- Segunda passada: soma os empreendimentos do mesmo prédio e decide a situação.
create or replace function public._im_situacao_predios()
returns void language sql security definer
set search_path = '' as $$
  with p as (
    select processos, sum(unidades_orulo) soma from public.im_conferencia_unidades
     where processos is not null group by processos)
  update public.im_conferencia_unidades c set
    unidades_predio = p.soma,
    situacao = case when p.soma > c.unidades_processo * 1.05 then 'suspeito'
                    when p.soma < c.unidades_processo * 0.95 then 'fase'
                    else 'conferido' end
  from p where c.processos = p.processos;
$$;

-- Confere um empreendimento (e refaz o prédio dele).
create or replace function public.im_conferir_unidade(p_orulo integer)
returns void language plpgsql security definer
set search_path = '' as $$
begin
  perform public._im_conferir_um(p_orulo);
  perform public._im_situacao_predios();
end $$;

-- Refaz a conferência de todos os empreendimentos com lote consultado.
create or replace function public.im_conferir_unidades()
returns json language plpgsql security definer
set search_path = '' as $$
declare v_id integer;
begin
  for v_id in select orulo_id from public.im_empreendimento_lotes loop
    perform public._im_conferir_um(v_id);
  end loop;
  perform public._im_situacao_predios();
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

revoke all on function public._im_nome_nao_residencial(text), public._im_unidades_orulo(integer),
  public._im_nome_orulo(integer), public._im_conferir_um(integer), public._im_situacao_predios(),
  public.im_conferir_unidade(integer), public.im_conferir_unidades() from public, anon, authenticated;
grant execute on function public._im_nome_nao_residencial(text), public._im_unidades_orulo(integer),
  public._im_nome_orulo(integer), public._im_conferir_um(integer), public._im_situacao_predios(),
  public.im_conferir_unidade(integer), public.im_conferir_unidades() to service_role;
