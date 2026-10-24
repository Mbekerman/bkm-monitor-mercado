-- =====================================================================
-- Monitor de mercado da BKM · fase 1 · só coleta e armazenamento
--
-- Sistema independente. Não usa o schema app, nem empresas, nem
-- concorrentes, nem auditoria do BKM Terrenos. O dado é da BKM inteira:
-- não há empresa_id. O BKM Terrenos só lê estas tabelas depois, pela API.
--
-- Tabelas (public, prefixo im_, minúsculo, sem aspas), todas com RLS:
--   im_municipios, im_config, im_tipos_unidade, im_coletas,
--   im_incorporadoras, im_empreendimentos, im_ids_externos,
--   im_empreendimento_estados, im_unidades, im_unidade_estados,
--   im_vendas, im_sugestoes_vinculo.
-- Leitura (select) para authenticated. Escrita só pelas funções
-- security definer, com execute apenas para service_role. Nada para anon.
--
-- Fora das tabelas im_*, esta migration também cria:
--   funções public.im_* (leitura e escrita) e public._im_* (internas);
--   a política im_leitura em cada tabela im_*;
--   o bucket privado storage im-bruto (insert ... on conflict do nothing);
--   as políticas im_bruto_leitura, im_bruto_insercao, im_bruto_atualizacao
--   e im_bruto_remocao, só no bucket im-bruto, só para service_role.
-- Não cria extensão, vista, publication, trigger em auth.users, nem
-- altera objeto que não seja desta migration.
--
-- O fechamento grava a venda e a vigência. Não há função de velocidade,
-- estoque, preço agregado, lançamento ou grupo, e não há vista.
-- Unidade fica uma a uma, com preco_primeiro, que não muda.
-- Distância é haversine em SQL. Similaridade é Jaccard de trigramas.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Funções puras
-- ---------------------------------------------------------------------
-- Maiúsculas, sem acento, sem as palavras TORRE, BLOCO, EDIFICIO e ED, sem espaço.
create or replace function public.im_norm_rotulo(p text)
returns text
language sql immutable parallel safe set search_path = '' as $$
  select coalesce(nullif(regexp_replace(regexp_replace(upper(translate(coalesce(p, ''),
           'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
           'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN')),
           '\m(EDIFICIO|TORRE|BLOCO|ED)\M', '', 'g'), '[[:space:]]+', '', 'g'), ''), '')
$$;

-- Jaccard dos trigramas, com dois espaços antes e um depois (a conta do pg_trgm).
create or replace function public.im_similaridade_trigrama(a text, b text)
returns numeric
language sql immutable set search_path = '' as $$
  with na as (select '  ' || lower(btrim(coalesce(a, ''))) || ' ' as s),
       nb as (select '  ' || lower(btrim(coalesce(b, ''))) || ' ' as s),
       ta as (
         select substring((select s from na) from i for 3) as g
         from generate_series(1, greatest(length((select s from na)) - 2, 0)) i
       ),
       tb as (
         select substring((select s from nb) from i for 3) as g
         from generate_series(1, greatest(length((select s from nb)) - 2, 0)) i
       ),
       inter as (select count(*)::numeric as c from (select g from ta intersect select g from tb) z),
       uni as (select count(*)::numeric as c from (select g from ta union select g from tb) z)
  select case when (select c from uni) = 0 then 0 else round((select c from inter) / (select c from uni), 4) end
$$;

-- Distância em metros. Sem postgis.
create or replace function public.im_distancia_m(lat1 float8, lng1 float8, lat2 float8, lng2 float8)
returns numeric
language sql immutable parallel safe set search_path = '' as $$
  select (2 * 6371000 * asin(sqrt(
           power(sin(radians(lat2 - lat1) / 2), 2)
         + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lng2 - lng1) / 2), 2))))::numeric
$$;

create or replace function public.im_normaliza_nome(p text)
returns text
language sql immutable parallel safe set search_path = '' as $$
  select regexp_replace(lower(btrim(translate(coalesce(p, ''),
           'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
           'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN'))), '[[:space:]]+', ' ', 'g')
$$;

-- ---------------------------------------------------------------------
-- Tabelas
-- ---------------------------------------------------------------------
create table public.im_municipios (
  id          uuid primary key default gen_random_uuid(),
  codigo_ibge text not null unique check (codigo_ibge ~ '^\d{7}$'),
  nome        text not null check (btrim(nome) <> ''),
  uf          text not null check (uf ~ '^[A-Z]{2}$')
);
comment on table public.im_municipios is 'Municípios que o monitor reconhece pelo nome. Fase 1: São Paulo e São José dos Campos.';

insert into public.im_municipios (codigo_ibge, nome, uf) values
  ('3550308', 'São Paulo', 'SP'),
  ('3549904', 'São José dos Campos', 'SP');

create or replace function public.im_municipio_por_nome(p_nome text, p_uf text)
returns uuid
language sql stable set search_path = '' as $$
  select m.id
    from public.im_municipios m
   where public.im_normaliza_nome(m.nome) = public.im_normaliza_nome(p_nome)
     and m.uf = upper(btrim(coalesce(p_uf, '')))
   limit 1
$$;

-- Uma linha só. unico é a chave fixa.
create table public.im_config (
  unico                      boolean primary key default true check (unico),
  meses_recem_entregue       int not null default 24 check (meses_recem_entregue > 0),
  meses_lancamento           int not null default 6 check (meses_lancamento > 0),
  dias_preco_lancamento      int not null default 60 check (dias_preco_lancamento >= 0),
  cobertura_minima_pct       numeric not null default 90 check (cobertura_minima_pct > 0 and cobertura_minima_pct <= 100),
  -- Studio "- NR": ponto em aberto. Padrão conta como residencial.
  nr_conta_como_residencial  boolean not null default true,
  municipios                 uuid[] not null default '{}'
);
comment on table public.im_config is 'Prazos e municípios do monitor. Uma linha para a BKM inteira.';

create table public.im_tipos_unidade (
  id          uuid primary key default gen_random_uuid(),
  codigo      text not null unique check (codigo ~ '^[a-z0-9_]+$'),
  residencial boolean not null,
  ativo       boolean not null default true
);
comment on table public.im_tipos_unidade is 'Tipos de unidade. Residencial entra no filtro e na venda; o resto é gravado e fica de fora.';

create table public.im_coletas (
  id            uuid primary key default gen_random_uuid(),
  fonte         text not null check (fonte in ('orulo_api','orulo_planilha','orulo_login','manual')),
  tipo          text not null check (tipo in ('completa_mensal','lancamentos')),
  referencia    date not null,
  iniciada_em   timestamptz not null default clock_timestamp(),
  concluida_em  timestamptz,
  situacao      text not null default 'rodando' check (situacao in ('rodando','completa','incompleta','falhou')),
  esperados     int check (esperados is null or esperados >= 0),
  coletados     int not null default 0 check (coletados >= 0),
  unidades      int not null default 0 check (unidades >= 0),
  falhas        jsonb not null default '[]',
  tipos_desconhecidos text[] not null default '{}',
  -- mercado/{referencia}-{tipo}.json.gz, no bucket im-bruto
  bruto_caminho text,
  unique (fonte, tipo, referencia)
);
comment on table public.im_coletas is 'Uma rodada por fonte, tipo e data. Rodar de novo a mesma chave desfaz o que ela gravou e recomeça.';

create table public.im_incorporadoras (
  id            uuid primary key default gen_random_uuid(),
  nome          text not null check (btrim(nome) <> ''),
  cnpj          text check (cnpj is null or cnpj ~ '^\d{14}$'),
  observacao    text,
  ativo         boolean not null default true,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);
create unique index im_incorporadoras_cnpj on public.im_incorporadoras (cnpj) where cnpj is not null;
create unique index im_incorporadoras_nome on public.im_incorporadoras (lower(btrim(nome))) where ativo;
comment on table public.im_incorporadoras is 'Incorporadora acompanhada. Casa por id de fora, senão CNPJ, senão nome.';

