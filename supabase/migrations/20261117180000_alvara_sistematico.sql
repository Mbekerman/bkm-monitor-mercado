-- Alvarás (processos de licenciamento) de São Paulo no monitor, de forma sistemática.
-- Pedido do Moisés, 17/nov/2026 ("concordo, vamos levar sim e colocar isso sistematico").
--
-- 1. im_licenciamento_extratos: os extratos do Aprova Digital (dados abertos da prefeitura,
--    conjunto "ad") já carregados. A etapa mensal do coletor só baixa extrato novo ou modificado.
-- 2. Colunas *_alvara em im_empreendimentos: a marcação HIS/HMP que vem do processo conferido
--    (im_conferencia_unidades). Ficam à parte de programa_social, que é do Órulo, é regravado a
--    cada coleta e é chave do agrupamento das unidades: programa_social e os grupos não mudam.
-- 3. Funções (service_role) para a etapa mensal: gravar processos e extratos, achar e gravar o
--    lote dos empreendimentos de SP ainda sem lote, reconferir e marcar.
-- 4. im_tabela_empreendimento devolve também a marcação do alvará (popup do BKM Terrenos).
--
-- A categoria de cada unidade (im_unidades.categoria_social) continua vindo só da tabela de
-- preços: o processo diz se o prédio tem HIS/HMP, não quais nem quantas unidades.
-- Nada aqui reaplica a migration de grupos.

create table if not exists public.im_licenciamento_extratos (
  recurso_id    text primary key,
  nome          text not null,
  url           text not null,
  modificado_em timestamptz,
  processos     integer,
  carregado_em  timestamptz not null default now()
);
comment on table public.im_licenciamento_extratos is
  'Extratos do Aprova Digital (dados abertos da prefeitura de SP, conjunto ad) já carregados em im_licenciamento_processos.';

-- Os seis extratos carregados em 13/nov/2026 (recursos do CKAN naquela data).
insert into public.im_licenciamento_extratos (recurso_id, nome, url, modificado_em, processos, carregado_em) values
  ('9f434c8a-0cda-4172-a9fa-55af7320773e', 'Extrato - Aprova Digital - 2020/2023',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/9f434c8a-0cda-4172-a9fa-55af7320773e/download/extrato_ad_2020-2023.csv',
   '2024-11-28 13:58:47.45649+00', null, '2026-11-13 17:47:37+00'),
  ('3b374394-eede-4f34-8c34-864cddfcde70', 'Extrato - Aprova Digital - 1º Sem./2024',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/3b374394-eede-4f34-8c34-864cddfcde70/download/extrato_ad_2024_1.csv',
   '2024-11-28 14:07:46.464126+00', null, '2026-11-13 17:47:37+00'),
  ('f359e18c-1fa6-450a-beca-d65ef036d594', 'Extrato - Aprova Digital - 2º sem./2024',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/f359e18c-1fa6-450a-beca-d65ef036d594/download/extrato_ad_2024_2.csv',
   '2025-02-26 18:58:24.030544+00', null, '2026-11-13 17:47:37+00'),
  ('01d0a9ab-1bbe-412c-a714-44a2d1a8977b', 'Extrato - Aprova Digital - 1º sem./2025',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/01d0a9ab-1bbe-412c-a714-44a2d1a8977b/download/extrato-aprova-digital-1-sem.-2025.csv',
   '2025-09-03 21:24:53.125675+00', null, '2026-11-13 17:47:37+00'),
  ('bfbe1003-c387-4ef6-a0b1-7bd7fa784d8f', 'Extrato - Aprova Digital - 2º sem./2025',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/bfbe1003-c387-4ef6-a0b1-7bd7fa784d8f/download/extrato-ad-2025-semestre-2.xlsx',
   '2026-03-30 22:28:31.383027+00', null, '2026-11-13 17:47:37+00'),
  ('5a02b880-227d-4e01-b35d-aa2f19bd2025', 'Extrato - Aprova Digital - 1º sem./2026',
   'https://dados.prefeitura.sp.gov.br/dataset/5afd3cf5-29aa-4c5d-8d7f-7780c77a434f/resource/5a02b880-227d-4e01-b35d-aa2f19bd2025/download/extrato-ad-2026-semestre-1-final.xlsx',
   '2026-08-20 16:41:22.254071+00', null, '2026-11-13 17:47:37+00')
