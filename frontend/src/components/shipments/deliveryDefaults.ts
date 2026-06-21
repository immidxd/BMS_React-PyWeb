/** Дефолти для нових товарів — два шари:
 *
 *  1) ГЛОБАЛЬНІ (📌) — діють у БУДЬ-ЯКОМУ завозі, зберігаються в localStorage
 *     (переживають перезапуск). Сід за замовчуванням: плоска підошва / шнурівка-застібка /
 *     Повсякденний стиль / круглий носок (БЕЗ типу — тип задається щоразу).
 *  2) ПО-ЗАВОЗУ — лише для конкретного завозу, у памʼяті процесу (переживають закриття
 *     вікна/перемикання вкладок; гинуть на закритті програми).
 *
 *  Ефективні дефолти = глобальні ⊕ по-завозу (по-завозу перекриває глобальні).
 *  Ключі = поля QuickAddProductForm (type_name, sole_type_name, fastening_type_name…).
 */
type Defaults = Record<string, string>;

// ── Глобальний шар (localStorage) ────────────────────────────────────────────
const GLOBAL_KEY = 'bms_global_product_defaults';
const SEED_FLAG = 'bms_global_defaults_seeded';
// Початковий набір (за вимогою користувача — без «Тип»).
const GLOBAL_SEED: Defaults = {
  sole_type_name: 'плоска',
  fastening_type_name: 'шнурівка',
  style_name: 'Повсякденний',
  toe_shape_name: 'круглий',
};

function loadGlobal(): Defaults {
  try {
    const raw = localStorage.getItem(GLOBAL_KEY);
    if (raw !== null) return JSON.parse(raw) as Defaults;
    // Перший запуск — засіяти набором за замовчуванням (одноразово).
    if (!localStorage.getItem(SEED_FLAG)) {
      localStorage.setItem(GLOBAL_KEY, JSON.stringify(GLOBAL_SEED));
      localStorage.setItem(SEED_FLAG, '1');
      return { ...GLOBAL_SEED };
    }
    return {};
  } catch { return {}; }
}
function saveGlobal(d: Defaults): void {
  try {
    localStorage.setItem(GLOBAL_KEY, JSON.stringify(d));
    localStorage.setItem(SEED_FLAG, '1');  // після ручної зміни більше не сіємо
  } catch { /* localStorage недоступний — ігноруємо */ }
}

export function getGlobalDefaults(): Defaults { return loadGlobal(); }
export function setGlobalDefault(key: string, value: string): void {
  const d = loadGlobal();
  if (value == null || value === '') delete d[key]; else d[key] = value;
  saveGlobal(d);
}
export function clearGlobalDefault(key: string): void {
  const d = loadGlobal(); delete d[key]; saveGlobal(d);
}
export function clearAllGlobalDefaults(): void { saveGlobal({}); }

// ── Шар по-завозу (in-memory) ────────────────────────────────────────────────
const store = new Map<number, Defaults>();

export function getDeliveryDefaultsRaw(deliveryId: number): Defaults {
  return { ...(store.get(deliveryId) || {}) };
}
export function setDeliveryDefault(deliveryId: number, key: string, value: string): void {
  const cur = store.get(deliveryId) || {};
  if (value == null || value === '') delete cur[key]; else cur[key] = value;
  store.set(deliveryId, cur);
}
export function clearDeliveryDefault(deliveryId: number, key: string): void {
  const cur = store.get(deliveryId);
  if (!cur) return;
  delete cur[key];
  store.set(deliveryId, cur);
}
export function clearAllDeliveryDefaults(deliveryId: number): void { store.delete(deliveryId); }

// ── Ефективні (глобальні ⊕ по-завозу) ────────────────────────────────────────
export function getDeliveryDefaults(deliveryId: number): Defaults {
  return { ...loadGlobal(), ...(store.get(deliveryId) || {}) };
}
