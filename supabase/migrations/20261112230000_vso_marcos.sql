-- VSO mensal médio (equivalente) por trimestre e marcos de venda de 25%, 75% e 99%
-- (pedido do Moisés, 12/nov/2026). Só refaz as funções de leitura do histórico do Órulo.
--
-- VSO mensal equivalente de um período de n meses = 1 − (1 − VSO do período)^(1/n): a taxa que,
-- repetida todo mês sobre o que sobra, vende o mesmo que o período vendeu. Parte do VSO do
-- período inteiro (vendas líquidas), por isso a recarga de tabela não distorce a média.
--
-- Marcos de 25, 50, 75 e 99% e quartis de venda: ver _im_marcos_orulo.
-- Marco de X% vendido: primeiro mês em que (unidades − estoque) ÷ unidades chega a X, contado do
-- mês do lançamento (o mês do lançamento é o mês 1). VSO mensal médio até o marco = 1 − (1 − X)^(1/meses).
-- Só se sabe o marco quando a passagem aconteceu dentro do histórico: o lançamento é conhecido e,
-- no primeiro mês do histórico, o empreendimento ainda estava abaixo de X ou acabava de ser lançado.

create or replace function public._im_vso_orulo(p_orulo integer, p_de date, p_ate date)
returns json
language sql stable security definer
set search_path = public, pg_temp
as $$
  with h as (
    select * from public.im_historico_orulo
     where orulo_id = p_orulo
       and mes between date_trunc('month', p_de)::date and date_trunc('month', p_ate)::date),
  t as (
    select min(mes) de, max(mes) ate, count(*) meses, sum(vendas) vendas, sum(distratos) distratos,
           sum(vendas - distratos) liquidas,
           (select estoque from h order by mes desc limit 1) estoque
      from h),
  v as (
    select t.*, case when t.liquidas >= 0 and t.liquidas + t.estoque > 0
                     then t.liquidas::numeric / (t.liquidas + t.estoque) end vso
      from t)
  select case when v.meses = 0 then null else json_build_object(
    'de', v.de, 'ate', v.ate, 'meses', v.meses, 'vendas', v.vendas, 'distratos', v.distratos,
    'liquidas', v.liquidas, 'estoque', v.estoque, 'vso', round(v.vso, 4),
    'vso_mes', round(1 - power(1 - v.vso, 1.0 / v.meses), 4))
  end
  from v
$$;

-- Marcos de venda (25, 50, 75 e 99%) e quartis de venda de um empreendimento do Órulo.
-- Quartil k = do marco anterior ao marco k (0–25, 25–50, 50–75, 75–100%). VSO mensal médio do
-- quartil = 1 − ((1 − fim) ÷ (1 − começo))^(1/meses no quartil): a taxa mensal sobre o que sobrava
-- ao entrar nele. O quartil em andamento vai até o último mês, com o vendido daquele mês. Quartil
-- vencido no mesmo mês do anterior conta como 1 mês e fica marcado (`mesmo_mes`); o que começou
-- antes do histórico fica sem VSO.
create or replace function public._im_marcos_orulo(p_orulo integer, p_lanc date)
returns json
language plpgsql stable security definer
set search_path = public, pg_temp
as $$
declare
  v_lanc date := date_trunc('month', p_lanc)::date;
  v_pri date; v_ult date; v_pct numeric; v_tot integer;
  v_alvos numeric[] := array[0.25, 0.50, 0.75, 0.99];
  v_mes date[] := array[null, null, null, null]::date[]; v_meses integer[] := array[null, null, null, null]::int[];
  v_marcos json; v_quartis json[] := '{}'; k integer; a numeric; ini numeric; fim numeric; n integer; m0 integer; sit text; vso numeric;