on conflict (recurso_id) do nothing;

alter table public.im_empreendimentos
  add column if not exists his_alvara      boolean,
  add column if not exists hmp_alvara      boolean,
  add column if not exists processo_alvara text,
  add column if not exists situacao_alvara text check (situacao_alvara in ('conferido','fase','suspeito')),
  add column if not exists alvara_em       timestamptz;
comment on column public.im_empreendimentos.his_alvara is
  'Processo de licenciamento (SP) do prédio tem HIS. Nulo = sem processo conferido com HIS/HMP. Gravado por im_marcar_alvara; a coleta não mexe.';
comment on column public.im_empreendimentos.hmp_alvara is
  'Processo de licenciamento (SP) do prédio tem HMP. Nulo = sem processo conferido com HIS/HMP. Gravado por im_marcar_alvara; a coleta não mexe.';
comment on column public.im_empreendimentos.processo_alvara is 'Processo (o de mais unidades do prédio) que deu a marcação HIS/HMP.';
comment on column public.im_empreendimentos.situacao_alvara is 'Situação da conferência Órulo × processo: conferido, fase ou suspeito.';

-- Grava processos (lista de objetos com as colunas de im_licenciamento_processos). O mesmo processo
-- em dois extratos: os lotes se somam e o resto vale pelo mais recente.
create or replace function public.im_registrar_processos(p jsonb)
returns integer language plpgsql security definer
set search_path = '' set statement_timeout = '10min' as $$
declare v_n integer;
begin
  insert into public.im_licenciamento_processos as t (
    processo, protocolo, relacionado, data_autuacao, assunto, endereco, categoria, subcategoria,
    his, hmp, r2v, social_no_assunto, modificativo, unidades, area_terreno, area_construir,
    blocos, pavimentos, lotes, origem, carregado_em)
  select x.processo, x.protocolo, x.relacionado, x.data_autuacao, x.assunto, x.endereco, x.categoria, x.subcategoria,
         coalesce(x.his, false), coalesce(x.hmp, false), coalesce(x.r2v, false), coalesce(x.social_no_assunto, false),
         coalesce(x.modificativo, false), x.unidades, x.area_terreno, x.area_construir, x.blocos, x.pavimentos,
         coalesce(x.lotes, '{}'), x.origem, now()
    from jsonb_to_recordset(coalesce(p, '[]'::jsonb)) as x(
      processo text, protocolo text, relacionado text, data_autuacao date, assunto text, endereco text,
      categoria text, subcategoria text, his boolean, hmp boolean, r2v boolean, social_no_assunto boolean,
      modificativo boolean, unidades integer, area_terreno numeric, area_construir numeric, blocos integer,
      pavimentos integer, lotes text[], origem text)
   where nullif(btrim(x.processo), '') is not null and nullif(btrim(x.assunto), '') is not null
  on conflict (processo) do update set
    protocolo = coalesce(excluded.protocolo, t.protocolo),
    relacionado = coalesce(excluded.relacionado, t.relacionado),
    data_autuacao = coalesce(excluded.data_autuacao, t.data_autuacao),
    assunto = excluded.assunto,
    endereco = coalesce(excluded.endereco, t.endereco),
    categoria = coalesce(excluded.categoria, t.categoria),
    subcategoria = coalesce(excluded.subcategoria, t.subcategoria),
    his = excluded.his, hmp = excluded.hmp, r2v = excluded.r2v,
    social_no_assunto = excluded.social_no_assunto, modificativo = excluded.modificativo,
    unidades = coalesce(excluded.unidades, t.unidades),
    area_terreno = coalesce(excluded.area_terreno, t.area_terreno),
    area_construir = coalesce(excluded.area_construir, t.area_construir),
    blocos = coalesce(excluded.blocos, t.blocos),
    pavimentos = coalesce(excluded.pavimentos, t.pavimentos),
    lotes = (select coalesce(array_agg(distinct l order by l), '{}') from unnest(t.lotes || excluded.lotes) l),
    origem = excluded.origem,
    carregado_em = excluded.carregado_em;
  get diagnostics v_n = row_count;
  return v_n;
end $$;

