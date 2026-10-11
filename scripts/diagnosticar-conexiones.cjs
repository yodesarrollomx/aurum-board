'use strict';
const crypto=require('node:crypto');
async function read(url,token){
 try{
  const r=await fetch(url,{headers:token?{Authorization:'Bearer '+token}:{},signal:AbortSignal.timeout(30000)});
  let data;try{data=await r.json();}catch{return {http:r.status,valid_json:false};}
  return {http:r.status,data};
 }catch{return {http:0,transport_failed:true};}
}
async function backend(url){
 const r=await read(url),j=r.data;
 const accepted=r.http===200&&j&&typeof j==='object'&&j.ok!==false&&!j.error&&Array.isArray(j.leads_utm);
 return {http:r.http,transport_failed:!!r.transport_failed,valid_json:!!j,
         rejected:!!(j&&(j.ok===false||j.error)),leads_contract_verified:!!accepted,
         ...(accepted?{records:j.leads_utm.length,with_campaign:j.leads_utm.filter(x=>x&&typeof x==='object'&&x.utm_campaign).length}:{} )};
}
async function publisher(){
 const id=process.env.YOD_APP_ID||'',key=process.env.YOD_APP_KEY||'';
 const result={id_configured:/^[0-9]+$/.test(id),key_configured:!!key.trim()};
 if(!result.id_configured||!result.key_configured)return result;
 try{
  const issued=Math.floor(Date.now()/1000)-60,enc=x=>Buffer.from(JSON.stringify(x)).toString('base64url');
  const unsigned=enc({alg:'RS256',typ:'JWT'})+'.'+enc({iat:issued,exp:issued+540,iss:id});
  const token=unsigned+'.'+crypto.sign('RSA-SHA256',Buffer.from(unsigned),key).toString('base64url');
  const app=await read('https://api.github.com/app',token),j=app.data;
  result.identity_verified=app.http===200&&String(j?.id)===id;
  result.expected_owner=j?.owner?.login==='yodesarrollomx';
  result.expected_permissions=j?.permissions?.contents==='read'&&j?.permissions?.pull_requests==='write'&&
    Object.entries(j?.permissions||{}).every(([p,v])=>['contents','pull_requests','metadata'].includes(p)||v==='none');
  if(result.identity_verified&&result.expected_owner&&result.expected_permissions){
   const repos=['sala-edicion','aurum-board'];
   const installations=await Promise.all(repos.map(repo=>read('https://api.github.com/repos/yodesarrollomx/'+repo+'/installation',token)));
   result.repository_installations_verified=installations.every(x=>x.http===200&&x.data?.account?.login==='yodesarrollomx'&&x.data?.repository_selection==='selected');
   result.same_installation=installations.length===2&&Number.isInteger(installations[0].data?.id)&&installations[0].data.id===installations[1].data?.id;
  }
 }catch{result.identity_verified=false;result.code='app_auth_not_verified';}
 return result;
}
(async()=>{
 const token=process.env.YOD_META_TOKEN||'';
 const report={at:new Date().toISOString(),business_writes:0,social_publications:0,meta:{token_configured:!!token},publisher_app:await publisher()};
 if(token){
  const r=await read('https://graph.facebook.com/v23.0/me/accounts?fields=id,instagram_business_account&limit=100',token);
  report.meta.http=r.http;
  report.meta.authorized=r.http===200&&Array.isArray(r.data?.data);
  if(report.meta.authorized){
   report.meta.pages=r.data.data.length;
   report.meta.pages_with_instagram=r.data.data.filter(x=>x.instagram_business_account?.id).length;
  }else if(Number.isInteger(r.data?.error?.code))report.meta.error_code=r.data.error.code;
 }
 const qaa=process.env.AURUM_QAA_TOKEN||'';
 report.arquitectura={machine_token_configured:!!qaa,...await backend(
  'https://script.google.com/macros/s/AKfycbztAKA7K5QwO6k45PqjixYLNppLypzCpoz2KvNIkML8kciBLZVKKoais8__0DnYuEQQOg/exec?recurso='+(
    qaa?'leads&token='+encodeURIComponent(qaa):'board'))};
 report.potencial={machine_auth_sent:false,...await backend(
  'https://script.google.com/macros/s/AKfycbw3EB-6Q9Mq-ouDU-JvKMrRUaw4auYVeGkKja783yJ7_dEpCOW8xoMhs8IQMDojmlDB3A/exec?recurso=board')};
 report.connections_complete=report.meta.authorized===true&&report.arquitectura.leads_contract_verified&&report.potencial.leads_contract_verified&&report.publisher_app.identity_verified===true&&report.publisher_app.expected_owner===true&&report.publisher_app.expected_permissions===true&&report.publisher_app.repository_installations_verified===true&&report.publisher_app.same_installation===true;
 console.log(JSON.stringify(report));
 if(!report.connections_complete)process.exitCode=2;
})().catch(()=>{console.log(JSON.stringify({ok:false,code:'connection_check_failed',business_writes:0}));process.exitCode=1;});
