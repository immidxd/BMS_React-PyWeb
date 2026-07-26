import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Product } from '../../types/product';
import { productService } from '../../services/productService';
import {
    effectiveProductNumber,
    getProductDisplayStatus,
    formatBrandName,
} from '../common/displayHelpers';

// ── Швидкий перегляд картки товару при наведенні на рядок ────────────────────
// Лаконічна плаваюча картка: фото + найважливіше (номер, бренд/модель,
// тип·підтип, розмір/СМ/габарити, колір/стать/сезон/стан, ціна, статус).
// Мета — гортати товари не відкриваючи повну картку. Рендериться в портал
// (поверх таблиці), позиціонується біля курсора з утриманням у межах екрана.

// Кеш фото по id товару (щоб при повторному наведенні не смикати мережу).
// null = вже завантажували й фото немає.
// TTL обов'язковий: фото з'являються й поза застосунком (парсер кладе файли в
// папку), а вкладка живе годинами — без нього прев'ю показувало б старе фото
// доти, доки вкладку не перезавантажать.
const PHOTO_CACHE_TTL_MS = 5 * 60_000;
const _imgCache = new Map<number, { url: string | null; at: number }>();

function cachedUrl(id: number): string | null | undefined {
    const hit = _imgCache.get(id);
    if (!hit) return undefined;
    if (Date.now() - hit.at > PHOTO_CACHE_TTL_MS) { _imgCache.delete(id); return undefined; }
    return hit.url;
}

// Додали/видалили/перенесли фото в картці → кеш прев'ю для цього товару застарів.
window.addEventListener('bms:product-photos-changed', (e) => {
    const id = (e as CustomEvent<{ productId: number }>).detail?.productId;
    if (typeof id === 'number') _imgCache.delete(id);
});

/**
 * Фото для швидкого перегляду: спершу ОФІЦІЙНЕ (студійне) — воно репрезентативніше
 * за реальне з рук; якщо офіційних нема — перше реальне; дефектні — в останню чергу.
 * Бекенд і так віддає список у цьому порядку, але вибір робимо явно, щоб прев'ю
 * не залежало від порядку сортування на сервері.
 */
function pickPreviewImage(images: { url: string; kind?: string; is_defect?: boolean }[]): string | null {
    if (!images || images.length === 0) return null;
    const byKind = (k: string) => images.find((i) => !i.is_defect && (i.kind ?? 'official') === k);
    const pick = byKind('official') || byKind('real') || images.find((i) => !i.is_defect) || images[0];
    return pick?.url ?? null;
}

const STATUS_PILL: Record<string, string> = {
    red:     'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
    green:   'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
    orange:  'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
    purple:  'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
    volcano: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
};

const CARD_W = 264;   // ширина картки (px) — потрібна для позиціонування

function Chip({ label, value }: { label?: string; value: React.ReactNode }) {
    return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700/60 text-[11px] text-gray-700 dark:text-gray-200 whitespace-nowrap">
            {label && <span className="text-[9px] uppercase tracking-wide text-gray-400 dark:text-gray-500">{label}</span>}
            <span className="font-medium">{value}</span>
        </span>
    );
}