create table public.im_empreendimentos (
  id                 uuid primary key default gen_random_uuid(),
  incorporadora_id   uuid not null references public.im_incorporadoras(id),
  nome               text not null check (btrim(nome) <> ''),
  latitude           double precision not null check (latitude between -90 and 90),
  longitude          double precision not null check (longitude between -180 and 180),
  endereco           text,
  municipio_id       uuid references public.im_municipios(id),
  bairro             text,
  unidades           int check (unidades is null or unidades > 0),
  situacao           text not null default 'nao_informado'
                     check (situacao in ('lancamento','breve_lancamento','em_obras','pronto','usado','nao_informado')),
  data_lancamento    date,
  previsao_entrega   date,
  torres             smallint check (torres is null or torres > 0),
  programa_social    text not null default 'nao_informado'
                     check (programa_social in ('sim','nao','nao_informado')),
  tipo               text not null default 'nao_informado'
                     check (tipo in ('vertical','horizontal','unidade_avulsa','sobrado','loteamento','industrial','galpao','nao_informado')),
  finalidade         text not null default 'nao_informado'
                     check (finalidade in ('residencial','comercial','misto','nao_informado')),
  origem             text not null default 'manual' check (origem in ('manual','importado')),
  link_fonte         text,
  primeira_coleta_id uuid references public.im_coletas(id),
  ultima_coleta_id   uuid references public.im_coletas(id),
  observacao         text,
  ativo              boolean not null default true,
  criado_em          timestamptz not null default now(),
  atualizado_em      timestamptz not null default now()
);
create index im_empreendimentos_incorporadora on public.im_empreendimentos (incorporadora_id);
create index im_empreendimentos_ativos on public.im_empreendimentos (ativo) where ativo;
comment on table public.im_empreendimentos is 'Empreendimento monitorado. origem manual não é alterada pela coleta; observacao e ativo nunca são sobrescritos.';

-- Id de fora, por fonte. A unidade do Órulo logado não tem id: a linha não é criada e o casamento é torre + número.
create table public.im_ids_externos (
  id          uuid primary key default gen_random_uuid(),
  entidade    text not null check (entidade in ('incorporadora','empreendimento','unidade')),
  entidade_id uuid not null,
  fonte       text not null check (fonte in ('orulo_api','orulo_planilha','orulo_login','manual')),
  id_externo  text not null check (btrim(id_externo) <> ''),
  unique (fonte, entidade, id_externo),
  unique (fonte, entidade, entidade_id)
);
comment on table public.im_ids_externos is 'Id da fonte para incorporadora, empreendimento ou unidade.';

create table public.im_empreendimento_estados (
  id                   uuid primary key default gen_random_uuid(),
  empreendimento_id    uuid not null references public.im_empreendimentos(id) on delete cascade,
  desde_coleta_id      uuid not null references public.im_coletas(id),
  ate_coleta_id        uuid references public.im_coletas(id),
  situacao             text not null check (situacao in ('lancamento','breve_lancamento','em_obras','pronto','usado','nao_informado')),
  data_lancamento      date,
  previsao_entrega     date,
  unidades_informadas  int check (unidades_informadas is null or unidades_informadas >= 0),
  estoque_informado    int check (estoque_informado is null or estoque_informado >= 0),
  tabela_atualizada_em date,
  no_filtro            boolean not null,
  marcas               text[] not null default '{}'
);
create unique index im_emp_estado_vigente on public.im_empreendimento_estados (empreendimento_id) where ate_coleta_id is null;
create index im_emp_estado_desde on public.im_empreendimento_estados (desde_coleta_id);
comment on table public.im_empreendimento_estados is 'Vigência do empreendimento. Uma linha com ate_coleta_id nulo é a vigente.';

create table public.im_unidades (
  id                  uuid primary key default gen_random_uuid(),
  empreendimento_id   uuid not null references public.im_empreendimentos(id) on delete cascade,
  torre               text,
  numero              text,
  torre_norm          text not null default '',
  numero_norm         text not null default '',
  andar               smallint,
  area_privativa_m2   numeric check (area_privativa_m2 is null or area_privativa_m2 > 0),
  dormitorios         smallint check (dormitorios is null or dormitorios between 0 and 20),
  suites              smallint check (suites is null or suites >= 0),
  vagas               smallint check (vagas is null or vagas >= 0),
  tipologia_fonte     text,
  tipo_unidade        text not null default 'nao_informado',
  tipo_fonte          text,
  studio_nr           boolean not null default false,
  residencial         boolean not null default false,
  preco_primeiro      numeric check (preco_primeiro is null or preco_primeiro > 0),
  preco_primeiro_em   date,
  preco_de_lancamento boolean not null default false,
  primeira_coleta_id  uuid not null references public.im_coletas(id),
  ultima_coleta_id    uuid references public.im_coletas(id),
  removida_coleta_id  uuid references public.im_coletas(id),
  vinculo_pendente_id uuid references public.im_unidades(id) on delete set null,
  marcas              text[] not null default '{}',
  unique (empreendimento_id, torre_norm, numero_norm)
);
create index im_unidades_ultima on public.im_unidades (ultima_coleta_id);
comment on table public.im_unidades is 'Unidade individual. preco_primeiro é o primeiro preço visto e não muda. Sem agrupamento.';

create table public.im_unidade_estados (
  id               uuid primary key default gen_random_uuid(),
  unidade_id       uuid not null references public.im_unidades(id) on delete cascade,
  desde_coleta_id  uuid not null references public.im_coletas(id),
  ate_coleta_id    uuid references public.im_coletas(id),
  situacao         text not null check (situacao in ('disponivel','reservada','vendida','indisponivel','nao_informado')),
  preco            numeric check (preco is null or preco > 0),
  preco_original   numeric check (preco_original is null or preco_original > 0),
  marcas           text[] not null default '{}'
);
create unique index im_un_estado_vigente on public.im_unidade_estados (unidade_id) where ate_coleta_id is null;
create index im_un_estado_desde on public.im_unidade_estados (desde_coleta_id);
comment on table public.im_unidade_estados is 'Vigência de situação e preço da unidade. Abre linha nova só quando muda.';

create table public.im_vendas (
  id                 uuid primary key default gen_random_uuid(),
  unidade_id         uuid not null references public.im_unidades(id) on delete cascade,
  coleta_id          uuid not null references public.im_coletas(id),
  vendida_de         date not null,
  vendida_ate        date not null,
  preco              numeric check (preco is null or preco > 0),
  origem_preco       text not null check (origem_preco in ('ultimo_visto','estimado')),
  antes_do_monitor   boolean not null default false,
  desfeita_coleta_id uuid references public.im_coletas(id)
);
create unique index im_vendas_aberta on public.im_vendas (unidade_id) where desfeita_coleta_id is null;
create index im_vendas_coleta on public.im_vendas (coleta_id);
comment on table public.im_vendas is 'Venda gravada no fechamento de uma mensal completa. Voltar a disponível desfaz.';

-- Empreendimento manual perto e com nome parecido: sugestão, sem vínculo automático.
create table public.im_sugestoes_vinculo (
  id                 uuid primary key default gen_random_uuid(),
  empreendimento_id  uuid not null references public.im_empreendimentos(id) on delete cascade,
  manual_id          uuid not null references public.im_empreendimentos(id) on delete cascade,
  similaridade       numeric not null,
  distancia_m        numeric not null,
  coleta_id          uuid not null references public.im_coletas(id),
  unique (empreendimento_id, manual_id)
);
comment on table public.im_sugestoes_vinculo is 'Sugestão de vínculo com empreendimento manual a até 150 m e nome parecido. Não vincula sozinha.';

