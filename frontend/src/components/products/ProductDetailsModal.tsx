import React, { useEffect, useState } from 'react';
import { productService } from '../../services/productService';
import type { Product } from '../../types/product';
import { Tag, Spin } from 'antd';
import { CloseOutlined, PictureOutlined } from '@ant-design/icons';

interface Props {
  productId: number | null;
  open: boolean;
  onClose: () => void;
}

const ProductDetailsModal: React.FC<Props> = ({ productId, open, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [product, setProduct] = useState<Product | null>(null);

  useEffect(() => {
    if (!open || !productId) return;
    setLoading(true);
    setProduct(null);
    productService.getProduct(productId)
      .then(setProduct)
      .finally(() => setLoading(false));
  }, [open, productId]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  if (!open) return null;

  const p = product;

  // Status display logic (matches ProductsTable)
  const getStatusDisplay = () => {
    if (!p) return { text: '', color: 'default' };
    const sold = p.sold_count ?? 0;
    const qty = p.quantity ?? 0;
    const staticStatus = (p as any).status_name || '';
    if (staticStatus === 'Подаровано') return { text: 'Подаровано', color: 'purple' };
    if (sold > 0 && sold >= qty && qty > 0) return { text: 'Продано', color: 'red' };
    if (sold > 0 && sold < qty) return { text: 'Непродано', color: 'green' };
    if (staticStatus === 'Непродано') return { text: 'Непродано', color: 'green' };
    return { text: staticStatus || 'Не вказано', color: staticStatus ? 'geekblue' : 'default' };
  };

  const status = getStatusDisplay();

  const InfoRow: React.FC<{ label: string; value?: React.ReactNode; className?: string }> = ({ label, value, className }) => (
    <div className={`flex items-baseline gap-2 py-1 ${className || ''}`}>
      <span className="text-xs text-gray-400 dark:text-gray-500 min-w-[100px] shrink-0">{label}</span>
      <span className="text-sm text-gray-800 dark:text-gray-200 break-words">{value ?? <span className="text-gray-300">—</span>}</span>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-4xl mx-4 max-h-[90vh] overflow-hidden flex flex-col">

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Spin size="large" />
          </div>
        )}

        {/* Not found */}
        {!loading && !p && (
          <div className="flex items-center justify-center py-20 text-gray-400">
            Товар не знайдено
          </div>
        )}

        {/* Product content */}
        {!loading && p && (
          <>
            {/* Header */}
            <div className="flex items-start justify-between px-6 pt-5 pb-3 border-b border-gray-100 dark:border-gray-700">
              <div className="min-w-0">
                <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100 truncate">
                  {[(p as any).brand_name, p.model].filter(Boolean).join(' ') || p.productnumber}
                </h2>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-sm text-gray-400 dark:text-gray-500 font-mono">#{p.productnumber}</span>
                  {p.is_rostovka && (
                    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700">
                      ▤ Ростовка
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={onClose}
                className="shrink-0 ml-4 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              >
                <CloseOutlined className="text-lg" />
              </button>
            </div>

            {/* Body */}
            <div className="overflow-y-auto flex-1">
              {/* Two-column: Photo + Key Info */}
              <div className="flex flex-col md:flex-row gap-6 p-6">

                {/* Left: Photo area */}
                <div className="shrink-0 w-full md:w-72">
                  <div className="w-full aspect-square bg-gray-50 dark:bg-gray-700/50 rounded-lg border-2 border-dashed border-gray-200 dark:border-gray-600 flex flex-col items-center justify-center">
                    {p.mainimage ? (
                      <img src={p.mainimage} alt={p.productnumber} className="w-full h-full object-cover rounded-lg" />
                    ) : (
                      <>
                        <PictureOutlined className="text-4xl text-gray-300 dark:text-gray-500 mb-2" />
                        <span className="text-xs text-gray-400 dark:text-gray-500">Фото відсутнє</span>
                      </>
                    )}
                  </div>
                </div>

                {/* Right: Key product info */}
                <div className="flex-1 min-w-0">
                  {/* Price block */}
                  <div className="flex items-baseline gap-3 mb-4">
                    {p.price != null && p.price > 0 && (
                      <span className="text-2xl font-bold text-gray-900 dark:text-gray-100">{Number(p.price).toFixed(0)}₴</span>
                    )}
                    {p.oldprice != null && p.oldprice > 0 && p.oldprice !== p.price && (
                      <span className="text-base text-gray-400 line-through">{Number(p.oldprice).toFixed(0)}₴</span>
                    )}
                    {(!p.price || p.price === 0) && (
                      <span className="text-lg text-gray-300 dark:text-gray-500">Ціна не вказана</span>
                    )}
                  </div>

                  {/* Status + Condition tags */}
                  <div className="flex items-center gap-2 mb-4">
                    <Tag color={status.color}>{status.text}</Tag>
                    {(p as any).condition_name && (
                      <Tag color="blue">{(p as any).condition_name}</Tag>
                    )}
                  </div>

                  {/* Available qty */}
                  <div className="flex items-center gap-2 mb-4">
                    <span className="text-xs text-gray-400">В наявності:</span>
                    {(() => {
                      const total = p.quantity ?? 0;
                      const avail = p.available_qty ?? total;
                      const sold = p.sold_count ?? 0;
                      if (total === 0) return <Tag color="red">0</Tag>;
                      if (sold === 0) return <Tag color="green">{total}</Tag>;
                      if (avail <= 0) return <Tag color="red">0 / {total}</Tag>;
                      return <Tag color="orange">{avail} / {total}</Tag>;
                    })()}
                  </div>

                  {/* Attributes grid */}
                  <div className="border-t border-gray-100 dark:border-gray-700 pt-3 space-y-0">
                    <InfoRow label="Тип" value={(p as any).type_name} />
                    <InfoRow label="Підтип" value={(p as any).subtype_name} />
                    <InfoRow label="Бренд" value={(p as any).brand_name} />
                    <InfoRow label="Модель" value={p.model} />
                    {p.marking && <InfoRow label="Маркування" value={p.marking} />}
                    {p.year && <InfoRow label="Рік" value={p.year} />}
                    <InfoRow label="Стать" value={(p as any).gender_name} />
                    <InfoRow label="Колір" value={(p as any).color_name} />
                    <div className="flex gap-6">
                      <InfoRow label="Розмір EU" value={p.sizeeu} />
                      {p.measurementscm && <InfoRow label="СМ" value={p.measurementscm} />}
                    </div>
                    {(p.sizeua || p.sizeusa || p.sizeuk) && (
                      <div className="flex gap-6">
                        {p.sizeua && <InfoRow label="UA" value={p.sizeua} />}
                        {p.sizeusa && <InfoRow label="USA" value={p.sizeusa} />}
                        {p.sizeuk && <InfoRow label="UK" value={p.sizeuk} />}
                      </div>
                    )}
                    {p.clonednumbers && <InfoRow label="Клони" value={p.clonednumbers} />}
                    {p.dateadded && <InfoRow label="Дата завозу" value={p.dateadded} />}
                    {p.created_at && <InfoRow label="Додано в базу" value={new Date(p.created_at).toLocaleDateString('uk-UA')} />}
                  </div>
                </div>
              </div>

              {/* Description */}
              {p.description && (
                <div className="px-6 pb-4">
                  <div className="text-xs text-gray-400 dark:text-gray-500 mb-1 font-medium uppercase tracking-wide">Опис</div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap bg-gray-50 dark:bg-gray-700/30 rounded-lg px-4 py-3">
                    {p.description}
                  </p>
                </div>
              )}

              {/* Extra notes */}
              {p.extranote && (
                <div className="px-6 pb-6">
                  <div className="text-xs text-gray-400 dark:text-gray-500 mb-1 font-medium uppercase tracking-wide">Примітки</div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap bg-amber-50 dark:bg-amber-900/20 rounded-lg px-4 py-3 border border-amber-100 dark:border-amber-800/30">
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
