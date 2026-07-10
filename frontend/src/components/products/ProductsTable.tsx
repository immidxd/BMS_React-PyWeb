import React, { useState, useRef, useEffect } from 'react';
import styled from 'styled-components';
import { 
    Table, 
    Button, 
    Space, 
    Tag, 
    Popconfirm, 
    message, 
    Tooltip,
    Switch,
    Row,
    Col
} from 'antd';
import { 
    EditOutlined, 
    DeleteOutlined, 
    PlusOutlined, 
    EyeOutlined, 
    EyeInvisibleOutlined 
} from '@ant-design/icons';
import { Product } from '../../types/product';
import type { TableProps } from 'antd';
import ProductDetailsModal from './ProductDetailsModal';
import MergeCandidatesModal from './MergeCandidatesModal';
import { LinkOutlined, LockFilled, PictureOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { productService } from '../../services/productService';
import { CopyOnClick, UnknownIf, isUnknownValue, BrandName, getProductDisplayStatus } from '../common/displayHelpers';
import { notify } from '../../ui/feedback';
// Pagination is rendered at page level

// Column configuration type
interface ColumnConfig {
  id: string;
  title: string;
  visible: boolean;
  optional: boolean;
  width?: string;
}

const TableContainer = styled.div`
    margin-top: 16px;
    position: relative;
`;

// Логотип Telegram (paper-plane) — інлайн SVG, фарбується currentColor.
const TelegramGlyph: React.FC<{ size?: number }> = ({ size = 14 }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden focusable="false">
        <path d="M9.04 15.47 8.7 20.2c.46 0 .66-.2.9-.43l2.16-2.06 4.48 3.28c.82.45 1.4.22 1.62-.76l2.94-13.8c.27-1.22-.44-1.7-1.24-1.4L2.2 9.9c-1.2.46-1.18 1.13-.2 1.43l4.4 1.37 10.2-6.43c.48-.3.92-.13.56.17z"/>
    </svg>
);

// Маркер OLX — власний лого (перефарбований у темно-смарагдовий
// `/media-logos/olx-mark-emerald.png`). Якщо картинка не завантажиться —
// запасний текстовий піл «OLX», щоб рядок ніколи не «ламався».
const OlxGlyph: React.FC<{ size?: number }> = ({ size = 13 }) => {
    const [failed, setFailed] = React.useState(false);
    if (failed) {
        return (
            <span style={{
                fontSize: size - 4, fontWeight: 800, lineHeight: 1, color: '#fff',
                background: '#064E3B', borderRadius: 3, padding: '1.5px 2.5px',
                letterSpacing: '0.2px', display: 'inline-flex', alignItems: 'center',
            }}>OLX</span>
        );
    }
    return (
        <img src="/media-logos/olx-mark-emerald.png" alt="OLX"
            style={{ height: size, width: 'auto', display: 'block' }}
            onError={() => setFailed(true)} />
    );
};

// Соц-маркери рядка (інлайн, на одній лінії): Telegram + OLX. Пиняться
// absolute-left у комірці «Номер» → висоту рядка/ширину колонки не чіпають.
// Рендеримо лише наявні; якщо жодного — нічого.
// Маркер Prom — власне лого. Текстовий фолбек, якщо картинка не завантажиться.
const PromGlyph: React.FC<{ size?: number }> = ({ size = 13 }) => {
    const [failed, setFailed] = React.useState(false);
    if (failed) {
        return (
            <span style={{
                fontSize: size - 4, fontWeight: 800, lineHeight: 1, color: '#fff',
                background: '#5B2D8E', borderRadius: 3, padding: '1.5px 2.5px',
                display: 'inline-flex', alignItems: 'center',
            }}>P</span>
        );
    }
    return (
        <img src="/media-logos/prom-logo.png" alt="Prom"
            style={{ height: size, width: 'auto', display: 'block', borderRadius: 2 }}
            onError={() => setFailed(true)} />
    );
};

const RowIndicators: React.FC<{ publishedTg?: boolean; publishedOlx?: boolean; publishedProm?: boolean }> = ({ publishedTg, publishedOlx, publishedProm }) => {
    if (!publishedTg && !publishedOlx && !publishedProm) return null;
    return (
        <span className="inline-flex items-center gap-1 leading-none select-none shrink-0">
            {publishedTg && (
                <Tooltip title="Опубліковано в Telegram">
                    <span style={{ color: '#229ED9', display: 'inline-flex' }}><TelegramGlyph size={11} /></span>
                </Tooltip>
            )}
            {publishedOlx && (
                <Tooltip title="Опубліковано на OLX">
                    <span style={{ display: 'inline-flex' }}><OlxGlyph size={11} /></span>
                </Tooltip>
            )}
            {publishedProm && (
                <Tooltip title="Опубліковано на Prom">
                    <span style={{ display: 'inline-flex' }}><PromGlyph size={11} /></span>
                </Tooltip>
            )}
        </span>
    );
};

const TableActions = styled(Row)`
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
`;

const PriceText = styled.span`
    font-weight: 700;
    color: var(--bms-fg, #111);
`;

const OldPriceText = styled.span`
    text-decoration: line-through;
    color: var(--bms-fg-disabled, #999);
    margin-left: 8px;
    font-size: 0.85em;
`;

interface ProductsTableProps {
  products: { items: Product[], total: number, page: number, per_page: number };
  loading: boolean;
  onDelete: (id: number) => Promise<void>;
  onPageChange: (page: number, pageSize?: number) => void;
  onVisibilityChange: (id: number, isVisible: boolean) => Promise<void>;
  onSortChange?: (sortBy: string, sortDir: 'asc' | 'desc') => void;
  selectedRowKeys: React.Key[];
  onSelectedRowKeysChange: (keys: React.Key[]) => void;
}

const ProductsTable: React.FC<ProductsTableProps> = ({ 
    products, 
    loading, 
    onDelete, 
    onPageChange, 
    onVisibilityChange,
    onSortChange,
    selectedRowKeys,
    onSelectedRowKeysChange,
}) => {
    const navigate = useNavigate();
    const [visibilityLoading, setVisibilityLoading] = useState<Record<number, boolean>>({});
    const [detailsId, setDetailsId] = useState<number | null>(null);
    const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
    // Відкладене відкриття картки після крос-сторінкового переходу:
    // 'first' → відкрити першу картку нової сторінки, 'last' → останню.
    const pendingNavRef = useRef<null | 'first' | 'last'>(null);
    // Після того як батько підвантажив нову сторінку (products.items змінився) —
    // відкриваємо відповідну картку. Спрацьовує лише після крос-сторінкової навігації.
    useEffect(() => {
        if (!pendingNavRef.current) return;
        const items = products.items || [];
        if (items.length === 0) return;
        const target = pendingNavRef.current === 'first' ? items[0] : items[items.length - 1];
        pendingNavRef.current = null;
        if (target) setDetailsId(target.id);
    }, [products.items]);
    const [mergeId, setMergeId] = useState<number | null>(null);
    const [mergeOpen, setMergeOpen] = useState<boolean>(false);
    // Контекстне меню керування колонками (тільки на шапці таблиці)
    const storageKey = 'products_table_columns_v4';
    const menuRef = useRef<HTMLDivElement | null>(null);
    const [menuOpen, setMenuOpen] = useState(false);
    const [menuPos, setMenuPos] = useState<{x:number;y:number}>({x:0,y:0});
    // Контекстне меню дій над товаром (на рядку таблиці)
    const rowMenuRef = useRef<HTMLDivElement | null>(null);
    const [rowMenuOpen, setRowMenuOpen] = useState(false);
    const [rowMenuPos, setRowMenuPos] = useState<{x:number;y:number}>({x:0,y:0});
    const [rowMenuRecord, setRowMenuRecord] = useState<Product | null>(null);
    const columnOrder: { id: string; title: string; optional: boolean }[] = [
        // 0.1 (опціонально перед №1)
        { id: 'id', title: 'ID', optional: true },
        // 1
        { id: 'productnumber', title: 'Номер', optional: false },
        // 1.1
        { id: 'clonednumbers', title: 'Номера-клони', optional: true },
        // 2
        { id: 'type_name', title: 'Тип', optional: false },
        // 2.1
        { id: 'subtype_name', title: 'Підтип', optional: true },
        { id: 'style_name', title: 'Стиль', optional: true },
        // 3
        { id: 'brand_name', title: 'Бренд', optional: false },
        // 3.1, 3.2, 3.3 (+ Колекція/GTIN — порядок як у журналі)
        { id: 'model', title: 'Модель', optional: true },
        { id: 'collection', title: 'Колекція', optional: true },
        { id: 'marking', title: 'Маркування', optional: true },
        { id: 'gtin', title: 'GTIN', optional: true },
        { id: 'year', title: 'Рік', optional: true },
        // 4
        { id: 'gender_name', title: 'Стать', optional: false },
        { id: 'season', title: 'Сезон', optional: false },
        // 5
        { id: 'color_name', title: 'Колір', optional: false },
        // 5.1
        { id: 'description', title: 'Опис', optional: true },
        // 6
        { id: 'sizeeu', title: 'Розмір', optional: false },
        { id: 'width', title: 'Ширина', optional: true },
        { id: 'dimensions', title: 'Габарити', optional: true },
        { id: 'geometric_shape', title: 'Геом. форма', optional: true },
        // 7
        { id: 'measurementscm', title: 'СМ', optional: false },
        { id: 'sole_type_name', title: 'Тип підошви', optional: true },
        { id: 'toe_shape_name', title: 'Форма носка', optional: true },
        { id: 'fastening_type_name', title: 'Застібка', optional: true },
        { id: 'lining_name', title: 'Підкладка', optional: true },
        { id: 'materials_summary', title: 'Матеріали', optional: true },
        // 8
        { id: 'price', title: 'Ціна', optional: false },
        // 8.1, 8.2
        { id: 'oldprice', title: 'Стара ціна', optional: true },
        { id: 'quantity', title: 'К-сть (заг.)', optional: true },
        { id: 'available_qty', title: 'В наявності', optional: true },
        // 9
        { id: 'status_name', title: 'Статус', optional: false },
        // 10 — основна колонка "Стан" відображає current_conditionid (актуальний стан).
        // Оригінальний conditionid схований за замовчуванням під назвою "Початковий стан".
        { id: 'current_condition_name', title: 'Стан', optional: false },
        { id: 'condition_name', title: 'Початковий стан', optional: true },
        // 11, 12, 13
        { id: 'supplier_name', title: 'Постачальник', optional: true },
        { id: 'is_visible', title: 'Видимість', optional: true },
        { id: 'actions', title: 'Дії', optional: true },
    ];
    const defaultVisibility: Record<string, boolean> = columnOrder.reduce((acc, c) => {
        acc[c.id] = !c.optional; return acc;
    }, {} as Record<string, boolean>);
    const [visibleMap, setVisibleMap] = useState<Record<string, boolean>>(() => {
        try {
            const raw = localStorage.getItem(storageKey);
            if (!raw) return defaultVisibility;
            const parsed = JSON.parse(raw);
            return { ...defaultVisibility, ...parsed };
        } catch {
            return defaultVisibility;
        }
    });
    useEffect(() => { localStorage.setItem(storageKey, JSON.stringify(visibleMap)); }, [visibleMap]);
    useEffect(() => {
        const onDocClick = (e: MouseEvent) => {
            if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
            else if (!menuRef.current) setMenuOpen(false);
            if (rowMenuRef.current && !rowMenuRef.current.contains(e.target as Node)) setRowMenuOpen(false);
            else if (!rowMenuRef.current) setRowMenuOpen(false);
        };
        document.addEventListener('mousedown', onDocClick);
        return () => document.removeEventListener('mousedown', onDocClick);
    }, []);
    // ПКМ на шапці таблиці → меню видимості колонок
    const handleHeaderContextMenu: React.MouseEventHandler<HTMLElement> = (e) => {
        e.preventDefault();
        setRowMenuOpen(false);
        setMenuPos({ x: e.clientX, y: e.clientY });
        setMenuOpen(true);
    };
    // ПКМ на рядку товару → меню дій над товаром
    const handleRowContextMenu = (e: React.MouseEvent, record: Product) => {
        e.preventDefault();
        setMenuOpen(false);
        setRowMenuRecord(record);
        setRowMenuPos({ x: e.clientX, y: e.clientY });
        setRowMenuOpen(true);
    };
    // "Показати в замовленнях" — кладемо фільтр і перемикаємо вкладку
    const handleShowInOrders = (record: Product) => {
        const sold = record.sold_count ?? 0;
        // Заброньовані товари ТЕЖ мають замовлення (Підтверджено без оплати), тож
        // дозволяємо перехід і для них, а не лише для проданих.
        const isReserved = !!(record as any).is_reserved || ((record as any).reserved_count ?? 0) > 0;
        if (sold <= 0 && !isReserved) {
            notify.info({ message: 'Товар не продано і не заброньовано — немає замовлень для показу' });
            return;
        }
        const label = (record.productnumber || '').replace(/^#/, '') || `ID ${record.id}`;
        localStorage.setItem('bms_orders_pending_filter', JSON.stringify({
            product_id: record.id,
            product_label: label,
        }));
        window.dispatchEvent(new CustomEvent('bms:switch-to-orders'));
        setRowMenuOpen(false);
    };
    
    // Обробник зміни видимості товару
    const handleVisibilityChange = async (id: number, isVisible: boolean) => {
        setVisibilityLoading(prev => ({ ...prev, [id]: true }));
        try {
            await onVisibilityChange(id, isVisible);
            notify.success({ message: `Видимість товару ${isVisible ? 'включена' : 'виключена'}` });
        } catch (error) {
            notify.error({ message: 'Помилка при зміні видимості товару' });
        } finally {
            setVisibilityLoading(prev => ({ ...prev, [id]: false }));
        }
    };
    
    // Обробник видалення товару
    const handleDelete = async (id: number) => {
        try {
            await onDelete(id);
            notify.success({ message: 'Товар успішно видалено' });
        } catch (error) {
            notify.error({ message: 'Помилка при видаленні товару' });
        }
    };
    
    // Click-to-Google: бренд + модель + маркування (зовнішній артикул бренду).
    // Внутрішній номер `Ф3713` — не справжній артикул, у Google такого нема, тому пропускаємо.
    // Маркування — це фабричний код виробника (напр. "IF6449"), якраз те що шукається.
    const buildGoogleUrl = (record: Product): string | null => {
        const r = record as any;
        const parts = [record.brand_name, r.model, r.marking].filter(Boolean) as string[];
        if (!parts.length) return null;
        const q = parts.join(' ').replace(/\s+/g, ' ').trim();
        if (!q) return null;
        return `https://www.google.com/search?q=${encodeURIComponent(q)}`;
    };
    const googleCellClass = "cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300";
    const googleTitle = "Пошук в Google: бренд + модель + артикул";

    // Опис усіх можливих колонок уніфіковано, з рендерами
    const allColumns: Record<string, any> = {
        id: {
            title: 'ID', dataIndex: 'id', key: 'id', width: 60, sorter: true,
            render: (id: number) => <CopyOnClick value={id} className="bms-mono text-xs" />,
        },
        productnumber: {
            title: 'Номер', dataIndex: 'productnumber', key: 'productnumber', width: 56,
            onCell: () => ({ className: 'bms-col-num' }),
            onHeaderCell: () => ({ className: 'bms-col-num' } as any),
            render: (text: string, record: Product) => {
                const label = (text || '').replace(/^#/, '');
                if (isUnknownValue(label)) {
                    return (
                        <div className="flex items-center justify-center gap-1.5">
                            <RowIndicators publishedTg={(record as any).published_tg} publishedOlx={(record as any).published_olx} publishedProm={(record as any).published_prom} />
                            <UnknownIf value={label} className="text-xs font-medium" />
                            <span className="invisible" aria-hidden="true">
                                <RowIndicators publishedTg={(record as any).published_tg} publishedOlx={(record as any).published_olx} publishedProm={(record as any).published_prom} />
                            </span>
                        </div>
                    );
                }
                const url = buildGoogleUrl(record);
                const numberCell = (
                    <CopyOnClick
                        value={label}
                        display={
                            url ? (
                                <a
                                    href={url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-xs font-medium cursor-pointer text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
                                    title={googleTitle}
                                    onClick={(e) => { e.stopPropagation(); }}
                                >{label}</a>
                            ) : (
                                <span className="text-xs font-medium text-gray-800 dark:text-gray-200">{label}</span>
                            )
                        }
                    />
                );
                return (
                    <div className="flex items-center justify-center gap-1.5">
                        <RowIndicators publishedTg={(record as any).published_tg} publishedOlx={(record as any).published_olx} publishedProm={(record as any).published_prom} />
                        {numberCell}
                        <span className="invisible" aria-hidden="true">
                            <RowIndicators publishedTg={(record as any).published_tg} publishedOlx={(record as any).published_olx} publishedProm={(record as any).published_prom} />
                        </span>
                    </div>
                );
            },
        },
        model: { title: 'Модель', dataIndex: 'model', key: 'model', width: 140,
            render: (text: string, record: Product) => {
                if (!text) return null;
                if (isUnknownValue(text)) return <UnknownIf value={text} />;
                const url = buildGoogleUrl(record);
                return url ? (
                    <a href={url} target="_blank" rel="noopener noreferrer"
                        className={googleCellClass} title={googleTitle}
                        onClick={(e) => e.stopPropagation()}
                    >{text}</a>
                ) : <span>{text}</span>;
            } },
        brand_name: { title: 'Бренд', dataIndex: 'brand_name', key: 'brand_name', width: 110,
            render: (v: string) => <BrandName value={v} /> },
        type_name: { title: 'Тип', dataIndex: 'type_name', key: 'type_name', width: 110,
            render: (v: string) => <UnknownIf value={v} /> },
        subtype_name: { title: 'Підтип', dataIndex: 'subtype_name', key: 'subtype_name', width: 120,
            render: (v: string) => <UnknownIf value={v} /> },
        gender_name: { title: 'Стать', dataIndex: 'gender_name', key: 'gender_name', width: 75,
            render: (v: string) => <UnknownIf value={v} /> },
        color_name: { title: 'Колір', dataIndex: 'color_name', key: 'color_name', width: 65,
            render: (v: string) => <UnknownIf value={v} /> },
        sizeeu: { title: 'Розмір', dataIndex: 'sizeeu', key: 'sizeeu', width: 70,
            render: (text: string, record: Product) => {
                const isRost = record.is_rostovka;
                const letter = (record as any).size_letter;
                // Колонка «Розмір»: за замовчуванням цифровий розмір (sizeeu). Якщо
                // цифри нема, але є буквений (валізи/одяг) — показуємо букву. Товарів
                // із цифрою це не змінює (буква їх не зачіпає). Лише відображення —
                // на пошук/фільтри/сортування не впливає (вони працюють по колонках БД).
                const display = text || letter || '';
                if (!display) return <span className="text-gray-300 text-xs">—</span>;
                return (
                    <span className={`text-xs ${isRost ? 'text-purple-700 font-medium' : ''}`}>
                        {display}
                        {isRost && record.quantity > 1 && (
                            <span className="text-purple-400 ml-0.5">×{record.quantity}</span>
                        )}
                    </span>
                );
            }},
        measurementscm: { title: 'СМ', dataIndex: 'measurementscm', key: 'measurementscm', width: 60,
            render: (text: string) => text ? <span className="text-xs">{text}</span> : <span className="text-gray-300 text-xs">—</span> },
        sole_type_name: { title: 'Тип підошви', dataIndex: 'sole_type_name', key: 'sole_type_name', width: 110,
            render: (text: string) => text ? <span className="text-xs">{text}</span> : null },
        toe_shape_name: { title: 'Форма носка', dataIndex: 'toe_shape_name', key: 'toe_shape_name', width: 110,
            render: (text: string) => text ? <span className="text-xs">{text}</span> : null },
        fastening_type_name: { title: 'Застібка', dataIndex: 'fastening_type_name', key: 'fastening_type_name', width: 90,
            render: (text: string) => text ? <span className="text-xs">{text}</span> : null },
        lining_name: { title: 'Підкладка', dataIndex: 'lining_name', key: 'lining_name', width: 90,
            render: (text: string) => text ? <span className="text-xs">{text}</span> : null },
        materials_summary: { title: 'Матеріали', key: 'materials_summary', width: 140,
            render: (_: any, record: Product) => {
                if (!record.materials || record.materials.length === 0) return null;
                const names = record.materials.map(m => m.materialname || '').filter(Boolean);
                return <span className="text-xs text-gray-600 dark:text-gray-400">{names.join(', ')}</span>;
            }},
        price: { title: 'Ціна', dataIndex: 'price', key: 'price', width: 70, sorter: true,
            render: (price: number, record: Product) => (
                <span className="text-xs">
                    {price !== undefined && price !== null && (
                        <CopyOnClick
                            value={Number(price).toFixed(0)}
                            display={<PriceText>{Number(price).toFixed(0)}₴</PriceText>}
                        />
                    )}
                </span>
            )},
        oldprice: { title: 'Стара ціна', dataIndex: 'oldprice', key: 'oldprice', width: 90,
            render: (value: number) => value ? <OldPriceText>{Number(value).toFixed(0)} ₴</OldPriceText> : null },
        quantity: { title: 'К-сть (заг.)', dataIndex: 'quantity', key: 'quantity', width: 65, align: 'center' as const, sorter: true,
            render: (q: number) => <Tag color={q > 0 ? 'blue' : 'red'}>{q}</Tag> },
        available_qty: { title: 'В наявності', key: 'available_qty', width: 80, align: 'center' as const,
            render: (_: any, record: Product) => {
                const total = record.quantity ?? 0;
                const avail = record.available_qty ?? total;
                const sold  = record.sold_count  ?? 0;
                if (total === 0) return <Tag color="red">0</Tag>;
                if (sold === 0)  return <Tag color="green">{total}</Tag>;
                if (avail <= 0) return <Tag color="red">0 / {total}</Tag>;
                return (
                    <Tooltip title={`Продано: ${sold} / ${total}`}>
                        <Tag color="orange">{avail} / {total}</Tag>
                    </Tooltip>
                );
            } },
        status_name: { title: 'Статус', key: 'status_name', width: 110,
            render: (_: any, record: Product) => {
                // Єдине джерело статусу: живий sold_count з фолбеком на знімок
                // лише там, де живих даних нема (див. getProductDisplayStatus).
                const { text: displayStatus, color } = getProductDisplayStatus(record);
                // «Бронь» (Підтверджено без оплати) — мінімалістичний чорний силует замка
                // ПОРЯД зі статусом (не під ним). Не показуємо для фінальних статусів.
                const showReserved = !!record.is_reserved && displayStatus === 'Непродано';
                // Grid 1fr/auto/1fr тримає чип СТРОГО по центру колонки незалежно від
                // наявності замка (інакше замок зсував чип → нерівність між рядками).
                return (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', columnGap: 4, width: '100%' }}>
                        <span />
                        <Tag
                            color={color}
                            style={{ display: 'block', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '0 6px', fontSize: 12, textAlign: 'center', margin: 0 }}
                        >
                            {displayStatus}
                        </Tag>
                        <span style={{ justifySelf: 'start', display: 'inline-flex', alignItems: 'center' }}>
                            {showReserved && (
                                <LockFilled
                                    title="Заброньовано: є Підтверджене замовлення без оплати"
                                    style={{ fontSize: 12, color: 'var(--bms-fg, #1f2937)' }}
                                />
                            )}
                        </span>
                    </div>
                );
            } },
        condition_name: { title: 'Початковий стан', dataIndex: 'condition_name', key: 'condition_name', width: 120 },
        current_condition_name: { title: 'Стан', dataIndex: 'current_condition_name', key: 'current_condition_name', width: 110,
            render: (text: string, record: Product) => (
                // Стан по центру + маркер «є фото» пришпилений до правого краю
                // комірки (absolute, з невеликим відступом) — це найправіша
                // колонка, тож іконка лягає в край таблиці й не заважає тексту.
                <div className="relative w-full flex items-center justify-center">
                    <span className="text-xs">{text}</span>
                    {(record as any).has_photo && (
                        <Tooltip title="Є фото">
                            <PictureOutlined className="absolute" style={{ right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 12, color: '#94a3b8' }} />
                        </Tooltip>
                    )}
                </div>
            ),
        },
        style_name: { title: 'Стиль', dataIndex: 'style_name', key: 'style_name', width: 110 },
        season: { title: 'Сезон', dataIndex: 'season', key: 'season', width: 150,
            render: (v: string) => {
                if (!v) return null;
                // CSV може містити кілька сезонів ('Демі, Єврозима'). Рендеримо кожен як окремий Tag.
                const seasonColor: Record<string, string> = {
                    'Зима':     'blue',
                    'Єврозима': 'cyan',
                    'Літо':     'gold',
                    'Демі':     'green',
                    'Всесезон': 'default',
                };
                const parts = v.split(',').map(s => s.trim()).filter(Boolean);
                return (
                    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 2 }}>
                        {parts.map((p, i) => (
                            <Tag
                                key={i}
                                color={seasonColor[p] || 'default'}
                                style={{ margin: 0, padding: '0 6px', fontSize: 12, textAlign: 'center' }}
                            >
                                {p}
                            </Tag>
                        ))}
                    </span>
                );
            } },
        width: { title: 'Ширина', dataIndex: 'width', key: 'width', width: 80,
            render: (v: string) => v ? <span className="text-xs">{v}</span> : null },
        dimensions: { title: 'Габарити', dataIndex: 'dimensions', key: 'dimensions', width: 100,
            render: (v: string) => v ? <span className="text-xs">{v}</span> : null },
        geometric_shape: { title: 'Геом. форма', dataIndex: 'geometric_shape', key: 'geometric_shape', width: 110,
            render: (v: string) => v ? <span className="text-xs">{v}</span> : null },
        collection: { title: 'Колекція', dataIndex: 'collection', key: 'collection', width: 130, ellipsis: true },
        gtin: { title: 'GTIN', dataIndex: 'gtin', key: 'gtin', width: 140,
            render: (v: string) => v ? <span className="text-xs tabular-nums">{v}</span> : null },
        clonednumbers: { title: 'Номера-клони', dataIndex: 'clonednumbers', key: 'clonednumbers', width: 160 },
        marking: { title: 'Маркування', dataIndex: 'marking', key: 'marking', width: 140,
            render: (text: string, record: Product) => {
                if (!text) return null;
                const url = buildGoogleUrl(record);
                return url ? (
                    <a href={url} target="_blank" rel="noopener noreferrer"
                        className={googleCellClass} title={googleTitle}
                        onClick={(e) => e.stopPropagation()}
                    >{text}</a>
                ) : <span>{text}</span>;
            } },
        year: { title: 'Рік', dataIndex: 'year', key: 'year', width: 50 },
        description: { title: 'Опис', dataIndex: 'description', key: 'description', width: 200, ellipsis: true },
        supplier_name: { title: 'Постачальник', dataIndex: 'supplier_name', key: 'supplier_name', width: 160 },
        is_visible: { title: 'Видимість', dataIndex: 'is_visible', key: 'is_visible', width: 85, align: 'center' as const,
            render: (isVisible: boolean, record: Product) => (
                <Switch
                    checked={isVisible}
                    onChange={(checked) => handleVisibilityChange(record.id, checked)}
                    loading={visibilityLoading[record.id]}
                    checkedChildren={<EyeOutlined />}
                    unCheckedChildren={<EyeInvisibleOutlined />}
                />
            ) },
        actions: { title: 'Дії', key: 'actions', width: 130, fixed: 'right' as const,
            render: (_: any, record: Product) => {
                const pendingCount = (record as any).pending_candidates_count || 0;
                return (
                    <Space>
                        {pendingCount > 0 && (
                            <Tooltip title={`Кандидатів на об'єднання: ${pendingCount}`}>
                                <Button
                                    icon={<LinkOutlined />}
                                    size="small"
                                    style={{ borderColor: '#fb923c', color: '#ea580c' }}
                                    onClick={(e) => { e.stopPropagation(); setMergeId(record.id); setMergeOpen(true); }}
                                >{pendingCount}</Button>
                            </Tooltip>
                        )}
                        <Tooltip title="Редагувати">
                            <Button type="primary" icon={<EditOutlined />} size="small" onClick={() => navigate(`/products/${record.id}/edit`)} />
                        </Tooltip>
                        <Tooltip title="Видалити">
                            <Popconfirm title="Ви впевнені, що хочете видалити цей товар?" onConfirm={() => handleDelete(record.id)} okText="Так" cancelText="Ні">
                                <Button danger icon={<DeleteOutlined />} size="small" />
                            </Popconfirm>
                        </Tooltip>
                    </Space>
                );
            } },
    };
    // Центрування всіх колонок за замовчуванням, окрім "Опис" (великий текст)
    // та колонок, де вже явно задано align (рідкісні випадки — не перетираємо).
    const NO_CENTER_KEYS = new Set(['description']);
    const columns = columnOrder
        .filter(c => visibleMap[c.id])
        .map(c => allColumns[c.id])
        .filter(Boolean)
        .map((col: any) => {
            if (col.align || NO_CENTER_KEYS.has(col.key)) return col;
            return { ...col, align: 'center' as const };
        });
    
    const handleTableChange: TableProps<Product>['onChange'] = (pagination, filters, sorter) => {
        // AntD can return sorter as object or array; handle both
        const s: any = Array.isArray(sorter) ? sorter[0] : sorter;
        if (s && s.field && s.order && onSortChange) {
            const sortBy = String(s.field);
            const sortDir = s.order === 'ascend' ? 'asc' : 'desc';
            onSortChange(sortBy, sortDir);
        }
    };

    // Тимчасово вимкнено колонку виділення рядків (чекбокси) — поки не
    // використовується. Поставити true, щоб повернути bulk-виділення
    // (масова видимість угорі працює лише коли є виділені рядки).
    const ENABLE_ROW_SELECTION = false;
    const rowSelection = {
        selectedRowKeys,
        onChange: (keys: React.Key[]) => onSelectedRowKeysChange(keys),
        columnWidth: 36,
    };

    // Циклічна навігація між картками в межах поточного списку (всі активні
    // фільтри — завіз/тип/пошук — уже застосовані до products). На межі сторінки
    // автоматично підвантажується наступна/попередня; на самому краю всього
    // діапазону — закільцьовується (остання сторінка → перша і навпаки).
    const totalPages = Math.max(1, Math.ceil((products.total || 0) / (products.per_page || 20)));
    const navigateDetails = (dir: 1 | -1) => {
        const items = products.items || [];
        if (items.length === 0 || detailsId == null) return;
        const idx = items.findIndex((it) => it.id === detailsId);
        if (idx === -1) return;
        const nextIdx = idx + dir;
        // У межах поточної сторінки
        if (nextIdx >= 0 && nextIdx < items.length) {
            setDetailsId(items[nextIdx].id);
            return;
        }
        // Одна сторінка → просте закільцьовування
        if (totalPages <= 1) {
            setDetailsId(items[(idx + dir + items.length) % items.length].id);
            return;
        }
        // Перехід через межу сторінки (з закільцьовуванням усього діапазону)
        const curPage = products.page || 1;
        if (dir === 1) {
            pendingNavRef.current = 'first';
            onPageChange(curPage >= totalPages ? 1 : curPage + 1);
        } else {
            pendingNavRef.current = 'last';
            onPageChange(curPage <= 1 ? totalPages : curPage - 1);
        }
    };
    const canNavigate = detailsId != null && ((products.items?.length ?? 0) > 1 || totalPages > 1);

    return (
        <TableContainer className="max-h-[calc(100vh-220px)]">
            <ProductDetailsModal
                productId={detailsId}
                open={detailsOpen}
                onClose={() => setDetailsOpen(false)}
                onPrev={canNavigate ? () => navigateDetails(-1) : undefined}
                onNext={canNavigate ? () => navigateDetails(1) : undefined}
            />
            <MergeCandidatesModal
                productId={mergeId}
                open={mergeOpen}
                onClose={() => setMergeOpen(false)}
                onMerged={() => { if (onPageChange) onPageChange(1); }}
            />
            
            <div className="overflow-x-auto rounded-lg shadow-md border border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-700">
                <Table
                    columns={columns}
                    dataSource={products.items}
                    rowKey="id"
                    rowSelection={ENABLE_ROW_SELECTION ? rowSelection : undefined}
                    pagination={false}
                    loading={loading}
                    onRow={(record: Product) => {
                        const issues: string[] = [];
                        const noNum = !record.productnumber || record.productnumber === '???'
                            || record.productnumber.startsWith('__tmp_rename_')
                            || record.productnumber.startsWith('???_');
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
                        const hasIssue = issues.length > 0;
                        const conflictTitle = issues.join(' • ');
                        // «Бронь» (Підтверджено без Оплачено) — subtle gray. Конфлікт (amber)
                        // має пріоритет над бронею.
                        const rowState = hasIssue ? 'bms-conflict-row' : (record.is_reserved ? 'bms-row-reserved' : 'bms-row-hover');
                        return {
                            title: hasIssue ? `⚠ ${conflictTitle}` : (record.is_reserved ? '🔒 Заброньовано (Підтверджено, без оплати)' : undefined),
                            className: `cursor-pointer ${rowState}`,
                            onDoubleClick: () => { setDetailsId(record.id); setDetailsOpen(true); },
                            onContextMenu: (e: React.MouseEvent) => handleRowContextMenu(e, record),
                        };
                    }}
                    onHeaderRow={() => ({ onContextMenu: handleHeaderContextMenu as any })}
                    // y: заповнюємо висоту вікна мінус шапка сторінки
                    scroll={{ x: 'max-content', y: 'calc(100vh - 260px)' }}
                    tableLayout="fixed"
                    className="min-w-[1100px] xl:min-w-[1250px] 2xl:min-w-[1400px]"
                    size="small"
                    bordered={false}
                    onChange={handleTableChange}
                />
            </div>

            {/* Контекстне меню керування колонками */}
            {menuOpen && (
                <div
                  ref={menuRef}
                  style={{ top: menuPos.y, left: menuPos.x }}
                  className="fixed z-[10000] w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-2"
                >
                  <div className="px-2 py-1 text-xs text-gray-500">Видимість колонок</div>
                  <div className="max-h-80 overflow-auto pr-1">
                    {columnOrder.map(c => (
                      <label key={c.id} className="flex items-center justify-between px-2 py-1 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 rounded">
                        <span>{c.title}</span>
                        <input
                          type="checkbox"
                          checked={!!visibleMap[c.id]}
                          onChange={(e) => setVisibleMap(v => ({ ...v, [c.id]: e.target.checked }))}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(() => columnOrder.reduce((a, c) => (a[c.id] = true, a), {} as Record<string, boolean>))}>Всі</button>
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(() => columnOrder.reduce((a, c) => (a[c.id] = false, a), {} as Record<string, boolean>))}>Приховати</button>
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(defaultVisibility)}>За умовч.</button>
                  </div>
                </div>
            )}

            {/* Контекстне меню дій над товаром (ПКМ на рядку) */}
            {rowMenuOpen && rowMenuRecord && (
                <div
                  ref={rowMenuRef}
                  style={{ top: rowMenuPos.y, left: rowMenuPos.x }}
                  className="fixed z-[10000] w-60 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1"
                >
                  <div className="px-3 py-1.5 text-[11px] text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700 truncate">
                    {(rowMenuRecord.productnumber || '').replace(/^#/, '') || `ID ${rowMenuRecord.id}`}
                  </div>
                  {(() => {
                    const sold = rowMenuRecord.sold_count ?? 0;
                    // Бронь = Підтверджене замовлення без оплати → теж має що показати.
                    const isReserved = !!(rowMenuRecord as any).is_reserved || ((rowMenuRecord as any).reserved_count ?? 0) > 0;
                    const hasOrder = sold > 0 || isReserved;
                    return (
                      <button
                        type="button"
                        disabled={!hasOrder}
                        onClick={() => handleShowInOrders(rowMenuRecord)}
                        title={hasOrder ? (sold > 0 ? 'Перейти у Замовлення та показати це замовлення' : 'Перейти у Замовлення (бронь)') : 'Товар ще не продано'}
                        className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left ${
                          hasOrder
                            ? 'text-gray-700 dark:text-gray-200 hover:bg-blue-50 dark:hover:bg-blue-900/30 cursor-pointer'
                            : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                        }`}
                      >
                        <span>📦</span>
                        <span>Показати в замовленнях</span>
                      </button>
                    );
                  })()}
                  <button
                    type="button"
                    onClick={() => { setDetailsId(rowMenuRecord.id); setDetailsOpen(true); setRowMenuOpen(false); }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
                  >
                    <span>🔍</span>
                    <span>Відкрити картку товару</span>
                  </button>
                </div>
            )}
        </TableContainer>
    );
};

export default ProductsTable; 