create or replace function public.im_registrar_extrato(p jsonb)
returns void language sql security definer
set search_path = '' as $$
  insert into public.im_licenciamento_extratos (recurso_id, nome, url, modificado_em, processos, carregado_em)
  values (p->>'recurso_id', p->>'nome', p->>'url', (p->>'modificado_em')::timestamptz, (p->>'processos')::int, now())
  on conflict (recurso_id) do update set nome = excluded.nome, url = excluded.url,
    modificado_em = excluded.modificado_em, processos = excluded.processos, carregado_em = excluded.carregado_em;
$$;

-- Empreendimentos ativos de São Paulo (do Órulo) que ainda não têm o lote consultado.
create or replace function public.im_lotes_pendentes()
returns jsonb language sql stable security definer
set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('orulo_id', x.orulo_id, 'lat', x.lat, 'lon', x.lon,
                                               'municipio', 'São Paulo') order by x.orulo_id), '[]'::jsonb)
    from (select distinct on (o.orulo_id) o.orulo_id, e.latitude lat, e.longitude lon
            from public.im_empreendimentos e
            join public.im_municipios m on m.id = e.municipio_id
            cross join lateral (select substring(e.link_fonte from '/buildings/([0-9]+)')::int orulo_id) o
           where e.ativo and m.nome = 'São Paulo' and o.orulo_id is not null
             and not exists (select 1 from public.im_empreendimento_lotes l where l.orulo_id = o.orulo_id)
           order by o.orulo_id, e.atualizado_em desc) x
$$;

-- Grava o lote de cada empreendimento: [{orulo_id, lote (10 dígitos ou nulo), distancia_m}].
create or replace function public.im_registrar_lotes(p jsonb)
returns integer language plpgsql security definer
set search_path = '' as $$
declare v_n integer;
begin
  insert into public.im_empreendimento_lotes as t (orulo_id, lote, distancia_m, consultado_em)
  select x.orulo_id, nullif(x.lote, ''), x.distancia_m, now()
    from jsonb_to_recordset(coalesce(p, '[]'::jsonb)) as x(orulo_id integer, lote text, distancia_m numeric)
   where x.orulo_id is not null
  on conflict (orulo_id) do update set lote = excluded.lote, distancia_m = excluded.distancia_m,
    consultado_em = excluded.consultado_em;
  get diagnostics v_n = row_count;
  return v_n;
end $$;

-- A mesma de 20261113180000_licenciamento, com limite de tempo próprio: chamada pela API, o
-- papel teria o limite curto do PostgREST.
create or replace function public.im_conferir_unidades()
returns json language plpgsql security definer
set search_path = '' set statement_timeout = '10min' as $$
declare v_id integer;
begin
  for v_id in select orulo_id from public.im_empreendimento_lotes loop
    perform public._im_conferir_um(v_id);
  end loop;
  perform public._im_situacao_predios();
  return (select json_object_agg(situacao, n) from
          (select situacao, count(*) n from public.im_conferencia_unidades group by situacao) s);
end $$;

-- Leva a conferência para o cadastro do empreendimento: HIS/HMP do processo, quando o prédio
-- foi conferido (conferido, fase ou suspeito) e o processo tem HIS ou HMP. Quem deixou de casar
-- volta a nulo. Não mexe em programa_social nem nas unidades.
create or replace function public.im_marcar_alvara()
returns json language plpgsql security definer
set search_path = '' set statement_timeout = '10min' as $$
begin
  with alvo as (
    select e.id, c.his, c.hmp, c.processo, c.situacao
      from public.im_empreendimentos e
      join public.im_conferencia_unidades c
        on c.orulo_id = substring(e.link_fonte from '/buildings/([0-9]+)')::int
     where c.situacao in ('conferido','fase','suspeito')
       and (coalesce(c.his, false) or coalesce(c.hmp, false)))
  update public.im_empreendimentos e set
    his_alvara = coalesce(a.his, false), hmp_alvara = coalesce(a.hmp, false),
    processo_alvara = a.processo, situacao_alvara = a.situacao, alvara_em = now()
  from alvo a
  where e.id = a.id
    and (e.his_alvara, e.hmp_alvara, e.processo_alvara, e.situacao_alvara)
        is distinct from (coalesce(a.his, false), coalesce(a.hmp, false), a.processo, a.situacao);

  update public.im_empreendimentos e set
    his_alvara = null, hmp_alvara = null, processo_alvara = null, situacao_alvara = null, alvara_em = now()
  where (e.his_alvara is not null or e.hmp_alvara is not null)
    and not exists (
      select 1 from public.im_conferencia_unidades c
       where c.orulo_id = substring(e.link_fonte from '/buildings/([0-9]+)')::int
         and c.situacao in ('conferido','fase','suspeito')
         and (coalesce(c.his, false) or coalesce(c.hmp, false)));

  return (select json_build_object(
    'marcados', count(*),
    'his', count(*) filter (where his_alvara),
    'hmp', count(*) filter (where hmp_alvara),
    'programa_social_nao', count(*) filter (where programa_social = 'nao'),
    'programa_social_sim', count(*) filter (where programa_social = 'sim'),
    'programa_social_nao_informado', count(*) filter (where programa_social = 'nao_informado'))
    from public.im_empreendimentos where his_alvara or hmp_alvara);
