/* State machine for human-observed main gallery images. No DOM or network access. */
(function (root) {
  'use strict';
  function product(url) {
    const u = new URL(url);
    if (u.protocol !== 'https:' || u.username || u.password || !['articulo.mercadolibre.com.mx','www.mercadolibre.com.mx'].includes(u.hostname)) throw new Error('Invalid detail URL');
    const found = u.pathname.match(/(?:^|\/)(MLM-?\d+)(?:-|\/|$)/i) || u.pathname.match(/\/up\/(MLMU\d+)(?:\/|$)/i);
    if (!found) throw new Error('Missing detail identity');
    u.search=''; u.hash='';
    return {id:found[1].replace('-','').toUpperCase(), url:u.href};
  }
  class ObservedGallery {
    constructor(binding) {
      const p = product(binding.detail_url);
      if (p.id !== binding.identity) throw new Error('Detail identity mismatch');
      if (!Number.isSafeInteger(binding.total) || binding.total < 1 || binding.total > 100) throw new Error('Invalid total');
      this.binding={identity:p.id,detail_url:p.url,total:binding.total}; this.rows=[];
    }
    add(observation) {
      const p=product(observation.detail_url), b=this.binding;
      if (observation.identity !== b.identity || p.id !== b.identity || p.url !== b.detail_url || observation.total !== b.total) throw new Error('Gallery binding changed');
      if (!Number.isSafeInteger(observation.position) || observation.position < 1 || observation.position > b.total) throw new Error('Invalid order');
      if (!Number.isSafeInteger(observation.width) || !Number.isSafeInteger(observation.height) || observation.width < 100 || observation.height < 100 || observation.width*observation.height > 40000000) throw new Error('Invalid dimensions');
      const u=new URL(observation.url);
      if (u.protocol!=='https:' || u.username || u.password || !/(^|\.)mlstatic\.com$/.test(u.hostname)) throw new Error('Invalid image URL');
      const value={url:u.href,width:observation.width,height:observation.height};
      const i=observation.position-1;
      if(i<this.rows.length) { if(JSON.stringify(this.rows[i])!==JSON.stringify(value)) throw new Error('Observation conflict'); return; }
      if(i!==this.rows.length) throw new Error('Gallery order mismatch');
      if(this.rows.some(x=>x.url===value.url)) throw new Error('duplicate gallery image');
      this.rows.push(value);
    }
    manifest() {
      if(this.rows.length!==this.binding.total) throw new Error('Gallery incomplete');
      return {detail_url:this.binding.detail_url,images:this.rows.map(x=>x.url)};
    }
  }
  if(typeof module!=='undefined' && module.exports) module.exports={ObservedGallery};
  else root.ObservedGallery=ObservedGallery;
})(globalThis);