-- ---------------------------------------------------------------------
-- RLS: select para authenticated. Sem política de escrita.
-- service_role tem bypassrls e é quem o coletor usa.
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'im_municipios','im_config','im_tipos_unidade','im_coletas','im_incorporadoras',
    'im_empreendimentos','im_ids_externos','im_empreendimento_estados','im_unidades',
    'im_unidade_estados','im_vendas','im_sugestoes_vinculo']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from public, anon, authenticated', t);
    execute format('grant select on table public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on table public.%I to service_role', t);
    execute format('create policy im_leitura on public.%I for select to authenticated using (true)', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- Classificação e filtro
-- ---------------------------------------------------------------------
-- Residencial sai do tipo cadastrado. Studio "- NR" obedece a config.
create or replace function public._im_residencial(p_codigo text, p_studio_nr boolean)
returns boolean
language plpgsql stable security definer set search_path = '' as $$
begin
  if p_codigo is null or p_codigo = 'nao_informado' then return false; end if;
  if p_studio_nr then
    return coalesce((select nr_conta_como_residencial from public.im_config), true);
  end if;
  return coalesce((select residencial from public.im_tipos_unidade where codigo = p_codigo and ativo), false);
end $$;

create or replace function public._im_classificar_tipo(p_tipo text)
returns table (codigo text, fonte text, studio_nr boolean, residencial boolean)
language plpgsql stable security definer set search_path = '' as $$
declare
  v text := lower(btrim(translate(coalesce(p_tipo, ''),
             'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
             'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN')));
  v_esp text;
  v_chave text;
  v_codigo text;
  v_nr boolean := false;
begin
  v := regexp_replace(v, '[[:space:]]+', ' ', 'g');
  v_esp := split_part(v, ' ', 1);
  v_chave := regexp_replace(v, '[^a-z0-9]+', '', 'g');
  if v ~ 'studio' and v ~ '(^|[^a-z0-9])nr($|[^a-z0-9])' then
    v_codigo := 'studio';
    v_nr := true;
  else
    select t.codigo into v_codigo from public.im_tipos_unidade t
     where t.ativo and t.codigo in (v_chave, v_esp, v)
     order by case when t.codigo = v_chave then 0 when t.codigo = v then 1 else 2 end
     limit 1;
    if v_codigo is null then v_codigo := 'nao_informado'; end if;
  end if;
  codigo := v_codigo;
  fonte := nullif(btrim(coalesce(p_tipo, '')), '');
  studio_nr := v_nr;
  residencial := public._im_residencial(v_codigo, v_nr);
  return next;
end $$;

create or replace function public._im_passa_filtro(
  p_tipo text, p_finalidade text, p_situacao text,
  p_lancamento date, p_entrega date, p_municipio uuid, p_referencia date,
  p_tem_lista boolean, p_tem_residencial boolean)
returns boolean
language plpgsql stable security definer set search_path = '' as $$
declare
  v_meses_lanc int;
  v_meses_rec int;
  v_muns uuid[];
begin
  select meses_lancamento, meses_recem_entregue, municipios
    into v_meses_lanc, v_meses_rec, v_muns
    from public.im_config;
  if not found then return false; end if;
  if p_situacao = 'usado' or p_tipo is distinct from 'vertical' then return false; end if;
  if p_finalidade not in ('residencial', 'misto') then return false; end if;
  if p_municipio is null or not (p_municipio = any(v_muns)) then return false; end if;
  if not p_tem_residencial then return false; end if;
  return p_situacao in ('lancamento', 'breve_lancamento', 'em_obras')
      or (p_lancamento is not null and p_lancamento >= (p_referencia - make_interval(months => v_meses_lanc))::date)
      or (p_situacao = 'pronto' and p_entrega is not null and p_entrega <= p_referencia
          and p_entrega >= (p_referencia - make_interval(months => v_meses_rec))::date);
end $$;

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
    ('loja', false), ('sala', false), ('laje', false), ('vaga', false), ('deposito', false)
  ) as t(codigo, residencial)
  where not exists (select 1 from public.im_tipos_unidade x where x.codigo = t.codigo);
end $$;

create or replace function public._im_recalcular_residencial()
returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.im_unidades u
     set residencial = public._im_residencial(u.tipo_unidade, u.studio_nr)
   where u.residencial is distinct from public._im_residencial(u.tipo_unidade, u.studio_nr);
end $$;

create or replace function public.im_salvar_config(p_dados jsonb)
returns void
language plpgsql security definer set search_path = '' as $$
declare c public.im_config;
begin
  perform public._im_semear();
  update public.im_config set
    meses_recem_entregue = coalesce((p_dados->>'meses_recem_entregue')::int, meses_recem_entregue),
    meses_lancamento = coalesce((p_dados->>'meses_lancamento')::int, meses_lancamento),
    dias_preco_lancamento = coalesce((p_dados->>'dias_preco_lancamento')::int, dias_preco_lancamento),
    cobertura_minima_pct = coalesce((p_dados->>'cobertura_minima_pct')::numeric, cobertura_minima_pct),
    nr_conta_como_residencial = coalesce((p_dados->>'nr_conta_como_residencial')::boolean, nr_conta_como_residencial),
    municipios = case when p_dados ? 'municipios' then coalesce((
                    select array_agg(x::uuid) from jsonb_array_elements_text(p_dados->'municipios') x), '{}')
                  else municipios end
  where unico
  returning * into c;
  if p_dados ? 'nr_conta_como_residencial' then
    perform public._im_recalcular_residencial();
  end if;
  if p_dados ? 'dias_preco_lancamento' then
    update public.im_unidades u
       set preco_de_lancamento = u.preco_primeiro_em <= e.data_lancamento + c.dias_preco_lancamento
      from public.im_empreendimentos e
     where u.empreendimento_id = e.id
       and u.preco_primeiro_em is not null and e.data_lancamento is not null
       and u.preco_de_lancamento is distinct from (u.preco_primeiro_em <= e.data_lancamento + c.dias_preco_lancamento);
  end if;
end $$;

create or replace function public.im_salvar_tipo_unidade(p_codigo text, p_residencial boolean, p_ativo boolean default true)
returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_codigo is null or p_codigo !~ '^[a-z0-9_]+$' then
    raise exception 'Código de tipo inválido' using errcode = '23514';
  end if;
  insert into public.im_tipos_unidade (codigo, residencial, ativo)
  values (p_codigo, p_residencial, coalesce(p_ativo, true))
  on conflict (codigo) do update
    set residencial = excluded.residencial, ativo = excluded.ativo;
  perform public._im_recalcular_residencial();
end $$;

-- ---------------------------------------------------------------------
-- Coleta
-- ---------------------------------------------------------------------
create or replace function public._im_desfazer(p_coleta uuid)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_ref date;
begin
  select referencia into v_ref from public.im_coletas where id = p_coleta;
  if not found then return; end if;

  if exists (
    select 1 from public.im_coletas o
    join public.im_coletas c on c.id = p_coleta
    where o.fonte = c.fonte and o.tipo = c.tipo
      and o.referencia > c.referencia)
  then
    raise exception 'Já existe coleta desta fonte e tipo depois desta data' using errcode = '23514';
  end if;

  update public.im_vendas set desfeita_coleta_id = null where desfeita_coleta_id = p_coleta;

  delete from public.im_ids_externos
   where entidade = 'unidade'
     and entidade_id in (select id from public.im_unidades where primeira_coleta_id = p_coleta);
  delete from public.im_ids_externos
   where entidade = 'empreendimento'
     and entidade_id in (select id from public.im_empreendimentos where primeira_coleta_id = p_coleta);

  delete from public.im_sugestoes_vinculo where coleta_id = p_coleta;

  delete from public.im_unidades where primeira_coleta_id = p_coleta;
  delete from public.im_empreendimentos where primeira_coleta_id = p_coleta;

  delete from public.im_unidade_estados where desde_coleta_id = p_coleta;
  update public.im_unidade_estados set ate_coleta_id = null where ate_coleta_id = p_coleta;
  delete from public.im_empreendimento_estados where desde_coleta_id = p_coleta;
  update public.im_empreendimento_estados set ate_coleta_id = null where ate_coleta_id = p_coleta;

  delete from public.im_vendas where coleta_id = p_coleta;

  update public.im_unidades set removida_coleta_id = null where removida_coleta_id = p_coleta;

  update public.im_unidades u set ultima_coleta_id = s.desde_coleta_id
    from (
      select distinct on (e.unidade_id) e.unidade_id, e.desde_coleta_id
      from public.im_unidade_estados e
      join public.im_coletas c on c.id = e.desde_coleta_id
      order by e.unidade_id, c.referencia desc, e.id desc
    ) s
   where u.id = s.unidade_id and u.ultima_coleta_id = p_coleta;

  update public.im_unidades u
     set preco_primeiro = null, preco_primeiro_em = null, preco_de_lancamento = false
   where u.preco_primeiro_em = v_ref
     and not exists (select 1 from public.im_unidade_estados e where e.unidade_id = u.id and e.preco is not null);

  update public.im_unidades u
     set preco_primeiro = x.preco, preco_primeiro_em = x.referencia
    from (
      select distinct on (e.unidade_id) e.unidade_id, e.preco, c.referencia
      from public.im_unidade_estados e
      join public.im_coletas c on c.id = e.desde_coleta_id
      where e.preco is not null
      order by e.unidade_id, c.referencia, e.id
    ) x
   where u.id = x.unidade_id and u.preco_primeiro_em = v_ref;

  update public.im_unidades u
     set preco_de_lancamento = (e.data_lancamento is not null
            and u.preco_primeiro_em <= e.data_lancamento + cfg.dias_preco_lancamento)
    from public.im_empreendimentos e
    join public.im_config cfg on true
   where u.empreendimento_id = e.id and u.preco_primeiro_em = v_ref and u.preco_primeiro is not null;

  update public.im_empreendimentos e set ultima_coleta_id = s.desde_coleta_id
    from (
      select distinct on (st.empreendimento_id) st.empreendimento_id, st.desde_coleta_id
      from public.im_empreendimento_estados st
      join public.im_coletas c on c.id = st.desde_coleta_id
      order by st.empreendimento_id, c.referencia desc, st.id desc
    ) s
   where e.id = s.empreendimento_id and e.ultima_coleta_id = p_coleta;
