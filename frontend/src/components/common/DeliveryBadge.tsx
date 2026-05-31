/**
 * DeliveryBadge — відображення способу доставки.
 *
 * Пріоритет: логотип сервісу (Нова пошта / Укрпошта / Meest / …).
 * Якщо логотип відсутній або не завантажився — fallback на кольоровий
 * текстовий бейдж (як було раніше). Поведінка без логотипів = без змін.
 *
 * Куди класти логотипи:
 *   frontend/public/delivery-logos/<slug>.(svg|png|webp)
 * Компонент по черзі пробує svg → png → webp; перший успішний показується.
 * Формат не критичний, але SVG найкращий (чіткий на будь-якому масштабі);
 * PNG з прозорим фоном теж добре. Рекомендована висота ~32–48px.
 *
 * slug-и (імена файлів):
 *   Нова пошта → nova-poshta
 *   Укрпошта   → ukrposhta
 *   Meest      → meest
 *   Самовивіз  → pickup
 *   Магазин    → shop
 */
import React, { useState } from 'react';

export const DELIVERY_COLORS: Record<string, string> = {
  'Нова пошта':  'bg-red-100 text-red-700 border-red-200',
  'НП':          'bg-red-100 text-red-700 border-red-200',
  'Укрпошта':    'bg-yellow-100 text-yellow-700 border-yellow-200',
  'УП':          'bg-yellow-100 text-yellow-700 border-yellow-200',
  'Meest':       'bg-orange-100 text-orange-700 border-orange-200',
  'самовивіз':   'bg-blue-100 text-blue-700 border-blue-200',
  'Самовивіз':   'bg-blue-100 text-blue-700 border-blue-200',
  'Локально':    'bg-violet-100 text-violet-700 border-violet-200',
  'Магазин':     'bg-pink-100 text-pink-700 border-pink-200',
  'Відкладено':  'bg-sky-100 text-sky-700 border-sky-200',
};

/** Normalize short delivery method names to full canonical form */
export const normalizeDelivery = (dm: string): string => {
  const up = dm.trim().toUpperCase();
  if (up === 'НП' || up === 'НОВА ПОШТА') return 'Нова пошта';
  if (up === 'УП' || up === 'УКРПОШТА') return 'Укрпошта';
  if (up === 'САМОВИВІЗ' || up === 'САМОВИВОЗ') return 'Самовивіз';
  if (up === 'MEEST' || up === 'МІСТ' || up === 'МЕЕСТ') return 'Meest';
  return dm.charAt(0).toUpperCase() + dm.slice(1);
};

/** Canonical name → можливі базові імена файлу логотипу (пробуються по черзі) */
const LOGO_SLUGS: Record<string, string[]> = {
  'Нова пошта': ['np-logo', 'nova-poshta'],
  'Укрпошта':   ['up-logo', 'ukrposhta'],
  'Meest':      ['meest-logo', 'meest'],
  'Самовивіз':  ['pickup'],
  'Магазин':    ['shop'],
};

const LOGO_EXTENSIONS = ['svg', 'png', 'webp', 'jpg', 'jpeg'];

const TextBadge: React.FC<{ label: string }> = ({ label }) => {
  const cls = DELIVERY_COLORS[label] || 'bg-gray-100 text-gray-600 border-gray-200';
  return (
    <span className={`inline-flex px-1.5 py-0 rounded text-[10px] font-semibold border ${cls}`}>
      {label}
    </span>
  );
};

interface DeliveryBadgeProps {
  /** Сира назва способу доставки з БД (може бути скороченою) */
  name?: string | null;
  /** Висота логотипу в px (за замовч. 20) */
  height?: number;
  /** Заглушка при відсутньому значенні (за замовч. «—») */
  emptyDash?: boolean;
}

export const DeliveryBadge: React.FC<DeliveryBadgeProps> = ({ name, height = 20, emptyDash = true }) => {
  const [candIdx, setCandIdx] = useState(0);
  const [logoFailed, setLogoFailed] = useState(false);

  if (!name || !name.trim()) {
    return emptyDash ? <span className="text-gray-300">—</span> : null;
  }

  const canonical = normalizeDelivery(name);
  const basenames = LOGO_SLUGS[canonical];

  // Усі кандидати: кожне базове ім'я × кожне розширення.
  const candidates = basenames
    ? basenames.flatMap(b => LOGO_EXTENSIONS.map(ext => `/delivery-logos/${b}.${ext}`))
    : [];

  // Немає кандидатів або всі не завантажились → текстовий бейдж (як раніше)
  if (candidates.length === 0 || logoFailed) {
    return <TextBadge label={canonical} />;
  }

  return (
    <img
      src={candidates[candIdx]}
      alt={canonical}
      title={canonical}
      style={{ height, width: 'auto', maxWidth: 54 }}
      className="inline-block object-contain align-middle"
      onError={() => {
        // Пробуємо наступного кандидата; якщо вичерпали — fallback на бейдж
        if (candIdx < candidates.length - 1) {
          setCandIdx(i => i + 1);
        } else {
          setLogoFailed(true);
        }
      }}
    />
  );
};

export default DeliveryBadge;
