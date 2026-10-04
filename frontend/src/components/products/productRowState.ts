import type { Product } from '../../types/product';

/**
 * Стан рядка товару — ОДНЕ правило для «Товарів» і картки поставки:
 *   • жовтий (`bms-conflict-row`) — товару бракує даних або є конфлікт;
 *   • сірий (`bms-row-reserved`) — бронь (Підтверджено, без оплати);
 *   • конфлікт має пріоритет над бронню.
 * Підказка (`title`) перелічує, чого саме бракує.
 */
export function productRowIssues(record: Product): string[] {
  const issues: string[] = [];
  const pn = record.productnumber;
  const noNum = !pn || pn === '???' || pn.startsWith('__tmp_rename_') || pn.startsWith('???_');
  if (noNum) {
    const clones = (record as any).clonednumbers;
    if (clones && String(clones).trim()) {
      issues.push(`Тільки номер-клон: ${String(clones).slice(0, 60)}`);
    } else {
      issues.push('Товар не має номера');
    }
  }
  if (!record.type_name) issues.push('Не вказано тип');
  if (!record.price) issues.push('Ціна = 0 або не вказана');
  if (!record.supplier_name) issues.push('Не вказано постачальника');
  const sold = record.sold_count ?? 0;
  const qty = record.quantity ?? 0;
  if (sold > qty) issues.push(`Перепродано: ${sold} продано з ${qty} наявних`);
  if ((record.pnum_dup_brands ?? 0) > 1) issues.push('Номер товару дублюється (різні бренди)');
  return issues;
}

export function productRowState(record: Product): { className: string; title?: string } {
  const issues = productRowIssues(record);
  if (issues.length) return { className: 'bms-conflict-row', title: `⚠ ${issues.join(' • ')}` };
  if (record.is_reserved) return { className: 'bms-row-reserved', title: '🔒 Заброньовано (Підтверджено, без оплати)' };
  return { className: '' };
}
