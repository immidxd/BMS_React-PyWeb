import React from 'react';

interface Props {
  productNumber: string;
  onOpen: (id: number) => void;
  className?: string;
}

/**
 * Клікабельний номер товару. На клік резолвить productnumber → id через
 * /api/products?search=... і викликає onOpen(id) для відкриття картки.
 */
const ProductNumberLink: React.FC<Props> = ({ productNumber, onOpen, className }) => {
  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const clean = (productNumber || '').replace(/^#/, '').trim();
    if (!clean) return;
    try {
      const res = await fetch(`/api/products?search=${encodeURIComponent(clean)}&per_page=5`);
      if (!res.ok) return;
      const data = await res.json();
      const items = data.items || [];
      const exact = items.find((p: any) =>
        (p.productnumber || '').replace(/^#/, '').toLowerCase() === clean.toLowerCase()
      );
      const target = exact || items[0];
      if (target?.id) onOpen(target.id);
    } catch { /* ignore */ }
  };

  return (
    <span
      className={className || 'cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300'}
      title="Відкрити картку товару"
      onClick={handleClick}
    >
      {productNumber}
    </span>
  );
};

export default ProductNumberLink;
