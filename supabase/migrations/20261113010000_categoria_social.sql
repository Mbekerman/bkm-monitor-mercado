-- =====================================================================
-- Categoria social por unidade (HIS1, HIS2, HMP, R2V, NR), lida da tabela
-- de preços em PDF do Órulo. Pedido do Moisés (12/nov/2026).
--
-- im_unidades.categoria_social
--   his1 | his2 | hmp | r2v | nr | nao_informado. Nulo = ainda não processada.
-- im_unidades.categoria_social_fonte
--   tabela_unidade  a linha da unidade na tabela traz a categoria
--   tabela_grupo    regra por tipo na tabela (finais + andares), expandida
--   nota_geral      nota da tabela que vale para o empreendimento todo
--   descricao       descrição do Órulo
--   manual          corrigido à mão; o coletor nunca sobrescreve
--   fora_da_tabela  a tabela tem categoria, mas a unidade não aparece (nao_informado)
--   tabela_sem_categoria  a tabela não fala em HIS/HMP (nao_informado)
--   sem_tabela      o empreendimento não tem tabela no Órulo (nao_informado)
-- im_unidades.categoria_social_em  quando foi gravada.
--
-- im_tabelas_preco: uma linha por tabela (arquivo do Órulo) processada por
-- empreendimento, ou uma linha sem arquivo quando não há tabela. A rodada
-- mensal só baixa tabela de empreendimento que ainda não tem linha aqui.
-- Funções (service_role): im_tabelas_pendentes, im_unidades_para_tabela,
-- im_registrar_tabela_preco. Nada aqui reaplica a migration de grupos.
-- =====================================================================

alter table public.im_unidades
  add column if not exists categoria_social text
    check (categoria_social in ('his1','his2','hmp','r2v','nr','nao_informado')),
  add column if not exists categoria_social_fonte text
    check (categoria_social_fonte in ('tabela_unidade','tabela_grupo','nota_geral','descricao','manual',
                                      'fora_da_tabela','tabela_sem_categoria','sem_tabela')),
  add column if not exists categoria_social_em timestamptz;
comment on column public.im_unidades.categoria_social is
  'HIS1, HIS2, HMP, R2V (mercado) ou NR, lida da tabela de preços do Órulo. nao_informado quando não deu para saber; nulo quando ainda não foi processada.';
comment on column public.im_unidades.categoria_social_fonte is
  'De onde veio categoria_social (tabela_unidade, tabela_grupo, nota_geral, descricao, manual) ou por que é nao_informado (fora_da_tabela, tabela_sem_categoria, sem_tabela).';
create index if not exists im_unidades_categoria_social on public.im_unidades (categoria_social)
  where categoria_social is not null and categoria_social <> 'nao_informado';

create table if not exists public.im_tabelas_preco (
  id                  uuid primary key default gen_random_uuid(),
  empreendimento_id   uuid not null references public.im_empreendimentos(id) on delete cascade,
  fonte               text not null default 'orulo_login',
  id_externo          text not null,
  arquivo_id          text,
  arquivo_nome        text,
  referencia          date,
  url                 text,
  baixado_em          timestamptz,
  processado_em       timestamptz not null default now(),
  status              text not null check (status in ('ok','sem_categoria','sem_tabela','sem_unidades','erro')),
  metodo_texto        text check (metodo_texto in ('pdftotext','ocr')),
  paginas             int,
  unidades_total      int,
  unidades_unidade    int,
  unidades_grupo      int,
  unidades_nota       int,
  categorias          jsonb not null default '{}'::jsonb,
  detalhe             text
);
create unique index if not exists im_tabelas_preco_arquivo
  on public.im_tabelas_preco (empreendimento_id, coalesce(arquivo_id, ''));
create index if not exists im_tabelas_preco_externo on public.im_tabelas_preco (fonte, id_externo);
comment on table public.im_tabelas_preco is
  'Tabela de preços (PDF do Órulo) baixada e lida por empreendimento. Sem arquivo = empreendimento sem tabela. A rodada mensal só baixa de quem não tem linha ok/sem_categoria/sem_unidades/sem_tabela.';

alter table public.im_tabelas_preco enable row level security;
revoke all on table public.im_tabelas_preco from public, anon, authenticated;
grant select on table public.im_tabelas_preco to authenticated;
grant select, insert, update, delete on table public.im_tabelas_preco to service_role;
drop policy if exists im_leitura on public.im_tabelas_preco;
create policy im_leitura on public.im_tabelas_preco for select to authenticated using (true);

-- Empreendimentos ativos dos municípios pedidos que ainda não têm tabela
-- processada. Erro volta a ser tentado. sem_tabela volta a ser tentado só
-- para lançamento / breve lançamento, depois de p_reabrir_dias.
create or replace function public.im_tabelas_pendentes(
  p_municipios text[] default null, p_reabrir_dias int default 25, p_limite int default null)
returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(x order by x->>'id_externo'), '[]'::jsonb) from (
    select jsonb_build_object(
      'empreendimento_id', e.id, 'id_externo', ie.id_externo, 'nome', e.nome,
      'situacao', e.situacao, 'municipio', m.nome) as x
    from public.im_empreendimentos e
    join public.im_ids_externos ie
      on ie.entidade = 'empreendimento' and ie.entidade_id = e.id and ie.fonte = 'orulo_login'
    left join public.im_municipios m on m.id = e.municipio_id
    where e.ativo
      and (p_municipios is null or m.nome = any(p_municipios))
      and not exists (
        select 1 from public.im_tabelas_preco t
        where t.empreendimento_id = e.id
          and t.status in ('ok','sem_categoria','sem_unidades')
      )
      and not exists (
        select 1 from public.im_tabelas_preco t
        where t.empreendimento_id = e.id and t.status = 'sem_tabela'
          and (e.situacao not in ('lancamento','breve_lancamento')
               or t.processado_em > now() - make_interval(days => p_reabrir_dias))
      )
    order by ie.id_externo
    limit p_limite
  ) s
