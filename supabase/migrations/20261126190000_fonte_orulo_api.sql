-- Fonte orulo_api (API oficial v2 do Órulo) no lugar do orulo_login, sem recriar nada.
--
-- O id do empreendimento e da incorporadora é o mesmo nas duas fontes: o número
-- do Órulo. im_importar casava o id pela fonte exata da coleta, então uma coleta
-- orulo_api não acharia nenhum dos 2,2 mil empreendimentos e criaria tudo de novo.
-- Agora as duas fontes usam o mesmo espaço de ids, o de 'orulo_login'
-- (im_fonte_ids): a coleta orulo_api acha e atualiza o empreendimento que já
-- existe e só cria o que é novo. As linhas de im_ids_externos continuam com
-- fonte 'orulo_login', que passa a querer dizer "id do Órulo"; quem lê o id por
-- essa fonte (tabelas de preço, licenciamento, histórico) não muda.
-- im_coletas.fonte continua dizendo de onde veio a rodada (orulo_api).
--
-- O fechamento também procura a mensal anterior pelo mesmo critério: sem isso
-- a primeira mensal pela API não teria anterior, perderia a checagem de 90% e
-- a comparação de situação que grava a venda. _im_desfazer idem.
--
-- Corpos copiados das versões em produção (conferidas por md5 em 26/11/2026):
-- _im_importar_um de 20261024220000_monitor, im_fechar_coleta de
-- 20261025161009_fechar_analisa e _im_desfazer de 20261024220000_monitor.
-- A única mudança neles é a troca de col.fonte por im_fonte_ids(col.fonte).

create or replace function public.im_fonte_ids(p_fonte text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case when p_fonte = 'orulo_api' then 'orulo_login' else p_fonte end;
$$;

comment on function public.im_fonte_ids(text) is
  'Fonte cujo espaço de ids vale para esta fonte: orulo_api usa os ids do orulo_login (o número do Órulo é o mesmo).';

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
     where fonte = public.im_fonte_ids(col.fonte) and entidade = 'empreendimento' and id_externo = v_fonte_id;
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
     where fonte = public.im_fonte_ids(col.fonte) and entidade = 'incorporadora' and id_externo = v_inc_id;
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
     where fonte = public.im_fonte_ids(col.fonte) and entidade = 'incorporadora' and entidade_id = v_conc)
  then
    insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
    values ('incorporadora', v_conc, public.im_fonte_ids(col.fonte), v_inc_id)
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
      values ('empreendimento', v_emp, public.im_fonte_ids(col.fonte), v_fonte_id);
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
       where fonte = public.im_fonte_ids(col.fonte) and entidade = 'unidade' and id_externo = btrim(u->>'id_externo');
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
        values ('unidade', v_uid, public.im_fonte_ids(col.fonte), btrim(u->>'id_externo'));
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
         where fonte = public.im_fonte_ids(col.fonte) and entidade = 'unidade' and entidade_id = v_uid)
      then
        insert into public.im_ids_externos (entidade, entidade_id, fonte, id_externo)
        values ('unidade', v_uid, public.im_fonte_ids(col.fonte), btrim(u->>'id_externo'))
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
  v_anterior int;
  v_fora int := 0;
  v_esp int;
  v_parciais text[];
  v_estimadas int := 0;
