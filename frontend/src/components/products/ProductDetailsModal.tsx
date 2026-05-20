import React, { useEffect, useState, useMemo } from 'react';
import { productService } from '../../services/productService';
import type { Product } from '../../types/product';
import { Tag, Spin, Image } from 'antd';
import { CloseOutlined, PictureOutlined, LeftOutlined, RightOutlined, WarningOutlined } from '@ant-design/icons';

interface Props {
  productId: number | null;
  open: boolean;
  onClose: () => void;
}

interface GalleryImage {
  filename: string;
  url: string;
  index: number;
  is_defect?: boolean;
}

const ProductDetailsModal: React.FC<Props> = ({ productId, open, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [product, setProduct] = useState<Product | null>(null);
  const [allImages, setAllImages] = useState<GalleryImage[]>([]);
  const [showDefects, setShowDefects] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const [previewVisible, setPreviewVisible] = useState(false);

  // Visible images: звичайні завжди; дефектні — лише коли користувач натиснув ⚠
  const images = useMemo(
    () => (showDefects ? allImages : allImages.filter((i) => !i.is_defect)),
    [allImages, showDefects],
  );
  const defectCount = useMemo(() => allImages.filter((i) => i.is_defect).length, [allImages]);

  useEffect(() => {
    if (!open || !productId) return;
    setLoading(true);
    setProduct(null);
    setAllImages([]);
    setShowDefects(false);
    setActiveIdx(0);
    Promise.allSettled([
      productService.getProduct(productId),
      productService.getProductImages(productId),
    ])
      .then(([prodRes, imgRes]) => {
        if (prodRes.status === 'fulfilled') setProduct(prodRes.value);
        if (imgRes.status === 'fulfilled') setAllImages(imgRes.value.images || []);
      })
      .finally(() => setLoading(false));
  }, [open, productId]);

  // Clamp activeIdx коли images повертаються чи перемикається showDefects
  useEffect(() => {
    if (activeIdx >= images.length) setActiveIdx(Math.max(0, images.length - 1));
  }, [images.length, activeIdx]);

  // Keyboard: Esc closes, ←/→ navigate gallery
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (previewVisible) return;
        onClose();
      }
      if (images.length > 1 && !previewVisible) {
        if (e.key === 'ArrowLeft') setActiveIdx((i) => (i - 1 + images.length) % images.length);
        if (e.key === 'ArrowRight') setActiveIdx((i) => (i + 1) % images.length);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose, images.length, previewVisible]);

  const p = product;

  const status = useMemo(() => {
    if (!p) return { text: '', color: 'default' };
    const sold = p.sold_count ?? 0;
    const qty = p.quantity ?? 0;
    const staticStatus = (p as any).status_name || '';
    if (staticStatus === 'Подаровано') return { text: 'Подаровано', color: 'purple' };
    if (sold > 0 && sold >= qty && qty > 0) return { text: 'Продано', color: 'red' };
    if (sold > 0 && sold < qty) return { text: 'Непродано', color: 'green' };
    if (staticStatus === 'Непродано') return { text: 'Непродано', color: 'green' };
    return { text: staticStatus || 'Не вказано', color: staticStatus ? 'geekblue' : 'default' };
  }, [p]);

  const sizesLine = useMemo(() => {
    if (!p) return null;
    const parts: { label: string; val: any }[] = [
      { label: 'EU', val: p.sizeeu },
      { label: 'UA', val: p.sizeua },
      { label: 'USA', val: p.sizeusa },
      { label: 'UK', val: p.sizeuk },
    ];
    return parts.filter((x) => x.val);
  }, [p]);

  if (!open) return null;

  const InfoRow: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => {
    if (value === null || value === undefined || value === '') return null;
    return (
      <div className="flex items-baseline gap-3 py-1.5">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 min-w-[90px] shrink-0 font-medium">{label}</span>
        <span className="text-sm text-gray-800 dark:text-gray-200 break-words">{value}</span>
      </div>
    );
  };

  const activeImage = images[activeIdx];
  const productTitle = p ? ([(p as any).brand_name, p.model].filter(Boolean).join(' ') || (p.productnumber || '').replace(/^#/, '')) : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-6xl mx-4 max-h-[92vh] overflow-hidden flex flex-col">

        {loading && (
          <div className="flex items-center justify-center py-32">
            <Spin size="large" />
          </div>
        )}

        {!loading && !p && (
          <div className="flex items-center justify-center py-32 text-gray-400">
            Товар не знайдено
          </div>
        )}

        {!loading && p && (
          <>
            {/* Header */}
            <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <span className="text-xs font-mono text-gray-400 dark:text-gray-500 px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800">
                    {(p.productnumber || '').replace(/^#/, '') || '—'}
                  </span>
                  {(p as any).type_name && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">{(p as any).type_name}{(p as any).subtype_name ? ` · ${(p as any).subtype_name}` : ''}</span>
                  )}
                  {p.is_rostovka && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700">
                      ▤ Ростовка
                    </span>
                  )}
                  {defectCount > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowDefects((v) => !v)}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border transition-colors ${
                        showDefects
                          ? 'bg-amber-500 text-white border-amber-600 dark:bg-amber-600 dark:border-amber-500'
                          : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700 dark:hover:bg-amber-900/50'
                      }`}
                      title={showDefects ? 'Сховати фото дефектів' : `Показати фото дефектів (${defectCount})`}
                    >
                      <WarningOutlined className="text-[11px]" />
                      <span>Дефект{defectCount > 1 ? `·${defectCount}` : ''}</span>
                    </button>
                  )}
                </div>
                <h2 className="text-2xl font-semibold text-gray-900 dark:text-gray-50 truncate leading-tight">
                  {productTitle}
                </h2>
              </div>
              {(() => {
                const parts = [(p as any).brand_name, p.model, p.marking].filter(Boolean) as string[];
                const q = parts.join(' ').replace(/\s+/g, ' ').trim();
                if (!q) return null;
                return (
                  <a
                    href={`https://www.google.com/search?q=${encodeURIComponent(q)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 ml-2 px-3 py-2 rounded-lg text-sm font-medium border border-blue-200 dark:border-blue-700 text-blue-600 hover:text-blue-800 hover:bg-blue-50 dark:text-blue-400 dark:hover:text-blue-300 dark:hover:bg-blue-900/20 transition-colors flex items-center gap-1.5"
                    title={`Пошук в Google: ${q}`}
                  >
                    <span className="font-bold text-xs">G</span>
                    <span>Знайти в Google</span>
                  </a>
                );
              })()}
              <button
                onClick={onClose}
                className="shrink-0 ml-2 p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                aria-label="Закрити"
              >
                <CloseOutlined className="text-base" />
              </button>
            </div>

            {/* Body — two columns */}
            <div className="overflow-y-auto flex-1">
              <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-8 p-6">

                {/* Left: Gallery */}
                <div className="flex flex-col gap-3">
                  {/* Main image */}
                  <div className="relative w-full aspect-square bg-gray-50 dark:bg-gray-800/40 rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800 flex items-center justify-center group">
                    {activeImage ? (
                      <>
                        <Image
                          src={activeImage.url}
                          alt={activeImage.filename}
                          preview={{
                            visible: previewVisible,
                            onVisibleChange: setPreviewVisible,
                            src: activeImage.url,
                          }}
                          className="!w-full !h-full"
                          style={{ objectFit: 'contain', width: '100%', height: '100%', cursor: 'zoom-in' }}
                          wrapperStyle={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                        />
                        {activeImage.is_defect && (
                          <div className="absolute top-3 left-3 inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-amber-500/90 text-white shadow-md pointer-events-none">
                            <WarningOutlined className="text-xs" />
                            <span>Дефект</span>
                          </div>
                        )}
                        {images.length > 1 && (
                          <>
                            <button
                              onClick={(e) => { e.stopPropagation(); setActiveIdx((i) => (i - 1 + images.length) % images.length); }}
                              className="absolute left-2 top-1/2 -translate-y-1/2 p-2 rounded-full bg-white/80 dark:bg-gray-900/80 hover:bg-white dark:hover:bg-gray-900 shadow-md text-gray-700 dark:text-gray-200 opacity-0 group-hover:opacity-100 transition-opacity"
                              aria-label="Попереднє фото"
                            >
                              <LeftOutlined />
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); setActiveIdx((i) => (i + 1) % images.length); }}
                              className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-full bg-white/80 dark:bg-gray-900/80 hover:bg-white dark:hover:bg-gray-900 shadow-md text-gray-700 dark:text-gray-200 opacity-0 group-hover:opacity-100 transition-opacity"
                              aria-label="Наступне фото"
                            >
                              <RightOutlined />
                            </button>
                            <div className="absolute bottom-3 right-3 px-2 py-1 rounded-md text-xs bg-black/60 text-white font-mono">
                              {activeIdx + 1} / {images.length}
                            </div>
                          </>
                        )}
                      </>
                    ) : (
                      <div className="flex flex-col items-center justify-center text-gray-300 dark:text-gray-600">
                        <PictureOutlined style={{ fontSize: 64 }} />
                        <span className="text-sm mt-3">Фото відсутнє</span>
                        <span className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">додайте файли з префіксом {(p.productnumber || '').replace(/^#/, '') || 'номер'}_</span>
                      </div>
                    )}
                  </div>

                  {/* Thumbnails */}
                  {images.length > 1 && (
                    <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
                      {images.map((img, i) => (
                        <button
                          key={img.filename}
                          onClick={() => setActiveIdx(i)}
                          className={`relative shrink-0 w-16 h-16 rounded-lg overflow-hidden border-2 transition-all ${
                            i === activeIdx
                              ? (img.is_defect
                                  ? 'border-amber-500 ring-2 ring-amber-200 dark:ring-amber-800'
                                  : 'border-primary-500 ring-2 ring-primary-200 dark:ring-primary-800')
                              : (img.is_defect
                                  ? 'border-amber-400/60 hover:border-amber-500 opacity-80 hover:opacity-100'
                                  : 'border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 opacity-70 hover:opacity-100')
                          }`}
                          title={img.is_defect ? `Дефект: ${img.filename}` : img.filename}
                        >
                          <img src={img.url} alt={img.filename} className="w-full h-full object-cover" loading="lazy" />
                          {img.is_defect && (
                            <span className="absolute top-0.5 right-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full bg-amber-500 text-white text-[9px] shadow">
                              <WarningOutlined style={{ fontSize: 9 }} />
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Right: Info */}
                <div className="flex flex-col min-w-0">
                  {/* Price */}
                  <div className="flex items-baseline gap-3 mb-3">
                    {p.price != null && p.price > 0 ? (
                      <span className="text-3xl font-bold text-gray-900 dark:text-gray-50">{Number(p.price).toFixed(0)} ₴</span>
                    ) : (
                      <span className="text-xl text-gray-300 dark:text-gray-600">Ціна не вказана</span>
                    )}
                    {p.oldprice != null && p.oldprice > 0 && p.oldprice !== p.price && (
                      <span className="text-base text-gray-400 line-through">{Number(p.oldprice).toFixed(0)} ₴</span>
                    )}
                  </div>

                  {/* Status + condition + availability */}
                  <div className="flex flex-wrap items-center gap-2 mb-5">
                    <Tag color={status.color} style={{ margin: 0 }}>{status.text}</Tag>
                    {(p as any).condition_name && <Tag color="blue" style={{ margin: 0 }}>{(p as any).condition_name}</Tag>}
                    {(() => {
                      const total = p.quantity ?? 0;
                      const avail = p.available_qty ?? total;
                      const sold = p.sold_count ?? 0;
                      let label = '', color = '';
                      if (total === 0) { label = '0 в наявності'; color = 'red'; }
                      else if (sold === 0) { label = `${total} в наявності`; color = 'green'; }
                      else if (avail <= 0) { label = `0 / ${total}`; color = 'red'; }
                      else { label = `${avail} / ${total} в наявності`; color = 'orange'; }
                      return <Tag color={color} style={{ margin: 0 }}>{label}</Tag>;
                    })()}
                  </div>

                  {/* Sizes — visual block, like e-commerce */}
                  {sizesLine && sizesLine.length > 0 && (
                    <div className="mb-5">
                      <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium">Розмір</div>
                      <div className="flex flex-wrap gap-2">
                        {sizesLine.map(({ label, val }) => (
                          <div key={label} className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">{label}</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{val}</span>
                          </div>
                        ))}
                        {p.measurementscm && (
                          <div className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">СМ</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{p.measurementscm}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Specifications */}
                  <div className="border-t border-gray-100 dark:border-gray-800 pt-4">
                    <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium">Характеристики</div>
                    <InfoRow label="Бренд" value={(p as any).brand_name} />
                    <InfoRow label="Модель" value={p.model} />
                    <InfoRow label="Тип" value={(p as any).type_name} />
                    <InfoRow label="Підтип" value={(p as any).subtype_name} />
                    <InfoRow label="Стиль" value={(p as any).style_name} />
                    <InfoRow label="Стать" value={(p as any).gender_name} />
                    <InfoRow label="Сезон" value={p.season} />
                    <InfoRow label="Колір" value={(p as any).color_name} />
                    <InfoRow label="Габарити" value={p.dimensions} />
                    <InfoRow label="Ширина" value={p.width} />
                    <InfoRow label="Поточний стан" value={(p as any).current_condition_name} />
                    <InfoRow label="Маркування" value={p.marking} />
                    <InfoRow label="Рік" value={p.year} />
                    <InfoRow label="Клони" value={p.clonednumbers} />
                    <InfoRow label="Завіз" value={p.dateadded} />
                    <InfoRow label="У базі з" value={p.created_at ? new Date(p.created_at).toLocaleDateString('uk-UA') : null} />
                  </div>
                </div>
              </div>

              {/* Description */}
              {p.description && (
                <div className="px-6 pb-4">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium">Опис</div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed bg-gray-50 dark:bg-gray-800/40 rounded-lg px-4 py-3">
                    {p.description}
                  </p>
                </div>
              )}

              {/* Notes */}
              {p.extranote && (
                <div className="px-6 pb-6">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium">Примітки</div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed bg-amber-50 dark:bg-amber-900/15 rounded-lg px-4 py-3 border border-amber-100 dark:border-amber-800/30">
                    {p.extranote}
                  </p>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default ProductDetailsModal;
