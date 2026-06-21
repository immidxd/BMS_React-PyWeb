import React, { useEffect, useMemo, useState } from 'react';
import { message, notification } from 'antd';
import { productService } from '../../services/productService';
import type { ProductFilters } from '../../types/product';
import { addProductToDelivery, fetchNextProductNumber } from '../../services/referenceService';
import {
  getDeliveryDefaults, getDeliveryDefaultsRaw, setDeliveryDefault, clearDeliveryDefault, clearAllDeliveryDefaults,
  getGlobalDefaults, setGlobalDefault, clearGlobalDefault, clearAllGlobalDefaults,
} from './deliveryDefaults';
import AutoCompleteInput from './AutoCompleteInput';

// Правила-імплікації: додавання поля авто-додає повʼязане з дефолт-значенням (редаговано).
// Напр. «Тип шнурівки» → «Застібка»=шнурівка (взуття зі шнурівкою застібається шнурками).
const IMPLICATIONS: Record<string, Record<string, string>> = {
  lace_type_name: { fastening_type_name: 'шнурівка' },
};

// Поле → джерело варіантів для автодоповнення (з ProductFilters).
const refNames = (arr?: { name: string }[]) => (arr || []).map(x => x.name);
const OPT_FROM_FILTERS: Record<string, (f: ProductFilters) => string[]> = {
  type_name: f => refNames(f.types as any),
  brand_name: f => refNames(f.brands as any),
  color_name: f => refNames(f.colors as any),
  sole_color_name: f => refNames(f.colors as any),
  condition_name: f => refNames(f.conditions as any),
  gender_name: f => refNames(f.genders as any),
  style_name: f => refNames(f.styles as any),
  subtype_name: f => refNames(f.subtypes as any),
  season: f => f.seasons || [],
  manufacturer_name: f => refNames(f.countries as any),
  packaging_name: f => f.lookups?.packagings || [],
  sole_type_name: f => f.lookups?.sole_types || [],
  toe_shape_name: f => f.lookups?.toe_shapes || [],
  fastening_type_name: f => f.lookups?.fastening_types || [],
  lace_type_name: f => f.lookups?.lace_types || [],
  heel_type_name: f => f.lookups?.heel_types || [],
  lining_name: f => f.lookups?.linings || [],
  technology_name: f => f.lookups?.technologies || [],
  material_upper: f => f.lookups?.materials || [],
  material_middle: f => f.lookups?.materials || [],
  material_sole: f => f.lookups?.materials || [],
  material_midsole: f => f.lookups?.materials || [],
  material_insole: f => f.lookups?.materials || [],
  material_membrane: f => f.lookups?.materials || [],
};

const inputCls =
  'w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 ' +
  'px-2.5 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400';

type DLKey = 'types' | 'brands' | 'colors' | 'conditions' | 'genders' | 'styles' | 'subtypes' | 'seasons';
interface FieldDef { key: string; label: string; number?: boolean; dl?: DLKey; }