end $$;

create or replace function public.im_abrir_coleta(
  p_fonte text, p_tipo text, p_referencia date,
  p_esperados int default null, p_bruto_caminho text default null)
returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_id uuid;
  v_caminho text;
begin
  if p_fonte not in ('orulo_api','orulo_planilha','orulo_login','manual') then
    raise exception 'Fonte de mercado desconhecida' using errcode = '23514';
  end if;
  if p_tipo not in ('completa_mensal','lancamentos') then
    raise exception 'Tipo de coleta desconhecido' using errcode = '23514';
  end if;
  if p_referencia is null then
    raise exception 'A coleta precisa da data de referência' using errcode = '23514';
  end if;
  perform public._im_semear();
  v_caminho := coalesce(p_bruto_caminho,
    'mercado/' || to_char(p_referencia, 'YYYY-MM-DD') || '-' || p_tipo || '.json.gz');

  select id into v_id from public.im_coletas
   where fonte = p_fonte and tipo = p_tipo and referencia = p_referencia;
  if found then
    perform public._im_desfazer(v_id);
    update public.im_coletas set
      situacao = 'rodando', iniciada_em = clock_timestamp(), concluida_em = null,
      esperados = p_esperados, coletados = 0, unidades = 0,
      falhas = '[]', tipos_desconhecidos = '{}', bruto_caminho = v_caminho
    where id = v_id;
  else
    insert into public.im_coletas (fonte, tipo, referencia, esperados, bruto_caminho)
    values (p_fonte, p_tipo, p_referencia, p_esperados, v_caminho)
    returning id into v_id;
  end if;
  return v_id;
end $$;

create or replace function public._im_falha(p_coleta uuid, p_id text, p_motivo text, p_detalhe text)
returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.im_coletas c
     set falhas = c.falhas || jsonb_build_array(jsonb_build_object(
           'id_externo', coalesce(p_id, ''), 'motivo', p_motivo, 'detalhe', p_detalhe))
   where c.id = p_coleta
     and not exists (
       select 1 from jsonb_array_elements(c.falhas) e
        where e->>'id_externo' = coalesce(p_id, '') and e->>'motivo' = p_motivo);
end $$;

create or replace function public._im_num(p jsonb, p_chave text)
returns numeric
language sql immutable set search_path = '' as $$
  select case when p->>p_chave is null or btrim(p->>p_chave) = '' then null else (p->>p_chave)::numeric end
$$;

create or replace function public._im_data(p jsonb, p_chave text)
returns date
language sql immutable set search_path = '' as $$
  select case when p->>p_chave is null or btrim(p->>p_chave) = '' then null else (p->>p_chave)::date end
$$;

-- Um empreendimento da carga. O bloco de exceção de im_importar desfaz este item.
create or replace function public._im_importar_um(p_coleta uuid, p jsonb)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  col public.im_coletas;
  cfg public.im_config;
  v_inc jsonb := coalesce(p->'incorporadora', '{}');
  v_fonte_id text := nullif(btrim(coalesce(p->>'id_externo', '')), '');
  v_inc_id text := nullif(btrim(coalesce(v_inc->>'id_externo', '')), '');
  v_cnpj text;
  v_conc uuid;
  v_emp uuid;
  v_mun uuid;
  v_tipo text;
  v_fin text;
  v_sit text;
  v_prog text;
  v_lanc date;
  v_ent date;
  v_tab date;
  v_lat float8;
  v_lng float8;
  v_torres smallint;
  v_unid int;
  v_est int;
  v_marcas text[] := '{}';
  v_tem_lista boolean;
  v_tem_res boolean := false;
  v_passa boolean;
  v_ja boolean;
  u jsonb;
  cls record;
  v_norm_torre text;
  v_norm_num text;
  v_preco numeric;
  v_preco_orig numeric;
  v_marcas_u text[];
  v_area numeric;
  v_uid uuid;
  v_estado uuid;
  v_sit_u text;
  v_desde uuid;