begin
  select * into col from public.im_coletas where id = p_coleta for update;
  if not found then raise exception 'Coleta não encontrada' using errcode = 'P0002'; end if;
  if col.situacao <> 'rodando' then
    raise exception 'A coleta não está aberta' using errcode = '23514';
  end if;
  if p_esperados is not null then
    update public.im_coletas set esperados = p_esperados where id = p_coleta;
  end if;
  perform public._im_totais_coleta(p_coleta);
  select * into col from public.im_coletas where id = p_coleta;
  select * into cfg from public.im_config;

  -- Esperados no filtro: tira quem foi anotado fora_do_filtro e não entrou na coleta.
  select count(distinct f->>'id_externo') into v_fora
    from jsonb_array_elements(col.falhas) f
   where f->>'motivo' = 'fora_do_filtro'
     and not exists (
       select 1 from public.im_ids_externos x
       join public.im_empreendimentos e on e.id = x.entidade_id
        where x.fonte = public.im_fonte_ids(col.fonte) and x.entidade = 'empreendimento'
          and x.id_externo = f->>'id_externo' and e.ultima_coleta_id = p_coleta);
  v_esp := case when col.esperados is null then null else greatest(col.esperados - v_fora, 0) end;

  v_sit := 'completa';
  if v_esp is not null and v_esp > 0
     and col.coletados < v_esp * cfg.cobertura_minima_pct / 100.0 then
    v_sit := 'incompleta';
  end if;
  select c.coletados into v_anterior
    from public.im_coletas c
   where public.im_fonte_ids(c.fonte) = public.im_fonte_ids(col.fonte) and c.tipo = 'completa_mensal'
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
    -- Vínculo: unidade nova parecida com uma que sumiu no mesmo empreendimento.
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
     where public.im_fonte_ids(fonte) = public.im_fonte_ids(col.fonte) and tipo = 'completa_mensal' and situacao = 'completa'
       and referencia < col.referencia and id <> p_coleta
     order by referencia desc limit 1;

    -- Unidades residenciais desta coleta, sem vínculo pendente, com a situação de agora.
    create temp table if not exists _im_fc_alu (
      id uuid primary key, empreendimento_id uuid, area numeric, dormitorios smallint, vagas smallint,
      agora text, antes text, tinha_estoque boolean, venda_aberta boolean) on commit drop;
    truncate pg_temp._im_fc_alu;
    insert into pg_temp._im_fc_alu (id, empreendimento_id, area, dormitorios, vagas, agora, venda_aberta)
    select un.id, un.empreendimento_id, un.area_privativa_m2, un.dormitorios, un.vagas, e.situacao,
           exists (select 1 from public.im_vendas v where v.unidade_id = un.id and v.desfeita_coleta_id is null)
      from public.im_unidades un
      left join public.im_unidade_estados e on e.unidade_id = un.id and e.ate_coleta_id is null
     where un.ultima_coleta_id = p_coleta and un.residencial and un.vinculo_pendente_id is null;
    -- Só importa quem pode vender (vendida sem venda aberta) ou desfazer (à venda com venda aberta).
    delete from pg_temp._im_fc_alu a
     where not ((a.agora = 'vendida' and not a.venda_aberta)
             or (a.agora in ('disponivel','reservada') and a.venda_aberta));
    analyze pg_temp._im_fc_alu;

    -- antes: situação na última mensal completa (até v_prev), como na versão com laço.
    if v_prev is not null then
      update pg_temp._im_fc_alu a set antes = x.situacao
        from (
          select distinct on (e.unidade_id) e.unidade_id, e.situacao
            from public.im_unidade_estados e
            join public.im_coletas d on d.id = e.desde_coleta_id
           where e.unidade_id in (select id from pg_temp._im_fc_alu)
             and public.im_fonte_ids(d.fonte) = public.im_fonte_ids(col.fonte) and d.tipo = 'completa_mensal' and d.situacao = 'completa'
             and d.referencia <= v_prev
           order by e.unidade_id, d.referencia desc, e.id desc
        ) x
       where a.id = x.unidade_id;
    end if;
    update pg_temp._im_fc_alu a set tinha_estoque = exists (
      select 1 from public.im_unidade_estados e
      join public.im_coletas c on c.id = e.desde_coleta_id
       where e.unidade_id = a.id and e.situacao in ('disponivel','reservada') and c.referencia < col.referencia)
     where a.agora = 'vendida';
    analyze pg_temp._im_fc_alu;

    -- Venda vista: estava à venda antes (em qualquer coleta anterior) e agora está vendida.
    -- (antes em disponivel/reservada implica tinha_estoque, porque v_prev < referencia.)
    insert into public.im_vendas (unidade_id, coleta_id, vendida_de, vendida_ate, preco, origem_preco, antes_do_monitor)
    select a.id, p_coleta,
           coalesce((select max(c.referencia)
                       from public.im_unidade_estados e join public.im_coletas c on c.id = e.desde_coleta_id
                      where e.unidade_id = a.id and e.situacao in ('disponivel','reservada')), col.referencia),
           col.referencia,
           (select e.preco
              from public.im_unidade_estados e join public.im_coletas c on c.id = e.desde_coleta_id
             where e.unidade_id = a.id and e.preco is not null
             order by c.referencia desc, e.id desc limit 1),
           'ultimo_visto', false
      from pg_temp._im_fc_alu a
     where a.agora = 'vendida' and a.antes is distinct from 'vendida' and a.tinha_estoque;
    get diagnostics v_vendas = row_count;

    -- Vendida antes do monitor: nunca vista à venda. Preço estimado pela mediana do m²
    -- das disponíveis do mesmo empreendimento, dormitórios e vagas, área até 3% diferente.
    with alvo as (
      select a.* from pg_temp._im_fc_alu a
       where a.agora = 'vendida' and a.antes is null and not a.tinha_estoque
    ), disp as (
      select un.id, un.empreendimento_id, un.area_privativa_m2 as area,
             coalesce(un.dormitorios, -1) as dk, coalesce(un.vagas, -1) as vk,
             e.preco / un.area_privativa_m2 as m2
        from public.im_unidades un
        join public.im_unidade_estados e on e.unidade_id = un.id and e.ate_coleta_id is null
       where un.residencial and e.situacao = 'disponivel' and e.preco is not null and un.area_privativa_m2 > 0
         and un.empreendimento_id in (select empreendimento_id from alvo)
    ), med as (
      select a.id, percentile_cont(0.5) within group (order by d.m2) as med
        from alvo a
        join disp d on d.empreendimento_id = a.empreendimento_id
         and d.dk = coalesce(a.dormitorios, -1) and d.vk = coalesce(a.vagas, -1)
         and d.id <> a.id
         and abs(d.area - a.area) / a.area <= 0.03
       where a.area is not null and a.area > 0
       group by a.id
    )
    insert into public.im_vendas (unidade_id, coleta_id, vendida_de, vendida_ate, preco, origem_preco, antes_do_monitor)
    select a.id, p_coleta, col.referencia, col.referencia,
           case when m.med is not null then round((m.med::numeric * a.area)::numeric, 2) end,
           'estimado', true
      from alvo a left join med m on m.id = a.id;
    get diagnostics v_estimadas = row_count;
    v_vendas := v_vendas + v_estimadas;

    -- Voltou à venda: desfaz a venda aberta.
    update public.im_vendas v set desfeita_coleta_id = p_coleta
      from pg_temp._im_fc_alu a
     where v.unidade_id = a.id and v.desfeita_coleta_id is null
       and a.antes = 'vendida' and a.agora in ('disponivel','reservada');

    -- Removidas: sumiram de um empreendimento visto nesta coleta, sem vínculo pendente.
    -- Empreendimento importado só em parte nesta coleta não remove nada.
    select coalesce(array_agg(distinct f->>'id_externo'), '{}') into v_parciais
      from jsonb_array_elements(col.falhas) f where f->>'motivo' = 'importacao_parcial';
    update public.im_unidades u set removida_coleta_id = p_coleta
      from public.im_empreendimentos emp
     where u.empreendimento_id = emp.id and emp.ultima_coleta_id = p_coleta
       and u.ultima_coleta_id is distinct from p_coleta
       and u.removida_coleta_id is null
       and not exists (select 1 from public.im_unidades n where n.vinculo_pendente_id = u.id)
       and not exists (
         select 1 from public.im_ids_externos x
          where x.fonte = public.im_fonte_ids(col.fonte) and x.entidade = 'empreendimento' and x.entidade_id = emp.id
            and x.id_externo = any(v_parciais));
    get diagnostics v_removidas = row_count;
  end if;

  update public.im_coletas set situacao = v_sit, concluida_em = clock_timestamp() where id = p_coleta;
  return jsonb_build_object('situacao', v_sit, 'vendas', v_vendas, 'removidas', v_removidas,
    'coletados', col.coletados, 'unidades', col.unidades,
    'esperados', col.esperados, 'esperados_no_filtro', v_esp, 'fora_do_filtro', v_fora);
end $$;

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
    where public.im_fonte_ids(o.fonte) = public.im_fonte_ids(c.fonte) and o.tipo = c.tipo
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

-- create or replace zera o SET da função: repõe o limite de 10 min do fechamento
-- (migration fechar_timeout) e as permissões de sempre.
alter function public.im_fechar_coleta(uuid, integer, boolean) set statement_timeout = '10min';
revoke all on function public.im_fechar_coleta(uuid, int, boolean) from public, anon, authenticated;
grant execute on function public.im_fechar_coleta(uuid, int, boolean) to service_role;
revoke all on function public._im_importar_um(uuid, jsonb), public._im_desfazer(uuid)
  from public, anon, authenticated, service_role;
