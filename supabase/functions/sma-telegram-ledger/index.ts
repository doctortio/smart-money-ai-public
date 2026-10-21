const CHANNEL = "@smartmoneyaioficial";
const MAX_SKEW = 300;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const enc = new TextEncoder();
function hex(bytes: ArrayBuffer) { return [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, "0")).join(""); }
async function validSignature(secret: string, value: string, supplied: string) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const raw = supplied.replace(/^sha256=/, "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(raw)) return false;
  return crypto.subtle.verify("HMAC", key, Uint8Array.from(raw.match(/.{2}/g)!.map(x => parseInt(x, 16))), enc.encode(value));
}
function canonical(b: Record<string, unknown>) {
  const clean = Object.fromEntries(Object.entries(b).filter(([key]) => key !== "signature").sort(([a], [b]) => a.localeCompare(b)));
  return JSON.stringify(clean);
}
async function rpc(name: string, payload: Record<string, unknown>) {
  const base = Deno.env.get("SUPABASE_URL")!; const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const r = await fetch(`${base}/rest/v1/rpc/${name}`, { method: "POST", headers: { apikey: key, Authorization: `Bearer ${key}`, "content-type": "application/json" }, body: JSON.stringify(payload) });
  if (!r.ok) throw new Error(`rpc_${name}_${r.status}`); return await r.json();
}
Deno.serve(async req => {
  if (req.method !== "POST") return json({ ok: false, error: "method_not_allowed" }, 405);
  try {
    const b = await req.json() as Record<string, unknown>; const secret = Deno.env.get("TELEGRAM_LEDGER_HMAC_SECRET") || "";
    if (!secret || b.method !== "POST" || b.path !== "/functions/v1/sma-telegram-ledger" || b.channel !== CHANNEL || !["reserve", "mark"].includes(String(b.operation)) || typeof b.nonce !== "string" || typeof b.execution_key !== "string") return json({ ok: false, error: "invalid_input" }, 400);
    const ts = Number(b.timestamp); if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > MAX_SKEW) return json({ ok: false, error: "timestamp_expired" }, 401);
    if (!await validSignature(secret, canonical(b), String(b.signature || ""))) return json({ ok: false, error: "invalid_signature" }, 401);
    const claimed = await rpc("sma_telegram_claim_nonce", { p_nonce: b.nonce, p_execution_key: b.execution_key, p_operation: b.operation, p_channel: b.channel, p_expires_at: new Date((ts + MAX_SKEW) * 1000).toISOString() });
    if (!claimed) return json({ ok: false, error: "replay" }, 409);
    if (b.operation === "reserve") {
      const result = await rpc("sma_telegram_reserve_publication", { p_execution_key: b.execution_key, p_channel: b.channel, p_assets: b.assets, p_timeframe: b.timeframe, p_scheduled_at: b.scheduled_at, p_owner_id: b.owner_id });
      return json({ ok: Boolean(result) });
    }
    if (!await rpc("sma_telegram_owner_matches", { p_execution_key: b.execution_key, p_owner_id: b.owner_id })) return json({ ok: false, error: "owner_mismatch" }, 403);
    const result = await rpc("sma_telegram_mark_publication", { p_execution_key: b.execution_key, p_status: b.status, p_message_id: b.message_id ?? null });
    return json({ ok: Boolean(result) });
  } catch { return json({ ok: false, error: "internal_error" }, 500); }
});
