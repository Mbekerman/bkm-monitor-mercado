-- Fechamento pela API: mais tempo de banco só para im_fechar_coleta.
--
-- O PostgREST conecta como authenticator (statement_timeout 8s) e troca para
-- service_role, que não tem timeout próprio; valia o de 8 s. O fechamento mensal
-- com ~400 mil unidades leva cerca de 2 minutos. O SET na função vale para a chamada
-- RPC (testado em produção: pg_sleep(10) por RPC passa com o SET e cai em 57014 sem).
-- Não mexe em papel nenhum: im_importar e o resto continuam com 8 s.
--
-- Atenção: o gateway da API do Supabase corta a requisição HTTP em ~125 s (504
-- "upstream request timeout"), e o banco continua até o fim. O coletor trata isso
-- esperando a coleta sair de 'rodando' (coletar/banco.py, fechar_coleta).
-- Um create or replace futuro de im_fechar_coleta apaga este SET; o teste em
-- supabase/tests/regras.sql pega isso.

alter function public.im_fechar_coleta(uuid, integer, boolean) set statement_timeout = '10min';
