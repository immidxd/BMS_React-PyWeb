/**
 * Спільні UI-хелпери для таблиць:
 *   • UnknownIf — рендер "Невідомо" italic-gray при '???' / порожньому значенні
 *   • CopyOnClick — клікабельне значення з copy-icon на hover і tooltip
 *   • OrderBadge — стандартизована мітка для статусу/оплати замовлення
 */
import React, { useState } from 'react';

// ── "Невідомо" placeholder ─────────────────────────────────────────────────
const UNKNOWN_MARKERS = new Set(['???', '#???', '?', '']);

export const isUnknownValue = (v: any): boolean => {
    if (v === null || v === undefined) return true;
    const s = String(v).trim();
    return UNKNOWN_MARKERS.has(s);
};

export const UnknownIf: React.FC<{ value: any; label?: string; className?: string }> = ({
    value, label = 'Невідомо', className = '',
}) => {
    if (isUnknownValue(value)) {
        return <span className={`text-gray-400 dark:text-gray-500 italic ${className}`}>{label}</span>;
    }
    return <span className={className}>{value}</span>;
};

// ── Стандартизація назв брендів (display-only) ──────────────────────────────
// Кожне слово: першу літеру у верхній регістр, АЛЕ зберігаємо:
//   • абревіатури з великих літер цілком: "UGG", "OLX"
//   • абревіатури з крапками: "U.S.", "A.B.C."
//   • внутрішні великі літери (камелкейс/італ.): "McQueen", "iPhone", "d'Oro"
// Решта символів слова не чіпаємо (щоб не ламати "Naturläufer", діакритику).
const _LETTER_RE = /[A-Za-zА-Яа-яҐЄІЇґєіїß]/;
export const formatBrandName = (name?: string | null): string => {
    if (!name) return name || '';
    const s = String(name).trim();
    if (!s) return s;
    return s.split(/(\s+)/).map(token => {
        if (/^\s+$/.test(token) || !token) return token; // пробіли зберігаємо як є
        const letters = token.replace(/[^A-Za-zА-Яа-яҐЄІЇґєіїß]/g, '');
        // Крапкова абревіатура (одна літера + крапка, повторювані): u.s. / a.b.c. → всі великі
        if (/^([A-Za-zА-Яа-яҐЄІЇґєії]\.){2,}$/.test(token)) return token.toUpperCase();
        // Абревіатура: всі літери великі (UGG, U.S.) → не чіпаємо
        if (letters && letters === letters.toUpperCase()) return token;
        // Внутрішня велика літера (McQueen, iPhone, d'Oro) → не чіпаємо
        if (/[A-ZА-ЯҐЄІЇ]/.test(token.slice(1))) return token;
        // Інакше — піднімаємо першу літеру (першу буквену позицію)
        const idx = token.search(_LETTER_RE);
        if (idx === -1) return token;
        return token.slice(0, idx) + token.charAt(idx).toUpperCase() + token.slice(idx + 1);
    }).join('');
};

// Рендер бренду: спершу "Невідомо"-guard, потім стандартизація регістру.
export const BrandName: React.FC<{ value?: string | null; className?: string }> = ({ value, className = '' }) => {
    if (isUnknownValue(value)) {
        return <span className={`text-gray-400 dark:text-gray-500 italic ${className}`}>Невідомо</span>;
    }
    return <span className={className}>{formatBrandName(value)}</span>;
};

// ── Copyable cell з полірованим UX ──────────────────────────────────────────
// При наведенні: показується іконка-clipboard поряд + tooltip "Скопіювати"
// нижче. Клік копіює значення в clipboard й показує "Скопійовано ✓".
//
// `groupDigits` форматує довге число (трекінг 14+ цифр) групами по 4
// для зручного читання: '20451449317719' → '2045 1449 3177 19'.
type CopyOnClickProps = {
    value: string | number;
    display?: React.ReactNode;
    title?: string;
    className?: string;
    groupDigits?: boolean;
};

const groupDigitsBy4 = (s: string): string =>
    /^\d+$/.test(s) && s.length > 8 ? s.replace(/(\d{4})(?=\d)/g, '$1 ') : s;

