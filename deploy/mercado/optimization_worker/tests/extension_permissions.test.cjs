const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const basePermissions = ['activeTab', 'downloads', 'nativeMessaging', 'storage'];
for (const [directory, expectedHosts] of Object.entries({
  chrome_extension: ['https://www.mercadolibre.com.mx/*', 'https://listado.mercadolibre.com.mx/*', 'https://articulo.mercadolibre.com.mx/*'],
  shein_extension: ['https://us.shein.com/*'],
})) {
  test(`${directory} only declares its authorized browser and platform permissions`, () => {
    const file = path.join(__dirname, '..', directory, 'manifest.json');
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
    const expected = directory === 'chrome_extension' ? [...basePermissions, 'tabs', 'alarms'] : basePermissions;
    assert.deepEqual([...manifest.permissions].sort(), [...expected].sort());
    assert.deepEqual([...manifest.host_permissions].sort(), [...expectedHosts].sort());
    for (const script of manifest.content_scripts) {
      assert.ok(script.matches.every(match => expectedHosts.includes(match)));
    }
  });
}
