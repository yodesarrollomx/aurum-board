'use strict';
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
let count=0;
for(const [,attrs,body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){
  if(/\bsrc\s*=/.test(attrs)||/\btype\s*=\s*["'](?:application\/json|application\/ld\+json)/.test(attrs))continue;
  new vm.Script(body,{filename:'index.html:script-'+(++count)});
}
if(!count)throw Error('Sin cliente inline');
console.log('Cliente: '+count+' scripts válidos');