export default function ProductHoverPreview({ record, x, y }: { record: Product; x: number; y: number }) {
    const cardRef = useRef<HTMLDivElement>(null);
    const [imgUrl, setImgUrl] = useState<string | null | undefined>(() => cachedUrl(record.id));
    const [pos, setPos] = useState<{ left: number; top: number }>({ left: x + 20, top: y });

    // Фото: офіційне (студійне) в пріоритеті, інакше реальне — див. pickPreviewImage.
    useEffect(() => {
        let cancelled = false;
        const cached = cachedUrl(record.id);
        if (cached !== undefined) { setImgUrl(cached); return; }
        if (!(record as any).has_photo) { _imgCache.set(record.id, { url: null, at: Date.now() }); setImgUrl(null); return; }
        setImgUrl(undefined);   // спінер, поки тягнемо (кеш міг протухнути)
        productService.getProductImages(record.id).then(res => {
            const url = pickPreviewImage(res.images || []);
            _imgCache.set(record.id, { url, at: Date.now() });
            if (!cancelled) setImgUrl(url);
        }).catch(() => { if (!cancelled) setImgUrl(null); });
        return () => { cancelled = true; };
    }, [record.id]);

    // Позиціонування: праворуч від курсора; якщо не влазить — ліворуч; по вертикалі
    // тримаємо в межах вікна. Міряємо реальну висоту картки після рендера.
    useLayoutEffect(() => {
        const margin = 10;
        const cardH = cardRef.current?.offsetHeight ?? 320;
        let left = x + 20;
        if (left + CARD_W + margin > window.innerWidth) left = x - CARD_W - 20;
        if (left < margin) left = margin;
        let top = y - 40;
        if (top + cardH + margin > window.innerHeight) top = window.innerHeight - cardH - margin;
        if (top < margin) top = margin;
        setPos({ left, top });
    }, [x, y, imgUrl]);

    const numLabel = effectiveProductNumber(
        record.productnumber, (record as any).clonednumbers, (record as any).display_number
    ).value || `ID ${record.id}`;
    const status = getProductDisplayStatus(record as any);
    const pill = STATUS_PILL[status.color] || STATUS_PILL.green;

    const sizeChips: React.ReactNode[] = [];
    if (record.sizeeu) sizeChips.push(<Chip key="eu" label="EU" value={record.sizeeu} />);
    if (record.size_letter) sizeChips.push(<Chip key="lt" value={record.size_letter} />);
    if (record.measurementscm) sizeChips.push(<Chip key="cm" label="СМ" value={record.measurementscm} />);
    if (record.dimensions) sizeChips.push(<Chip key="dim" label="Габ" value={record.dimensions} />);

    const metaChips: React.ReactNode[] = [];
    if (record.color_name) metaChips.push(<Chip key="col" value={record.color_name} />);
    if (record.gender_name) metaChips.push(<Chip key="gen" value={record.gender_name} />);
    if (record.season) metaChips.push(<Chip key="sea" value={record.season} />);
    const cond = record.current_condition_name || record.condition_name;
    if (cond) metaChips.push(<Chip key="cnd" value={cond} />);

    return createPortal(
        <div
            ref={cardRef}
            style={{ left: pos.left, top: pos.top, width: CARD_W }}
            className="fixed z-[10050] pointer-events-none rounded-xl overflow-hidden bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-2xl"
        >
            {/* Фото — квадратне (усі фото квадратні), заповнює всю ширину картки
                без порожніх полів по боках. object-cover: край-у-край. */}
            <div className="w-full aspect-square bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
                {imgUrl === undefined ? (
                    <div className="w-6 h-6 border-2 border-gray-300 border-t-transparent rounded-full animate-spin" />
                ) : imgUrl ? (
                    <img src={imgUrl} alt={numLabel} className="w-full h-full object-cover" />
                ) : (
                    <div className="flex flex-col items-center gap-1 text-gray-300 dark:text-gray-600">
                        <svg className="w-9 h-9" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M18 12h.008M2.25 6.75a1.5 1.5 0 011.5-1.5h16.5a1.5 1.5 0 011.5 1.5v10.5a1.5 1.5 0 01-1.5 1.5H3.75a1.5 1.5 0 01-1.5-1.5V6.75z" />
                        </svg>
                        <span className="text-[10px]">Без фото</span>
                    </div>
                )}
            </div>

            {/* Інфо */}
            <div className="p-2.5 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-sm text-gray-900 dark:text-gray-100 truncate">{numLabel}</span>
                    <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold ${pill}`}>{status.text}</span>
                </div>

                <div className="text-[13px] leading-tight">
                    <span className="font-semibold text-gray-800 dark:text-gray-100">{formatBrandName(record.brand_name) || '—'}</span>
                    {record.model && <span className="text-gray-500 dark:text-gray-400"> · {record.model}</span>}
                </div>

                {(record.type_name || record.subtype_name) && (
                    <div className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                        {record.type_name}{record.subtype_name ? ` · ${record.subtype_name}` : ''}
                    </div>
                )}

                {sizeChips.length > 0 && <div className="flex flex-wrap gap-1">{sizeChips}</div>}
                {metaChips.length > 0 && <div className="flex flex-wrap gap-1">{metaChips}</div>}

                {(record.price || record.oldprice) && (
                    <div className="flex items-baseline gap-2 pt-0.5">
                        {record.price ? (
                            <span className="text-base font-bold text-gray-900 dark:text-gray-100">{Number(record.price).toFixed(0)}₴</span>
                        ) : null}
                        {record.oldprice ? (
                            <span className="text-xs text-gray-400 line-through">{Number(record.oldprice).toFixed(0)}₴</span>
                        ) : null}
                    </div>
                )}
            </div>
        </div>,
        document.body
    );
}