begin
  select min(mes), max(mes) into v_pri, v_ult from public.im_historico_orulo where orulo_id = p_orulo;
  if v_pri is null then return null; end if;
  select case when unidades > 0 then (unidades - estoque)::numeric / unidades end into v_pct
    from public.im_historico_orulo where orulo_id = p_orulo and mes = v_ult;
  v_tot := case when v_lanc is null then null
                else (extract(year from age(v_ult, v_lanc)) * 12 + extract(month from age(v_ult, v_lanc)) + 1)::int end;
  for k in 1..4 loop
    a := v_alvos[k];
    v_mes[k] := (select min(mes) from public.im_historico_orulo
                  where orulo_id = p_orulo and unidades > 0 and (unidades - estoque)::numeric / unidades >= a);
    if v_mes[k] is not null and v_lanc is not null and v_mes[k] >= v_lanc
       and (v_pri <= v_lanc or exists (select 1 from public.im_historico_orulo where orulo_id = p_orulo and mes < v_mes[k]
                                        and unidades > 0 and (unidades - estoque)::numeric / unidades < a)) then
      v_meses[k] := (extract(year from age(v_mes[k], v_lanc)) * 12 + extract(month from age(v_mes[k], v_lanc)) + 1)::int;
    end if;
  end loop;
  select json_object_agg(to_char(v_alvos[i] * 100, 'FM99'), json_build_object(
           'mes', case when v_meses[i] is not null then v_mes[i] end, 'meses', v_meses[i],
           'vso_mes', case when v_meses[i] > 0 then round(1 - power(1 - v_alvos[i], 1.0 / v_meses[i]), 4) end)
         order by i)
    into v_marcos from generate_series(1, 4) i;
  for k in 1..4 loop
    ini := case when k = 1 then 0 else v_alvos[k - 1] end;
    m0 := case when k = 1 then 0 else v_meses[k - 1] end;
    if v_mes[k] is not null then
      sit := 'concluido'; fim := v_alvos[k]; n := case when m0 is null then null else v_meses[k] - m0 end;
    elsif k = 1 or v_mes[k - 1] is not null then
      sit := 'em_andamento'; fim := greatest(coalesce(v_pct, 0), ini); n := case when m0 is null or v_tot is null then null else v_tot - m0 end;
    else
      sit := 'futuro'; fim := null; n := null;
    end if;
    vso := case when n is not null and fim is not null and sit <> 'futuro' and (sit = 'concluido' or n > 0)
                then round(1 - power((1 - fim) / (1 - ini), 1.0 / greatest(n, 1)), 4) end;
    v_quartis := v_quartis || json_build_object('quartil', k, 'de', ini, 'ate', v_alvos[k], 'situacao', sit,
      'vendido_ate', fim, 'meses', case when n is null then null else greatest(n, 1) end,
      'mesmo_mes', sit = 'concluido' and n = 0, 'vso_mes', vso);
  end loop;
  return json_build_object('marcos', v_marcos, 'quartis', array_to_json(v_quartis),
    'quartil_atual', case when v_pct is null then null when v_pct >= 0.99 then 4 else least(4, floor(v_pct / 0.25)::int + 1) end,
    'vendido_pct', round(v_pct, 4));
end $$;

create or replace function public.im_vso_historico(p_emp uuid)
returns json
language plpgsql stable security definer
set search_path = public, pg_temp
as $$
declare
  v_orulo integer := public._im_orulo_id(p_emp);
  v_pri date; v_ult date; v_lanc date; v_uni integer; v_est integer; v_meses numeric;