begin
  select * into col from public.im_coletas where id = p_coleta;
  select * into cfg from public.im_config;

  v_cnpj := regexp_replace(coalesce(v_inc->>'cnpj', ''), '\D', '', 'g');
  if length(v_cnpj) <> 14 then v_cnpj := null; end if;

  v_tipo := lower(translate(coalesce(nullif(btrim(p->>'tipo'), ''), 'nao_informado'),
             'áàâãäéèêëíìîïóòôõöúùûüçñ', 'aaaaaeeeeiiiiooooouuuucn'));
  if v_tipo not in ('vertical','horizontal','unidade_avulsa','sobrado','loteamento','industrial','galpao','nao_informado') then
    v_tipo := 'nao_informado';
  end if;
  v_fin := lower(translate(coalesce(nullif(btrim(p->>'finalidade'), ''), 'nao_informado'),
            'áàâãäéèêëíìîïóòôõöúùûüçñ', 'aaaaaeeeeiiiiooooouuuucn'));
  if v_fin not in ('residencial','comercial','misto','nao_informado') then v_fin := 'nao_informado'; end if;
  v_sit := coalesce(nullif(btrim(p->>'situacao'), ''), 'nao_informado');
  if v_sit not in ('lancamento','breve_lancamento','em_obras','pronto','usado','nao_informado') then
    v_sit := 'nao_informado';
  end if;
  v_prog := coalesce(nullif(btrim(p->>'programa_social'), ''), 'nao_informado');
  if v_prog not in ('sim','nao','nao_informado') then v_prog := 'nao_informado'; end if;

  v_lanc := public._im_data(p, 'data_lancamento');
  v_ent := public._im_data(p, 'previsao_entrega');
  v_tab := public._im_data(p, 'tabela_atualizada_em');
  v_lat := (p->>'latitude')::float8;
  v_lng := (p->>'longitude')::float8;
  if v_lat is null or v_lng is null then
    raise exception 'Empreendimento sem coordenada' using errcode = '23514';
  end if;
  v_torres := nullif(p->>'torres', '')::smallint;
  v_unid := nullif(p->>'unidades_informadas', '')::int;
  v_est := nullif(p->>'estoque_informado', '')::int;
  if v_unid is not null and v_unid <= 0 then v_unid := null; end if;
  if v_est is not null and v_unid is not null and v_est > v_unid then
    v_est := null;
    v_marcas := array['estoque_maior_que_unidades'];
  end if;

  v_mun := public.im_municipio_por_nome(p->>'municipio', p->>'uf');

  v_tem_lista := p ? 'unidades' and jsonb_typeof(p->'unidades') = 'array';
  if v_tem_lista then
    for u in select value from jsonb_array_elements(p->'unidades') loop
      select * into cls from public._im_classificar_tipo(u->>'tipo_unidade');
      if cls.residencial then v_tem_res := true; end if;
    end loop;
  end if;

  if v_fonte_id is not null then
    select entidade_id into v_emp from public.im_ids_externos
     where fonte = col.fonte and entidade = 'empreendimento' and id_externo = v_fonte_id;
  end if;

  v_ja := v_emp is not null and exists (
    select 1 from public.im_empreendimento_estados s where s.empreendimento_id = v_emp and s.no_filtro);
  if not v_tem_lista and v_ja then
    v_passa := public._im_passa_filtro(v_tipo, v_fin, v_sit, v_lanc, v_ent, v_mun, col.referencia, true, true);
  elsif not v_tem_lista then
    v_passa := false;
  else
    v_passa := public._im_passa_filtro(v_tipo, v_fin, v_sit, v_lanc, v_ent, v_mun, col.referencia, true, v_tem_res);
  end if;
  if not v_passa and not v_ja then
    perform public._im_falha(p_coleta, v_fonte_id, 'fora_do_filtro', coalesce(p->>'nome', ''));
    return;
  end if;

  if v_inc_id is not null then
    select entidade_id into v_conc from public.im_ids_externos
     where fonte = col.fonte and entidade = 'incorporadora' and id_externo = v_inc_id;
  end if;
  if v_conc is null and v_cnpj is not null then
    select id into v_conc from public.im_incorporadoras where cnpj = v_cnpj;
  end if;
  if v_conc is null and nullif(btrim(coalesce(v_inc->>'nome', '')), '') is not null then
    select id into v_conc from public.im_incorporadoras
     where ativo and public.im_normaliza_nome(nome) = public.im_normaliza_nome(v_inc->>'nome')
     limit 1;
  end if;
  if v_conc is null then
    begin
      insert into public.im_incorporadoras (nome, cnpj)
      values (coalesce(nullif(btrim(v_inc->>'nome'), ''), 'Não informada'), v_cnpj)
      returning id into v_conc;
    exception when unique_violation then
      select id into v_conc from public.im_incorporadoras
       where cnpj is not distinct from v_cnpj
          or lower(btrim(nome)) = lower(btrim(coalesce(nullif(btrim(v_inc->>'nome'), ''), 'Não informada')))
       limit 1;
    end;
  end if;
  if v_inc_id is not null and not exists (
    select 1 from public.im_ids_externos
     where fonte = col.fonte and entidade = 'incorporadora' and entidade_id = v_conc)
  then
    insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
    values ('incorporadora', v_conc, col.fonte, v_inc_id)
    on conflict do nothing;
  end if;

  if v_emp is null then
    insert into public.im_empreendimentos (
      incorporadora_id, nome, latitude, longitude, endereco, municipio_id, bairro,
      unidades, situacao, data_lancamento, previsao_entrega, torres, programa_social, tipo, finalidade,
      origem, link_fonte, primeira_coleta_id, ultima_coleta_id)
    values (
      v_conc, coalesce(nullif(btrim(p->>'nome'), ''), 'Sem nome'), v_lat, v_lng,
      nullif(btrim(p->>'endereco'), ''), v_mun, nullif(btrim(p->>'bairro'), ''),
      case when v_unid is not null and v_unid > 0 then v_unid end, v_sit, v_lanc, v_ent, v_torres, v_prog, v_tipo, v_fin,
      'importado', nullif(btrim(p->>'link_fonte'), ''), p_coleta, p_coleta)
    returning id into v_emp;
    if v_fonte_id is not null then
      insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
      values ('empreendimento', v_emp, col.fonte, v_fonte_id);
    end if;
    insert into public.im_sugestoes_vinculo (empreendimento_id, manual_id, similaridade, distancia_m, coleta_id)
    select v_emp, m.id, public.im_similaridade_trigrama(m.nome, p->>'nome'),
           round(public.im_distancia_m(m.latitude, m.longitude, v_lat, v_lng), 1), p_coleta
      from public.im_empreendimentos m
     where m.origem = 'manual' and m.ativo and m.id <> v_emp
       and public.im_distancia_m(m.latitude, m.longitude, v_lat, v_lng) <= 150
       and public.im_similaridade_trigrama(m.nome, p->>'nome') >= 0.5;
    insert into public.im_empreendimento_estados (
      empreendimento_id, desde_coleta_id, situacao, data_lancamento, previsao_entrega,
      unidades_informadas, estoque_informado, tabela_atualizada_em, no_filtro, marcas)
    values (v_emp, p_coleta, v_sit, v_lanc, v_ent, v_unid, v_est, v_tab, v_passa, v_marcas);
  else
    if (select origem from public.im_empreendimentos where id = v_emp) = 'importado' then
      update public.im_empreendimentos set
        incorporadora_id = v_conc,
        nome = coalesce(nullif(btrim(p->>'nome'), ''), nome),
        latitude = v_lat, longitude = v_lng,
        endereco = coalesce(nullif(btrim(p->>'endereco'), ''), endereco),
        municipio_id = coalesce(v_mun, municipio_id),
        bairro = coalesce(nullif(btrim(p->>'bairro'), ''), bairro),
        unidades = case when v_unid is not null and v_unid > 0 then v_unid else unidades end,
        situacao = v_sit,
        data_lancamento = coalesce(v_lanc, data_lancamento),
        previsao_entrega = coalesce(v_ent, previsao_entrega),
        torres = coalesce(v_torres, torres),
        programa_social = v_prog,
        tipo = v_tipo,
        finalidade = v_fin,
        link_fonte = coalesce(nullif(btrim(p->>'link_fonte'), ''), link_fonte),
        ultima_coleta_id = p_coleta,
        atualizado_em = clock_timestamp()
      where id = v_emp;
    else
      update public.im_empreendimentos
         set ultima_coleta_id = p_coleta, atualizado_em = clock_timestamp()
       where id = v_emp;
    end if;

    select id, desde_coleta_id into v_estado, v_desde from public.im_empreendimento_estados
     where empreendimento_id = v_emp and ate_coleta_id is null;
    if v_desde = p_coleta then
      update public.im_empreendimento_estados set
        situacao = v_sit, data_lancamento = v_lanc, previsao_entrega = v_ent,
        unidades_informadas = v_unid, estoque_informado = v_est, tabela_atualizada_em = v_tab,
        no_filtro = v_passa, marcas = v_marcas
      where id = v_estado;
    elsif v_estado is null or exists (
      select 1 from public.im_empreendimento_estados s
       where s.id = v_estado and (
         s.situacao is distinct from v_sit or s.data_lancamento is distinct from v_lanc
         or s.previsao_entrega is distinct from v_ent or s.unidades_informadas is distinct from v_unid
         or s.estoque_informado is distinct from v_est or s.tabela_atualizada_em is distinct from v_tab
         or s.no_filtro is distinct from v_passa or s.marcas is distinct from v_marcas))
    then
      update public.im_empreendimento_estados set ate_coleta_id = p_coleta where id = v_estado;
      insert into public.im_empreendimento_estados (
        empreendimento_id, desde_coleta_id, situacao, data_lancamento, previsao_entrega,
        unidades_informadas, estoque_informado, tabela_atualizada_em, no_filtro, marcas)
      values (v_emp, p_coleta, v_sit, v_lanc, v_ent, v_unid, v_est, v_tab, v_passa, v_marcas);
    end if;
  end if;

  if not v_tem_lista then
    return;
  end if;

  for u in select value from jsonb_array_elements(p->'unidades') loop
    select * into cls from public._im_classificar_tipo(u->>'tipo_unidade');
    v_norm_torre := public.im_norm_rotulo(u->>'torre');
    v_norm_num := public.im_norm_rotulo(u->>'numero');
    v_preco := public._im_num(u, 'preco');
    v_preco_orig := public._im_num(u, 'preco_original');
    v_marcas_u := '{}';
    if v_preco = 0.1 then
      v_preco := null;
      v_marcas_u := v_marcas_u || array['preco_sentinela'];
    elsif v_preco is not null and v_preco <= 0 then
      v_preco := null;
    end if;
    if v_preco_orig = 0.1 then
      v_preco_orig := null;
      if not ('preco_sentinela' = any(v_marcas_u)) then v_marcas_u := v_marcas_u || array['preco_sentinela']; end if;
    elsif v_preco_orig is not null and v_preco_orig <= 0 then
      v_preco_orig := null;
    end if;
    v_area := public._im_num(u, 'area_privativa_m2');
    if v_area is not null and v_area <= 0 then v_area := null; end if;
    v_sit_u := coalesce(nullif(btrim(u->>'situacao'), ''), 'nao_informado');
    if v_sit_u not in ('disponivel','reservada','vendida','indisponivel','nao_informado') then
      v_sit_u := 'nao_informado';
    end if;

    v_uid := null;
    if nullif(btrim(coalesce(u->>'id_externo', '')), '') is not null then
      select entidade_id into v_uid from public.im_ids_externos
       where fonte = col.fonte and entidade = 'unidade' and id_externo = btrim(u->>'id_externo');
    end if;
    if v_uid is null then
      select id into v_uid from public.im_unidades
       where empreendimento_id = v_emp and torre_norm = v_norm_torre and numero_norm = v_norm_num;
    end if;

    if v_uid is null then
      insert into public.im_unidades (
        empreendimento_id, torre, numero, torre_norm, numero_norm, andar,
        area_privativa_m2, dormitorios, suites, vagas, tipologia_fonte,
        tipo_unidade, tipo_fonte, studio_nr, residencial,
        preco_primeiro, preco_primeiro_em, preco_de_lancamento,
        primeira_coleta_id, ultima_coleta_id, marcas)
      values (
        v_emp, nullif(btrim(u->>'torre'), ''), nullif(btrim(u->>'numero'), ''),
        v_norm_torre, v_norm_num, nullif(u->>'andar', '')::smallint,
        v_area, nullif(u->>'dormitorios', '')::smallint, nullif(u->>'suites', '')::smallint, nullif(u->>'vagas', '')::smallint,
        nullif(btrim(u->>'tipologia_fonte'), ''),
        cls.codigo, cls.fonte, cls.studio_nr, cls.residencial,
        v_preco, case when v_preco is not null then col.referencia end,
        v_preco is not null and v_lanc is not null and col.referencia <= v_lanc + cfg.dias_preco_lancamento,
        p_coleta, p_coleta,
        case when v_area is null then array['area_nula'] else '{}' end)
      returning id into v_uid;
      if nullif(btrim(coalesce(u->>'id_externo', '')), '') is not null then
        insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
        values ('unidade', v_uid, col.fonte, btrim(u->>'id_externo'));
      end if;
      insert into public.im_unidade_estados (unidade_id, desde_coleta_id, situacao, preco, preco_original, marcas)
      values (v_uid, p_coleta, v_sit_u, v_preco, v_preco_orig, v_marcas_u);
    else
      update public.im_unidades set
        torre = coalesce(nullif(btrim(u->>'torre'), ''), torre),
        numero = coalesce(nullif(btrim(u->>'numero'), ''), numero),
        andar = coalesce(nullif(u->>'andar', '')::smallint, andar),
        area_privativa_m2 = coalesce(v_area, area_privativa_m2),
        dormitorios = coalesce(nullif(u->>'dormitorios', '')::smallint, dormitorios),
        suites = coalesce(nullif(u->>'suites', '')::smallint, suites),
        vagas = coalesce(nullif(u->>'vagas', '')::smallint, vagas),
        tipologia_fonte = coalesce(nullif(btrim(u->>'tipologia_fonte'), ''), tipologia_fonte),
        tipo_unidade = cls.codigo,
        tipo_fonte = coalesce(cls.fonte, tipo_fonte),
        studio_nr = cls.studio_nr,
        residencial = cls.residencial,
        ultima_coleta_id = p_coleta,
        removida_coleta_id = null,
        marcas = case when coalesce(v_area, area_privativa_m2) is null then array['area_nula'] else array_remove(marcas, 'area_nula') end
      where id = v_uid;

      if v_preco is not null and (select preco_primeiro is null from public.im_unidades where id = v_uid) then
        update public.im_unidades set
          preco_primeiro = v_preco,
          preco_primeiro_em = col.referencia,
          preco_de_lancamento = v_lanc is not null and col.referencia <= v_lanc + cfg.dias_preco_lancamento
        where id = v_uid;
      end if;

      if nullif(btrim(coalesce(u->>'id_externo', '')), '') is not null and not exists (
        select 1 from public.im_ids_externos
         where fonte = col.fonte and entidade = 'unidade' and entidade_id = v_uid)
      then
        insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
        values ('unidade', v_uid, col.fonte, btrim(u->>'id_externo'))
        on conflict do nothing;
      end if;

      select id, desde_coleta_id into v_estado, v_desde from public.im_unidade_estados
       where unidade_id = v_uid and ate_coleta_id is null;
      if v_desde = p_coleta then
        update public.im_unidade_estados set
          situacao = v_sit_u, preco = v_preco, preco_original = v_preco_orig, marcas = v_marcas_u
        where id = v_estado;
      elsif v_estado is null or exists (
        select 1 from public.im_unidade_estados s where s.id = v_estado
         and (s.situacao is distinct from v_sit_u or s.preco is distinct from v_preco
              or s.preco_original is distinct from v_preco_orig or s.marcas is distinct from v_marcas_u))
      then
        update public.im_unidade_estados set ate_coleta_id = p_coleta where id = v_estado;
        insert into public.im_unidade_estados (unidade_id, desde_coleta_id, situacao, preco, preco_original, marcas)
        values (v_uid, p_coleta, v_sit_u, v_preco, v_preco_orig, v_marcas_u);
      end if;
    end if;
  end loop;
