-- Objetos fictícios no formato do outro sistema que já mora no mesmo banco.
-- A migration do monitor não pode mudá-los. A foto fica nesta tabela de teste.
create schema atlas_private;

create table public.atlas_exemplo (
  id uuid primary key default gen_random_uuid(),
  nome text not null
);
insert into public.atlas_exemplo (nome) values ('nao mexer');
create index atlas_exemplo_nome on public.atlas_exemplo (nome);
comment on table public.atlas_exemplo is 'tabela fictícia do outro sistema';

create table atlas_private.atlas_segredo (
  id int primary key,
  segredo text not null
);
insert into atlas_private.atlas_segredo (id, segredo) values (1, 'sigilo');

create function public.atlas_soma(a int, b int) returns int
language sql immutable as $$ select a + b $$;

insert into storage.buckets (id, name, public)
values ('atlas-documentos', 'atlas-documentos', false);

create policy atlas_arquivos_leitura on storage.objects
  for select to authenticated
  using (bucket_id = 'atlas-documentos');

create policy atlas_arquivos_escrita on storage.objects
  for insert to authenticated
  with check (bucket_id = 'atlas-documentos');

create publication atlas_pub for table public.atlas_exemplo;

create function public._teste_atlas_foto() returns text
language sql stable as $$
  select coalesce(string_agg(linha, E'\n' order by linha), '')
    from (
      select format('ext %s', extname) as linha from pg_extension
      union all
      select format('role %s super=%s bypass=%s login=%s', rolname, rolsuper, rolbypassrls, rolcanlogin)
        from pg_roles
       where rolname in ('anon', 'authenticated', 'service_role', 'postgres')
      union all
      select format('nsp %s acl=%s', nspname, coalesce(nspacl::text, ''))
        from pg_namespace
       where nspname in ('public', 'atlas_private', 'storage', 'auth', 'extensions')
      union all
      select format('rel %s.%s kind=%s acl=%s', n.nspname, c.relname, c.relkind, coalesce(c.relacl::text, ''))
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
       where (n.nspname = 'atlas_private')
          or (n.nspname = 'public' and c.relname like 'atlas\_%' escape '\')
          or (n.nspname = 'storage' and c.relname in ('buckets', 'objects'))
      union all
      select format('col %s.%s.%s tipo=%s null=%s num=%s', n.nspname, c.relname, a.attname, a.atttypid, a.attnotnull, a.attnum)
        from pg_attribute a
        join pg_class c on c.oid = a.attrelid
        join pg_namespace n on n.oid = c.relnamespace
       where a.attnum > 0 and not a.attisdropped
         and ((n.nspname = 'atlas_private')
           or (n.nspname = 'public' and c.relname like 'atlas\_%' escape '\')
           or (n.nspname = 'storage' and c.relname in ('buckets', 'objects')))
      union all
      select format('con %s.%s %s %s', n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid))
        from pg_constraint con
        join pg_class c on c.oid = con.conrelid
        join pg_namespace n on n.oid = c.relnamespace
       where (n.nspname = 'atlas_private')
          or (n.nspname = 'public' and c.relname like 'atlas\_%' escape '\')
      union all
      select format('idx %s %s', ic.relname, pg_get_indexdef(i.indexrelid))
        from pg_index i
        join pg_class ic on ic.oid = i.indexrelid
        join pg_class tc on tc.oid = i.indrelid
        join pg_namespace n on n.oid = tc.relnamespace
       where (n.nspname = 'atlas_private')
          or (n.nspname = 'public' and tc.relname like 'atlas\_%' escape '\')
      union all
      select format('fn %s %s', p.proname, pg_get_functiondef(p.oid))
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'public' and p.proname like 'atlas\_%' escape '\'
      union all
      select format(
               'pol %s cmd=%s roles=%s qual=%s check=%s',
               pol.polname, pol.polcmd, pol.polroles::text,
               coalesce(pg_get_expr(pol.polqual, pol.polrelid), ''),
               coalesce(pg_get_expr(pol.polwithcheck, pol.polrelid), ''))
        from pg_policy pol
       where pol.polname like 'atlas\_%' escape '\'
      union all
      select format('bucket %s public=%s limite=%s', id, public, file_size_limit)
        from storage.buckets
       where id like 'atlas%'
      union all
      select format('dado %s', nome) from public.atlas_exemplo
      union all
      select format('segredo %s %s', id, segredo) from atlas_private.atlas_segredo
      union all
      select format('pub %s %s', pubname, puballtables) from pg_publication where pubname like 'atlas%'
      union all
      select format('pubrel %s %s', p.pubname, c.relname)
        from pg_publication_rel pr
        join pg_publication p on p.oid = pr.prpubid
        join pg_class c on c.oid = pr.prrelid
       where p.pubname like 'atlas%'
    ) s
$$;

create table public.atlas_fingerprint (
  id int primary key default 1 check (id = 1),
  foto text not null
);
insert into public.atlas_fingerprint (foto) values (public._teste_atlas_foto());
