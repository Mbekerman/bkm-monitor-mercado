-- Regras do monitor de mercado, do zero, sem o schema app.
-- Roda como postgres (dono) para a carga e troca de papel para provar o RLS.
\set ON_ERROR_STOP on
\set QUIET on
set client_min_messages = warning;

create function pg_temp.ok(cond boolean, msg text) returns void language plpgsql as $$
begin if not coalesce(cond,false) then raise exception 'FALHOU: %', msg; end if; raise notice 'ok  %', msg; end $$;
create function pg_temp.falha(sqlcmd text, msg text) returns void language plpgsql as $$
begin
  begin execute sqlcmd; exception when others then raise notice 'ok  % (%)', msg, sqlerrm; return; end;
  raise exception 'FALHOU: esperava erro em %', msg;
end $$;
create function pg_temp.uj(p_num text, p_area numeric, p_preco numeric, p_sit text default 'disponivel',
  p_dorm int default 2, p_vaga int default 1, p_andar int default 1, p_tipo text default 'apartamento',
  p_torre text default 'A', p_id text default null) returns jsonb language sql immutable as $$
  select jsonb_strip_nulls(jsonb_build_object(
    'id_externo', p_id, 'torre', p_torre, 'numero', p_num, 'area_privativa_m2', p_area, 'preco', p_preco,
    'situacao', p_sit, 'dormitorios', p_dorm, 'vagas', p_vaga, 'andar', p_andar, 'tipo_unidade', p_tipo)) $$;
create function pg_temp.ej(p_id text, p_nome text, p_uns jsonb, p_tipo text default 'vertical',
  p_fin text default 'residencial', p_sit text default 'em_obras', p_lanc date default null, p_ent date default null,
  p_cidade text default 'São Paulo', p_uf text default 'SP', p_lat float8 default -23.55, p_lng float8 default -46.63)
  returns jsonb language sql stable as $$
  select jsonb_strip_nulls(jsonb_build_object(
    'id_externo', p_id, 'nome', p_nome,
    'incorporadora', jsonb_build_object('id_externo','inc-monitor','nome','Incorporadora Monitor','cnpj','12345678000199'),
    'tipo', p_tipo, 'finalidade', p_fin, 'situacao', p_sit, 'data_lancamento', p_lanc, 'previsao_entrega', p_ent,
    'latitude', p_lat, 'longitude', p_lng, 'municipio', p_cidade, 'uf', p_uf, 'bairro', 'Moema',
    'unidades_informadas', case when p_uns is null then null else jsonb_array_length(p_uns) end,
    'estoque_informado', case when p_uns is null then null else jsonb_array_length(p_uns) end,
    'unidades', p_uns)) $$;
set client_min_messages = notice;

-- ---------------------------------------------------------------------
\echo '# forma do banco'
select id as sp from public.im_municipios where codigo_ibge = '3550308' \gset
select id as sjc from public.im_municipios where codigo_ibge = '3549904' \gset
select pg_temp.ok(not exists (select 1 from pg_namespace where nspname = 'app'), 'não cria o schema app');
select pg_temp.ok(not exists (select 1 from pg_extension where extname in ('postgis','pg_trgm')), 'não instala postgis nem pg_trgm');
select pg_temp.ok(not exists (
  select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'v' and c.relname like 'im_%'), 'não cria vista');
select pg_temp.ok(not exists (
  select 1 from pg_trigger t
  join pg_class c on c.oid = t.tgrelid
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'auth' and c.relname = 'users' and not t.tgisinternal), 'não cria trigger em auth.users');
select pg_temp.ok((select count(*) = 22 from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'im_%'), 'vinte e duas tabelas im_');
select pg_temp.ok((select public = false from storage.buckets where id = 'im-bruto'), 'bucket im-bruto é privado');
select pg_temp.ok(not exists (
  select 1 from pg_policy pol
  where pol.polname like 'im_bruto%'
    and exists (
      select 1 from pg_roles r
      where r.oid = any(pol.polroles) and r.rolname in ('anon','authenticated'))),
  'política do bruto não abre para anon nem authenticated');
select pg_temp.ok(
  has_table_privilege('authenticated', 'public.im_unidades', 'select')
  and not has_table_privilege('authenticated', 'public.im_unidades', 'insert')
  and not has_table_privilege('authenticated', 'public.im_unidades', 'update')
  and not has_table_privilege('authenticated', 'public.im_unidades', 'delete')
  and not has_table_privilege('anon', 'public.im_unidades', 'select')
  and has_function_privilege('service_role', 'public.im_importar(uuid, jsonb)', 'execute')
  and not has_function_privilege('authenticated', 'public.im_importar(uuid, jsonb)', 'execute')
  and not has_function_privilege('anon', 'public.im_abrir_coleta(text, text, date, integer, text)', 'execute')
  and not has_function_privilege('service_role', 'public._im_importar_um(uuid, jsonb)', 'execute'),
  'grants: select para authenticated, escrita da função só para service_role, interna fechada');

select pg_temp.ok((select count(*) = 12 and bool_and((codigo in ('apartamento','studio','cobertura','garden','duplex','loft','triplex')) = residencial)
  from public.im_tipos_unidade), 'tipos de unidade nascem com a config');
select pg_temp.ok((select residencial and ativo from public.im_tipos_unidade where codigo = 'loft')
  and (select residencial and ativo from public.im_tipos_unidade where codigo = 'triplex'), 'loft e triplex nascem residenciais');
select public._im_semear();
select pg_temp.ok((select count(*) = 12 from public.im_tipos_unidade), 'semear de novo não duplica loft nem triplex');
select pg_temp.ok((select meses_lancamento = 6 and dias_preco_lancamento = 60 and meses_recem_entregue = 24
  and nr_conta_como_residencial and :'sp' = any(municipios) and :'sjc' = any(municipios)
  from public.im_config), 'config nasce com os prazos, São Paulo e São José dos Campos');
select pg_temp.ok(public.im_municipio_por_nome('sao paulo', 'SP') = :'sp'
  and public.im_municipio_por_nome('São José dos Campos', 'sp') = :'sjc'
  and public.im_municipio_por_nome('São Paulo', 'RJ') is null
  and public.im_municipio_por_nome('Campinas', 'SP') is null, 'município por nome, sem acento; cidade de fora não entra');
select pg_temp.ok(public.im_norm_rotulo('Torre A') = 'A' and public.im_norm_rotulo('Bloco A') = 'A'
  and public.im_norm_rotulo('Edifício B') = 'B', 'rótulo tira torre, bloco e edifício');
select pg_temp.ok(public.im_distancia_m(-23.55, -46.63, -23.55, -46.63) = 0, 'haversine no mesmo ponto é zero');

