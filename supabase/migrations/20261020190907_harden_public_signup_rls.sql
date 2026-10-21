-- Harden signup control tables without changing the public signup contract.
-- SECURITY DEFINER RPCs retain operational access; no client role receives table access.

alter table if exists public.sma_signup_attempts enable row level security;
alter table if exists public.sma_signup_email_attempts enable row level security;
alter table if exists public.sma_signup_email_verifications enable row level security;
alter table if exists public.sma_signup_rate_config enable row level security;

revoke all on table public.sma_signup_attempts from public, anon, authenticated;
revoke all on table public.sma_signup_email_attempts from public, anon, authenticated;
revoke all on table public.sma_signup_email_verifications from public, anon, authenticated;
revoke all on table public.sma_signup_rate_config from public, anon, authenticated;

revoke all on function public.sma_public_signup(text, text, text, text) from public, anon, authenticated;
revoke all on function public.sma_public_signup(text, text, text, text, timestamptz) from public, anon, authenticated;
revoke all on function public.sma_confirm_signup_email(text, text) from public, anon, authenticated;
revoke all on function public.sma_cleanup_signup_attempts(timestamptz) from public, anon, authenticated;

grant execute on function public.sma_public_signup(text, text, text, text, timestamptz) to service_role;
grant execute on function public.sma_confirm_signup_email(text, text) to service_role;
grant execute on function public.sma_cleanup_signup_attempts(timestamptz) to service_role;
