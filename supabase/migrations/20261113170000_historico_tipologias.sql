-- Histórico do Órulo por tipologia e mês (relatório "variação 36 meses" da RM São Paulo e RM Vale,
-- dez/2023 a out/2026), para a inteligência de mercado (relatório de VSO, safras e índice de preço
-- por raio e recorte de área). Pedido do Moisés, 13/nov/2026.
--
-- Só acrescenta: nenhuma tabela existente muda. O `im_historico_orulo` (por empreendimento e mês)
-- continua servindo ao card do VSO; aqui fica o grão do relatório, a tipologia do Órulo, com área,
-- dormitórios, vagas e preço, que a soma por empreendimento perdeu. Os empreendimentos ficam pelo
-- número do Órulo, sem chave para im_empreendimentos: o relatório tem empreendimentos que o
-- monitor não coleta (esgotados, de outras cidades) e eles também contam no histórico.
--
-- Carga única a partir do xlsx (tools/historico/exportar.py gera os CSVs). A partir de nov/2026
-- a série continua pelas coletas mensais do monitor, lidas pelo motor do relatório.

create table public.im_historico_orulo_empreendimentos (
  orulo_id       integer primary key,
  nome           text not null,
  incorporadora  text,
  tipo           text,                -- Vertical, Horizontal
  bairro         text,
  municipio      text not null,
  uf             text not null,
  lat            double precision not null check (lat between -90 and 90),
  lon            double precision not null check (lon between -180 and 180),
  unidades       numeric check (unidades >= 0),   -- unidades do projeto (UTOT do Órulo)
  lancamento     date,
  entrega        date,
  fonte          text not null default 'orulo_relatorio_36m'
);
comment on table public.im_historico_orulo_empreendimentos is
  'Empreendimentos do relatório de 36 meses do Órulo (carga única de nov/2026), pelo número do Órulo. Só leitura.';

create table public.im_historico_orulo_tipologias (
  orulo_tid            integer not null,
  orulo_id             integer not null references public.im_historico_orulo_empreendimentos (orulo_id),
  mes                  date not null check (mes = date_trunc('month', mes)::date),
  situacao             text check (situacao in ('lancamento','em_obras','pronto')),
  finalidade           text,            -- Residencial, Comercial
  subtipo              text,            -- Apartamento, Studio, Garden, Loja...
  area_privativa       numeric check (area_privativa >= 0),
  dormitorios          integer check (dormitorios >= 0),
  suites               integer check (suites >= 0),
  vagas                integer check (vagas >= 0),
  unidades             numeric check (unidades >= 0),   -- unidades da tipologia
  estoque              integer not null check (estoque >= 0),
  estoque_mes_anterior integer check (estoque_mes_anterior >= 0),
  vendas               integer not null check (vendas >= 0),
  distratos            integer not null check (distratos >= 0),
  valor_vendas         numeric,         -- R$ das vendas do mês
  vgv_estoque          numeric,         -- R$ do estoque no fim do mês
  preco_m2             numeric,         -- R$/m² médio do estoque (só com estoque)
  fonte                text not null default 'orulo_relatorio_36m',
  primary key (orulo_tid, mes)
);
create index im_hist_tip_emp_mes on public.im_historico_orulo_tipologias (orulo_id, mes);
create index im_hist_tip_area on public.im_historico_orulo_tipologias (area_privativa);
comment on table public.im_historico_orulo_tipologias is
  'Histórico mensal por tipologia do Órulo (relatório de 36 meses, carga única de nov/2026): estoque, vendas, distratos e preço. Só leitura.';

alter table public.im_historico_orulo_empreendimentos enable row level security;
alter table public.im_historico_orulo_tipologias enable row level security;
revoke all on table public.im_historico_orulo_empreendimentos, public.im_historico_orulo_tipologias
  from public, anon, authenticated;
grant select on table public.im_historico_orulo_empreendimentos, public.im_historico_orulo_tipologias
  to authenticated;
grant select, insert, update, delete on table public.im_historico_orulo_empreendimentos,
  public.im_historico_orulo_tipologias to service_role;
create policy im_leitura on public.im_historico_orulo_empreendimentos for select to authenticated using (true);
create policy im_leitura on public.im_historico_orulo_tipologias for select to authenticated using (true);
