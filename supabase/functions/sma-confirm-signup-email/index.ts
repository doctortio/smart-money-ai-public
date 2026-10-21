import { createClient } from "npm:@supabase/supabase-js@2";
const redirect = (ok: boolean): Response => {
  const raw = Deno.env.get("APP_LOGIN_URL")?.trim() || "https://smart-money.streamlit.app/";
  try {
    const target = new URL(raw);
    if (target.protocol !== "https:" || !target.hostname || target.username || target.password) throw new Error("invalid login url");
    target.searchParams.set("signup_confirmation", ok ? "success" : "failed");
    return new Response(null, { status: 302, headers: { "Location": target.toString(), "Cache-Control": "no-store" } });
  } catch (_err) {
    return new Response(null, { status: 500, headers: { "Cache-Control": "no-store" } });
  }
};
const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
Deno.serve(async (req) => {
  try {
    const url = new URL(req.url);
    const email = String(url.searchParams.get("email") ?? "").trim().toLowerCase();
    const token = String(url.searchParams.get("token") ?? "");
    if (!email || !token || token.length > 128) return redirect(false);
    const digest = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token))));
    const client = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const { data, error } = await client.rpc("sma_confirm_signup_email", { p_email: email, p_token_hash: digest });
    return !error && Boolean(data?.ok) ? redirect(true) : redirect(false);
  } catch (_err) { return redirect(false); }
});
