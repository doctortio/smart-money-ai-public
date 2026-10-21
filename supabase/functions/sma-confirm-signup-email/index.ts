import { createClient } from "npm:@supabase/supabase-js@2";
const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch] ?? ch);
const html = (title: string, message: string, ok: boolean) => {
  const configuredLoginUrl = Deno.env.get("APP_LOGIN_URL")?.trim() ?? "";
  let loginUrl = "";
  try {
    const parsed = new URL(configuredLoginUrl);
    if (parsed.protocol === "https:") loginUrl = parsed.toString();
  } catch (_err) { /* URL ausente ou inválida: confirmação continua sem link. */ }
  const link = ok && loginUrl
    ? `<p><a href="${escapeHtml(loginUrl)}">Entrar no Smart Money AI</a></p>`
    : (ok ? "<p>Abra o endereço da aplicação para entrar com sua conta confirmada.</p>" : "");
  return `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><body style="font-family:Arial;max-width:620px;margin:48px auto;padding:24px"><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p>${link}</body></html>`;
};
const reply = (ok: boolean) => new Response(
  html(ok ? "Cadastro confirmado" : "Link inválido ou expirado", ok ? "Seu e-mail foi confirmado. Agora você pode entrar." : "Solicite um novo cadastro para receber outro link.", ok),
  { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
);
const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
Deno.serve(async (req) => {
  try {
    const url = new URL(req.url);
    const email = String(url.searchParams.get("email") ?? "").trim().toLowerCase();
    const token = String(url.searchParams.get("token") ?? "");
    if (!email || !token || token.length > 128) return reply(false);
    const digest = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token))));
    const client = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const { data, error } = await client.rpc("sma_confirm_signup_email", { p_email: email, p_token_hash: digest });
    return !error && Boolean(data?.ok) ? reply(true) : reply(false);
  } catch (_err) { return reply(false); }
});