end $$;

create or replace function public.im_importar(p_coleta uuid, p_dados jsonb)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_lista jsonb;
  v_item jsonb;
  v_erros int := 0;
  v_ign int := 0;
begin
  if not exists (select 1 from public.im_coletas where id = p_coleta) then
    raise exception 'Coleta não encontrada' using errcode = 'P0002';
  end if;
  if (select situacao from public.im_coletas where id = p_coleta) <> 'rodando' then
    raise exception 'A coleta não está aberta' using errcode = '23514';
  end if;
  if jsonb_typeof(p_dados) = 'array' then
    v_lista := p_dados;
  elsif jsonb_typeof(p_dados) = 'object' then
    v_lista := jsonb_build_array(p_dados);
  else
    raise exception 'A carga do mercado é uma lista de empreendimentos' using errcode = '22023';
  end if;

  for v_item in select value from jsonb_array_elements(v_lista) loop
    begin
      perform public._im_importar_um(p_coleta, v_item);
    exception when others then
      v_erros := v_erros + 1;
      perform public._im_falha(p_coleta, v_item->>'id_externo', 'erro', sqlerrm);
    end;
  end loop;

  update public.im_coletas c set
    coletados = (select count(*) from public.im_empreendimentos e where e.ultima_coleta_id = p_coleta),
    unidades = (select count(*) from public.im_unidades u where u.ultima_coleta_id = p_coleta),
    tipos_desconhecidos = coalesce((
      select array_agg(distinct u.tipo_fonte order by u.tipo_fonte)
      from public.im_unidades u
      where u.ultima_coleta_id = p_coleta and u.tipo_unidade = 'nao_informado' and u.tipo_fonte is not null), '{}')
  where c.id = p_coleta;

  select count(*) into v_ign from jsonb_array_elements((select falhas from public.im_coletas where id = p_coleta)) e
   where e->>'motivo' = 'fora_do_filtro';
  return jsonb_build_object(
    'coletados', (select coletados from public.im_coletas where id = p_coleta),
    'unidades', (select unidades from public.im_coletas where id = p_coleta),
    'ignorados', v_ign,
    'erros', v_erros);
end $$;

create or replace function public.im_fechar_coleta(
  p_coleta uuid, p_esperados int default null, p_forcar_incompleta boolean default false)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  col public.im_coletas;
  cfg public.im_config;
  v_prev date;
  v_sit text;
  v_vendas int := 0;
  v_removidas int := 0;
  alu record;
  v_agora text;
  v_antes text;
  v_preco numeric;
  v_de date;
  v_med numeric;
  v_anterior int;