export const CopyOnClick: React.FC<CopyOnClickProps> = ({
    value, display, title, className = '', groupDigits = false,
}) => {
    const [copied, setCopied] = useState(false);
    const handleCopy = async (e: React.MouseEvent | React.KeyboardEvent) => {
        e.stopPropagation();
        try {
            await navigator.clipboard.writeText(String(value));
        } catch {
            // Fallback для не-secure context
            const ta = document.createElement('textarea');
            ta.value = String(value);
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); } catch {}
            document.body.removeChild(ta);
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
    };
    const shown = display ?? (groupDigits ? groupDigitsBy4(String(value)) : String(value));
    return (
        <span
            role="button"
            tabIndex={0}
            onClick={handleCopy}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleCopy(e); } }}
            title={title}
            className={`group relative inline-flex items-center cursor-pointer
                px-1 py-0.5 rounded transition-colors
                outline-none focus:outline-none focus-visible:ring-1 focus-visible:ring-blue-300
                hover:bg-blue-50/70 dark:hover:bg-blue-900/30
                ${className}`}
        >
            <span className="select-none">{shown}</span>
            {/* Іконка clipboard. ВАЖЛИВО: absolute (поза потоком) — щоб поява на
                hover НЕ зсувала текст/таблицю. left-full = одразу праворуч від
                значення, у вільному просторі клітинки. На клік стає галочкою. */}
            <span
                aria-hidden
                className={`absolute left-full ml-1 top-1/2 -translate-y-1/2
                    inline-flex items-center justify-center w-3.5 h-3.5 rounded transition-opacity
                    ${copied
                        ? 'opacity-100 text-green-600 dark:text-green-400'
                        : 'opacity-0 group-hover:opacity-100 text-gray-500 dark:text-gray-300'}`}
            >
                {copied ? (
                    <svg viewBox="0 0 16 16" fill="none" className="w-3 h-3">
                        <path d="M3 8.5L6.5 12L13 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                ) : (
                    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                        <path d="M10.5 1.5h-5a1 1 0 00-1 1V3h-1a1 1 0 00-1 1v9.5a1 1 0 001 1h7a1 1 0 001-1V13h1a1 1 0 001-1V4.5L10.5 1.5zM11.5 13h-7V4h1v8a1 1 0 001 1h5v.001zm1-1.999h-6V2.5h4V5h2v6.001z"/>
                    </svg>
                )}
            </span>
            {/* Tooltip під значенням (тільки на hover) */}
            <span
                role="tooltip"
                className="pointer-events-none absolute left-1/2 -translate-x-1/2 top-full mt-1
                    px-2 py-0.5 rounded text-[10px] font-medium whitespace-nowrap
                    bg-gray-800 text-white shadow-md z-50
                    opacity-0 group-hover:opacity-100 transition-opacity duration-150"
            >
                {copied ? 'Скопійовано ✓' : 'Скопіювати'}
            </span>
        </span>
    );
};

// ── Standardized order status / payment badges ─────────────────────────────
type BadgeStyle = { bg: string; border: string; text: string };

const ORDER_STATUS_STYLES: Record<string, BadgeStyle> = {
    'Підтверджено':  { bg: 'bg-green-50',  border: 'border-green-300',  text: 'text-green-700' },
    'Відміна':       { bg: 'bg-red-50',    border: 'border-red-300',    text: 'text-red-700' },
    'Скасовано':     { bg: 'bg-red-50',    border: 'border-red-300',    text: 'text-red-700' },
    'В черзі':       { bg: 'bg-orange-50', border: 'border-orange-300', text: 'text-orange-700' },
    'Подарунок':     { bg: 'bg-purple-50', border: 'border-purple-300', text: 'text-purple-700' },
    'Ігнорування':   { bg: 'bg-red-100',   border: 'border-red-800',    text: 'text-red-900' },
    'Ігнор':         { bg: 'bg-red-100',   border: 'border-red-800',    text: 'text-red-900' },
    'ФОТО':          { bg: 'bg-sky-100',   border: 'border-sky-300',    text: 'text-sky-700' },
    'Фото':          { bg: 'bg-sky-100',   border: 'border-sky-300',    text: 'text-sky-700' },
    'Уточнити':      { bg: 'bg-sky-100',   border: 'border-sky-300',    text: 'text-sky-700' },
    'Повернення':    { bg: 'bg-[#e7d3bd]', border: 'border-[#9c6b3f]',  text: 'text-[#6b4423]' },
    'Очікується':    { bg: 'bg-gray-100',  border: 'border-gray-400',   text: 'text-gray-700' },
};

const PAYMENT_STATUS_STYLES: Record<string, BadgeStyle> = {
    'Оплачено':     { bg: 'bg-green-50', border: 'border-green-300', text: 'text-green-700' },
    'Не оплачено':  { bg: 'bg-red-50',   border: 'border-red-300',   text: 'text-red-700' },
    'Передоплата':  { bg: 'bg-blue-50',  border: 'border-blue-300',  text: 'text-blue-700' },
    'Часткова':     { bg: 'bg-orange-50',border: 'border-orange-300',text: 'text-orange-700' },
    'Повернуто':    { bg: 'bg-amber-50', border: 'border-amber-700', text: 'text-amber-800' },
};