const F: Record<string, FieldDef> = {
  type_name: { key: 'type_name', label: 'Тип', dl: 'types' },
  brand_name: { key: 'brand_name', label: 'Бренд', dl: 'brands' },
  model: { key: 'model', label: 'Модель' },
  marking: { key: 'marking', label: 'Маркування' },
  gender_name: { key: 'gender_name', label: 'Стать', dl: 'genders' },
  color_name: { key: 'color_name', label: 'Колір', dl: 'colors' },
  price: { key: 'price', label: 'Ціна', number: true },
  condition_name: { key: 'condition_name', label: 'Стан', dl: 'conditions' },
  sizeeu: { key: 'sizeeu', label: 'Розмір' },
  dimensions: { key: 'dimensions', label: 'Габарити' },
  size_letter: { key: 'size_letter', label: 'Буквений' },
  measurementscm: { key: 'measurementscm', label: 'СМ' },
  packaging_name: { key: 'packaging_name', label: 'Пакування' },
  style_name: { key: 'style_name', label: 'Стиль', dl: 'styles' },
  subtype_name: { key: 'subtype_name', label: 'Підтип', dl: 'subtypes' },
  season: { key: 'season', label: 'Сезон', dl: 'seasons' },
  collection: { key: 'collection', label: 'Колекція' },
  gtin: { key: 'gtin', label: 'GTIN' },
  geometric_shape: { key: 'geometric_shape', label: 'Форма' },
  width: { key: 'width', label: 'Ширина' },
  year: { key: 'year', label: 'Рік', number: true },
  oldprice: { key: 'oldprice', label: 'Стара ціна', number: true },
  description: { key: 'description', label: 'Опис' },
  extranote: { key: 'extranote', label: 'Примітка' },
  manufacturer_name: { key: 'manufacturer_name', label: 'Виробник' },
  // Матеріали
  material_upper: { key: 'material_upper', label: 'Верх' },
  material_middle: { key: 'material_middle', label: 'Середина' },
  material_sole: { key: 'material_sole', label: 'Підошва' },
  material_midsole: { key: 'material_midsole', label: 'Проміжна' },
  material_insole: { key: 'material_insole', label: 'Устілка' },
  lining_name: { key: 'lining_name', label: 'Підкладка' },
  // Деталі
  material_membrane: { key: 'material_membrane', label: 'Мембрана' },
  sole_type_name: { key: 'sole_type_name', label: 'Тип підошви' },
  fastening_type_name: { key: 'fastening_type_name', label: 'Застібка' },
  sole_color_name: { key: 'sole_color_name', label: 'Колір підошви', dl: 'colors' },
  toe_shape_name: { key: 'toe_shape_name', label: 'Форма носка' },
  technology_name: { key: 'technology_name', label: 'Технологія' },
  heel_type_name: { key: 'heel_type_name', label: 'Тип каблука' },
  lace_type_name: { key: 'lace_type_name', label: 'Тип шнурівки' },
  height: { key: 'height', label: 'Висота', number: true },
  sole_thickness: { key: 'sole_thickness', label: 'Товщина підошви', number: true },
  // Одягові виміри
  chest: { key: 'chest', label: 'Груди (н/о)', number: true },
  waist: { key: 'waist', label: 'Талія (н/о)', number: true },
  hips: { key: 'hips', label: 'Бедра (н/о)', number: true },
  sleeve: { key: 'sleeve', label: 'Рукав', number: true },
  length: { key: 'length', label: 'Довжина', number: true },
};

type Cat = 'shoe' | 'bag' | 'suitcase' | 'clothing';
type HubView = 'root' | 'materials' | 'details';

const SUBHUBS: Record<Exclude<HubView, 'root'>, { label: string; fields: string[] }> = {
  materials: { label: 'Матеріали', fields: ['material_upper', 'material_middle', 'material_sole', 'material_midsole', 'material_insole', 'lining_name'] },
  details: { label: 'Деталі', fields: ['material_membrane', 'sole_type_name', 'fastening_type_name', 'sole_color_name', 'toe_shape_name', 'technology_name', 'heel_type_name', 'lace_type_name', 'height', 'sole_thickness'] },
};
const SUBHUB_KEYS = [...SUBHUBS.materials.fields, ...SUBHUBS.details.fields];
const CLOTHING_MEAS = ['chest', 'waist', 'hips', 'sleeve', 'length'];

// Поля, придатні як «дефолт на лот» (класифікація + деталі; БЕЗ унікальних per-item:
// номер/розмір/ціна/модель/маркування/виміри тощо).
const DEFAULTABLE_KEYS = [
  'type_name', 'subtype_name', 'brand_name', 'style_name', 'gender_name', 'color_name',
  'condition_name', 'season', 'packaging_name', 'manufacturer_name', 'width', 'geometric_shape',
  'lining_name', 'sole_type_name', 'fastening_type_name', 'sole_color_name', 'toe_shape_name',
  'technology_name', 'heel_type_name', 'lace_type_name',
  'material_upper', 'material_middle', 'material_sole', 'material_midsole', 'material_insole', 'material_membrane',
];

function categoryOf(t?: string): Cat {
  const s = (t || '').toLowerCase();
  if (/валіз|чемодан/.test(s)) return 'suitcase';
  if (/сумк|рюкзак|клатч|барсетк|борсетк|гаман|косметичк|шопер|портфел|саквояж/.test(s)) return 'bag';
  if (/куртк|штан|джинс|футболк|сорочк|світшот|худі|плат|сукн|спідниц|шорт|пальт|кофт|светр|комбінезон|костюм|жилет|толстовк|лонгслів|майк|бомбер|вітровк|пуховик|парк|жакет|кардиган|поло|туніка|блуз|рейтуз|лосин|легінс|бермуд|сарафан/.test(s)) return 'clothing';
  return 'shoe';
}