begin
  select * into col from public.im_coletas where id = p_coleta;
  if not found then raise exception 'Coleta não encontrada' using errcode = 'P0002'; end if;
  if col.situacao <> 'rodando' then
    raise exception 'A coleta não está aberta' using errcode = '23514';
  end if;
  if p_esperados is not null then
    update public.im_coletas set esperados = p_esperados where id = p_coleta;
    col.esperados := p_esperados;
  end if;
  select * into cfg from public.im_config;
  select coletados into col.coletados from public.im_coletas where id = p_coleta;

  v_sit := 'completa';
  if col.esperados is not null and col.esperados > 0
     and col.coletados < col.esperados * cfg.cobertura_minima_pct / 100.0 then
    v_sit := 'incompleta';
  end if;
  select c.coletados into v_anterior
    from public.im_coletas c
   where c.fonte = col.fonte and c.tipo = 'completa_mensal'
     and c.referencia < col.referencia and c.situacao in ('completa','incompleta') and c.id <> p_coleta
   order by c.referencia desc limit 1;
  if v_anterior is not null and v_anterior > 0 and col.tipo = 'completa_mensal'
     and col.coletados < v_anterior * cfg.cobertura_minima_pct / 100.0 then
    v_sit := 'incompleta';
  end if;
  if coalesce(p_forcar_incompleta, false) then
    v_sit := 'incompleta';
  end if;

  if col.tipo = 'completa_mensal' and v_sit = 'completa' then
    update public.im_unidades n set vinculo_pendente_id = par.antiga
      from (
        with novas as (
          select u.id, u.empreendimento_id, u.area_privativa_m2 as area, u.andar,
                 coalesce(e.preco, u.preco_primeiro) as preco
          from public.im_unidades u
          left join public.im_unidade_estados e on e.unidade_id = u.id and e.ate_coleta_id is null
          where u.primeira_coleta_id = p_coleta and u.vinculo_pendente_id is null
        ),
        sumidas as (
          select u.id, u.empreendimento_id, u.area_privativa_m2 as area, u.andar,
                 coalesce(e.preco, u.preco_primeiro) as preco
          from public.im_unidades u
          join public.im_empreendimentos emp on emp.id = u.empreendimento_id and emp.ultima_coleta_id = p_coleta
          left join public.im_unidade_estados e on e.unidade_id = u.id and e.ate_coleta_id is null
          where u.ultima_coleta_id is distinct from p_coleta and u.removida_coleta_id is null
            and u.primeira_coleta_id is distinct from p_coleta
        ),
        pares as (
          select n.id as nova, s.id as antiga, abs(n.preco - s.preco) / s.preco as dif
          from novas n
          join sumidas s on s.empreendimento_id = n.empreendimento_id
           and s.area is not distinct from n.area
           and s.andar is not distinct from n.andar
           and n.preco is not null and s.preco > 0
           and abs(n.preco - s.preco) / s.preco <= 0.05
        ),
        por_antiga as (
          select distinct on (antiga) nova, antiga, dif from pares order by antiga, dif, nova
        )
        select distinct on (nova) nova, antiga from por_antiga order by nova, dif, antiga
      ) par
     where n.id = par.nova and n.vinculo_pendente_id is null;

    select referencia into v_prev from public.im_coletas
     where fonte = col.fonte and tipo = 'completa_mensal' and situacao = 'completa'
       and referencia < col.referencia and id <> p_coleta
     order by referencia desc limit 1;

    for alu in
      select un.id, un.area_privativa_m2, un.dormitorios, un.vagas, un.empreendimento_id
      from public.im_unidades un
      where un.ultima_coleta_id = p_coleta and un.residencial and un.vinculo_pendente_id is null
    loop
      select e.situacao into v_agora from public.im_unidade_estados e where e.unidade_id = alu.id and e.ate_coleta_id is null;
      v_antes := null;
      if v_prev is not null then
        select e.situacao into v_antes
          from public.im_unidade_estados e
          join public.im_coletas d on d.id = e.desde_coleta_id
         where e.unidade_id = alu.id and d.fonte = col.fonte
           and d.tipo = 'completa_mensal' and d.situacao = 'completa'
           and d.referencia <= v_prev
         order by d.referencia desc, e.id desc
         limit 1;
      end if;

      if v_agora = 'vendida' and v_antes is distinct from 'vendida'
         and not exists (select 1 from public.im_vendas v where v.unidade_id = alu.id and v.desfeita_coleta_id is null)
         and (v_antes in ('disponivel','reservada')
              or exists (
                select 1 from public.im_unidade_estados e
                join public.im_coletas c on c.id = e.desde_coleta_id
                where e.unidade_id = alu.id and e.situacao in ('disponivel','reservada') and c.referencia < col.referencia))
      then
        select e.preco into v_preco
          from public.im_unidade_estados e
          join public.im_coletas c on c.id = e.desde_coleta_id
         where e.unidade_id = alu.id and e.preco is not null
         order by c.referencia desc, e.id desc limit 1;
        select c.referencia into v_de
          from public.im_unidade_estados e
          join public.im_coletas c on c.id = e.desde_coleta_id
         where e.unidade_id = alu.id and e.situacao in ('disponivel','reservada')
         order by c.referencia desc limit 1;
        insert into public.im_vendas (unidade_id, coleta_id, vendida_de, vendida_ate, preco, origem_preco, antes_do_monitor)
        values (alu.id, p_coleta, coalesce(v_de, col.referencia), col.referencia, v_preco, 'ultimo_visto', false);
        v_vendas := v_vendas + 1;
      elsif v_antes is null and v_agora = 'vendida'
         and not exists (
           select 1 from public.im_unidade_estados e
           join public.im_coletas c on c.id = e.desde_coleta_id
                where e.unidade_id = alu.id and e.situacao in ('disponivel','reservada') and c.referencia < col.referencia)
         and not exists (select 1 from public.im_vendas v where v.unidade_id = alu.id and v.desfeita_coleta_id is null)
      then
        v_med := null;
        if alu.area_privativa_m2 is not null and alu.area_privativa_m2 > 0 then
          select percentile_cont(0.5) within group (order by e.preco / un.area_privativa_m2) into v_med
            from public.im_unidades un
            join public.im_unidade_estados e on e.unidade_id = un.id and e.ate_coleta_id is null
           where un.empreendimento_id = alu.empreendimento_id and un.id <> alu.id and un.residencial
             and e.situacao = 'disponivel' and e.preco is not null and un.area_privativa_m2 > 0
             and un.dormitorios is not distinct from alu.dormitorios
             and un.vagas is not distinct from alu.vagas
             and abs(un.area_privativa_m2 - alu.area_privativa_m2) / alu.area_privativa_m2 <= 0.03;
        end if;
        insert into public.im_vendas (unidade_id, coleta_id, vendida_de, vendida_ate, preco, origem_preco, antes_do_monitor)
        values (alu.id, p_coleta, col.referencia, col.referencia,
                case when v_med is not null then round((v_med * alu.area_privativa_m2)::numeric, 2) end,
                'estimado', true);
        v_vendas := v_vendas + 1;
      elsif v_antes = 'vendida' and v_agora in ('disponivel','reservada') then
        update public.im_vendas set desfeita_coleta_id = p_coleta
         where unidade_id = alu.id and desfeita_coleta_id is null;
      end if;
    end loop;

    update public.im_unidades u set removida_coleta_id = p_coleta
      from public.im_empreendimentos emp
     where u.empreendimento_id = emp.id and emp.ultima_coleta_id = p_coleta
       and u.ultima_coleta_id is distinct from p_coleta
       and u.removida_coleta_id is null
       and not exists (select 1 from public.im_unidades n where n.vinculo_pendente_id = u.id);
    get diagnostics v_removidas = row_count;
  end if;

  update public.im_coletas set situacao = v_sit, concluida_em = clock_timestamp() where id = p_coleta;
  return jsonb_build_object('situacao', v_sit, 'vendas', v_vendas, 'removidas', v_removidas);
end $$;

create or replace function public.im_anotar_falha(p_coleta uuid, p_id text, p_motivo text, p_detalhe text)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_sit text;
begin
  select situacao into v_sit from public.im_coletas where id = p_coleta;
  if not found then
    raise exception 'Coleta não encontrada' using errcode = 'P0002';
  end if;
  if v_sit <> 'rodando' then
    raise exception 'A coleta não está aberta' using errcode = '23514';
  end if;
  perform public._im_falha(p_coleta, p_id, p_motivo, p_detalhe);
