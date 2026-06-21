/** Категорія товару за назвою «Тип». Спільне джерело істини між формою «Додати товар»
 *  (`QuickAddProductForm`) та карткою товару (`ProductDetailsModal`) — щоб тип-залежні
 *  поля показувались/ховались однаково всюди.
 *
 *  Категорії:
 *    - shoe     — взуття (кросівки, чоботи, ботільйони, балетки, …)
 *    - bag      — сумки/рюкзаки/клатчі/гаманці/косметички/портфелі/шопери
 *    - suitcase — валіза/чемодан
 *    - clothing — одяг (з підтипом bottom|dress|top)
 *
 *  Для одягу важливий ПІДКАТЕГОРІЙНИЙ підбір вимірів:
 *    - bottom (штани/шорти/спідниці)   → талія, бедра, довжина
 *    - dress  (плаття/сукні/комбінез.) → груди, талія, бедра, довжина
 *    - top    (футболки, светри, …)    → груди, рукав, довжина
 */
export type ProductCategory = 'shoe' | 'bag' | 'suitcase' | 'clothing';
export type ClothingSubcat = 'bottom' | 'dress' | 'top';

export function categoryOf(typeName?: string | null): ProductCategory {
  const s = (typeName || '').toLowerCase();
  if (/валіз|чемодан/.test(s)) return 'suitcase';
  if (/сумк|рюкзак|клатч|барсетк|борсетк|гаман|косметичк|шопер|портфел|саквояж/.test(s)) return 'bag';
  if (/куртк|штан|джинс|футболк|сорочк|світшот|худі|плат|сукн|спідниц|шорт|пальт|кофт|светр|комбінезон|костюм|жилет|толстовк|лонгслів|майк|бомбер|вітровк|пуховик|парк|жакет|кардиган|поло|туніка|блуз|рейтуз|лосин|легінс|бермуд|сарафан/.test(s)) return 'clothing';
  return 'shoe';
}

export function clothingSubcat(typeName?: string | null): ClothingSubcat {
  const s = (typeName || '').toLowerCase();
  if (/штан|джинс|шорт|спідниц|лосин|рейтуз|легінс|бермуд/.test(s)) return 'bottom';
  if (/плат|сукн|комбінезон|сарафан|костюм/.test(s)) return 'dress';
  return 'top';
}

/** Які виміри одягу актуальні для підкатегорії. */
const CLOTHING_MEASUREMENTS: Record<ClothingSubcat, Set<string>> = {
  bottom: new Set(['pot', 'pob', 'length']),       // талія, бедра, довжина
  dress:  new Set(['pog', 'pot', 'pob', 'length']), // груди, талія, бедра, довжина
  top:    new Set(['pog', 'sleeve', 'length']),     // груди, рукав, довжина
};

/** Тип-залежна видимість полів edit-mode картки товару.
 *  Повертає Set ключів, які НЕ треба показувати для типу `typeName`. */
export function hiddenFieldsForType(typeName?: string | null): Set<string> {
  const cat = categoryOf(typeName);
  const hidden = new Set<string>();

  // 1) Розмірні поля (sizeeu/measurementscm/size_letter/dimensions/geometric_shape)
  if (cat === 'shoe') {
    // взуття: НЕ показуємо «Габарити», «Геом. форма», «Буквений»
    hidden.add('dimensions');
    hidden.add('geometric_shape');
    hidden.add('size_letter');
  } else if (cat === 'bag') {
    // сумки: НЕ показуємо EU/СМ/Буквений (розмір — це габарити)
    hidden.add('sizeeu');
    hidden.add('measurementscm');
    hidden.add('size_letter');
  } else if (cat === 'suitcase') {
    // валізи: НЕ показуємо EU/СМ/Геом. форма (розмір — буквений + габарити)
    hidden.add('sizeeu');
    hidden.add('measurementscm');
    hidden.add('geometric_shape');
  } else {
    // одяг: НЕ показуємо EU/СМ/Габарити/Геом. форма
    hidden.add('sizeeu');
    hidden.add('measurementscm');
    hidden.add('dimensions');
    hidden.add('geometric_shape');
  }

  // 2) Взуттєві характеристики «Інше» (Тип підошви/Форма носка/Застібка/…)
  // ⚠️ «Застібка» (fastening) і «Підкладка» (lining) — УНІВЕРСАЛЬНІ (сумки/валізи/одяг теж
  // мають застібку: блискавка/магніт/замок/ґудзики; та підкладку). Решта — суто взуттєві.
  const SHOE_ONLY = [
    'sole_type_name', 'sole_color_name', 'toe_shape_name',
    'lace_type_name', 'heel_type_name', 'technology_name',
  ];
  if (cat !== 'shoe') {
    for (const f of SHOE_ONLY) hidden.add(f);
  }

  // 3) Заміри (MEASUREMENTS: height/sole_thickness/heel/length/pog/pob/pot/sleeve).
  //    Для взуття: лише взуттєві (height/sole_thickness/heel) + length як універсальний.
  //    Для одягу — лише виміри відповідної підкатегорії.
  //    Для сумок/валіз — без замірів.
  const SHOE_MEAS = ['height', 'sole_thickness', 'heel'];
  const CLOTH_MEAS_ALL = ['pog', 'pob', 'pot', 'sleeve', 'length'];
  if (cat === 'shoe') {
    for (const m of CLOTH_MEAS_ALL) {
      if (m === 'length') continue;  // довжину дозволяємо для взуття (устілка)
      hidden.add(`meas_${m}`);
    }
  } else if (cat === 'clothing') {
    const sub = clothingSubcat(typeName);
    const allowed = CLOTHING_MEASUREMENTS[sub];
    for (const m of CLOTH_MEAS_ALL) {
      if (!allowed.has(m)) hidden.add(`meas_${m}`);
    }
    for (const m of SHOE_MEAS) hidden.add(`meas_${m}`);
  } else {
    // bag / suitcase — без замірів
    for (const m of [...SHOE_MEAS, ...CLOTH_MEAS_ALL]) hidden.add(`meas_${m}`);
  }

  // 4) Матеріали-«Мембрана» / «Проміжна підошва» — лише взуття
  if (cat !== 'shoe') {
    hidden.add('material_membrane');
    hidden.add('material_midsole');
  }

  return hidden;
}