begin
  if v_orulo is null then return null; end if;
  select min(mes), max(mes) into v_pri, v_ult from public.im_historico_orulo where orulo_id = v_orulo;
  if v_pri is null then return null; end if;
  select unidades, estoque into v_uni, v_est from public.im_historico_orulo where orulo_id = v_orulo and mes = v_ult;
  select data_lancamento into v_lanc from public.im_empreendimentos where id = p_emp;
  v_meses := case when v_lanc is null then null
                  else (extract(year from age(v_ult, date_trunc('month', v_lanc))) * 12
                        + extract(month from age(v_ult, date_trunc('month', v_lanc))) + 1) end;
  return json_build_object(
    'fonte', 'orulo_relatorio_36m', 'orulo_id', v_orulo, 'primeiro_mes', v_pri, 'ultimo_mes', v_ult,
    'lancamento', v_lanc, 'completo', v_lanc is not null and date_trunc('month', v_lanc)::date >= v_pri,
    'mensal', (select json_agg(json_build_object(
        'mes', h.mes, 'situacao', h.situacao, 'unidades', h.unidades, 'estoque', h.estoque, 'vendas', h.vendas,
        'distratos', h.distratos, 'liquidas', h.vendas - h.distratos,
        'vso', case when h.vendas >= h.distratos and h.vendas - h.distratos + h.estoque > 0
                    then round((h.vendas - h.distratos)::numeric / (h.vendas - h.distratos + h.estoque), 4) end,
        'vso_mes_3m', case when h.mes >= (v_pri + interval '2 months')::date
                           then (public._im_vso_orulo(v_orulo, (h.mes - interval '2 months')::date, h.mes)->>'vso_mes')::numeric end)
        order by h.mes) from public.im_historico_orulo h where h.orulo_id = v_orulo),
    'janelas', json_build_object(
        '1m', public._im_vso_orulo(v_orulo, v_ult, v_ult),
        '3m', public._im_vso_orulo(v_orulo, (v_ult - interval '2 months')::date, v_ult),
        '6m', public._im_vso_orulo(v_orulo, (v_ult - interval '5 months')::date, v_ult),
        '12m', public._im_vso_orulo(v_orulo, (v_ult - interval '11 months')::date, v_ult),
        'todo', public._im_vso_orulo(v_orulo, v_pri, v_ult)),
    'trimestres', (select json_agg(public._im_vso_orulo(v_orulo, p, (p + interval '2 months')::date) order by p)
        from (select distinct date_trunc('quarter', mes)::date p from public.im_historico_orulo where orulo_id = v_orulo) q),
    'semestres', (select json_agg(public._im_vso_orulo(v_orulo, p, (p + interval '5 months')::date) order by p)
        from (select distinct make_date(extract(year from mes)::int, case when extract(month from mes) <= 6 then 1 else 7 end, 1) p
                from public.im_historico_orulo where orulo_id = v_orulo) s),
    'anos', (select json_agg(public._im_vso_orulo(v_orulo, p, (p + interval '11 months')::date) order by p)
        from (select distinct date_trunc('year', mes)::date p from public.im_historico_orulo where orulo_id = v_orulo) a),
    'vendas', public._im_marcos_orulo(v_orulo, v_lanc),
    'geral', json_build_object(
        'unidades', v_uni, 'estoque', v_est, 'vendido', v_uni - v_est,
        'vendido_pct', case when v_uni > 0 then round((v_uni - v_est)::numeric / v_uni, 4) end,
        'meses_desde_lancamento', v_meses,
        'media_mensal', case when v_uni > 0 and v_meses > 0 then round((v_uni - v_est)::numeric / v_uni / v_meses, 4) end,
        'vso_mes', case when v_uni > 0 and v_meses > 0 and v_est > 0
                        then round(1 - power(v_est::numeric / v_uni, 1.0 / v_meses), 4) end));
end $$;

revoke all on function public._im_marcos_orulo(integer, date) from public, anon, authenticated;
grant execute on function public._im_marcos_orulo(integer, date) to service_role;
revoke all on function public._im_vso_orulo(integer, date, date) from public, anon, authenticated;
grant execute on function public._im_vso_orulo(integer, date, date) to service_role;
revoke all on function public.im_vso_historico(uuid) from public;
grant execute on function public.im_vso_historico(uuid) to anon, authenticated, service_role;
