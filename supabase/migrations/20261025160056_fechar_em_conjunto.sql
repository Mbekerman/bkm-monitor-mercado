-- =====================================================================
-- Monitor de mercado · importação leve e fechamento em conjunto
--
-- Aditiva: só objetos im_*. Mesmas assinaturas e mesmos grants.
--
-- 1. im_importar não recalcula mais os totais da coleta (coletados,
--    unidades, tipos_desconhecidos) a cada chamada. Com ~400 mil unidades
--    isso custava mais de 5 s por chamada, de um limite de 8 s. Os totais
--    passam a ser calculados em _im_totais_coleta, chamada por
--    im_fechar_coleta. O retorno de im_importar mantém ignorados e erros;
--    coletados e unidades voltam nulos (o fechamento devolve os dois).
--
-- 2. im_fechar_coleta sem laço por unidade: venda (com preço visto ou
--    estimado pela mediana), venda desfeita, vínculo e remoção em comandos
--    sobre o conjunto. Mesmas regras e mesmas desempates da versão anterior.
--    Novo: empreendimento com falha importacao_parcial nesta coleta não tem
--    unidade marcada como removida (parte das unidades pode não ter chegado).
--
-- 3. Cobertura: esperados vem do coletor como "empreendimentos visitados",
--    que inclui os que ficam fora do filtro. O limite de cobertura passa a
--    comparar coletados com esperados no filtro: esperados menos os
--    empreendimentos anotados fora_do_filtro nesta coleta (e que não
--    entraram nela). O coletor continua mandando o mesmo número.
--
-- 4. Índices: tipo desconhecido por coleta (parcial, pequeno) e histórico
--    de estado por unidade (o índice vigente é parcial e não serve para o
--    histórico).
-- =====================================================================

create index if not exists im_unidades_nao_informado
  on public.im_unidades (ultima_coleta_id) where tipo_unidade = 'nao_informado';
create index if not exists im_un_estado_unidade
  on public.im_unidade_estados (unidade_id);

create or replace function public._im_totais_coleta(p_coleta uuid)
returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.im_coletas c set
    coletados = (select count(*) from public.im_empreendimentos e where e.ultima_coleta_id = p_coleta),
    unidades = (select count(*) from public.im_unidades u where u.ultima_coleta_id = p_coleta),
    tipos_desconhecidos = coalesce((
      select array_agg(distinct u.tipo_fonte order by u.tipo_fonte)
      from public.im_unidades u
      where u.ultima_coleta_id = p_coleta and u.tipo_unidade = 'nao_informado' and u.tipo_fonte is not null), '{}')
  where c.id = p_coleta;
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

  -- Totais da coleta: só no fechamento (_im_totais_coleta).
  select count(*) into v_ign from jsonb_array_elements((select falhas from public.im_coletas where id = p_coleta)) e
   where e->>'motivo' = 'fora_do_filtro';
  return jsonb_build_object('coletados', null, 'unidades', null, 'ignorados', v_ign, 'erros', v_erros);
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
        where x.fonte = col.fonte and x.entidade = 'empreendimento'
          and x.id_externo = f->>'id_externo' and e.ultima_coleta_id = p_coleta);
  v_esp := case when col.esperados is null then null else greatest(col.esperados - v_fora, 0) end;

  v_sit := 'completa';
  if v_esp is not null and v_esp > 0
     and col.coletados < v_esp * cfg.cobertura_minima_pct / 100.0 then
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
     where fonte = col.fonte and tipo = 'completa_mensal' and situacao = 'completa'
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

    -- antes: situação na última mensal completa (até v_prev), como na versão com laço.
    if v_prev is not null then
      update pg_temp._im_fc_alu a set antes = x.situacao
        from (
          select distinct on (e.unidade_id) e.unidade_id, e.situacao
            from public.im_unidade_estados e
            join public.im_coletas d on d.id = e.desde_coleta_id
           where e.unidade_id in (select id from pg_temp._im_fc_alu)
             and d.fonte = col.fonte and d.tipo = 'completa_mensal' and d.situacao = 'completa'
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
          where x.fonte = col.fonte and x.entidade = 'empreendimento' and x.entidade_id = emp.id
            and x.id_externo = any(v_parciais));
    get diagnostics v_removidas = row_count;
  end if;

  update public.im_coletas set situacao = v_sit, concluida_em = clock_timestamp() where id = p_coleta;
  return jsonb_build_object('situacao', v_sit, 'vendas', v_vendas, 'removidas', v_removidas,
    'coletados', col.coletados, 'unidades', col.unidades,
    'esperados', col.esperados, 'esperados_no_filtro', v_esp, 'fora_do_filtro', v_fora);
end $$;

revoke all on function public._im_totais_coleta(uuid) from public, anon, authenticated, service_role;
revoke all on function public.im_importar(uuid, jsonb), public.im_fechar_coleta(uuid, int, boolean)
  from public, anon, authenticated;
grant execute on function public.im_importar(uuid, jsonb), public.im_fechar_coleta(uuid, int, boolean)
  to service_role;