// Під-категорія одягу → набір вимірів у базі (штани не мають «Груди» тощо)
function clothingSubcat(t: string): 'bottom' | 'dress' | 'top' {
  const s = (t || '').toLowerCase();
  if (/штан|джинс|шорт|спідниц|лосин|рейтуз|легінс|бермуд/.test(s)) return 'bottom';
  if (/плат|сукн|комбінезон|сарафан|костюм/.test(s)) return 'dress';
  return 'top';
}

interface Layout { cat: Cat; key: string; base: string[]; gender: boolean; hubHide: string[]; hubExtra: string[]; subhubs: boolean; }

function layout(typeName?: string): Layout {
  const cat = categoryOf(typeName);
  if (cat === 'shoe')
    return { cat, key: 'shoe', base: ['sizeeu', 'measurementscm'], gender: true, subhubs: true,
      hubHide: ['dimensions', 'size_letter', 'geometric_shape', ...CLOTHING_MEAS], hubExtra: [] };
  if (cat === 'bag')
    return { cat, key: 'bag', base: ['dimensions'], gender: true, subhubs: false,
      hubHide: ['sizeeu', 'measurementscm', 'size_letter', ...CLOTHING_MEAS], hubExtra: ['geometric_shape'] };
  if (cat === 'suitcase')
    return { cat, key: 'suitcase', base: ['size_letter', 'dimensions'], gender: false, subhubs: false,
      hubHide: ['sizeeu', 'measurementscm', ...CLOTHING_MEAS], hubExtra: ['gender_name'] };
  const sub = clothingSubcat(typeName || '');
  const baseMeas = sub === 'bottom' ? ['waist', 'hips', 'length']
    : sub === 'dress' ? ['chest', 'waist', 'hips', 'length']
    : ['chest', 'sleeve', 'length'];
  return { cat, key: 'clothing:' + sub, base: ['size_letter', ...baseMeas], gender: true, subhubs: false,
    hubHide: ['sizeeu', 'measurementscm', 'dimensions', 'geometric_shape'],
    hubExtra: CLOTHING_MEAS.filter(m => !baseMeas.includes(m)) };
}

const OPTIONAL_POOL = [
  'packaging_name', 'style_name', 'subtype_name', 'season', 'measurementscm',
  'collection', 'gtin', 'geometric_shape', 'width', 'dimensions', 'size_letter',
  'manufacturer_name', 'year', 'oldprice', 'description', 'extranote',
];
const NUMBER_KEYS = new Set(['price', 'year', 'oldprice', 'height', 'sole_thickness', ...CLOTHING_MEAS]);
const KEEP_ON_CHANGE = ['productnumber', 'brand_name', 'model', 'marking', 'gender_name', 'color_name', 'price', 'condition_name'];

const baseFields = (lay: Layout): FieldDef[] => [
  F.type_name, F.brand_name, F.model, F.marking,
  ...(lay.gender ? [F.gender_name] : []),
  F.color_name,
  ...lay.base.map(k => F[k]),
  F.price, F.condition_name,
];
const optionalFor = (lay: Layout): string[] =>
  [...OPTIONAL_POOL, ...lay.hubExtra].filter(k => !lay.base.includes(k) && !lay.hubHide.includes(k));

interface Props {
  deliveryId: number;
  onSaved: () => void;
  filters?: ProductFilters | null;
}