set role authenticated;
select pg_temp.falha($$insert into public.im_unidades (empreendimento_id, primeira_coleta_id) values ('00000000-0000-0000-0000-0000000000aa', '00000000-0000-0000-0000-0000000000bb')$$, 'authenticated não insere unidade');
select pg_temp.falha($$select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2026-01-01')$$, 'authenticated não abre coleta');
select pg_temp.falha($$select public.im_salvar_config('{"meses_lancamento":9}')$$, 'authenticated não muda a config');
set role anon;
select pg_temp.falha('select count(*) from public.im_coletas', 'anon não lê a coleta');
set role postgres;
select pg_temp.falha($$select public.im_abrir_coleta('planilha', 'completa_mensal', '2026-01-01')$$, 'fonte desconhecida é recusada');

-- ---------------------------------------------------------------------
\echo '# coleta, venda e cobertura'
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2026-02-01') as c_fev \gset
select pg_temp.ok((select bruto_caminho = 'mercado/2026-02-01-completa_mensal.json.gz' from public.im_coletas where id = :'c_fev'), 'caminho do bruto leva referência e tipo, sem empresa');
select pg_temp.ok((public.im_importar(:'c_fev', jsonb_build_array(pg_temp.ej('vso-velho', 'Estoque Velho',
  (select jsonb_agg(pg_temp.uj(i::text, 45, 450000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'v' || i)) from generate_series(1, 10) i),
  'vertical', 'residencial', 'em_obras', '2026-01-15')))->>'erros')::int = 0, 'fevereiro importa o estoque');
select pg_temp.ok((public.im_fechar_coleta(:'c_fev')->>'situacao') = 'completa', 'primeira mensal sem anterior fecha completa');
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2026-03-01') as c_mar \gset
select pg_temp.ok((public.im_importar(:'c_mar', jsonb_build_array(
  pg_temp.ej('vso-velho', 'Estoque Velho',
    (select jsonb_agg(pg_temp.uj(i::text, 45, 450000, case when i <= 2 then 'vendida' else 'disponivel' end, 2, 1, 1, 'apartamento', 'A', 'v' || i)) from generate_series(1, 10) i),
    'vertical', 'residencial', 'em_obras', '2026-01-15'),
  pg_temp.ej('vso-novo', 'Lancamento Marco',
    (select jsonb_agg(pg_temp.uj(i::text, 38, 380000, 'disponivel', 1, 1, 2, 'apartamento', 'B', 'l' || i)) from generate_series(1, 4) i),
    'vertical', 'residencial', 'lancamento', '2026-03-10')))->>'erros')::int = 0, 'março importa a venda e o lançamento');
select pg_temp.ok((public.im_fechar_coleta(:'c_mar')->>'vendas')::int = 2, 'duas vendas no fechamento de março');
select pg_temp.ok((select count(*) = 2 and bool_and(not antes_do_monitor and origem_preco = 'ultimo_visto' and preco = 450000
  and vendida_de = '2026-02-01' and vendida_ate = '2026-03-01')
  from public.im_vendas v
  join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'empreendimento' and x.entidade_id = u.empreendimento_id and x.id_externo = 'vso-velho'),
  'as duas vendas de março ficam gravadas na tabela');

select public.im_salvar_config('{"cobertura_minima_pct": 1}');

select public.im_abrir_coleta('orulo_planilha', 'completa_mensal', '2026-08-01') as c_ago \gset
select pg_temp.ok((public.im_importar(:'c_ago', jsonb_build_array(pg_temp.ej('sumiu', 'Some Uma', jsonb_build_array(
  pg_temp.uj('1', 42, 420000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'sa'),
  pg_temp.uj('2', 42, 420000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'sb')))))->>'erros')::int = 0, 'agosto grava as duas unidades');
select pg_temp.ok((public.im_fechar_coleta(:'c_ago')->>'situacao') = 'completa', 'agosto fecha completo');
select public.im_salvar_config('{"cobertura_minima_pct": 90}');
select public.im_abrir_coleta('orulo_planilha', 'completa_mensal', '2026-09-01') as c_set \gset
select pg_temp.ok((public.im_importar(:'c_set', jsonb_build_array(pg_temp.ej('sumiu', 'Some Uma', jsonb_build_array(
  pg_temp.uj('1', 42, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 'sa')))))->>'erros')::int = 0, 'setembro manda só a unidade vendida');
select pg_temp.ok((public.im_fechar_coleta(:'c_set', 100)->>'situacao') = 'incompleta', 'cobertura baixa deixa a mensal incompleta');
select pg_temp.ok((select count(*) = 0 from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade_id = u.empreendimento_id and x.id_externo = 'sumiu'), 'incompleta não gera venda');
select pg_temp.ok((select u.removida_coleta_id is null from public.im_unidades u where u.id in (
  select entidade_id from public.im_ids_externos where id_externo = 'sb')), 'incompleta não marca remoção');
select public.im_salvar_config('{"cobertura_minima_pct": 1}');
select public.im_abrir_coleta('orulo_planilha', 'completa_mensal', '2026-09-15') as c_pon \gset
select pg_temp.ok((public.im_importar(:'c_pon', jsonb_build_array(pg_temp.ej('ponte', 'Ponte', jsonb_build_array(
  pg_temp.uj('1', 40, 400000)))))->>'erros')::int = 0 and (public.im_fechar_coleta(:'c_pon')->>'situacao') = 'completa',
  'uma mensal completa de outro empreendimento entra no meio');
select public.im_abrir_coleta('orulo_planilha', 'completa_mensal', '2026-10-01') as c_out \gset
select pg_temp.ok((public.im_importar(:'c_out', jsonb_build_array(pg_temp.ej('sumiu', 'Some Uma', jsonb_build_array(
  pg_temp.uj('1', 42, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 'sa')))))->>'erros')::int = 0, 'outubro confirma a vendida');
select pg_temp.ok((public.im_fechar_coleta(:'c_out')->>'situacao') = 'completa', 'outubro fecha completo');
select pg_temp.ok((select count(*) = 1 and bool_and(not v.antes_do_monitor and v.origem_preco = 'ultimo_visto' and v.preco = 420000
  and v.vendida_de = '2026-08-01' and v.vendida_ate = '2026-10-01' and v.desfeita_coleta_id is null)
  from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'sa'),
  'a passagem sobrevive à incompleta e à mensal do meio');
select pg_temp.ok((select removida_coleta_id = :'c_out' from public.im_unidades u
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'sb'), 'a que sumiu na mensal completa é removida');

-- ---------------------------------------------------------------------
\echo '# filtro, tipo, sugestão e coleta repetida'
insert into public.im_incorporadoras (nome) values ('Incorporadora Manual') returning id as inc_manual \gset
insert into public.im_empreendimentos (incorporadora_id, nome, latitude, longitude, situacao, observacao, origem, municipio_id)
  values (:'inc_manual', 'Residencial Aurora', -23.61, -46.66, 'lancamento', 'não mexer', 'manual', :'sp') returning id as aurora_manual \gset
select public.im_abrir_coleta('manual', 'completa_mensal', '2026-04-01') as c_man \gset
create temp table carga_manual (dados jsonb);
insert into carga_manual values (jsonb_build_array(
  pg_temp.ej('h', 'Horizontal', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'horizontal'),
  pg_temp.ej('s', 'Sobrado', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'sobrado'),
  pg_temp.ej('l', 'Loteamento', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'loteamento'),
  pg_temp.ej('i', 'Industrial', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'industrial'),
  pg_temp.ej('g', 'Galpao', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'galpao'),
  pg_temp.ej('u', 'Avulsa', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'unidade_avulsa'),
  pg_temp.ej('c', 'Comercial', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'comercial'),
  pg_temp.ej('us', 'Usado', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'residencial', 'usado'),
  pg_temp.ej('pv', 'Pronto velho', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'residencial', 'pronto', '2019-01-01', '2020-01-01'),
  pg_temp.ej('cp', 'Campinas', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'residencial', 'em_obras', null, null, 'Campinas', 'SP'),
  pg_temp.ej('sl', 'So loja', jsonb_build_array(pg_temp.uj('1', 30, 300000, 'disponivel', 0, 0, 0, 'loja')), 'vertical', 'misto'),
  pg_temp.ej('vz', 'Sem unidade', null),
  pg_temp.ej('misto-1', 'Misto Centro', jsonb_build_array(
    pg_temp.uj('1', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'ma1'),
    pg_temp.uj('2', 40, 400000, 'disponivel', 2, 1, 2, 'apartamento', 'A', 'ma2'),
    pg_temp.uj('3', 28, 280000, 'disponivel', 0, 0, 0, 'loja', 'A', 'ml'),
    pg_temp.uj('4', 27, 270000, 'disponivel', 0, 1, 1, 'Studio - NR', 'A', 'ms'),
    pg_temp.uj('5', 20, 200000, 'disponivel', 0, 0, 1, 'box', 'A', 'mb')), 'vertical', 'misto'),
  pg_temp.ej('sent', 'Sentinela', jsonb_build_array(pg_temp.uj('1', 40, 0.1)), 'vertical', 'residencial', 'lancamento', '2026-04-01'),
  pg_temp.ej('aurora-imp', 'Residencial Aurora', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'residencial', 'lancamento', '2025-01-01', null, 'São Paulo', 'SP', -23.61, -46.66)
));
select (public.im_importar(:'c_man', dados)->>'ignorados')::int = 12 and (public.im_importar(:'c_man', dados)->>'erros')::int = 0
  as imp_ok from carga_manual \gset
select pg_temp.ok(:'imp_ok', 'fora do filtro não entra; o resto importa');
select pg_temp.ok((select count(*) = 12 from jsonb_array_elements((select falhas from public.im_coletas where id = :'c_man')) f where f->>'motivo' = 'fora_do_filtro'), 'os doze rejeitados ficam no relatório');
select pg_temp.ok((public.im_fechar_coleta(:'c_man')->>'situacao') = 'completa', 'coleta manual fecha');
select (select count(*) from public.im_unidades) as un_antes \gset
select (select count(*) from public.im_empreendimentos where origem = 'importado') as emp_antes \gset
select public.im_abrir_coleta('manual', 'completa_mensal', '2026-04-01') as c_man2 \gset
select pg_temp.ok(:'c_man2' = :'c_man', 'a mesma chave reabre a coleta e desfaz a anterior');
select pg_temp.ok((public.im_importar(:'c_man', (select dados from carga_manual))->>'erros')::int = 0, 'reimportar a mesma carga não dá erro');
select pg_temp.ok((public.im_fechar_coleta(:'c_man')->>'situacao') = 'completa', 'a coleta reaberta fecha de novo');
select pg_temp.ok((select count(*) = :un_antes from public.im_unidades)
  and (select count(*) = :emp_antes from public.im_empreendimentos where origem = 'importado'), 'rodar de novo não duplica unidade nem empreendimento');
select e.id as emisto from public.im_empreendimentos e join public.im_ids_externos x on x.entidade_id = e.id and x.id_externo = 'misto-1' \gset
select pg_temp.ok((select count(*) = 5 from public.im_unidades where empreendimento_id = :'emisto'), 'loja e tipo desconhecido são gravados');
select pg_temp.ok((select not residencial and tipo_unidade = 'loja' from public.im_unidades where empreendimento_id = :'emisto' and numero = '3'), 'loja fica de fora do residencial');
select pg_temp.ok((select tipo_unidade = 'nao_informado' and not residencial and tipo_fonte = 'box' from public.im_unidades where empreendimento_id = :'emisto' and numero = '5'), 'tipo desconhecido vira não informado');
select pg_temp.ok((select tipos_desconhecidos = array['box'] from public.im_coletas where id = :'c_man'), 'o tipo desconhecido entra no relatório da coleta');
select pg_temp.ok((select count(*) filter (where residencial) = 3 from public.im_unidades where empreendimento_id = :'emisto'), 'no misto só apartamento e studio NR ficam residenciais');
select pg_temp.ok((select residencial from public.im_unidades where empreendimento_id = :'emisto' and numero = '4'), 'studio NR conta como residencial');
select public.im_salvar_config('{"nr_conta_como_residencial": false}');
select pg_temp.ok((select not residencial from public.im_unidades where empreendimento_id = :'emisto' and numero = '4'), 'desligar o NR tira o studio do residencial');
select public.im_salvar_config('{"nr_conta_como_residencial": true}');
select pg_temp.ok((select residencial from public.im_unidades where empreendimento_id = :'emisto' and numero = '4'), 'ligar o NR devolve o studio');

\echo '# NR e categoria'
select pg_temp.ok(public.im_e_nr('Fort Ipiranga - NR') and public.im_e_nr('Pin Mooca - NR - Breve Lançamento')
  and public.im_e_nr('TORRE - STUDIO NR') and public.im_e_nr('NÃO RESIDENCIAL') and public.im_e_nr('Torre 2 N.R.')
  and public.im_e_nr('Unidades não-residenciais'), 'NR no nome, na torre e por extenso');
select pg_temp.ok(not public.im_e_nr('Monroe') and not public.im_e_nr('Torre NRA') and not public.im_e_nr('Residencial Aurora')
  and not public.im_e_nr(null), 'NR só como palavra inteira');
select pg_temp.ok((select categoria = 'residencial_nr' and residencial from public.im_unidades where empreendimento_id = :'emisto' and numero = '4'), 'studio NR é residencial_nr');
select pg_temp.ok((select categoria = 'residencial' from public.im_unidades where empreendimento_id = :'emisto' and numero = '1'), 'apartamento comum é residencial');
select pg_temp.ok((select bool_and(categoria = 'nao_residencial') from public.im_unidades where empreendimento_id = :'emisto' and numero in ('3', '5')), 'loja e tipo desconhecido são nao_residencial');
select public.im_abrir_coleta('manual', 'lancamentos', '2026-04-20') as c_nr \gset
select pg_temp.ok((public.im_importar(:'c_nr', jsonb_build_array(
  pg_temp.ej('nr-1', 'Soul Miguel Yunes - NR', jsonb_build_array(
    pg_temp.uj('11', 30, 300000, 'disponivel', 1, 0, 1, 'Apartamento', 'A', 'nr1'),
    pg_temp.uj('12', 30, 300000, 'disponivel', 0, 0, 1, 'loja', 'A', 'nr2'))),
  pg_temp.ej('nr-2', 'Torres Comuns', jsonb_build_array(
    pg_temp.uj('21', 30, 300000, 'disponivel', 1, 0, 2, 'Studio', 'TORRE A NR', 'nr3'),
    pg_temp.uj('22', 30, 300000, 'disponivel', 1, 0, 2, 'Studio', 'TORRE B', 'nr4')))
))->>'ignorados')::int = 0, 'empreendimento NR passa no filtro e é gravado');
select e.id as enr1 from public.im_empreendimentos e join public.im_ids_externos x on x.entidade_id = e.id and x.id_externo = 'nr-1' \gset
select e.id as enr2 from public.im_empreendimentos e join public.im_ids_externos x on x.entidade_id = e.id and x.id_externo = 'nr-2' \gset
select pg_temp.ok((select nr from public.im_empreendimentos where id = :'enr1') and not (select nr from public.im_empreendimentos where id = :'enr2'), 'im_empreendimentos.nr vem do nome');
select pg_temp.ok((select categoria = 'residencial_nr' and residencial and studio_nr from public.im_unidades where empreendimento_id = :'enr1' and numero = '11'), 'apartamento em empreendimento NR é residencial_nr');
select pg_temp.ok((select categoria = 'nao_residencial' and not residencial from public.im_unidades where empreendimento_id = :'enr1' and numero = '12'), 'loja em empreendimento NR segue nao_residencial');
select pg_temp.ok((select categoria = 'residencial_nr' from public.im_unidades where empreendimento_id = :'enr2' and numero = '21')
  and (select categoria = 'residencial' and not studio_nr from public.im_unidades where empreendimento_id = :'enr2' and numero = '22'), 'torre NR marca só as unidades dela');
select public.im_salvar_config('{"nr_conta_como_residencial": false}');
select pg_temp.ok((select not residencial and categoria = 'residencial_nr' from public.im_unidades where empreendimento_id = :'enr1' and numero = '11'), 'desligar o NR tira do residencial e mantém a categoria');
select public.im_salvar_config('{"nr_conta_como_residencial": true}');
update public.im_empreendimentos set nome = 'Soul Miguel Yunes' where id = :'enr1';
select pg_temp.ok((select categoria = 'residencial' and not studio_nr from public.im_unidades where empreendimento_id = :'enr1' and numero = '11'), 'nome sem NR recalcula as unidades');
update public.im_empreendimentos set nome = 'Soul Miguel Yunes - NR' where id = :'enr1';

\echo '# correção de andar e vagas'
select pg_temp.ok((public.im_corrigir_andar_vagas(jsonb_build_array(
  jsonb_build_object('e', 'nr-2', 't', 'Torre A NR', 'n', '21', 'a', null, 'v', null),
  jsonb_build_object('e', 'nr-2', 't', 'TORRE B', 'n', '22', 'a', 2, 'v', 0),
  jsonb_build_object('e', 'nao-existe', 't', 'A', 'n', '1', 'a', 1, 'v', 1)), 'manual')->>'atualizadas')::int = 1,
  'grava só a unidade que difere e ignora empreendimento desconhecido');
select pg_temp.ok((select andar is null and vagas is null from public.im_unidades where empreendimento_id = :'enr2' and numero = '21'), 'null limpa andar e vagas');
select pg_temp.ok((public.im_corrigir_andar_vagas(jsonb_build_array(
  jsonb_build_object('e', 'nr-2', 't', 'Torre A NR', 'n', '21', 'a', null, 'v', null)), 'manual')->>'atualizadas')::int = 0, 'correção é idempotente');
select public.im_corrigir_andar_vagas(jsonb_build_array(
  jsonb_build_object('e', 'nr-2', 't', 'TORRE B', 'n', '22', 'a', -1, 'v', 0)), 'manual') as corr_neg \gset
select pg_temp.ok((:'corr_neg'::jsonb->>'atualizadas')::int = 1
  and (select andar = -1 and vagas = 0 from public.im_unidades
       where empreendimento_id = :'enr2' and numero = '22'),
  'andar negativo de subsolo é gravado');
select pg_temp.ok(not has_function_privilege('authenticated', 'public.im_corrigir_andar_vagas(jsonb, text)', 'execute')
  and has_function_privilege('service_role', 'public.im_corrigir_andar_vagas(jsonb, text)', 'execute'), 'correção só para service_role');
select pg_temp.ok((public.im_fechar_coleta(:'c_nr')->>'situacao') is not null, 'coleta NR fecha');
select pg_temp.ok((select s.preco is null and 'preco_sentinela' = any(s.marcas) from public.im_unidade_estados s
  join public.im_unidades u on u.id = s.unidade_id
  join public.im_ids_externos x on x.entidade_id = u.empreendimento_id and x.id_externo = 'sent'), 'preço 0,1 é sentinela e não entra como preço');
select pg_temp.ok((select observacao = 'não mexer' and origem = 'manual' from public.im_empreendimentos where id = :'aurora_manual'), 'o empreendimento manual não é alterado');
select pg_temp.ok((select count(*) = 1 and min(similaridade) >= 0.5 and min(distancia_m) = 0 from public.im_sugestoes_vinculo where manual_id = :'aurora_manual'), 'nome igual e perto vira sugestão, sem vínculo automático');

-- ---------------------------------------------------------------------
\echo '# venda anterior, distrato, lançamento e vínculo'
select public.im_abrir_coleta('orulo_login', 'completa_mensal', '2026-07-01') as c_ljul \gset
select pg_temp.ok(:'c_ljul' is not null, 'a fonte orulo_login abre coleta');
select pg_temp.ok((public.im_importar(:'c_ljul', jsonb_build_array(
  pg_temp.ej('venda-1', 'Venda Um', jsonb_build_array(
    pg_temp.uj('1', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a1'),
    pg_temp.uj('2', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a2'),
    pg_temp.uj('3', 50, 900000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a3'),
    pg_temp.uj('4', 41, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 's1'),
    pg_temp.uj('5', 30, 300000, 'disponivel', 0, 0, 0, 'loja', 'A', 'lj'),
    pg_temp.uj('101', 36, 300000, 'disponivel', 2, 1, 2, 'apartamento', 'Torre A', null)),
    'vertical', 'residencial', 'em_obras', '2026-01-01'),
  pg_temp.ej('preco-lanc', 'Preco de lancamento', jsonb_build_array(
    pg_temp.uj('1', 40, 410000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'p1')),
    'vertical', 'residencial', 'lancamento', '2026-06-01'),
  pg_temp.ej('so-lanc', 'So lancamentos', jsonb_build_array(
    pg_temp.uj('1', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'sl1'),
    pg_temp.uj('2', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'sl2')))
))->>'erros')::int = 0, 'julho do login importa venda, lançamento e loja');
select pg_temp.ok((public.im_fechar_coleta(:'c_ljul')->>'situacao') = 'completa', 'julho do login fecha');
select pg_temp.ok((select v.antes_do_monitor and v.origem_preco = 'estimado' and v.preco = 410000 and v.vendida_de = '2026-07-01' and v.vendida_ate = '2026-07-01'
  from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 's1'),
  'venda anterior ao monitor usa a mediana dos pares a até 3%');
select pg_temp.ok((select not exists (select 1 from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'lj')), 'loja vendida ou não, não vira venda');
select pg_temp.ok((select preco_de_lancamento from public.im_unidades u
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'p1'), 'preço visto a até 60 dias do lançamento é preço de lançamento');
select public.im_abrir_coleta('orulo_login', 'lancamentos', '2026-07-20') as c_lanc \gset
select pg_temp.ok((public.im_importar(:'c_lanc', jsonb_build_array(pg_temp.ej('so-lanc', 'So lancamentos', jsonb_build_array(
  pg_temp.uj('1', 40, 400000, 'vendida', 2, 1, 1, 'apartamento', 'A', 'sl1')))))->>'erros')::int = 0, 'coleta de lançamentos atualiza quem veio');
select pg_temp.ok((public.im_fechar_coleta(:'c_lanc')->>'vendas')::int = 0, 'coleta de lançamentos não gera venda no fechamento');
select pg_temp.falha(format($$select public.im_fechar_coleta(%L)$$, :'c_lanc'), 'coleta fechada não fecha de novo');
select pg_temp.ok((select removida_coleta_id is null from public.im_unidades u
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'sl2'), 'coleta de lançamentos não remove quem faltou');
select pg_temp.ok((select count(*) = 0 from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'sl1'), 'coleta de lançamentos não gera venda');

select public.im_abrir_coleta('orulo_login', 'completa_mensal', '2026-08-01') as c_lago \gset
select pg_temp.ok((public.im_importar(:'c_lago', jsonb_build_array(
  pg_temp.ej('venda-1', 'Venda Um', jsonb_build_array(
    pg_temp.uj('1', 40, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 'a1'),
    pg_temp.uj('2', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a2'),
    pg_temp.uj('3', 50, 900000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a3'),
    pg_temp.uj('4', 41, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 's1'),
    pg_temp.uj('5', 30, 300000, 'vendida', 0, 0, 0, 'loja', 'A', 'lj'),
    pg_temp.uj('101', 36, 300000, 'disponivel', 2, 1, 2, 'apartamento', 'Bloco A', null)),
    'vertical', 'residencial', 'em_obras', '2026-01-01'),
  pg_temp.ej('preco-lanc', 'Preco de lancamento', jsonb_build_array(
    pg_temp.uj('1', 40, 410000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'p1')),
    'vertical', 'residencial', 'lancamento', '2026-06-01')
))->>'erros')::int = 0, 'agosto marca a venda e normaliza o bloco');
select pg_temp.ok((public.im_fechar_coleta(:'c_lago')->>'situacao') = 'completa', 'agosto do login fecha');
select pg_temp.ok((select count(*) = 1 from public.im_unidades u
  join public.im_empreendimentos e on e.id = u.empreendimento_id
  join public.im_ids_externos x on x.entidade_id = e.id and x.id_externo = 'venda-1'
  where u.numero_norm = '101'), 'Torre A e Bloco A são a mesma unidade');
select pg_temp.ok((select count(*) = 1 and bool_and(not antes_do_monitor and origem_preco = 'ultimo_visto' and preco = 400000
  and vendida_de = '2026-07-01' and vendida_ate = '2026-08-01')
  from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'a1'),
  'disponível que vira vendida gera venda com o último preço');
select pg_temp.ok((select count(*) = 0 from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'lj'), 'loja que passa a vendida continua fora das vendas');
select public.im_abrir_coleta('orulo_login', 'completa_mensal', '2026-09-01') as c_lset \gset
select pg_temp.ok((public.im_importar(:'c_lset', jsonb_build_array(
  pg_temp.ej('venda-1', 'Venda Um', jsonb_build_array(
    pg_temp.uj('1', 40, 410000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a1'),
    pg_temp.uj('2', 40, 400000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a2'),
    pg_temp.uj('3', 50, 900000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'a3'),
    pg_temp.uj('4', 41, null, 'vendida', 2, 1, 1, 'apartamento', 'A', 's1'),
    pg_temp.uj('5', 30, 300000, 'vendida', 0, 0, 0, 'loja', 'A', 'lj'),
    pg_temp.uj('101', 36, 300000, 'disponivel', 2, 1, 2, 'apartamento', 'Bloco A', null)),
    'vertical', 'residencial', 'em_obras', '2026-01-01'),
  pg_temp.ej('preco-lanc', 'Preco de lancamento', jsonb_build_array(
    pg_temp.uj('1', 40, 410000, 'disponivel', 2, 1, 1, 'apartamento', 'A', 'p1'),
    pg_temp.uj('2', 40, 420000, 'disponivel', 2, 1, 2, 'apartamento', 'A', 'p2')),
    'vertical', 'residencial', 'lancamento', '2026-06-01')
))->>'erros')::int = 0, 'setembro desfaz a venda e traz unidade fora da janela');
select pg_temp.ok((public.im_fechar_coleta(:'c_lset')->>'situacao') = 'completa', 'setembro do login fecha');
select pg_temp.ok((select desfeita_coleta_id = :'c_lset' from public.im_vendas v
  join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'a1'), 'voltar a disponível desfaz a venda');
select pg_temp.ok((select not preco_de_lancamento from public.im_unidades u
  join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'p2'), 'preço visto depois de 60 dias não é de lançamento');

select public.im_abrir_coleta('orulo_login', 'completa_mensal', '2026-10-01') as c_v1 \gset
select pg_temp.ok((public.im_importar(:'c_v1', jsonb_build_array(
  pg_temp.ej('liga', 'Liga', jsonb_build_array(pg_temp.uj('101', 40, 400000, 'disponivel', 2, 1, 5, 'apartamento', 'A', 'old-u'))),
  pg_temp.ej('liga-nao', 'Liga Nao', jsonb_build_array(pg_temp.uj('201', 44, 440000, 'disponivel', 2, 1, 4, 'apartamento', 'A', 'old-n')))
))->>'erros')::int = 0 and (public.im_fechar_coleta(:'c_v1')->>'situacao') = 'completa', 'outubro grava as unidades que vão trocar de número');
select public.im_abrir_coleta('orulo_login', 'completa_mensal', '2026-10-20') as c_v2 \gset
select pg_temp.ok((public.im_importar(:'c_v2', jsonb_build_array(
  pg_temp.ej('liga', 'Liga', jsonb_build_array(pg_temp.uj('101A', 40, 400000, 'vendida', 2, 1, 5, 'apartamento', 'A', 'new-u'))),
  pg_temp.ej('liga-nao', 'Liga Nao', jsonb_build_array(pg_temp.uj('201B', 44, 440000, 'disponivel', 2, 1, 4, 'apartamento', 'A', 'new-n')))
))->>'erros')::int = 0, 'a unidade nova chega com a mesma área, andar e preço');
select pg_temp.ok((public.im_fechar_coleta(:'c_v2')->>'situacao') = 'completa', 'o par fica pendente no fechamento');
select u.id as unew from public.im_unidades u join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'new-u' \gset
select u.id as unewn from public.im_unidades u join public.im_ids_externos x on x.entidade = 'unidade' and x.entidade_id = u.id and x.id_externo = 'new-n' \gset
select pg_temp.ok((select vinculo_pendente_id is not null from public.im_unidades where id = :'unew')
  and (select removida_coleta_id is null from public.im_unidades u join public.im_ids_externos x on x.entidade_id = u.id and x.id_externo = 'old-u'),
  'par pendente não remove a unidade antiga');
select public.im_confirmar_vinculo_unidade(:'unew', true);
select pg_temp.ok(not exists (select 1 from public.im_unidades where id = :'unew')
  and exists (select 1 from public.im_ids_externos where id_externo = 'new-u' and entidade_id in (select id from public.im_unidades)),
  'confirmar funde na unidade antiga e leva o id de fora');
select pg_temp.ok((select count(*) = 1 and bool_and(origem_preco = 'ultimo_visto' and not antes_do_monitor)
  from public.im_vendas v join public.im_unidades u on u.id = v.unidade_id
  join public.im_ids_externos x on x.entidade_id = u.id and x.id_externo = 'new-u'), 'o vínculo confirmado gera a venda');
select public.im_confirmar_vinculo_unidade(:'unewn', false);
select pg_temp.ok((select vinculo_pendente_id is null from public.im_unidades where id = :'unewn')
  and (select removida_coleta_id is not null from public.im_unidades u join public.im_ids_externos x on x.entidade_id = u.id and x.id_externo = 'old-n'),
  'recusar o vínculo remove a antiga e fica com a nova');
select pg_temp.falha($$select public.im_confirmar_vinculo_unidade('00000000-0000-0000-0000-0000000000aa', true)$$, 'sem vínculo pendente não confirma');

select public.im_abrir_coleta('orulo_login', 'lancamentos', '2026-10-22') as c_sem_lista \gset
select pg_temp.ok((public.im_importar(:'c_sem_lista', jsonb_build_array(
  pg_temp.ej('venda-1', 'Venda Um', null),
  pg_temp.ej('nunca-vi', 'Nunca Vi', null)
))->>'ignorados')::int = 1, 'lançamentos sem unidades atualiza quem já passou e ignora o novo');
select pg_temp.ok((select e.ultima_coleta_id = :'c_sem_lista' and s.no_filtro and s.situacao = 'em_obras'
  from public.im_empreendimentos e
  join public.im_ids_externos x on x.entidade_id = e.id and x.id_externo = 'venda-1'
  join public.im_empreendimento_estados s on s.empreendimento_id = e.id and s.ate_coleta_id is null),
  'lançamentos sem lista mantém o empreendimento no filtro');
select pg_temp.ok((public.im_fechar_coleta(:'c_sem_lista')->>'vendas')::int = 0, 'lançamentos sem lista não vende');

select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2026-11-01') as c_forca \gset
select pg_temp.ok((public.im_fechar_coleta(:'c_forca', null, true)->>'situacao') = 'incompleta', 'parada na fonte força a coleta incompleta');
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2026-11-02') as c_sem_forca \gset
select pg_temp.ok((public.im_fechar_coleta(:'c_sem_forca')->>'situacao') = 'completa', 'sem esperados e sem parada a mensal nova fecha completa');

select public.im_abrir_coleta('orulo_api', 'lancamentos', '2026-11-03') as c_auth \gset
select public.im_anotar_falha(:'c_auth', '1', 'http', '500 depois das tentativas');
select pg_temp.ok((select falhas->0->>'motivo' = 'http' and falhas->0->>'id_externo' = '1'
  from public.im_coletas where id = :'c_auth'), 'a coleta anota a falha do adaptador');
select public.im_falhar_coleta(:'c_auth', '401 depois do novo login');
select pg_temp.ok((select situacao = 'falhou' and concluida_em is not null from public.im_coletas where id = :'c_auth'), 'segundo 401 encerra a coleta como falhou');
select pg_temp.falha(format($$select public.im_fechar_coleta(%L)$$, :'c_auth'), 'coleta que falhou não fecha');

set role service_role;
select public.im_abrir_coleta('orulo_planilha', 'lancamentos', '2026-11-04') as c_sr \gset
select pg_temp.ok(:'c_sr' is not null, 'service_role abre a coleta');
select public.im_falhar_coleta(:'c_sr', 'encerrar o teste');
set role authenticated;
select pg_temp.ok((select count(*) >= 3 from public.im_unidades) and (select count(*) >= 1 from public.im_vendas) and (select count(*) >= 1 from public.im_coletas), 'authenticated lê as tabelas do monitor');
select pg_temp.falha($$select public.im_falhar_coleta('00000000-0000-0000-0000-0000000000aa', 'x')$$, 'authenticated não marca a coleta como falhou');
set role postgres;

select pg_temp.ok(not exists (
  select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relname in ('empresas','concorrentes','concorrencia_empreendimentos')),
  'não cria tabela do BKM Terrenos');

-- ---------------------------------------------------------------------
\echo '# cobertura no filtro, totais no fechamento e importação parcial'
select public.im_salvar_config('{"cobertura_minima_pct": 90}');
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2027-01-01') as c_cob \gset
select public.im_importar(:'c_cob', jsonb_build_array(
  pg_temp.ej('parc', 'Parcial', jsonb_build_array(pg_temp.uj('1', 40, 400000), pg_temp.uj('2', 40, 400000), pg_temp.uj('3', 40, 400000))),
  pg_temp.ej('ctrl', 'Controle', jsonb_build_array(pg_temp.uj('1', 40, 400000), pg_temp.uj('2', 40, 400000), pg_temp.uj('3', 40, 400000))),
  pg_temp.ej('fc1', 'Comercial 1', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'comercial'),
  pg_temp.ej('fc2', 'Comercial 2', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'comercial'),
  pg_temp.ej('fc3', 'Comercial 3', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'comercial'))) as imp_cob \gset
select pg_temp.ok((:'imp_cob'::jsonb->>'coletados') is null and (:'imp_cob'::jsonb->>'ignorados')::int = 3,
  'im_importar não recalcula os totais; devolve ignorados');
select pg_temp.ok((select coletados = 0 and unidades = 0 from public.im_coletas where id = :'c_cob'), 'os totais ficam para o fechamento');
select public.im_fechar_coleta(:'c_cob', 5) as fc_cob \gset
select pg_temp.ok((:'fc_cob'::jsonb->>'situacao') = 'completa' and (:'fc_cob'::jsonb->>'esperados_no_filtro')::int = 2
  and (:'fc_cob'::jsonb->>'fora_do_filtro')::int = 3 and (:'fc_cob'::jsonb->>'coletados')::int = 2,
  'fora do filtro não conta nos esperados: 2 de 5 visitados fecha completa');
select pg_temp.ok((select coletados = 2 and unidades = 6 and esperados = 5 from public.im_coletas where id = :'c_cob'),
  'o fechamento grava os totais e mantém os esperados do coletor');
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2027-02-01') as c_cob2 \gset
select public.im_importar(:'c_cob2', jsonb_build_array(
  pg_temp.ej('parc', 'Parcial', jsonb_build_array(pg_temp.uj('1', 40, 400000), pg_temp.uj('2', 40, 400000), pg_temp.uj('3', 40, 400000))),
  pg_temp.ej('ctrl', 'Controle', jsonb_build_array(pg_temp.uj('1', 40, 400000), pg_temp.uj('2', 40, 400000), pg_temp.uj('3', 40, 400000))),
  pg_temp.ej('fc1', 'Comercial 1', jsonb_build_array(pg_temp.uj('1', 40, 400000)), 'vertical', 'comercial')));
select public.im_fechar_coleta(:'c_cob2', 6) as fc_cob2 \gset
select pg_temp.ok((:'fc_cob2'::jsonb->>'situacao') = 'incompleta' and (:'fc_cob2'::jsonb->>'esperados_no_filtro')::int = 5,
  'faltar no filtro continua deixando incompleta: 2 de 5 no filtro');
select public.im_abrir_coleta('orulo_api', 'completa_mensal', '2027-03-01') as c_par \gset
select public.im_importar(:'c_par', jsonb_build_array(
  pg_temp.ej('parc', 'Parcial', jsonb_build_array(pg_temp.uj('1', 40, 400000))),
  pg_temp.ej('ctrl', 'Controle', jsonb_build_array(pg_temp.uj('1', 40, 400000), pg_temp.uj('2', 40, 400000)))));
select public.im_anotar_falha(:'c_par', 'parc', 'importacao_parcial', 'parou na parte 2');
select pg_temp.ok((public.im_fechar_coleta(:'c_par')->>'removidas')::int = 1, 'só o controle tem unidade removida');
select pg_temp.ok((select count(*) = 0 from public.im_unidades u join public.im_ids_externos x
    on x.entidade = 'empreendimento' and x.entidade_id = u.empreendimento_id and x.id_externo = 'parc'
   where u.removida_coleta_id = :'c_par'), 'importação parcial não marca remoção');
select pg_temp.ok((select count(*) = 1 from public.im_unidades u join public.im_ids_externos x
    on x.entidade = 'empreendimento' and x.entidade_id = u.empreendimento_id and x.id_externo = 'ctrl'
   where u.removida_coleta_id = :'c_par' and u.numero = '3'), 'a que sumiu do controle é removida');
select pg_temp.ok(not exists (select 1 from pg_class where relname = '_im_fc_alu'), 'tabela de trabalho do fechamento não fica');
select pg_temp.ok((select count(*) = 2 from regexp_matches(
    pg_get_functiondef('public.im_fechar_coleta(uuid,int,boolean)'::regprocedure), 'analyze pg_temp\._im_fc_alu', 'g')),
  'fechamento analisa a tabela de trabalho (sem isso o plano vira laço aninhado)');
select pg_temp.ok((select 'statement_timeout=10min' = any(proconfig) from pg_proc
   where oid = 'public.im_fechar_coleta(uuid,int,boolean)'::regprocedure),
  'fechamento tem statement_timeout próprio de 10 min (a API corta em 8 s)');

-- ---------------------------------------------------------------------
\echo '# resumo do empreendimento: grupos (regra do Moisés, 4/nov/2026)'
create function pg_temp.emp(p_ext text) returns uuid language sql stable as $$
  select entidade_id from public.im_ids_externos where entidade = 'empreendimento' and id_externo = p_ext $$;
-- tamanhos dos grupos abertos, em ordem
create function pg_temp.tam(p_ext text) returns text language sql stable as $$
  select coalesce(string_agg(n::text, ',' order by n), '') from (
    select count(*) n from public.im_unidade_grupos ug join public.im_grupos g on g.id = ug.grupo_id
     where g.empreendimento_id = pg_temp.emp(p_ext) and ug.ate is null and g.encerrado_em is null group by g.id) x $$;
create function pg_temp.peq(p_ext text) returns text language sql stable as $$
  select coalesce(string_agg(case when g.pequeno then 'p' else '-' end || n::text, ',' order by n), '') from (
    select g.id, count(*) n from public.im_unidade_grupos ug join public.im_grupos g on g.id = ug.grupo_id
     where g.empreendimento_id = pg_temp.emp(p_ext) and ug.ate is null and g.encerrado_em is null group by g.id) x
    join public.im_grupos g on g.id = x.id $$;
-- unidades: (número, área, R$/m², andar[, dorm, vagas]); preço vazio quando m2 é nulo
create function pg_temp.us(p jsonb) returns jsonb language sql immutable as $$
  select jsonb_agg(pg_temp.uj(x->>0, (x->>1)::numeric,
    case when x->>2 is null then null else round((x->>1)::numeric * (x->>2)::numeric) end, 'disponivel',
    coalesce((x->>4)::int, 2), coalesce((x->>5)::int, 0), (x->>3)::int)) from jsonb_array_elements(p) x $$;

select public.im_abrir_coleta('orulo_api', 'lancamentos', '2026-11-04') as c_g1 \gset
select public.im_importar(:'c_g1', jsonb_build_array(
  pg_temp.ej('g-area', 'Áreas 40 41 42', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",41,10000,1],["5",41,10000,2],["6",41,10000,3],["7",42,10000,1],["8",42,10000,2],["9",42,10000,3]]')),
  pg_temp.ej('g-4044', 'Áreas 40 e 44', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",44,10000,1],["5",44,10000,2],["6",44,10000,3]]')),
  pg_temp.ej('g-rampa', 'Rampa', (select pg_temp.us(jsonb_agg(jsonb_build_array(a::text, 40, 10000 * 1.01 ^ a, a))) from generate_series(1, 20) a)),
  pg_temp.ej('g-salto', 'Salto', (select pg_temp.us(jsonb_agg(jsonb_build_array(a::text, 40, case when a <= 5 then 10000 else 10500 end, a))) from generate_series(1, 10) a)),
  pg_temp.ej('g-15', 'Mesmo andar 1,5%', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10150,1],["5",40,10150,2],["6",40,10150,3]]')),
  pg_temp.ej('g-25', 'Mesmo andar 2,5%', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10250,1],["5",40,10250,2],["6",40,10250,3]]')),
  pg_temp.ej('g-vaga', 'Com e sem vaga', pg_temp.us('[["1",40,10000,1,2,0],["2",40,10000,2,2,0],["3",40,10000,3,2,0],["4",40,10000,1,2,1],["5",40,10000,2,2,1],["6",40,10000,3,2,1]]')),
  pg_temp.ej('g-peq-preco', 'Pequeno com outro preço', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10000,4],["5",40,10000,5],["6",40,11000,6],["7",40,11000,7]]')),
  pg_temp.ej('g-peq-area', 'Pequeno com outra área', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10000,4],["5",40,10000,5],["6",44,10100,6],["7",44,10100,7]]')),
  pg_temp.ej('g-peq-ambos', 'Pequeno com outra área e outro preço', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10000,4],["5",40,10000,5],["6",44,10500,6],["7",44,10500,7]]')),
  pg_temp.ej('g-peq-dorm', 'Pequeno com outro dormitório', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10000,4],["5",40,10000,5],["6",44,10000,6,3],["7",44,10000,7,3]]')),
  pg_temp.ej('g-sem', 'Sem preço', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",40,10500,4],["5",40,10500,5],["6",40,10500,6],["7",40,null,5]]')),
  pg_temp.ej('g-nula', 'Área nula', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3]]') || jsonb_build_array(pg_temp.uj('4', null, 400000, 'disponivel', 2, 0, 4)))
));
select pg_temp.ok(public.im_fechar_coleta(:'c_g1') is not null, 'coleta dos grupos fechada');
set role service_role;
select pg_temp.ok((public.im_agrupar()->>'unidades')::int > 0, 'service_role agrupa');
set role authenticated;
select pg_temp.falha($$select public.im_agrupar()$$, 'authenticated não agrupa');
select pg_temp.falha($$select public.im_reagrupar()$$, 'authenticated não reagrupa');
select pg_temp.ok((select count(*) > 0 from public.im_grupos), 'authenticated lê os grupos');
set role postgres;
select pg_temp.ok(pg_temp.tam('g-area') = '9', 'áreas 40, 41 e 42 formam um grupo só');
select pg_temp.ok(pg_temp.tam('g-4044') = '3,3', '40 e 44 ficam em grupos separados');
select pg_temp.ok(pg_temp.tam('g-rampa') = '20', 'rampa de 1% em 20 andares fica num grupo só');
select pg_temp.ok(pg_temp.tam('g-salto') = '5,5', 'salto de 5% entre andares separa');
select pg_temp.ok(pg_temp.tam('g-15') = '6', 'mesmo andar com 1,5% fica junto');
select pg_temp.ok(pg_temp.tam('g-25') = '3,3', 'mesmo andar com 2,5% separa');
select pg_temp.ok(pg_temp.tam('g-vaga') = '3,3', 'com e sem vaga não se misturam');
select pg_temp.ok(pg_temp.peq('g-peq-preco') = 'p2,-5', 'grupo de 2 que difere no preço fica separado e pequeno');
select pg_temp.ok(pg_temp.peq('g-peq-area') = 'p7', 'grupo de 2 que difere só na área junta e fica pequeno');
select pg_temp.ok(pg_temp.tam('g-peq-ambos') = '2,5', 'grupo de 2 com outra área e outro preço fica separado');
select pg_temp.ok(pg_temp.tam('g-peq-dorm') = '2,5', 'grupo pequeno não junta com outro dormitório');
select pg_temp.ok((select bool_and(u.andar >= 4) from public.im_unidade_grupos ug join public.im_unidades u on u.id = ug.unidade_id
   where ug.grupo_id = (select ug2.grupo_id from public.im_unidade_grupos ug2 join public.im_unidades u2 on u2.id = ug2.unidade_id
                         where u2.empreendimento_id = pg_temp.emp('g-sem') and u2.preco_primeiro is null)),
  'unidade sem preço vai para o grupo do mesmo andar');
select pg_temp.ok(pg_temp.tam('g-nula') = '3', 'área nula fica fora');
select pg_temp.ok((select (public.im_agrupar()->>'unidades')::int = 0), 'agrupar de novo não mexe em nada');

\echo '# grupos estáveis'
select string_agg(ug.grupo_id::text || ':' || ug.unidade_id, ',' order by ug.unidade_id) as g_antes
  from public.im_unidade_grupos ug join public.im_grupos g on g.id = ug.grupo_id
 where g.empreendimento_id = pg_temp.emp('g-4044') \gset
select public.im_abrir_coleta('orulo_api', 'lancamentos', '2026-11-11') as c_g2 \gset
select public.im_importar(:'c_g2', jsonb_build_array(
  pg_temp.ej('g-4044', 'Áreas 40 e 44', pg_temp.us('[["1",40,10000,1],["2",40,10000,2],["3",40,10000,3],["4",44,10000,1],["5",44,10000,2],["6",44,10000,3],["7",40.5,10050,4],["8",60,9000,4],["9",60,9000,5],["10",60,9000,6]]'))));
select pg_temp.ok(public.im_fechar_coleta(:'c_g2') is not null, 'segunda coleta fechada');
select pg_temp.ok((public.im_agrupar(pg_temp.emp('g-4044'))->>'unidades')::int = 4, 'só as 4 unidades novas são agrupadas');
select pg_temp.ok(pg_temp.tam('g-4044') = '3,3,4', 'a de 40,5 m² entra no grupo de 40 e as de 60 m² abrem grupo novo');
select pg_temp.ok((select string_agg(ug.grupo_id::text || ':' || ug.unidade_id, ',' order by ug.unidade_id)
  from public.im_unidade_grupos ug join public.im_grupos g on g.id = ug.grupo_id join public.im_unidades u on u.id = ug.unidade_id
 where g.empreendimento_id = pg_temp.emp('g-4044') and u.numero not in ('7','8','9','10')) = :'g_antes', 'as unidades antigas não mudam de grupo');
select pg_temp.ok((public.im_reagrupar(pg_temp.emp('g-4044'))->>'unidades')::int = 10, 'reagrupar refaz o empreendimento');
select pg_temp.ok(pg_temp.tam('g-4044') = '3,3,4' and (select count(*) = 10 from public.im_unidade_grupos ug
   join public.im_grupos g on g.id = ug.grupo_id where g.empreendimento_id = pg_temp.emp('g-4044') and ug.ate is not null),
  'a ligação antiga fica com data de fim (a venda guarda o grupo da época)');
select pg_temp.ok(pg_temp.tam('g-area') = '9', 'reagrupar um empreendimento não mexe nos outros');
set role service_role;
select pg_temp.ok((public.im_salvar_mercado_config('{"area_mediana_pct": 5}')).area_mediana_pct = 5, 'service_role muda os limites');
set role postgres;
select pg_temp.ok(not exists (select 1 from pg_class where relname = '_im_ag'), 'tabela de trabalho do agrupamento não fica');

\echo '# histórico mensal do Órulo'
insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
  values ('empreendimento', pg_temp.emp('g-area'), 'orulo_planilha', '999001');
update public.im_empreendimentos set data_lancamento = '2025-11-10' where id = pg_temp.emp('g-area');
insert into public.im_historico_orulo (orulo_id, mes, situacao, unidades, estoque, vendas, distratos) values
  (999001, '2025-11-01', 'lancamento', 100, 100, 0, 0),
  (999001, '2025-12-01', 'lancamento', 100, 70, 30, 0),
  (999001, '2026-01-01', 'em_obras',   100, 60, 10, 0),
  (999001, '2026-02-01', 'em_obras',   100, 90, 0, 30),
  (999001, '2026-03-01', 'em_obras',   100, 55, 35, 0),
  (999001, '2026-04-01', 'em_obras',   100, 50, 5, 0);
select public.im_vso_historico(pg_temp.emp('g-area'))::jsonb as h \gset
select pg_temp.ok((:'h'::jsonb->>'completo')::boolean, 'histórico que começa no lançamento é completo');
select pg_temp.ok(jsonb_array_length(:'h'::jsonb->'mensal') = 6, 'série mensal com os seis meses');
select pg_temp.ok((:'h'::jsonb->'mensal'->1->>'vso')::numeric = 0.3, 'VSO do mês = líquidas ÷ (líquidas + estoque)');
select pg_temp.ok((:'h'::jsonb->'mensal'->3->>'liquidas')::int = -30 and :'h'::jsonb->'mensal'->3->>'vso' is null,
  'mês com mais distrato que venda fica sem VSO');
select pg_temp.ok((:'h'::jsonb->'janelas'->'3m'->>'liquidas')::int = 10 and (:'h'::jsonb->'janelas'->'3m'->>'vso')::numeric = round(10/60.0, 4),
  'recarga de tabela (distrato e venda no mês seguinte) se anula em 3 meses');
select pg_temp.ok((:'h'::jsonb->'janelas'->'todo'->>'vso')::numeric = 0.5, 'VSO do período inteiro');
select pg_temp.ok(jsonb_array_length(:'h'::jsonb->'anos') = 2 and (:'h'::jsonb->'anos'->0->>'liquidas')::int = 30, 'anos fechados');
select pg_temp.ok(jsonb_array_length(:'h'::jsonb->'trimestres') = 3 and jsonb_array_length(:'h'::jsonb->'semestres') = 2, 'trimestres e semestres');
select pg_temp.ok((:'h'::jsonb->'geral'->>'vendido_pct')::numeric = 0.5 and (:'h'::jsonb->'geral'->>'meses_desde_lancamento')::int = 6
  and (:'h'::jsonb->'geral'->>'media_mensal')::numeric = round(0.5/6, 4), 'geral do projeto');
select pg_temp.ok((:'h'::jsonb->'trimestres'->0->>'vso_mes')::numeric = round(1 - power(0.7, 1.0/2), 4),
  'VSO mensal equivalente do trimestre = 1 − (1 − VSO)^(1/meses)');
select pg_temp.ok(:'h'::jsonb->'mensal'->1->>'vso_mes_3m' is null and (:'h'::jsonb->'mensal'->2->>'vso_mes_3m')::numeric = round(1 - power(0.6, 1.0/3), 4),
  'VSO mensal móvel de 3 meses começa no terceiro mês');
select pg_temp.ok((:'h'::jsonb->'vendas'->'marcos'->'25'->>'meses')::int = 2 and (:'h'::jsonb->'vendas'->'marcos'->'25'->>'vso_mes')::numeric = round(1 - power(0.75, 0.5), 4),
  'marco de 25% no segundo mês desde o lançamento');
select pg_temp.ok(:'h'::jsonb->'vendas'->'marcos'->'75'->>'meses' is null and :'h'::jsonb->'vendas'->'marcos'->'99'->>'vso_mes' is null, 'marco não atingido fica nulo');
select pg_temp.ok((:'h'::jsonb->'vendas'->>'quartil_atual')::int = 3 and (:'h'::jsonb->'vendas'->'marcos'->'50'->>'meses')::int = 6,
  'com 50% vendido no sexto mês, está no terceiro quartil');
select pg_temp.ok((:'h'::jsonb->'vendas'->'quartis'->1->>'meses')::int = 4
  and (:'h'::jsonb->'vendas'->'quartis'->1->>'vso_mes')::numeric = round(1 - power(0.5/0.75, 1.0/4), 4),
  'VSO do quartil de 25 a 50% = 1 − (0,50 ÷ 0,75)^(1/4)');
select pg_temp.ok(:'h'::jsonb->'vendas'->'quartis'->2->>'situacao' = 'em_andamento' and :'h'::jsonb->'vendas'->'quartis'->3->>'situacao' = 'futuro',
  'quartil atual em andamento e o seguinte futuro');

select pg_temp.ok((:'h'::jsonb->'geral'->>'vso_mes')::numeric = round(1 - power(0.5, 1.0/6), 4), 'VSO mensal equivalente do projeto');
insert into public.im_historico_orulo (orulo_id, mes, situacao, unidades, estoque, vendas, distratos) values
  (999002, '2025-01-01', 'em_obras', 100, 40, 0, 0), (999002, '2025-02-01', 'em_obras', 100, 20, 20, 0);
select pg_temp.ok((public._im_marcos_orulo(999002, '2024-06-01')->'marcos'->'25'->>'meses') is null
  and (public._im_marcos_orulo(999002, '2024-06-01')->'marcos'->'75'->>'meses')::int = 9,
  'marco já passado antes do histórico fica nulo; o que passa dentro conta desde o lançamento');
update public.im_empreendimentos set data_lancamento = '2024-01-01' where id = pg_temp.emp('g-area');
select pg_temp.ok(not (public.im_vso_historico(pg_temp.emp('g-area'))->>'completo')::boolean, 'lançado antes do histórico não é completo');
select pg_temp.ok(public.im_vso_historico(pg_temp.emp('g-4044')) is null, 'sem número do Órulo devolve nulo');
select pg_temp.falha($$insert into public.im_historico_orulo (orulo_id, mes, unidades, estoque, vendas, distratos) values (1, '2026-01-15', 1, 1, 0, 0)$$, 'mês tem que ser o dia 1');
select pg_temp.emp('g-area') as e_hist \gset
set role anon;
select pg_temp.ok(public.im_vso_historico(:'e_hist') is not null, 'anon lê o histórico pela função');
select pg_temp.falha($$select 1 from public.im_historico_orulo$$, 'anon não lê a tabela');
select pg_temp.falha($$select public._im_vso_orulo(999001, '2026-01-01', '2026-02-01')$$, 'anon não chama a função interna');
set role authenticated;
select pg_temp.falha($$insert into public.im_historico_orulo (orulo_id, mes, unidades, estoque, vendas, distratos) values (2, '2026-01-01', 1, 1, 0, 0)$$, 'authenticated não grava');
set role postgres;

\echo '# categoria social (tabela de preços)'
insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
  values ('empreendimento', pg_temp.emp('g-area'), 'orulo_login', '88001');
select pg_temp.ok((
  select bool_or(x->>'id_externo' = '88001')
  from jsonb_array_elements(public.im_tabelas_pendentes(array['São Paulo'])) x
), 'empreendimento sem tabela entra nos pendentes');
select id as u_cat from public.im_unidades
 where empreendimento_id = pg_temp.emp('g-area') and numero = '1' \gset
select id as u_man from public.im_unidades
 where empreendimento_id = pg_temp.emp('g-area') and numero = '2' \gset
update public.im_unidades
   set categoria_social = 'hmp', categoria_social_fonte = 'manual', categoria_social_em = now()
 where id = :'u_man';
select pg_temp.ok((
  select bool_or(x->>'numero' = '1' and x->>'categoria_social_fonte' is null)
  from jsonb_array_elements(public.im_unidades_para_tabela(pg_temp.emp('g-area'))) x
), 'unidades para a tabela trazem o número e a fonte ainda vazia');
select public.im_registrar_tabela_preco(jsonb_build_object(
  'empreendimento_id', pg_temp.emp('g-area'), 'id_externo', '88001',
  'arquivo_id', '42', 'arquivo_nome', 'tabela_area_novembro_2026.pdf',
  'referencia', '2026-11-01', 'status', 'ok', 'metodo_texto', 'pdftotext', 'paginas', 2,
  'resto_fonte', 'fora_da_tabela',
  'unidades', jsonb_build_array(jsonb_build_object(
    'id', :'u_cat', 'categoria', 'his2', 'fonte', 'tabela_unidade')))) as reg_cat \gset
select pg_temp.ok((:'reg_cat'::jsonb->>'tabela_unidade')::int = 1, 'registra a unidade casada');
select pg_temp.ok((select categoria_social = 'his2' and categoria_social_fonte = 'tabela_unidade'
  from public.im_unidades where id = :'u_cat'), 'unidade da tabela fica HIS2');
select pg_temp.ok((select categoria_social = 'hmp' and categoria_social_fonte = 'manual'
  from public.im_unidades where id = :'u_man'), 'fonte manual não é sobrescrita');
select pg_temp.ok((select count(*) = 7 from public.im_unidades
  where empreendimento_id = pg_temp.emp('g-area')
    and categoria_social = 'nao_informado' and categoria_social_fonte = 'fora_da_tabela'),
  'unidade fora da tabela fica nao_informado');
select public.im_registrar_tabela_preco(jsonb_build_object(
  'empreendimento_id', pg_temp.emp('g-area'), 'id_externo', '88001', 'arquivo_id', '42',
  'arquivo_nome', 'tabela_area_novembro_2026.pdf', 'status', 'ok', 'resto_fonte', 'fora_da_tabela',
  'unidades', jsonb_build_array(jsonb_build_object(
    'id', :'u_cat', 'categoria', 'his1', 'fonte', 'tabela_unidade'))));
select pg_temp.ok((select count(*) = 1 from public.im_tabelas_preco
  where empreendimento_id = pg_temp.emp('g-area') and arquivo_id = '42'),
  'a mesma tabela atualiza a linha, sem duplicar');
select pg_temp.ok((select categoria_social = 'his1' from public.im_unidades where id = :'u_cat'),
  'reprocessar a tabela atualiza a categoria');
select public.im_registrar_tabela_preco(jsonb_build_object(
  'empreendimento_id', pg_temp.emp('g-area'), 'id_externo', '88001', 'arquivo_id', '99',
  'status', 'erro', 'resto_fonte', 'fora_da_tabela', 'detalhe', 'falha de leitura'));
select pg_temp.ok((select categoria_social = 'his1' and categoria_social_fonte = 'tabela_unidade'
    from public.im_unidades where id = :'u_cat')
  and (select status = 'erro' from public.im_tabelas_preco
       where empreendimento_id = pg_temp.emp('g-area') and arquivo_id = '99'),
  'status erro só registra a linha');
select pg_temp.ok(not coalesce((select bool_or(x->>'id_externo' = '88001')
  from jsonb_array_elements(public.im_tabelas_pendentes(array['São Paulo'])) x), false),
  'tabela ok tira o empreendimento dos pendentes');
set role anon;
select pg_temp.falha($$select public.im_registrar_tabela_preco('{}'::jsonb)$$, 'anon não grava a tabela');
select pg_temp.falha($$select public.im_tabelas_pendentes()$$, 'anon não lista pendentes');
select pg_temp.falha($$select 1 from public.im_tabelas_preco$$, 'anon não lê a tabela de preços');
set role authenticated;
select pg_temp.ok((select count(*) >= 1 from public.im_tabelas_preco), 'authenticated lê a tabela de preços');
select pg_temp.falha($$select public.im_registrar_tabela_preco('{}'::jsonb)$$, 'authenticated não grava a tabela');
set role postgres;

\echo '# histórico do Órulo por tipologia'
insert into public.im_historico_orulo_empreendimentos (orulo_id, nome, municipio, uf, lat, lon, unidades, lancamento)
  values (999100, 'Teste Tipologias', 'São Paulo', 'SP', -23.55, -46.63, 120, '2025-01-10');
insert into public.im_historico_orulo_tipologias (orulo_tid, orulo_id, mes, situacao, area_privativa, dormitorios, unidades, estoque, vendas, distratos, preco_m2) values
  (1, 999100, '2025-01-01', 'lancamento', 30.5, 1, 60, 40, 0, 0, 9000),
  (1, 999100, '2025-02-01', 'lancamento', 30.5, 1, 60, 35, 5, 0, 9100),
  (2, 999100, '2025-01-01', 'lancamento', 45, 2, 60, 50, 0, 0, 8500);
select pg_temp.ok((select sum(vendas) = 5 from public.im_historico_orulo_tipologias where orulo_id = 999100), 'grava tipologia por mês');
select pg_temp.falha($$insert into public.im_historico_orulo_tipologias (orulo_tid, orulo_id, mes, estoque, vendas, distratos) values (3, 999100, '2025-01-15', 1, 0, 0)$$, 'mês da tipologia tem que ser o dia 1');
select pg_temp.falha($$insert into public.im_historico_orulo_tipologias (orulo_tid, orulo_id, mes, estoque, vendas, distratos) values (3, 1, '2025-01-01', 1, 0, 0)$$, 'tipologia precisa do empreendimento');
select pg_temp.falha($$insert into public.im_historico_orulo_tipologias (orulo_tid, orulo_id, mes, estoque, vendas, distratos) values (1, 999100, '2025-01-01', 1, 0, 0)$$, 'uma linha por tipologia e mês');
set role anon;
select pg_temp.falha($$select 1 from public.im_historico_orulo_tipologias$$, 'anon não lê as tipologias');
select pg_temp.falha($$select 1 from public.im_historico_orulo_empreendimentos$$, 'anon não lê os empreendimentos do histórico');
set role authenticated;
select pg_temp.ok((select count(*) = 3 from public.im_historico_orulo_tipologias where orulo_id = 999100), 'authenticated lê as tipologias');
select pg_temp.falha($$insert into public.im_historico_orulo_empreendimentos (orulo_id, nome, municipio, uf, lat, lon) values (2, 'x', 'x', 'SP', 0, 0)$$, 'authenticated não grava');
set role postgres;

\echo '# processos de licenciamento e conferência das unidades'
insert into public.im_historico_orulo_empreendimentos (orulo_id, nome, municipio, uf, lat, lon, unidades) values
  (999201, 'A', 'São Paulo', 'SP', -23.5, -46.6, 120), (999202, 'B - Fase 1', 'São Paulo', 'SP', -23.5, -46.6, 60),
  (999208, 'B - Fase 2', 'São Paulo', 'SP', -23.5, -46.6, 60), (999209, 'B - NR', 'São Paulo', 'SP', -23.5, -46.6, 40),
  (999203, 'C', 'São Paulo', 'SP', -23.5, -46.6, 50), (999204, 'D', 'São Paulo', 'SP', -23.5, -46.6, 50),
  (999205, 'E', 'São Paulo', 'SP', -23.5, -46.6, 50), (999206, 'F', 'São Paulo', 'SP', -23.5, -46.6, 102);
insert into public.im_historico_orulo_tipologias (orulo_tid, orulo_id, mes, finalidade, unidades, estoque, vendas, distratos) values
  (9201, 999201, '2025-01-01', 'Residencial', 100, 1, 0, 0), (9201, 999201, '2025-02-01', 'Residencial', 120, 1, 0, 0),
  (9202, 999201, '2025-02-01', 'Comercial', 10, 1, 0, 0),
  (9203, 999202, '2025-02-01', 'Residencial', 60, 1, 0, 0), (9208, 999208, '2025-02-01', 'Residencial', 60, 1, 0, 0),
  (9209, 999209, '2025-02-01', 'Residencial', 40, 1, 0, 0),
  (9204, 999203, '2025-02-01', 'Residencial', 50, 1, 0, 0), (9205, 999204, '2025-02-01', 'Residencial', 50, 1, 0, 0),
  (9206, 999205, '2025-02-01', 'Residencial', 50, 1, 0, 0), (9207, 999206, '2025-02-01', 'Residencial', 102, 1, 0, 0);
insert into public.im_licenciamento_processos (processo, data_autuacao, assunto, unidades, his, lotes) values
  ('P1', '2021-01-10', 'Alvará de Aprovação de Edificação Nova', 100, true, '{0010010001,0010010002}'),
  ('P2', '2022-03-10', 'Alvará de Execução de Edificação Nova', 96, false, '{0010010001}'),
  ('P2b', '2023-03-10', 'Projeto Modificativo de Alvará de Edificação Nova', 4, false, '{0010010002}'),
  ('P3', '2021-05-01', 'Alvará de Aprovação e Execução de Edificação Nova', 150, false, '{0020020005,0020020006}'),
  ('P4', '2021-05-01', 'Alvará de Aprovação e Execução de Edificação Nova', 30, false, '{0030030005}'),
  ('P6', '2023-05-01', 'Alvará de Aprovação e Execução de Edificação Nova', 100, false, '{0060060001}');
insert into public.im_empreendimento_lotes (orulo_id, lote, distancia_m) values
  (999201, '0010010001', 0), (999202, '0020020005', 0), (999208, '0020020006', 0), (999209, '0020020005', 0),
  (999203, '0030030000', 0), (999204, '0040040001', 0), (999205, null, null), (999206, '0060060001', 0);
select pg_temp.falha($$insert into public.im_empreendimento_lotes (orulo_id, lote) values (1, '123')$$, 'lote tem 10 dígitos');
select public.im_conferir_unidades();
select pg_temp.ok((select situacao = 'suspeito' and unidades_orulo = 120 and unidades_processo = 100 and processos = '{P1,P2,P2b}'
  from public.im_conferencia_unidades where orulo_id = 999201),
  'Órulo (só residencial, última situação) acima do processo é suspeito; o prédio junta os processos ligados por lote');
select pg_temp.ok((select processo = 'P1' and his from public.im_conferencia_unidades where orulo_id = 999201),
  'vale o processo de mais unidades, não o modificativo pequeno');
select pg_temp.ok((select bool_and(situacao = 'fase' and unidades_predio = 120) from public.im_conferencia_unidades
  where orulo_id in (999202, 999208)), 'fases do mesmo prédio somam (120 < 150 = fase)');
select pg_temp.ok((select situacao = 'nao_residencial' from public.im_conferencia_unidades where orulo_id = 999209),
  'parte NR fica de fora');
select pg_temp.ok((select situacao = 'sem_processo' from public.im_conferencia_unidades where orulo_id = 999203),
  'condomínio sem o lote exato no processo fica sem processo (não busca na quadra)');
select pg_temp.ok((select situacao = 'sem_processo' from public.im_conferencia_unidades where orulo_id = 999204), 'sem processo');
select pg_temp.ok((select situacao = 'sem_lote' from public.im_conferencia_unidades where orulo_id = 999205), 'sem lote');
select pg_temp.ok((select situacao = 'conferido' from public.im_conferencia_unidades where orulo_id = 999206), 'dentro de 5% confere');
set role anon;
select pg_temp.falha($$select 1 from public.im_conferencia_unidades$$, 'anon não lê a conferência');
select pg_temp.falha($$select public.im_conferir_unidades()$$, 'anon não refaz a conferência');
set role authenticated;
select pg_temp.ok((select count(*) = 8 from public.im_conferencia_unidades where orulo_id between 999201 and 999209), 'authenticated lê a conferência');
select pg_temp.falha($$select public.im_conferir_unidades()$$, 'authenticated não refaz a conferência');
set role postgres;

\echo 'TODOS OS TESTES PASSARAM'
