// Each browser session owns its refresh token. Legacy global-token sessions fail closed.
const RETURNS = new Set([
  "https://jvust.github.io/Model/", "https://jvust2.github.io/Model/",
  "https://jvust.github.io/drive-original-player/",
  "http://127.0.0.1:8765/", "http://localhost:8765/",
  "http://127.0.0.1:8000/", "http://localhost:8000/"
]);
const ORIGINS = new Set([...RETURNS].map(value => new URL(value).origin));
const COOKIE = "__Host-drive_oauth_state";
const TTL = 30 * 24 * 3600;
const encoder = new TextEncoder();
const b64 = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64 = value => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0));
const random = () => b64(crypto.getRandomValues(new Uint8Array(32)));
async function key(env, usage) {
  if (!env.GOOGLE_CLIENT_SECRET) throw new Error("OAuth secret not configured");
  const raw = await crypto.subtle.digest("SHA-256", encoder.encode("Model-v2-" + usage + ":" + env.GOOGLE_CLIENT_SECRET));
  return crypto.subtle.importKey("raw", raw, usage === "state" ? {name:"HMAC",hash:"SHA-256"} : {name:"AES-GCM"}, false, usage === "state" ? ["sign","verify"] : ["encrypt","decrypt"]);
}
async function signState(value, env) {
  const body = b64(encoder.encode(JSON.stringify(value)));
  return body + "." + b64(new Uint8Array(await crypto.subtle.sign("HMAC", await key(env,"state"), encoder.encode(body))));
}
async function readState(value, env) {
  try {
    const [body,signature,extra] = String(value||"").split(".");
    if (extra || !signature || !await crypto.subtle.verify("HMAC",await key(env,"state"),unb64(signature),encoder.encode(body))) return null;
    const result = JSON.parse(new TextDecoder().decode(unb64(body)));
    return result.expiresAt > Date.now() && RETURNS.has(result.returnTo) ? result : null;
  } catch { return null; }
}
async function seal(value, env) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({name:"AES-GCM",iv},await key(env,"session"),encoder.encode(JSON.stringify(value)));
  return JSON.stringify({v:2,iv:b64(iv),data:b64(new Uint8Array(data))});
}
async function unseal(value, env) {
  const box = JSON.parse(value);
  if (box.v !== 2) throw new Error("Reauthorization required");
  const raw = await crypto.subtle.decrypt({name:"AES-GCM",iv:unb64(box.iv)},await key(env,"session"),unb64(box.data));
  return JSON.parse(new TextDecoder().decode(raw));
}
async function sessionKey(secret) {
  return "session:v2:" + b64(new Uint8Array(await crypto.subtle.digest("SHA-256",encoder.encode(secret))));
}
function cookie(value, ttl=600) { return `${COOKIE}=${value}; Path=/; Max-Age=${ttl}; HttpOnly; Secure; SameSite=Lax`; }
function headers(origin) {
  return {"Cache-Control":"no-store","Referrer-Policy":"no-referrer","Vary":"Origin",...(ORIGINS.has(origin)?{"Access-Control-Allow-Origin":origin}:{}),"Access-Control-Allow-Headers":"Authorization, Content-Type","Access-Control-Allow-Methods":"GET, POST, OPTIONS"};
}
function json(value,status,origin) {return new Response(JSON.stringify(value),{status,headers:{...headers(origin),"Content-Type":"application/json"}});}
function redirect(location,stateCookie) {return new Response(null,{status:302,headers:{...headers(null),Location:location,"Set-Cookie":stateCookie}});}
async function tokenRequest(body) {
  const response = await fetch("https://oauth2.googleapis.com/token",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body});
  return {ok:response.ok,data:await response.json()};
}
export default {
  async fetch(request,env) {
    const url = new URL(request.url), origin = request.headers.get("Origin");
    try {
      if (request.method === "OPTIONS") return new Response(null,{status:ORIGINS.has(origin)?204:403,headers:headers(origin)});
      if (url.pathname === "/" && request.method === "GET") return json({service:"Drive OAuth Bridge",version:2,session_isolation:true},200,origin);
      if (url.pathname === "/auth" && request.method === "GET") {
        const returnTo = url.searchParams.get("return_to") || "https://jvust.github.io/Model/";
        if (!RETURNS.has(returnTo)) return json({error:"invalid_return_to"},400,origin);
        const state = random(), verifier = random();
        const challenge = b64(new Uint8Array(await crypto.subtle.digest("SHA-256",encoder.encode(verifier))));
        const record = {state,verifier,returnTo,expiresAt:Date.now()+600000};
        const target = new URL("https://accounts.google.com/o/oauth2/v2/auth");
        const params = {client_id:env.GOOGLE_CLIENT_ID,redirect_uri:env.REDIRECT_URI,response_type:"code",scope:"https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.install",access_type:"offline",prompt:"consent",include_granted_scopes:"true",state,code_challenge:challenge,code_challenge_method:"S256"};
        Object.entries(params).forEach(([name,value])=>target.searchParams.set(name,value));
        return redirect(target.toString(),cookie(await signState(record,env)));
      }
      if (url.pathname === "/callback" && request.method === "GET") {
        const encoded = (request.headers.get("Cookie")||"").split(";").map(x=>x.trim()).find(x=>x.startsWith(COOKIE+"="))?.slice(COOKIE.length+1);
        const record = await readState(encoded,env);
        if (!record || record.state !== url.searchParams.get("state") || !url.searchParams.get("code") || url.searchParams.has("error")) return json({error:"invalid_or_expired_oauth_state"},400,origin);
        const grant = await tokenRequest(new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,client_secret:env.GOOGLE_CLIENT_SECRET,redirect_uri:env.REDIRECT_URI,grant_type:"authorization_code",code:url.searchParams.get("code"),code_verifier:record.verifier}));
        if (!grant.ok || !grant.data.refresh_token) return json({error:"reauthorization_required",reauthorize:true},401,origin);
        const secret = random();
        const value = {origin:new URL(record.returnTo).origin,refreshToken:grant.data.refresh_token,expiresAt:Date.now()+TTL*1000};
        await env.OAUTH_KV.put(await sessionKey(secret),await seal(value,env),{expirationTtl:TTL});
        return redirect(record.returnTo+"#oauth_session="+encodeURIComponent(secret),cookie("",0));
      }
      if (["/token","/logout"].includes(url.pathname)) {
        if (!ORIGINS.has(origin)) return json({error:"origin_not_allowed"},403,origin);
        if (request.method !== (url.pathname==="/logout"?"POST":"GET")) return json({error:"method_not_allowed"},405,origin);
        const supplied = request.headers.get("Authorization")||"";
        if (!/^Bearer [A-Za-z0-9_-]{43}$/.test(supplied)) return json({error:"invalid_session",reauthorize:true},401,origin);
        const id = await sessionKey(supplied.slice(7)), raw = await env.OAUTH_KV.get(id);
        if (!raw) return json({error:"invalid_or_legacy_session",reauthorize:true},401,origin);
        let session;
        try {session=await unseal(raw,env);} catch {return json({error:"invalid_session",reauthorize:true},401,origin);}
        if (session.expiresAt <= Date.now()) return json({error:"expired_session",reauthorize:true},401,origin);
        if (session.origin !== origin) return json({error:"session_origin_mismatch"},403,origin);
        if (url.pathname === "/logout") {await env.OAUTH_KV.delete(id);return json({ok:true},200,origin);}
        const grant=await tokenRequest(new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,client_secret:env.GOOGLE_CLIENT_SECRET,grant_type:"refresh_token",refresh_token:session.refreshToken}));
        if (!grant.ok || !grant.data.access_token) return json({error:"reauthorization_required",reauthorize:true},401,origin);
        return json({access_token:grant.data.access_token,expires_in:grant.data.expires_in,token_type:"Bearer"},200,origin);
      }
      return json({error:"not_found"},404,origin);
    } catch {return json({error:"oauth_bridge_unavailable"},503,origin);}
  }
};