$$;

-- Unidades de um empreendimento para casar com a tabela (jsonb: sem limite de linhas do PostgREST).
create or replace function public.im_unidades_para_tabela(p_emp uuid)
returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', u.id, 'torre', u.torre, 'numero', u.numero, 'torre_norm', u.torre_norm,
    'numero_norm', u.numero_norm, 'andar', u.andar, 'area', u.area_privativa_m2,
    'dormitorios', u.dormitorios, 'removida', u.removida_coleta_id is not null,
    'categoria_social_fonte', u.categoria_social_fonte)), '[]'::jsonb)
  from public.im_unidades u where u.empreendimento_id = p_emp
$$;

-- Grava uma tabela processada e a categoria das unidades, numa transação.
-- p: {empreendimento_id, id_externo, arquivo_id, arquivo_nome, referencia, url,
--     baixado_em, status, metodo_texto, paginas, detalhe,
--     unidades: [{id, categoria, fonte}], resto_fonte}
-- Unidades fora da lista ficam nao_informado com resto_fonte (padrão
-- fora_da_tabela; sem_tabela / tabela_sem_categoria quando é o caso).
-- Status erro só registra a linha, sem mexer em unidade. fonte manual nunca muda.
create or replace function public.im_registrar_tabela_preco(p jsonb)
returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '5min' as $$
declare
  v_emp uuid := (p->>'empreendimento_id')::uuid;
  v_status text := p->>'status';
  v_resto text := coalesce(p->>'resto_fonte', 'fora_da_tabela');
  v_agora timestamptz := now();
  v_cats jsonb;
  v_total int; v_un int; v_gr int; v_nota int;
begin
  if v_emp is null then raise exception 'empreendimento_id obrigatório'; end if;
  if v_status not in ('ok','sem_categoria','sem_tabela','sem_unidades','erro') then
    raise exception 'status inválido: %', v_status;
  end if;
  if v_resto not in ('fora_da_tabela','tabela_sem_categoria','sem_tabela') then
    raise exception 'resto_fonte inválido: %', v_resto;
  end if;

  if v_status <> 'erro' then
    update public.im_unidades u
       set categoria_social = x.categoria,
           categoria_social_fonte = x.fonte,
           categoria_social_em = v_agora
      from jsonb_to_recordset(coalesce(p->'unidades', '[]'::jsonb)) as x(id uuid, categoria text, fonte text)
     where u.id = x.id and u.empreendimento_id = v_emp
       and u.categoria_social_fonte is distinct from 'manual';

    update public.im_unidades u
       set categoria_social = 'nao_informado',
           categoria_social_fonte = v_resto,
           categoria_social_em = v_agora
     where u.empreendimento_id = v_emp
       and u.categoria_social_fonte is distinct from 'manual'
       and (u.categoria_social_em is null or u.categoria_social_em < v_agora);
  end if;

  select coalesce(jsonb_object_agg(c, n), '{}'::jsonb) into v_cats from (
    select coalesce(categoria_social, 'nulo') c, count(*) n
    from public.im_unidades where empreendimento_id = v_emp group by 1) s;
  select count(*),
         count(*) filter (where categoria_social_fonte = 'tabela_unidade'),
         count(*) filter (where categoria_social_fonte = 'tabela_grupo'),
         count(*) filter (where categoria_social_fonte = 'nota_geral')
    into v_total, v_un, v_gr, v_nota
    from public.im_unidades where empreendimento_id = v_emp;

  insert into public.im_tabelas_preco as t (
    empreendimento_id, fonte, id_externo, arquivo_id, arquivo_nome, referencia, url, baixado_em,
    processado_em, status, metodo_texto, paginas, unidades_total, unidades_unidade, unidades_grupo,
    unidades_nota, categorias, detalhe)
  values (
    v_emp, coalesce(p->>'fonte', 'orulo_login'), p->>'id_externo', nullif(p->>'arquivo_id', ''),
    p->>'arquivo_nome', (p->>'referencia')::date, p->>'url', (p->>'baixado_em')::timestamptz,
    v_agora, v_status, p->>'metodo_texto', (p->>'paginas')::int, v_total, v_un, v_gr, v_nota,
    v_cats, left(p->>'detalhe', 2000))
  on conflict (empreendimento_id, (coalesce(arquivo_id, ''))) do update set
    arquivo_nome = excluded.arquivo_nome, referencia = excluded.referencia, url = excluded.url,
    baixado_em = excluded.baixado_em, processado_em = excluded.processado_em, status = excluded.status,
    metodo_texto = excluded.metodo_texto, paginas = excluded.paginas,
    unidades_total = excluded.unidades_total, unidades_unidade = excluded.unidades_unidade,
    unidades_grupo = excluded.unidades_grupo, unidades_nota = excluded.unidades_nota,
    categorias = excluded.categorias, detalhe = excluded.detalhe;

  return jsonb_build_object('unidades', v_total, 'tabela_unidade', v_un, 'tabela_grupo', v_gr,
                            'nota_geral', v_nota, 'categorias', v_cats);
end $$;

revoke all on function
  public.im_tabelas_pendentes(text[], int, int),
  public.im_unidades_para_tabela(uuid),
  public.im_registrar_tabela_preco(jsonb)
from public, anon, authenticated;
grant execute on function
  public.im_tabelas_pendentes(text[], int, int),
  public.im_unidades_para_tabela(uuid),
  public.im_registrar_tabela_preco(jsonb)
to service_role;