end $$;

-- Popup "Ver tabela completa" do BKM Terrenos: acrescenta a marcação do alvará ao cabeçalho.
create or replace function public.im_tabela_empreendimento(p_id uuid)
returns json
language sql stable security definer
set search_path = public, pg_temp
as $$
  select json_build_object(
    'empreendimento', json_build_object(
      'id', e.id, 'nome', e.nome, 'incorporadora', i.nome, 'cnpj', i.cnpj,
      'endereco', e.endereco, 'bairro', e.bairro, 'torres', e.torres, 'tipo', e.tipo,
      'finalidade', e.finalidade, 'situacao', e.situacao, 'lancamento', e.data_lancamento,
      'entrega', e.previsao_entrega, 'programa_social', e.programa_social, 'link', e.link_fonte,
      'unidades', e.unidades, 'his_alvara', e.his_alvara, 'hmp_alvara', e.hmp_alvara,
      'processo_alvara', e.processo_alvara),
    'unidades', coalesce((
      select json_agg(json_build_object(
          'torre', u.torre, 'numero', u.numero, 'andar', u.andar, 'dormitorios', u.dormitorios,
          'suites', u.suites, 'vagas', u.vagas, 'area', u.area_privativa_m2, 'tipo', u.tipo_unidade,
          'situacao', s.situacao, 'preco', s.preco,
          'primeiro_preco', u.preco_primeiro, 'primeiro_preco_em', u.preco_primeiro_em,
          'vendida_de', v.vendida_de, 'vendida_ate', v.vendida_ate)
        order by u.torre_norm nulls last, u.andar nulls last, u.numero_norm nulls last)
        from public.im_unidades u
        left join public.im_unidade_estados s on s.unidade_id = u.id and s.ate_coleta_id is null
        left join public.im_vendas v on v.unidade_id = u.id and v.desfeita_coleta_id is null and not v.antes_do_monitor
       where u.empreendimento_id = e.id and u.residencial and u.removida_coleta_id is null), '[]'::json))
    from public.im_empreendimentos e
    left join public.im_incorporadoras i on i.id = e.incorporadora_id
   where e.id = p_id
$$;

alter table public.im_licenciamento_extratos enable row level security;
revoke all on table public.im_licenciamento_extratos from public, anon, authenticated;
grant select on table public.im_licenciamento_extratos to authenticated;
grant select, insert, update, delete on table public.im_licenciamento_extratos to service_role;
drop policy if exists im_leitura on public.im_licenciamento_extratos;
create policy im_leitura on public.im_licenciamento_extratos for select to authenticated using (true);
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'im_leitor') then
    grant select on public.im_licenciamento_extratos to im_leitor;
    drop policy if exists im_leitor on public.im_licenciamento_extratos;
    create policy im_leitor on public.im_licenciamento_extratos for select to im_leitor using (true);
  end if;
end $$;

revoke all on function public.im_registrar_processos(jsonb), public.im_registrar_extrato(jsonb),
  public.im_lotes_pendentes(), public.im_registrar_lotes(jsonb), public.im_conferir_unidades(),
  public.im_marcar_alvara() from public, anon, authenticated;
grant execute on function public.im_registrar_processos(jsonb), public.im_registrar_extrato(jsonb),
  public.im_lotes_pendentes(), public.im_registrar_lotes(jsonb), public.im_conferir_unidades(),
  public.im_marcar_alvara() to service_role;
revoke all on function public.im_tabela_empreendimento(uuid) from public;
grant execute on function public.im_tabela_empreendimento(uuid) to anon, authenticated, service_role;