const QuickAddProductForm: React.FC<Props> = ({ deliveryId, onSaved, filters: filtersProp }) => {
  const [values, setValues] = useState<Record<string, string>>(
    () => ({ productnumber: '', ...getDeliveryDefaults(deliveryId) })
  );
  const [extras, setExtras] = useState<string[]>([]);
  const [hubOpen, setHubOpen] = useState(false);
  const [hubView, setHubView] = useState<HubView>('root');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okFlash, setOkFlash] = useState(false);
  const [filters, setFilters] = useState<ProductFilters | null>(filtersProp || null);
  const [showDefaults, setShowDefaults] = useState(false);
  const [defTick, setDefTick] = useState(0);  // ре-рендер при зміні дефолтів
  const [pickKey, setPickKey] = useState('');   // обране поле в панелі дефолтів
  const [pickVal, setPickVal] = useState('');
  const [defScope, setDefScope] = useState<'global' | 'delivery'>('global');  // куди додавати дефолт

  useEffect(() => {
    if (filtersProp) { setFilters(filtersProp); return; }
    productService.getFilters().then(setFilters).catch(() => {});
  }, [filtersProp]);

  // Підставити дефолти завозу у форму (productnumber завжди порожній). Optional-поля
  // з дефолтів показуємо як extras, щоб користувач бачив підставлене значення.
  const applyDefaults = React.useCallback(() => {
    const d = getDeliveryDefaults(deliveryId);
    setValues({ productnumber: '', ...d });
    const lay2 = layout(d.type_name);
    const baseKeys = new Set(baseFields(lay2).map(f => f.key));
    setExtras(Object.keys(d).filter(k => F[k] && !baseKeys.has(k)));
  }, [deliveryId]);

  // Перезастосувати при зміні завозу або редагуванні дефолтів.
  useEffect(() => { applyDefaults(); /* eslint-disable-next-line */ }, [deliveryId, defTick]);

  const lay = useMemo(() => layout(values.type_name), [values.type_name]);
  const set = (k: string, v: string) => setValues(s => ({ ...s, [k]: v }));

  // Зміна типу → інший layout: анулюємо невластиві поля (лишаємо лише базові спільні).
  const onTypeChange = (v: string) => {
    const newLay = layout(v);
    if (newLay.key !== lay.key) {
      setExtras([]); setHubView('root');
      setValues(s => {
        const next: Record<string, string> = { type_name: v };
        KEEP_ON_CHANGE.forEach(k => { if (s[k]) next[k] = s[k]; });
        if (!newLay.gender) delete next.gender_name;  // напр. Валіза — без «Стать» за замовч.
        return next;
      });
    } else {
      setValues(s => ({ ...s, type_name: v }));
    }
  };

  const dlItems = (k?: DLKey): { name: string }[] => {
    if (!k || !filters) return [];
    if (k === 'seasons') return (filters.seasons || []).map(s => ({ name: s }));
    return ((filters as any)[k] || []) as { name: string }[];
  };

  const generate = async () => {
    const typed = (values.productnumber || '').trim().replace('#', '');
    const m = typed.match(/^([А-ЯҐЄІЇA-Za-zа-яґєії]+)/);
    const pfx = m ? m[1].toUpperCase() : 'Ф';
    try { set('productnumber', await fetchNextProductNumber(pfx)); } catch { /* ignore */ }
  };

  // Варіанти автодоповнення для поля (порожньо → звичайний інпут без підказок).
  const optionsFor = React.useCallback(
    (key: string): string[] => (filters ? (OPT_FROM_FILTERS[key]?.(filters) || []) : []),
    [filters]
  );

  const addExtra = (key: string) => {
    const imp = IMPLICATIONS[key] || {};
    const impKeys = Object.keys(imp);
    setExtras(e => {
      const ne = e.includes(key) ? [...e] : [...e, key];
      impKeys.forEach(k => { if (!ne.includes(k)) ne.push(k); });  // авто-додати повʼязані
      return ne;
    });
    if (impKeys.length) {
      setValues(s => { const ns = { ...s }; for (const [k, v] of Object.entries(imp)) if (!ns[k]) ns[k] = v; return ns; });
    }
    setHubOpen(false); setHubView('root');
  };
  const removeExtra = (key: string) => { setExtras(e => e.filter(k => k !== key)); setValues(s => ({ ...s, [key]: '' })); };

  const rootAvailable = useMemo(() => optionalFor(lay).filter(k => !extras.includes(k)), [lay, extras]);
  const subAvailable = (v: Exclude<HubView, 'root'>) => SUBHUBS[v].fields.filter(k => !extras.includes(k));
  const showSubhubs = lay.subhubs;

  const save = async () => {
    setError(null);
    const pnum = (values.productnumber || '').trim();
    if (!pnum) {
      setError('Вкажіть або згенеруйте номер');
      notification.warning({ message: 'Немає номера', description: 'Вкажіть номер товару або натисніть ⚡ для генерації.', placement: 'topRight' });
      return;
    }
    setSubmitting(true);
    try {
      const payload: any = { productnumber: pnum };
      Object.entries(values).forEach(([k, v]) => {
        if (k === 'productnumber' || v == null || v === '') return;
        payload[k] = NUMBER_KEYS.has(k) ? Number(v) : v;
      });
      await addProductToDelivery(deliveryId, payload);
      applyDefaults();  // скид форми, але дефолти лишаються підставленими (лот-за-лотом)
      setOkFlash(true); setTimeout(() => setOkFlash(false), 1500);
      message.success(`Товар ${pnum} додано`);
      onSaved();
    } catch (e: any) {
      const st = e?.response?.status;
      const d = e?.response?.data?.detail;
      // Категоризація причини → заголовок + опис у попапі (і дублюємо в inline-текст).
      let title = 'Не вдалося додати товар';
      let desc = typeof d === 'string' ? d : '';
      if (!e?.response) {
        title = 'Немає зв\'язку з програмою';
        desc = 'Бекенд не відповідає. Перевірте, що програма запущена, і спробуйте ще раз.';
      } else if (st === 409) {
        title = 'Такий номер уже існує';
        desc = desc || `Товар «${pnum}» вже є в базі. Згенеруйте новий номер (⚡) або змініть його.`;
      } else if (st === 400) {
        title = 'Некоректні дані';
        desc = desc || 'Перевірте заповнені поля (напр. порожній або хибний номер).';
      } else if (st === 403) {
        title = 'Додавання вимкнено';
        desc = desc || 'Функцію додавання вимкнено на бекенді (PARSER_ADD_PRODUCT=0).';
      } else if (st === 404) {
        title = 'Завіз не знайдено';
        desc = desc || 'Цей завіз більше не існує — оновіть сторінку.';
      } else if (st === 502) {
        title = 'Проблема зв\'язку з Google Sheets';
        desc = desc || 'Товар НЕ додано через тимчасову помилку мережі. Спробуйте ще раз за кілька секунд.';
      } else if (st === 500) {
        title = 'Помилка збереження в базі';
        desc = desc || 'Внутрішня помилка. Спробуйте ще раз; якщо повторюється — перезапустіть програму.';
      }
      setError(desc || title);
      notification.error({ message: title, description: desc, duration: 8, placement: 'topRight' });
    } finally { setSubmitting(false); }
  };

  const renderField = (f: FieldDef, removable = false) => {
    const opts = optionsFor(f.key);
    const onVal = (v: string) => (f.key === 'type_name' ? onTypeChange(v) : set(f.key, v));
    return (
    <div key={f.key} className="relative">
      {opts.length > 0 ? (
        <AutoCompleteInput
          value={values[f.key] || ''}
          options={opts}
          placeholder={f.label}
          className={inputCls}
          onChange={onVal}
          onPick={onVal}
        />
      ) : (
        <input
          type={f.number ? 'number' : 'text'}
          value={values[f.key] || ''}
          onChange={e => onVal(e.target.value)}
          placeholder={f.label}
          className={inputCls}
          autoCapitalize="none" autoCorrect="off" spellCheck={false}
        />
      )}
      {removable && (
        <button type="button" onClick={() => removeExtra(f.key)} title="Прибрати поле"
          className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-gray-300 dark:bg-gray-600 text-white text-[10px] leading-none flex items-center justify-center hover:bg-red-500">×</button>
      )}
    </div>
    );
  };

  const chip = (label: string, onClick: () => void, accent = false) => (
    <button key={label} type="button" onClick={onClick}
      className={`px-3 py-1.5 rounded-lg text-sm border transition-all hover:scale-[1.03]
        ${accent ? 'bg-gray-900 text-white border-gray-900 hover:bg-gray-700'
                 : 'bg-white dark:bg-gray-700 border-gray-200 dark:border-gray-600 hover:border-gray-400 hover:bg-gray-100 dark:hover:bg-gray-600'}`}>
      {label}
    </button>
  );

  const hubHasContent = rootAvailable.length > 0 || (showSubhubs && SUBHUB_KEYS.some(k => !extras.includes(k)));

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2.5">
        <div className="flex gap-1.5 col-span-2">
          <input value={values.productnumber || ''} onChange={e => set('productnumber', e.target.value)}
            placeholder="Номер" className={inputCls}
            autoCapitalize="none" autoCorrect="off" spellCheck={false} />
          <button onClick={generate} type="button" title="Згенерувати наступний вільний номер"
            className="whitespace-nowrap px-2 py-1.5 rounded-lg text-sm border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700">⚡</button>
        </div>
        {baseFields(lay).map(f => renderField(f))}
        {extras.map(k => renderField(F[k], true))}
        {hubHasContent && (
          <button type="button" onClick={() => { setHubOpen(o => !o); setHubView('root'); }}
            className={`flex items-center justify-center gap-1 rounded-lg border border-dashed text-sm font-medium transition-colors py-1.5
              ${hubOpen ? 'border-gray-500 text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-700'
                        : 'border-gray-300 dark:border-gray-600 text-gray-500 hover:border-gray-400 hover:text-gray-700'}`}>
            <span className="text-lg leading-none">＋</span> ще поле
          </button>
        )}
      </div>

      {/* Хаб (з під-хабами Матеріали/Деталі) */}
      <div className={`overflow-hidden transition-all duration-300 ${hubOpen ? 'max-h-72 opacity-100 mt-3' : 'max-h-0 opacity-0'}`}>
        <div key={hubView} className="bms-fade-in flex flex-wrap items-center gap-2 p-3 rounded-xl bg-gray-50 dark:bg-gray-800/60 border border-gray-100 dark:border-gray-700">
          {hubView !== 'root' && (
            <button type="button" onClick={() => setHubView('root')}
              className="px-2.5 py-1.5 rounded-lg text-sm text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">‹ Назад</button>
          )}
          {hubView === 'root' && rootAvailable.map(k => chip(F[k].label, () => addExtra(k)))}
          {hubView === 'root' && showSubhubs && subAvailable('materials').length > 0 && chip('Матеріали ▸', () => setHubView('materials'), true)}
          {hubView === 'root' && showSubhubs && subAvailable('details').length > 0 && chip('Деталі ▸', () => setHubView('details'), true)}
          {hubView !== 'root' && subAvailable(hubView).map(k => chip(F[k].label, () => addExtra(k)))}
        </div>
      </div>

      <datalist id="qf-types">{dlItems('types').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-brands">{dlItems('brands').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-colors">{dlItems('colors').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-conditions">{dlItems('conditions').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-genders">{dlItems('genders').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-styles">{dlItems('styles').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-subtypes">{dlItems('subtypes').map((x, i) => <option key={i} value={x.name} />)}</datalist>
      <datalist id="qf-seasons">{dlItems('seasons').map((x, i) => <option key={i} value={x.name} />)}</datalist>

      {/* Панель «Дефолти» — глобальні (📌 усі завози) + по-завозу */}
      {showDefaults && (() => {
        const globalD = getGlobalDefaults();
        const perD = getDeliveryDefaultsRaw(deliveryId);
        const gKeys = Object.keys(globalD);
        const pKeys = Object.keys(perD).filter(k => !(k in globalD)); // по-завозу, що не дублюють глобальні
        const removeKey = (k: string) => {
          if (k in perD) clearDeliveryDefault(deliveryId, k); else clearGlobalDefault(k);
          setDefTick(t => t + 1);
        };
        const chip = (k: string, val: string, isGlobal: boolean) => (
          <span key={k} className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[12px] border ${isGlobal
            ? 'bg-white dark:bg-gray-800 border-amber-300 dark:border-amber-700' : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600'}`}>
            {isGlobal && <span title="Для всіх завозів">📌</span>}
            <b className="font-medium">{F[k]?.label || k}:</b> {val}
            <button type="button" onClick={() => removeKey(k)} className="ml-0.5 text-gray-400 hover:text-red-500">×</button>
          </span>
        );
        return (
          <div className="mt-3 p-3 rounded-xl bg-amber-50 dark:bg-amber-900/15 border border-amber-200 dark:border-amber-800/40">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[12px] font-medium text-amber-800 dark:text-amber-300">
                Дефолти — підставляються в кожен новий лот (📌 = для всіх завозів)
              </span>
              {(gKeys.length > 0 || pKeys.length > 0) && (
                <button type="button" onClick={() => { clearAllGlobalDefaults(); clearAllDeliveryDefaults(deliveryId); setDefTick(t => t + 1); }}
                  className="text-[11px] text-amber-700 dark:text-amber-400 hover:underline">Очистити всі</button>
              )}
            </div>
            {(gKeys.length > 0 || pKeys.length > 0) && (
              <div className="flex flex-wrap gap-1.5 mb-2.5">
                {gKeys.map(k => chip(k, globalD[k], true))}
                {pKeys.map(k => chip(k, perD[k], false))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {/* Куди додавати: для всіх завозів (глоб.) чи лише цей */}
              <div className="inline-flex rounded-lg border border-amber-300 dark:border-amber-700 overflow-hidden text-[11px]">
                <button type="button" onClick={() => setDefScope('global')}
                  className={`px-2 py-1.5 ${defScope === 'global' ? 'bg-amber-200 dark:bg-amber-800/50 text-amber-900 dark:text-amber-200' : 'text-amber-700 dark:text-amber-400'}`}>📌 Усі завози</button>
                <button type="button" onClick={() => setDefScope('delivery')}
                  className={`px-2 py-1.5 border-l border-amber-300 dark:border-amber-700 ${defScope === 'delivery' ? 'bg-amber-200 dark:bg-amber-800/50 text-amber-900 dark:text-amber-200' : 'text-amber-700 dark:text-amber-400'}`}>Цей завіз</button>
              </div>
              <select value={pickKey} onChange={e => { setPickKey(e.target.value); setPickVal(''); }}
                className={inputCls + ' max-w-[160px]'}>
                <option value="">— поле —</option>
                {DEFAULTABLE_KEYS.filter(k => F[k]).map(k => <option key={k} value={k}>{F[k].label}</option>)}
              </select>
              {pickKey && optionsFor(pickKey).length > 0 ? (
                <div className="w-[160px]">
                  <AutoCompleteInput value={pickVal} options={optionsFor(pickKey)} placeholder="значення"
                    className={inputCls} onChange={setPickVal} />
                </div>
              ) : (
                <input value={pickVal} onChange={e => setPickVal(e.target.value)}
                  placeholder="значення" disabled={!pickKey}
                  autoCapitalize="none" autoCorrect="off" spellCheck={false}
                  className={inputCls + ' max-w-[160px]'} />
              )}
              <button type="button" disabled={!pickKey || !pickVal.trim()}
                onClick={() => {
                  if (defScope === 'global') setGlobalDefault(pickKey, pickVal.trim());
                  else setDeliveryDefault(deliveryId, pickKey, pickVal.trim());
                  setPickKey(''); setPickVal(''); setDefTick(t => t + 1);
                }}
                className="px-3 py-1.5 rounded-lg text-sm border border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900/30 disabled:opacity-40">
                Додати дефолт
              </button>
            </div>
          </div>
        );
      })()}

      {error && <div className="mt-2 text-sm text-red-600">{error}</div>}
      {okFlash && <div className="mt-2 text-sm text-green-600">✓ Товар додано</div>}
      <div className="mt-3 flex items-center justify-between">
        <button type="button" onClick={() => setShowDefaults(s => !s)}
          className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${showDefaults || Object.keys(getDeliveryDefaults(deliveryId)).length > 0
            ? 'border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/15'
            : 'border-gray-300 dark:border-gray-600 text-gray-500 hover:text-gray-700'}`}>
          ⚙ Дефолти{(() => { const n = Object.keys(getDeliveryDefaults(deliveryId)).length; return n > 0 ? ` · ${n}` : ''; })()}
        </button>
        <button onClick={save} disabled={submitting}
          className="px-4 py-1.5 rounded-lg text-sm font-medium bg-black text-white hover:bg-gray-800 disabled:opacity-50">
          {submitting ? 'Додавання…' : 'Зберегти товар'}
        </button>
      </div>
    </div>
  );
};

export default QuickAddProductForm;