end $$;

create or replace function public.im_falhar_coleta(p_coleta uuid, p_motivo text)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  col public.im_coletas;
begin
  select * into col from public.im_coletas where id = p_coleta;
  if not found then
    raise exception 'Coleta não encontrada' using errcode = 'P0002';
  end if;
  if col.situacao <> 'rodando' then
    raise exception 'A coleta não está aberta' using errcode = '23514';
  end if;
  perform public._im_falha(p_coleta, '', 'falhou', coalesce(p_motivo, ''));
  update public.im_coletas set situacao = 'falhou', concluida_em = clock_timestamp() where id = p_coleta;
end $$;

create or replace function public.im_confirmar_vinculo_unidade(p_unidade uuid, p_confirmar boolean)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  n public.im_unidades;
  a public.im_unidades;
  est public.im_unidade_estados;
  col public.im_coletas;
  v_sit_antes text;
  v_preco numeric;
  v_de date;
  v_ext text;
begin
  select * into n from public.im_unidades where id = p_unidade;
  if not found or n.vinculo_pendente_id is null then
    raise exception 'Unidade sem vínculo pendente' using errcode = '22023';
  end if;
  select * into a from public.im_unidades where id = n.vinculo_pendente_id;
  select * into est from public.im_unidade_estados where unidade_id = n.id and ate_coleta_id is null;
  select * into col from public.im_coletas where id = n.primeira_coleta_id;

  if not coalesce(p_confirmar, false) then
    update public.im_unidades set vinculo_pendente_id = null where id = n.id;
    update public.im_unidades set removida_coleta_id = coalesce(removida_coleta_id, n.primeira_coleta_id) where id = a.id;
    return;
  end if;

  select id_externo into v_ext from public.im_ids_externos
   where entidade = 'unidade' and entidade_id = n.id limit 1;

  select e.situacao into v_sit_antes from public.im_unidade_estados e where e.unidade_id = a.id and e.ate_coleta_id is null;

  delete from public.im_ids_externos where entidade = 'unidade' and entidade_id = n.id;
  delete from public.im_unidade_estados where unidade_id = n.id;
  delete from public.im_vendas where unidade_id = n.id;
  update public.im_unidades set vinculo_pendente_id = null where id = n.id;
  delete from public.im_unidades where id = n.id;

  update public.im_unidades set
    torre = coalesce(n.torre, torre), numero = coalesce(n.numero, numero),
    torre_norm = n.torre_norm, numero_norm = n.numero_norm,
    andar = coalesce(n.andar, andar),
    area_privativa_m2 = coalesce(n.area_privativa_m2, area_privativa_m2),
    dormitorios = coalesce(n.dormitorios, dormitorios),
    suites = coalesce(n.suites, suites),
    vagas = coalesce(n.vagas, vagas),
    tipo_unidade = n.tipo_unidade, tipo_fonte = coalesce(n.tipo_fonte, tipo_fonte),
    studio_nr = n.studio_nr, residencial = n.residencial,
    ultima_coleta_id = col.id, removida_coleta_id = null
  where id = a.id;

  if v_ext is not null then
    delete from public.im_ids_externos
     where fonte = col.fonte and entidade = 'unidade' and entidade_id = a.id
       and id_externo is distinct from v_ext;
    insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
    values ('unidade', a.id, col.fonte, v_ext)
    on conflict do nothing;
  end if;

  if est.situacao is not null and exists (
    select 1 from public.im_unidade_estados e where e.unidade_id = a.id and e.ate_coleta_id is null
     and (e.situacao is distinct from est.situacao or e.preco is distinct from est.preco
          or e.preco_original is distinct from est.preco_original))
  then
    update public.im_unidade_estados set ate_coleta_id = col.id where unidade_id = a.id and ate_coleta_id is null and desde_coleta_id <> col.id;
    if not exists (select 1 from public.im_unidade_estados where unidade_id = a.id and ate_coleta_id is null) then
      insert into public.im_unidade_estados (unidade_id, desde_coleta_id, situacao, preco, preco_original, marcas)
      values (a.id, col.id, est.situacao, est.preco, est.preco_original, est.marcas);
    end if;
  end if;

  if col.tipo = 'completa_mensal' and col.situacao = 'completa'
     and v_sit_antes in ('disponivel','reservada') and est.situacao = 'vendida'
     and not exists (select 1 from public.im_vendas v where v.unidade_id = a.id and v.desfeita_coleta_id is null)
  then
    select e.preco into v_preco from public.im_unidade_estados e
      join public.im_coletas c on c.id = e.desde_coleta_id
     where e.unidade_id = a.id and e.preco is not null order by c.referencia desc limit 1;
    select max(c.referencia) into v_de from public.im_unidade_estados e
      join public.im_coletas c on c.id = e.desde_coleta_id
     where e.unidade_id = a.id and e.situacao in ('disponivel','reservada');
    insert into public.im_vendas (unidade_id, coleta_id, vendida_de, vendida_ate, preco, origem_preco, antes_do_monitor)
    values (a.id, col.id, coalesce(v_de, col.referencia), col.referencia, v_preco, 'ultimo_visto', false);
  end if;
end $$;

do $$ begin perform public._im_semear(); end $$;

-- Leitura pura: authenticated e service_role. Escrita: só service_role.
-- Internas: ninguém além do dono (as security definer chamam por dentro).
revoke all on function
  public.im_norm_rotulo(text),
  public.im_similaridade_trigrama(text, text),
  public.im_distancia_m(double precision, double precision, double precision, double precision),
  public.im_normaliza_nome(text),
  public.im_municipio_por_nome(text, text)
from public, anon;
grant execute on function
  public.im_norm_rotulo(text),
  public.im_similaridade_trigrama(text, text),
  public.im_distancia_m(double precision, double precision, double precision, double precision),
  public.im_normaliza_nome(text),
  public.im_municipio_por_nome(text, text)
to authenticated, service_role;

revoke all on function
  public.im_salvar_config(jsonb),
  public.im_salvar_tipo_unidade(text, boolean, boolean),
  public.im_abrir_coleta(text, text, date, int, text),
  public.im_importar(uuid, jsonb),
  public.im_fechar_coleta(uuid, int, boolean),
  public.im_anotar_falha(uuid, text, text, text),
  public.im_falhar_coleta(uuid, text),
  public.im_confirmar_vinculo_unidade(uuid, boolean)
from public, anon, authenticated;
grant execute on function
  public.im_salvar_config(jsonb),
  public.im_salvar_tipo_unidade(text, boolean, boolean),
  public.im_abrir_coleta(text, text, date, int, text),
  public.im_importar(uuid, jsonb),
  public.im_fechar_coleta(uuid, int, boolean),
  public.im_anotar_falha(uuid, text, text, text),
  public.im_falhar_coleta(uuid, text),
  public.im_confirmar_vinculo_unidade(uuid, boolean)
to service_role;

revoke all on function
  public._im_residencial(text, boolean),
  public._im_classificar_tipo(text),
  public._im_passa_filtro(text, text, text, date, date, uuid, date, boolean, boolean),
  public._im_semear(),
  public._im_recalcular_residencial(),
  public._im_desfazer(uuid),
  public._im_falha(uuid, text, text, text),
  public._im_num(jsonb, text),
  public._im_data(jsonb, text),
  public._im_importar_um(uuid, jsonb)
from public, anon, authenticated, service_role;

-- Bucket do JSON bruto. Privado. Não mexe no bucket de outro sistema.
insert into storage.buckets (id, name, public)
values ('im-bruto', 'im-bruto', false)
on conflict (id) do nothing;

create policy im_bruto_leitura on storage.objects
  for select to service_role
  using (bucket_id = 'im-bruto');

create policy im_bruto_insercao on storage.objects
  for insert to service_role
  with check (bucket_id = 'im-bruto');

create policy im_bruto_atualizacao on storage.objects
  for update to service_role
  using (bucket_id = 'im-bruto')
  with check (bucket_id = 'im-bruto');

create policy im_bruto_remocao on storage.objects
  for delete to service_role
  using (bucket_id = 'im-bruto');
