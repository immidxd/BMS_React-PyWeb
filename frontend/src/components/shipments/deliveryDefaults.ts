/** Дефолти для нових товарів — два шари:
 *
 *  1) ГЛОБАЛЬНІ (📌) — діють у БУДЬ-ЯКОМУ завозі, зберігаються в localStorage
 *     (переживають перезапуск). Сід за замовчуванням: плоска підошва / шнурівка-застібка /
 *     Повсякденний стиль / круглий носок (БЕЗ типу — тип задається щоразу).
 *  2) ПО-ЗАВОЗУ — лише для конкретного завозу, ПЕРСИСТЕНТНІ (localStorage, keyed by
 *     deliveryId): виставлені в завозі дефолти памʼятаються й після перезапуску — коли
 *     користувач повертається до того завозу, вони активні.
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

// ── Шар по-завозу (localStorage, keyed by deliveryId) ────────────────────────
const PER_KEY = 'bms_delivery_defaults';  // { [deliveryId]: Defaults }

function loadPer(): Record<string, Defaults> {
  try {
    const raw = localStorage.getItem(PER_KEY);
    return raw ? (JSON.parse(raw) as Record<string, Defaults>) : {};
  } catch { return {}; }
}
function savePer(all: Record<string, Defaults>): void {
  try { localStorage.setItem(PER_KEY, JSON.stringify(all)); } catch { /* недоступно */ }
}

export function getDeliveryDefaultsRaw(deliveryId: number): Defaults {
  return { ...(loadPer()[String(deliveryId)] || {}) };
}
export function setDeliveryDefault(deliveryId: number, key: string, value: string): void {
  const all = loadPer();
  const cur = all[String(deliveryId)] || {};
  if (value == null || value === '') delete cur[key]; else cur[key] = value;
  all[String(deliveryId)] = cur;
  savePer(all);
}
export function clearDeliveryDefault(deliveryId: number, key: string): void {
  const all = loadPer();
  const cur = all[String(deliveryId)];
  if (!cur) return;
  delete cur[key];
  all[String(deliveryId)] = cur;
  savePer(all);
}
export function clearAllDeliveryDefaults(deliveryId: number): void {
  const all = loadPer();
  delete all[String(deliveryId)];
  savePer(all);
}

// ── Ефективні (глобальні ⊕ по-завозу) ────────────────────────────────────────
export function getDeliveryDefaults(deliveryId: number): Defaults {
  return { ...loadGlobal(), ...(loadPer()[String(deliveryId)] || {}) };
}
