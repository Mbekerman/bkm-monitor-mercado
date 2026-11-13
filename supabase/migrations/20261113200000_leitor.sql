-- Usuário só de leitura para o serviço de inteligência de mercado (repositório bkm-inteligencia-mercado).
-- Pedido do Moisés, 13/nov/2026: agente de fora (Grok, outros) consulta os dados e gera os relatórios
-- por um serviço no Cloud Run, que lê o banco com este usuário.
--
-- A migration cria o papel SEM login e sem senha. A senha nunca entra no repositório: quem administra
-- o banco liga o login à mão no SQL Editor e guarda a conexão no Secret Manager do Google Cloud:
--   alter role im_leitor login password '<senha>';
-- Lê só as tabelas im_* do esquema public (nada de app, fluxo, auth ou storage) e nunca escreve.
-- O uso do esquema public já é de todos (PUBLIC): não se mexe na permissão dele (teste do atlas).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'im_leitor') then
    create role im_leitor nologin noinherit;
  end if;
end $$;


-- Todas as tabelas im_* de hoje, com a política de leitura (RLS ligada nelas).
do $$
declare t text;
begin
  for t in select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'im\_%' loop
    execute format('grant select on public.%I to im_leitor', t);
    if exists (select 1 from pg_class where oid = format('public.%I', t)::regclass and relrowsecurity)
       and not exists (select 1 from pg_policies where schemaname = 'public' and tablename = t and policyname = 'im_leitor') then
      execute format('create policy im_leitor on public.%I for select to im_leitor using (true)', t);
    end if;
  end loop;
end $$;

-- Sem limite de tempo curto: o serviço lê o histórico inteiro (cerca de 330 mil linhas) de uma vez.
alter role im_leitor set statement_timeout = '5min';