const NEUTRAL_STYLE: BadgeStyle = { bg: 'bg-gray-100', border: 'border-gray-300', text: 'text-gray-600' };

export const OrderStatusBadge: React.FC<{ name?: string | null }> = ({ name }) => {
    const label = (name && name.trim()) || 'Невідомо';
    const style = ORDER_STATUS_STYLES[label] || NEUTRAL_STYLE;
    return (
        <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium whitespace-nowrap ${style.bg} ${style.border} ${style.text}`}>
            {label}
        </span>
    );
};

export const PaymentStatusBadge: React.FC<{ name?: string | null }> = ({ name }) => {
    if (!name || !name.trim()) {
        return <span className="text-gray-300">—</span>;
    }
    const label = name.trim();
    const style = PAYMENT_STATUS_STYLES[label] || NEUTRAL_STYLE;
    return (
        <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium whitespace-nowrap ${style.bg} ${style.border} ${style.text}`}>
            {label}
        </span>
    );
};

// ── Статус товару «Продано/Непродано» — ЄДИНЕ джерело для всього UI ──────────
//
// Чому окремий хелпер: `products.statusid` (поле `status_name`) — це заморожений
// знімок, який пише лише `sync_product_statuses` під час парсингу. Між парсингами
// журнал замовлень змінюється (Підтверджено→Обмін/Відміна), а знімок «висить» →
// товар показується «Продано», хоча фактично в наявності. Тому продаж рахуємо
// НАЖИВО з `sold_count`/`quantity` (вони обчислюються з реальних замовлень при
// кожному запиті), а знімку довіряємо лише там, де живих даних немає.
//
// Правило:
//   1. sold_count >= quantity (є реальні продажі) → «Продано» (або «Подаровано»).
//   2. Знімок=Продано/Подаровано, АЛЕ є замовлення і всі вони не-продажні
//      (order_count>0, sold<qty) → знімок застарілий → «Непродано».
//   3. Знімок=Продано/Подаровано БЕЗ жодного замовлення → довіряємо знімку
//      (легітимний неформальний продаж без формального ордера).
//   4. «Повернуто»/«Пошкоджений» — журнальні стани, не виводяться з sold_count →
//      довіряємо знімку.
//   5. Решта → «Непродано».
export interface ProductStatusInput {
    sold_count?: number | null;
    quantity?: number | null;
    status_name?: string | null;
    order_count?: number | null;
}

// color — назва кольору antd <Tag>
export function getProductDisplayStatus(p: ProductStatusInput): { text: string; color: string } {
    const sold = p.sold_count ?? 0;
    const qty = p.quantity ?? 0;
    const orders = p.order_count ?? 0;
    const staticStatus = (p.status_name || '').trim();

    // 1) Фактично спожитий реальними замовленнями.
    if (sold > 0 && qty > 0 && sold >= qty) {
        return staticStatus === 'Подаровано'
            ? { text: 'Подаровано', color: 'purple' }
            : { text: 'Продано', color: 'red' };
    }

    // 2) Застарілий знімок, який спростовують реальні замовлення.
    const staleSold =
        (staticStatus === 'Продано' || staticStatus === 'Подаровано') &&
        orders > 0 && sold < qty;

    // 3-4) Журнальні фінальні стани (коли знімок НЕ застарілий).
    if (!staleSold) {
        if (staticStatus === 'Подаровано') return { text: 'Подаровано', color: 'purple' };
        if (staticStatus === 'Продано')    return { text: 'Продано', color: 'red' };
        if (staticStatus === 'Повернуто')  return { text: 'Повернуто', color: 'orange' };
        if (staticStatus === 'Пошкоджений') return { text: 'Пошкоджений', color: 'volcano' };
    }

    // 5) Default — непродано.
    return { text: 'Непродано', color: 'green' };
}

// ── Колір чипа «Стан» товару за значенням ───────────────────────────────────
// Новий → зелений, Хороший → синій, Легковживаний → жовтий,
// Вживаний → помаранчевий, Пошкоджений → червоний. Невідоме → сірий.
// color — назва кольору antd <Tag>.
export function getConditionColor(condition?: string | null): string {
    const c = (condition || '').trim().toLowerCase();
    switch (c) {
        case 'новий':         return 'green';
        case 'хороший':       return 'blue';
        case 'легковживаний': return 'gold';   // жовтий
        case 'вживаний':      return 'orange';
        case 'пошкоджений':   return 'red';
        default:              return 'default';
    }
}
