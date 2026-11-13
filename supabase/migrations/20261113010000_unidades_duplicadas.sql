-- Unidade que o Órulo manda duas vezes (13/nov/2026).
--
-- Em parte dos empreendimentos, a lista de unidades do Órulo traz cada unidade
-- com a torre (torre "Bloco 01", número "101") e de novo sem torre, com a torre
-- colada no número ("101Bloco01", "101TA", "101-A"). O monitor gravava as duas.
-- Em 13/nov eram 192 empreendimentos com 47,8 mil unidades a mais (o 011
-- Brooklin Residence Riva com 951 em vez de 724). Desde então o coletor junta
-- as duas antes de importar (coletar/mapeamento.py, juntar_duplicadas).
--
-- Aqui as que já estão no banco viram uma só. Fica a que está à venda (no
-- máximo uma do par está); sem nenhuma à venda, a que tem venda vista pelo
-- monitor; senão, a da torre. A que fica sempre leva a torre e o número da
-- unidade com torre, que é como o coletor passa a mandar. A outra sai, com os
-- estados, as vendas e os grupos dela. Par ambíguo (mais de um candidato) não
-- se mexe. Depois, os grupos dos empreendimentos tocados são refeitos.

create temp table _im_dup as
with u as (
  select x.id, x.empreendimento_id e, x.torre_norm, x.torre, x.numero, x.numero_norm, x.andar,
         x.preco_primeiro, x.preco_primeiro_em, x.preco_de_lancamento,
         regexp_replace(upper(translate(coalesce(x.numero, ''),
           'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
           'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN')), '[^A-Z0-9]', '', 'g') kn,
         regexp_replace(upper(translate(coalesce(x.torre, ''),
           'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
           'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN')), '[^A-Z0-9]', '', 'g') kt
    from public.im_unidades x
   where x.removida_coleta_id is null
),
par as (
  select b.id bid, v.id vid, b.e
    from u b join u v on v.e = b.e and b.torre_norm = '' and v.torre_norm <> '' and v.kt <> '' and v.kn <> ''
     and b.kn in (v.kn || v.kt, v.kt || v.kn)
),
unico as (
  select bid, vid, e from (
    select p.*, count(*) over (partition by bid) nb, count(*) over (partition by vid) nv from par p
  ) z where nb = 1 and nv = 1
),
sit as (
  select s.unidade_id, s.situacao in ('disponivel', 'reservada') a_venda
    from public.im_unidade_estados s where s.ate_coleta_id is null
),
venda as (
  select distinct unidade_id from public.im_vendas
   where desfeita_coleta_id is null and not antes_do_monitor
)
select p.e, p.bid, p.vid,
       case when coalesce(sb.a_venda, false) and not coalesce(sv.a_venda, false) then p.bid
            when not coalesce(sv.a_venda, false) and vb.unidade_id is not null and vv.unidade_id is null then p.bid
            else p.vid end ganha
  from unico p
  left join sit sb on sb.unidade_id = p.bid left join sit sv on sv.unidade_id = p.vid
  left join venda vb on vb.unidade_id = p.bid left join venda vv on vv.unidade_id = p.vid;

-- Dados das duas antes de apagar a que sai.
create temp table _im_dup_dados as
select d.e, d.ganha, case when d.ganha = d.bid then d.vid else d.bid end perde,
       v.torre, v.numero, v.torre_norm, v.numero_norm, v.andar,
       coalesce(g.preco_primeiro, o.preco_primeiro) preco_primeiro,
       case when g.preco_primeiro is not null then g.preco_primeiro_em else o.preco_primeiro_em end preco_primeiro_em,
       case when g.preco_primeiro is not null then g.preco_de_lancamento else o.preco_de_lancamento end preco_de_lancamento
  from _im_dup d
  join public.im_unidades v on v.id = d.vid
  join public.im_unidades g on g.id = d.ganha
  join public.im_unidades o on o.id = case when d.ganha = d.bid then d.vid else d.bid end;

delete from public.im_unidades x using _im_dup_dados d where x.id = d.perde;

update public.im_unidades x set
  torre = d.torre, numero = d.numero, torre_norm = d.torre_norm, numero_norm = d.numero_norm,
  andar = coalesce(d.andar, x.andar),
  preco_primeiro = d.preco_primeiro, preco_primeiro_em = d.preco_primeiro_em,
  preco_de_lancamento = coalesce(d.preco_de_lancamento, x.preco_de_lancamento)
  from _im_dup_dados d where x.id = d.ganha;

select public.im_reagrupar(e) from (select distinct e from _im_dup_dados) z;

drop table _im_dup_dados;
drop table _im_dup;
