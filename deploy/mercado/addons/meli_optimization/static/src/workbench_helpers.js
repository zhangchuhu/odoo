/** @odoo-module **/
export function displayValue(value) {
    if (typeof value === 'boolean') return value ? '是' : '否';
    return value === null || value === undefined ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}
export function candidateRows(result) {
    return result?.candidates || result?.facts || result?.keywords || result?.images || result?.checks || (result?.image ? [result.image] : []);
}
export function moveSelection(selection, index, delta) {
    const copy = [...selection];
    const target = index + delta;
    if (target >= 0 && target < copy.length) [copy[index], copy[target]] = [copy[target], copy[index]];
    return copy;
}
