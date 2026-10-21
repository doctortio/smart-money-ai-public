import { createClient } from "npm:@supabase/supabase-js@2";

const reply = (ok: boolean, status = 200) => new Response(JSON.stringify({ ok, code: ok ? "CREATED" : "REQUEST_ACCEPTED" }), { status, headers: { "content-type": "application/json" } });
const reject = (code: string) => { console.warn(`[sma-public-signup] ${code}`); return reply(false); };
const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const validEmail = (v: string) => v.length >= 3 && v.length <= 320 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
const validPassword = (v: string) => v.length >= 8 && v.length <= 1024;
async function hmacSha256(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value))));
}
async function sha256(value: string): Promise<string> { return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)))); }
async function validateCaptcha(token: string): Promise<boolean> {
  const url = Deno.env.get("CAPTCHA_VERIFY_URL"); const secret = Deno.env.get("CAPTCHA_SECRET_KEY");
  if (!url || !secret || !token) return false;
  try { const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ secret, response: token }) }); const result = await r.json(); return r.ok && result?.success === true; } catch (_) { return false; }
}
async function sendVerification(email: string, token: string): Promise<boolean> {
  const url = Deno.env.get("EMAIL_PROVIDER_URL"); const key = Deno.env.get("EMAIL_PROVIDER_API_KEY"); const base = Deno.env.get("EMAIL_VERIFY_BASE_URL"); const from = Deno.env.get("EMAIL_FROM");
  if (!url || !key || !base || !from) return false;
  const link = `${base}?email=${encodeURIComponent(email)}&token=${encodeURIComponent(token)}`;
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${key}` }, body: JSON.stringify({ from, to: [email], subject: "Confirme seu cadastro no Smart Money AI", html: `<p>Confirme seu cadastro no Smart Money AI.</p><p><a href="${link}">Confirmar e-mail</a></p>` }) });
  return r.ok;
}
Deno.serve(async (req) => {
  if (req.method !== "POST") return reply(false, 405);
  try {
    const body = await req.json(); const nome = String(body?.nome ?? "").trim(); const email = String(body?.email ?? "").trim().toLowerCase(); const confirmar = String(body?.confirmar ?? ""); const captchaToken = String(body?.captcha_token ?? ""); const hash = String(body?.senha_hash ?? ""); const proof = String(body?.senha_proof ?? ""); const proofTimestamp = Number(body?.senha_proof_timestamp ?? 0); const internalSecret = Deno.env.get("PUBLIC_SIGNUP_INTERNAL_HMAC_SECRET") ?? "";
    if (!nome || !validEmail(email) || !validPassword(confirmar) || !internalSecret) return reject("INPUT_INVALID");
    if (!/^scrypt\$131072\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}$/.test(hash)) return reject("HASH_INVALID");
    if (!Number.isInteger(proofTimestamp) || Math.abs(Date.now() - proofTimestamp * 1000) > 5 * 60 * 1000 || proof.length !== 64) return reject("HMAC_INVALID");
    const expected = await hmacSha256(internalSecret, `${email}\n${hash}\n${proofTimestamp}`); let different = expected.length ^ proof.length; for (let i = 0; i < expected.length; i++) different |= expected.charCodeAt(i) ^ proof.charCodeAt(i);
    if (different !== 0) return reject("HMAC_FAILED");
    if (!(await validateCaptcha(captchaToken))) return reject("CAPTCHA_FAILED");
    const rawToken = hex(crypto.getRandomValues(new Uint8Array(32))); const tokenHash = await sha256(rawToken); const expires = new Date(Date.now() + 30 * 60 * 1000).toISOString(); const client = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const { data, error } = await client.rpc("sma_public_signup", { p_nome: nome, p_email: email, p_senha_hash: hash, p_token_hash: tokenHash, p_token_expires: expires });
    if (error || !data?.ok) return reject("RPC_FAILED");
    if (!(await sendVerification(email, rawToken))) return reject("EMAIL_FAILED");
    return reply(true);
  } catch (_) { return reject("INTERNAL_ERROR"); }
});
