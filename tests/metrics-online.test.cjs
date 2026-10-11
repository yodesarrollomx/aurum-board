const test=require('node:test');
const assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const online=require('../metrics-online.js');
const raw=JSON.stringify({posts:[{id:'sintetico',cover:'covers/ejemplo.jpg'}]});
const hash=x=>createHash('sha256').update(x).digest('hex');
const receipt={schema:1,code_sha:'a'.repeat(40),data_sha:'b'.repeat(64),run_id:'12',
               metrics_sha256:hash(raw)};
function fake(r=receipt,text=raw){
 return async url=>url.includes('version-online.json')?
 {ok:true,json:async()=>r}:{ok:true,text:async()=>text};
}
test('lee recibo y datos exactos sin red ni credenciales',async()=>{
 const out=await online.load({fetch:fake(),sha256:hash});
 assert.equal(out.metrics.posts[0].id,'sintetico');
 assert.equal(out.metrics.posts[0].cover,online.BASE+'covers/ejemplo.jpg?version='+receipt.data_sha);
});
test('hash diferente no confirma la actualización',async()=>{
 await assert.rejects(online.load({fetch:fake(receipt,raw+' '),sha256:hash}),/version_sin_confirmar/);
});
test('recibo incompleto no convierte ausencia en cero',async()=>{
 await assert.rejects(online.load({fetch:fake({schema:1}),sha256:hash}),/recibo_invalido/);
});
test('sin contrato de posts no hay métrica válida',async()=>{
 const text=JSON.stringify({error:'sin acceso'});
 await assert.rejects(online.load({fetch:fake({...receipt,metrics_sha256:hash(text)},text),sha256:hash}),/metricas_sin_contrato/);
});
test('error HTTP se mantiene como error',async()=>{
 await assert.rejects(online.load({fetch:async()=>({ok:false}),sha256:hash}),/recibo_no_disponible/);
});
test('no transforma rutas de portada que salgan del contrato',()=>{
 assert.equal(online.coverURL('covers/../privado.jpg','b'),'covers/../privado.jpg');
 assert.equal(online.coverURL('https://example.invalid/imagen.jpg','b'),'https://example.invalid/imagen.jpg');
});
