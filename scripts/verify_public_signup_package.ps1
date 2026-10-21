param(
  [string]$ProjectRef = "tigccvvuemverlbczpoh"
)

$ErrorActionPreference = "Stop"
$required = @(
  "CAPTCHA_VERIFY_URL", "CAPTCHA_SECRET_KEY", "EMAIL_PROVIDER_URL",
  "EMAIL_PROVIDER_API_KEY", "EMAIL_VERIFY_BASE_URL", "EMAIL_FROM",
  "APP_LOGIN_URL", "PUBLIC_SIGNUP_INTERNAL_HMAC_SECRET"
)

Write-Host "Read-only checklist for project $ProjectRef"
Write-Host "Required secrets (names only): $($required -join ', ')"
Write-Host "Run manually after authentication:"
Write-Host "  supabase link --project-ref $ProjectRef"
Write-Host "  supabase db push --linked --dry-run"
Write-Host "  supabase secrets list --project-ref $ProjectRef"
Write-Host "No migration, deploy, repair, or secret mutation is performed by this script."
