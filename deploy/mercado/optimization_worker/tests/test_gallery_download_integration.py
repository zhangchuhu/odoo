"""Run the extension download loop against the original host in a private fixture."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest


# Chrome Downloads/storage are local fixtures. The original JS sends both native
# operations across this pipe; the original Python host validates the real files.
DOWNLOAD_DRIVER = r"""
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const input = readline.createInterface({input:process.stdin})[Symbol.asyncIterator]();
const read = async () => JSON.parse((await input.next()).value);
const write = obj => process.stdout.write(JSON.stringify(obj)+'\n');
(async () => {
  const conf = await read();
  const {downloadGallery} = require(conf.module);
  const files = new Map(), storage = new Map(), extension = 'fixture-extension';
  const api={runtime:{id:extension}, storage:{local:{
    get:async key=>({[key]:storage.get(key)}),set:async data=>Object.entries(data).forEach(([k,v])=>storage.set(k,v)),remove:async key=>storage.delete(key),
  }}, downloads:{
    download: async ({url, filename})=>{
      const data = Buffer.from(conf.bytes[conf.draft.images.indexOf(url)],'base64');
      const dest = path.join(conf.root, filename);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.writeFileSync(dest,data,{flag:'wx'});
      const id = files.size+1;
      files.set(id,{id,url,finalUrl:url,byExtensionId:extension,filename:dest,fileSize:data.length,exists:true,danger:'safe',state:'complete'});
      return id;
    }, search:async ({id})=>[files.get(id)],
  }};
  async function checkpoint(operation, data) {write({operation,...data});return await read();}
  const count=await downloadGallery({api,draft:conf.draft,platform:'mercado-libre-mx',requestId:conf.request,checkpoint,verifyPage:async()=>{}});
  write({done:count,downloaded:files.size});
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>process.stdin.destroy());
"""


class GalleryDownloadIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Chrome-extension tests require Node.js')
    def test_extension_consumes_host_issued_slots_and_confirms_real_rasters(self):
        from optimization_worker.tests.test_gallery_download import GalleryDownloadTests
        fixture = GalleryDownloadTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manifest = fixture.store.inspect(fixture.request)['collector_pending']['gallery_manifests'][fixture.identity]
        worker_dir = Path(__file__).resolve().parents[1]
        proc = subprocess.Popen(['node','-e',DOWNLOAD_DRIVER], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            def reply(value):
                proc.stdin.write(json.dumps(value)+'\n')
                proc.stdin.flush()
            reply({'module':str(worker_dir/'chrome_extension'/'gallery_download.js'), 'request':fixture.request,
                   'root':str(fixture.downloads), 'bytes':[base64.b64encode(v).decode() for v in fixture.raw],
                   'draft':{'identity':fixture.identity, 'total':len(fixture.sources), 'images':fixture.sources,
                            'confirmed':manifest['screenshot_sha256']}})
            operations = []
            for raw in proc.stdout:
                event = json.loads(raw)
                if 'done' in event:
                    self.assertEqual(event, {'done':2,'downloaded':2})
                    break
                operations.append(event['operation'])
                self.assertLessEqual(len(operations), 5)
                reply(fixture.handler.handle(fixture.broker, dict(fixture.base,**event)))
            self.assertEqual(proc.wait(timeout=5),0,proc.stderr.read())
            self.assertEqual(operations,['gallery_download_next','gallery_download_saved']*2+['gallery_download_next'])
            self.assertEqual(fixture.client.create_research_artifact.call_count, 2)
            self.assertEqual(fixture.client.artifact.call_count, 2)
        finally:
            proc.kill()
            proc.communicate(timeout=5)
