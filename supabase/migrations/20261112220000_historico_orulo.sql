-- Histórico mensal de vendas do Órulo (relatório "variação 36 meses" da RM São Paulo e RM Vale,
-- entregue pela Órulo à BKM em nov/2026; carga única, decisão do Moisés de 12/nov/2026).
--
-- Um registro por empreendimento do Órulo por mês (dez/2023 a out/2026), somando as tipologias
-- residenciais: unidades, estoque no fim do mês, vendas e distratos do mês. Fica guardado pelo
-- número do Órulo (o mesmo de im_ids_externos), sem chave para im_empreendimentos: o relatório tem
-- empreendimentos que o monitor ainda não coleta e eles passam a valer quando entrarem.
--
-- Nada do que o monitor coletou muda: as unidades, as vendas que o monitor viu e os grupos ficam
-- como estão. O histórico é outra fonte, só leitura, marcada em `fonte`.
--
-- VSO do período = vendas líquidas (vendas − distratos) ÷ (vendas líquidas + estoque no fim do
-- período). Mesma leitura do VSO do monitor (vendas ÷ vendas + à venda). A venda do Órulo é a
-- unidade que saiu da tabela e o distrato a que voltou; recargas de tabela aparecem como distrato
-- num mês e venda no seguinte e se anulam na venda líquida. O VSO que vem pronto no relatório
-- (vendido acumulado ÷ meses) não é guardado. Período com mais distrato que venda fica sem VSO
-- (é ajuste de tabela, não venda negativa); as quantidades aparecem do mesmo jeito.
create table public.im_historico_orulo (
  orulo_id    integer not null,
  mes         date not null check (mes = date_trunc('month', mes)::date),
  situacao    text check (situacao in ('lancamento','em_obras','pronto')),
  unidades    integer not null check (unidades >= 0),
  estoque     integer not null check (estoque >= 0),
  vendas      integer not null check (vendas >= 0),
  distratos   integer not null check (distratos >= 0),
  tipologias  integer,
  fonte       text not null default 'orulo_relatorio_36m',
  primary key (orulo_id, mes)
);
comment on table public.im_historico_orulo is
  'Histórico mensal de vendas do Órulo por empreendimento (relatório de 36 meses, carga única de nov/2026). Só leitura.';

alter table public.im_historico_orulo enable row level security;
revoke all on table public.im_historico_orulo from public, anon, authenticated;
grant select on table public.im_historico_orulo to authenticated;
grant select, insert, update, delete on table public.im_historico_orulo to service_role;
create policy im_leitura on public.im_historico_orulo for select to authenticated using (true);

-- Número do Órulo de um empreendimento do monitor.
create or replace function public._im_orulo_id(p_emp uuid)
returns integer
language sql stable security definer
set search_path = public, pg_temp
as $$
  select min(x.id_externo::integer)
    from public.im_ids_externos x
   where x.entidade = 'empreendimento' and x.entidade_id = p_emp
     and x.fonte like 'orulo%' and x.id_externo ~ '^\d+$'
$$;

-- Vendas e VSO de um empreendimento do Órulo entre dois meses (inclusive).
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
      from h)
  select case when t.meses = 0 then null else json_build_object(
    'de', t.de, 'ate', t.ate, 'meses', t.meses, 'vendas', t.vendas, 'distratos', t.distratos,
    'liquidas', t.liquidas, 'estoque', t.estoque,
    'vso', case when t.liquidas >= 0 and t.liquidas + t.estoque > 0 then round(t.liquidas::numeric / (t.liquidas + t.estoque), 4) end)
  end
  from t
$$;

-- Histórico de um empreendimento do monitor para o BKM Terrenos: a série mensal, as janelas de
-- 1, 3, 6 e 12 meses até o último mês, os trimestres, semestres e anos fechados e o geral do
-- projeto (vendido ÷ unidades e a média por mês desde o lançamento). `completo` diz se o
-- histórico começa no lançamento (antes dele, o relatório não tem a distribuição das vendas).
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
        'mes', mes, 'situacao', situacao, 'unidades', unidades, 'estoque', estoque, 'vendas', vendas,
        'distratos', distratos, 'liquidas', vendas - distratos,
        'vso', case when vendas >= distratos and vendas - distratos + estoque > 0 then round((vendas - distratos)::numeric / (vendas - distratos + estoque), 4) end)
        order by mes) from public.im_historico_orulo where orulo_id = v_orulo),
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
    'geral', json_build_object(
        'unidades', v_uni, 'estoque', v_est, 'vendido', v_uni - v_est,
        'vendido_pct', case when v_uni > 0 then round((v_uni - v_est)::numeric / v_uni, 4) end,
        'meses_desde_lancamento', v_meses,
        'media_mensal', case when v_uni > 0 and v_meses > 0 then round((v_uni - v_est)::numeric / v_uni / v_meses, 4) end));
end $$;

revoke all on function public._im_orulo_id(uuid), public._im_vso_orulo(integer, date, date) from public, anon, authenticated;
grant execute on function public._im_orulo_id(uuid), public._im_vso_orulo(integer, date, date) to service_role;
revoke all on function public.im_vso_historico(uuid) from public;
grant execute on function public.im_vso_historico(uuid) to anon, authenticated, service_role;
comment on function public.im_vso_historico(uuid) is
  'VSO histórico de um empreendimento pelo relatório de 36 meses do Órulo: mensal, janelas, trimestres, semestres, anos e geral. Só leitura, para o BKM Terrenos.';
