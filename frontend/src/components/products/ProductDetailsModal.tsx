import React, { useEffect, useState } from 'react';
import { productService } from '../../services/productService';
import type { Product } from '../../types/product';

interface Props {
  productId: number | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

const ProductDetailsModal: React.FC<Props> = ({ productId, open, onClose, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [product, setProduct] = useState<Product | null>(null);
  const [saving, setSaving] = useState(false);
  const [edit, setEdit] = useState<Partial<Product>>({});

  useEffect(() => {
    if (!open || !productId) return;
    setLoading(true);
    productService.getProduct(productId)
      .then((p) => { setProduct(p); setEdit({
        price: p.price,
        oldprice: p.oldprice,
        quantity: p.quantity,
        is_visible: p.is_visible,
        description: p.description,
      }); })
      .finally(() => setLoading(false));
  }, [open, productId]);

  if (!open) return null;

  const Row: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
    <div className="grid grid-cols-3 gap-3 text-sm">
      <div className="text-gray-500">{label}</div>
      <div className="col-span-2 font-medium break-words">{value ?? '—'}</div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-label="Закрити" />
      <div className="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-3xl mx-4 p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Товар {productId}</h2>
          <div className="flex gap-2">
            <button disabled={saving} onClick={async () => {
              if (!productId) return;
              setSaving(true);
              try {
                await productService.updateProduct(productId, edit);
                if (onSaved) onSaved();
              } finally { setSaving(false); }
            }} className="px-2 py-1 text-sm rounded border border-blue-500 text-blue-600 hover:bg-blue-50 disabled:opacity-60">Зберегти</button>
            <button onClick={onClose} className="px-2 py-1 text-sm rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-700">Закрити</button>
          </div>
        </div>
        {loading ? (
          <div className="py-8 text-center text-gray-500">Завантаження...</div>
        ) : !product ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Row label="Номер" value={product.productnumber} />
                <Row label="Модель" value={product.model} />
                <Row label="Бренд" value={(product as any).brand_name || product.brand?.name} />
                <Row label="Тип" value={(product as any).type_name || product.type?.name} />
                <Row label="Статус" value={(product as any).status_name || product.status?.name} />
                <Row label="Кількість" value={product.quantity} />
              </div>
              <div className="space-y-2">
                <div className="grid grid-cols-3 gap-3 text-sm items-center">
                  <div className="text-gray-500">Ціна</div>
                  <input type="number" value={edit.price ?? ''} onChange={(e) => setEdit({ ...edit, price: e.target.value === '' ? undefined : Number(e.target.value) })} className="col-span-2 border rounded px-2 py-1" />
                </div>
                <div className="grid grid-cols-3 gap-3 text-sm items-center">
                  <div className="text-gray-500">Стара ціна</div>
                  <input type="number" value={edit.oldprice ?? ''} onChange={(e) => setEdit({ ...edit, oldprice: e.target.value === '' ? undefined : Number(e.target.value) })} className="col-span-2 border rounded px-2 py-1" />
                </div>
                <Row label="Розмір (EU)" value={product.sizeeu} />
                <Row label="Колір" value={product.color?.name} />
                <div className="grid grid-cols-3 gap-3 text-sm items-center">
                  <div className="text-gray-500">Видимий</div>
                  <input type="checkbox" checked={!!edit.is_visible} onChange={(e) => setEdit({ ...edit, is_visible: e.target.checked })} />
                </div>
              </div>
            </div>
            <div>
              <div className="text-sm text-gray-500 mb-1">Опис</div>
              <textarea className="w-full border rounded px-2 py-1 text-sm" rows={3} value={edit.description ?? ''} onChange={(e) => setEdit({ ...edit, description: e.target.value || undefined })} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ProductDetailsModal;


