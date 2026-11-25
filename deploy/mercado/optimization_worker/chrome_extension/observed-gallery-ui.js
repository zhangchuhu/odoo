(function(root){
 function createGalleryController(Gallery){let current=null;return {
  start(binding){current=new Gallery(binding);return this.progress();},
  add(row){if(!current)throw new Error('start gallery first');current.add(row);return this.progress();},
  progress(){if(!current)throw new Error('start gallery first');return `已记录 ${current.rows.length} / ${current.binding.total} 张`;},
  export(){if(!current)throw new Error('start gallery first');return current.manifest();}
 };}
 if(typeof module!=='undefined'&&module.exports)module.exports={createGalleryController};else root.createGalleryController=createGalleryController;
})(globalThis);
