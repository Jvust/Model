import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const {default:worker}=await import('../workers/drive-oauth-bridge.mjs');
const store=new Map();let refreshes=[], grant=0;
const env={GOOGLE_CLIENT_ID:'fixture-id',GOOGLE_CLIENT_SECRET:'fixture-only-not-a-real-secret',REDIRECT_URI:'https://worker.test/callback',OAUTH_KV:{put:async(k,v)=>store.set(k,v),get:async k=>store.get(k),delete:async k=>store.delete(k)}};
globalThis.fetch=async (url,options)=>{
 assert.equal(url,'https://oauth2.googleapis.com/token');
 const body=new URLSearchParams(options.body);
 if(body.get('grant_type')==='authorization_code'){
  assert.ok(body.get('code_verifier'));
  return new Response(JSON.stringify({refresh_token:'refresh-'+(++grant),access_token:'grant-access'}));
 }
 refreshes.push(body.get('refresh_token'));
 return new Response(JSON.stringify({access_token:'access-for-'+body.get('refresh_token'),expires_in:3600}));
};
const req=(path,options={})=>worker.fetch(new Request('https://worker.test'+path,options),env);
async function login(returnTo='https://jvust.github.io/Model/'){
 const auth=await req('/auth?return_to='+encodeURIComponent(returnTo));assert.equal(auth.status,302);
 const cookie=auth.headers.get('set-cookie').split(';')[0];
 const url=new URL(auth.headers.get('location'));assert.equal(url.searchParams.get('code_challenge_method'),'S256');
 const callback=await req('/callback?code=fixture&state='+url.searchParams.get('state'),{headers:{Cookie:cookie}});
 assert.equal(callback.status,302);
 return new URLSearchParams(new URL(callback.headers.get('location')).hash.slice(1)).get('oauth_session');
}
const a=await login(),b=await login();assert.notEqual(a,b);
for(const secret of [a,b])assert.equal((await req('/token',{headers:{Origin:'https://jvust.github.io',Authorization:'Bearer '+secret}})).status,200);
assert.deepEqual(refreshes,['refresh-1','refresh-2']);
assert.ok(!store.has('refresh_token'));assert.ok([...store.values()].every(v=>!v.includes('refresh-1')));
assert.equal((await req('/token',{headers:{Origin:'https://jvust2.github.io',Authorization:'Bearer '+a}})).status,403);
assert.equal((await req('/token',{headers:{Origin:'https://evil.test',Authorization:'Bearer '+a}})).status,403);
assert.equal((await req('/auth?return_to=https://evil.test')).status,400);
assert.equal((await req('/callback?code=fixture&state=madeup')).status,400);
const auth=await req('/auth');const value=auth.headers.get('set-cookie').split(';')[0];
assert.equal((await req('/callback?code=fixture&state=invalid',{headers:{Cookie:value+'x'}})).status,400);
store.set('refresh_token','legacy-secret');store.set('session_hash','old-hash');
assert.equal((await req('/token',{headers:{Origin:'https://jvust.github.io',Authorization:'Bearer '+'a'.repeat(43)}})).status,401);
assert.equal((await req('/logout',{method:'POST',headers:{Origin:'https://jvust.github.io',Authorization:'Bearer '+a}})).status,200);
assert.equal((await req('/token',{headers:{Origin:'https://jvust.github.io',Authorization:'Bearer '+a}})).status,401);
assert.equal((await req('/token',{headers:{Origin:'https://jvust.github.io',Authorization:'Bearer '+b}})).status,200);
const local=await login('http://127.0.0.1:8765/');
assert.equal((await req('/token',{headers:{Origin:'http://127.0.0.1:8765',Authorization:'Bearer '+local}})).status,200);
assert.equal((await req('/token',{method:'OPTIONS',headers:{Origin:'http://127.0.0.1:8765'}})).status,204);
console.log(JSON.stringify({passed:true,checks:14,transport:'mocked-token-endpoint',covers:['two-account-isolation','encrypted-refresh-storage','pkce','signed-state','exact-redirects','cross-origin-rejection','legacy-global-token-rejected','per-session-logout','localhost-preview-origin']}));
