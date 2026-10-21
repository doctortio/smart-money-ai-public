-- Read-only post-migration verification. Run manually against the target project.
select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in ('sma_signup_attempts','sma_signup_email_attempts','sma_signup_email_verifications','sma_signup_rate_config')
order by c.relname;

select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('sma_signup_attempts','sma_signup_email_attempts','sma_signup_email_verifications','sma_signup_rate_config')
order by table_name, grantee, privilege_type;

select routine_name, routine_schema, grantee, privilege_type
from information_schema.role_routine_grants
where routine_schema = 'public'
  and routine_name in ('sma_public_signup','sma_confirm_signup_email','sma_cleanup_signup_attempts')
order by routine_name, grantee, privilege_type;
