/* Métricas públicas: recibo + SHA256, conservar último valor ante fallo. */
(function(root, factory){
  if(typeof module==='object' && module.exports) module.exports=factory();
  else root.MetricsOnline=factory();
})(typeof globalThis!=='undefined'?globalThis:this, function(){
  'use strict';
  const BASE='https://raw.githubusercontent.com/yodesarrollomx/aurum-board/datos-online/';
  async function sha256(text){
    const bytes=new TextEncoder().encode(text);
    const sum=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    return Array.from(new Uint8Array(sum),x=>x.toString(16).padStart(2,'0')).join('');
  }
  function coverURL(path,dataSha){
    if(/^covers\/[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp)$/.test(path||'')){
      return BASE+path+'?version='+encodeURIComponent(dataSha);
    }
    return path;
  }
  async function load(options){
    options=options||{};
    const get=options.fetch||globalThis.fetch.bind(globalThis);
    const hash=options.sha256||sha256;
    const query='?cb='+Date.now();
    const receiptResponse=await get(BASE+'version-online.json'+query,{cache:'no-store',credentials:'omit'});
    if(!receiptResponse.ok)throw new Error('recibo_no_disponible');
    const receipt=await receiptResponse.json();
    if(!receipt||receipt.schema!==1||!/^[a-f0-9]{40}$/.test(receipt.code_sha||'')||
       !/^[a-f0-9]{64}$/.test(receipt.data_sha||'')||
       !/^[a-f0-9]{64}$/.test(receipt.metrics_sha256||'')||
       !/^\d+$/.test(String(receipt.run_id||'')))throw new Error('recibo_invalido');
    const response=await get(BASE+'metrics.json'+query,{cache:'no-store',credentials:'omit'});
    if(!response.ok)throw new Error('metricas_no_disponibles');
    const text=await response.text();
    if(await hash(text)!==receipt.metrics_sha256)throw new Error('version_sin_confirmar');
    const metrics=JSON.parse(text);
    if(!metrics||!Array.isArray(metrics.posts))throw new Error('metricas_sin_contrato');
    metrics.posts=metrics.posts.map(p=>Object.assign({},p,{cover:coverURL(p.cover,receipt.data_sha)}));
    return {metrics:metrics,receipt:receipt};
  }
  return {load:load,coverURL:coverURL,sha256:sha256,BASE:BASE};
});
