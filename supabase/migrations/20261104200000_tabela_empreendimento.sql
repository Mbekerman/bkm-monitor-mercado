-- Tabela completa de um empreendimento para o BKM Terrenos (popup "Ver tabela completa" do card da
-- concorrência; decisão do Moisés, 4/nov/2026). Só leitura, aberta ao papel anon: o protótipo chama
-- com a chave publicável, e os dados do monitor já ficam abertos a quem tem o link (decisão do Moisés).
-- Devolve o cabeçalho (incorporadora, CNPJ, endereço, torres, tipo...) e as unidades residenciais
-- ativas, cada uma com a situação e o preço atuais e, se vendida no monitor, o período da venda.
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
      'unidades', e.unidades),
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
revoke all on function public.im_tabela_empreendimento(uuid) from public;
grant execute on function public.im_tabela_empreendimento(uuid) to anon, authenticated, service_role;
comment on function public.im_tabela_empreendimento(uuid) is
  'Tabela completa de um empreendimento (cabeçalho e unidades ativas com situação e preço atuais), só leitura, para o BKM Terrenos.';
