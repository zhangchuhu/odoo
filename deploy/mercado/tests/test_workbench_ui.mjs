import { test } from 'node:test';
import assert from 'node:assert/strict';
import { candidateRows, moveSelection, displayValue } from '../addons/meli_optimization/static/src/workbench_helpers.js';
test('renders real result cards and preserves unknown metrics', () => {
    assert.equal(displayValue(null), '未知');
    assert.equal(displayValue(0), '0');
    assert.equal(displayValue(false), '否');
    assert.deepEqual(candidateRows({facts: [{name:'库存', value:null}]}), [{name:'库存', value:null}]);
});
test('image ordering preserves selected identity and rejects bounds', () => {
    assert.deepEqual(moveSelection([2,0,1], 1, -1), [0,2,1]);
    assert.deepEqual(moveSelection([2,0,1], 0, -1), [2,0,1]);
});
