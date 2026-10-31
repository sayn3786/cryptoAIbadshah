import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const code=readFileSync(new URL('./worker.js',import.meta.url),'utf8');
const {tick,notifyFailure,runScheduled,default:worker}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const start=1800000000;
const env={EXECUTION_PILOT_ENABLED:'true',EXECUTION_PILOT_START_UTC_SECONDS:String(start),EXECUTION_PILOT_TOKEN:'test'};
const forbidden=()=>assert.fail('unexpected network request');
assert.equal((await tick({...env,EXECUTION_PILOT_ENABLED:'false'},start*1000,forbidden)).skipped,'disabled');
assert.equal((await tick(env,start*1000,forbidden)).skipped,'outside_window');
assert.equal((await tick(env,(start+1021)*1000,forbidden)).error_code,'MISSED_COLLECTION_WINDOW');
assert.equal((await tick({...env,EXECUTION_PILOT_START_UTC_SECONDS:'bad'},0,forbidden)).error_code,'INVALID_START');
assert.ok(!code.includes('api.telegram.org'));
assert.ok(!code.includes('TELEGRAM_BOT_TOKEN'));
let calls=[];
const fetcher=async(url,init)=>{
  calls.push(url);
  assert.equal(init.redirect,'manual');
  assert.equal(init.headers['x-execution-pilot-token'],'test');
  if(url.endsWith('/alert')){
    const body=JSON.parse(init.body);
    assert.deepEqual(Object.keys(body).sort(),['category','slot','test_only']);
    assert.equal(body.category,'COLLECTION_FAILED');
  }
  return {ok:true,status:200,json:async()=>({ok:true})};
};
assert.equal((await tick(env,(start+960)*1000,fetcher)).ok,true);
assert.equal((await notifyFailure(env,{error_code:'SECRET',slot:999},fetcher,true)).ok,true);
assert.equal(calls.length,2);
assert.equal((await notifyFailure(env,{},async()=>({ok:false,status:403}))).status,403);
assert.equal((await notifyFailure(env,{},async()=>{throw Error('SECRET')})).error_code,'ALERT_FAILED_OR_DELIVERY_UNKNOWN');
await assert.rejects(runScheduled({...env,EXECUTION_PILOT_START_UTC_SECONDS:'bad'},0,async()=>({ok:true,json:async()=>({ok:true})}),()=>{}));
const oldFetch=globalThis.fetch,oldNow=Date.now;
try{
 let pending, sends=0;
 globalThis.fetch=async(url)=>{assert.ok(url.endsWith('/alert'));sends++;return {ok:true,json:async()=>({ok:true})};};
 Date.now=()=>start*1000+48000;
 const testEnv={...env,EXECUTION_PILOT_ENABLED:'false',EXECUTION_PILOT_ALERT_TEST_UTC_SECONDS:String(start)};
 await worker.scheduled({scheduledTime:start*1000+47000},testEnv,{waitUntil(p){pending=p;}});
 await pending;assert.equal(sends,1);
 Date.now=()=>start*1000+121000;
 await worker.scheduled({scheduledTime:start*1000+47000},testEnv,{waitUntil(p){pending=p;}});
 await pending;assert.equal(sends,1);
}finally{globalThis.fetch=oldFetch;Date.now=oldNow;}
console.log('Vercel relay Worker tests passed');
