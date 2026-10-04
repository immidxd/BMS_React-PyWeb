import React from 'react';
import { duplicateTitle, useDuplicateNumber } from '../../services/duplicateNumbers';

/**
 * Номер товару, який червоніє, коли номер дублюється (див. services/duplicateNumbers).
 * Обгортка лише фарбує текст і додає підказку — клік, копіювання й решту
 * поведінки лишає батьківському елементу.
 */
const ProductNumberText: React.FC<{ value?: string | null; className?: string; children?: React.ReactNode; onDark?: boolean }> = ({ value, className, children, onDark }) => {
  const dup = useDuplicateNumber(value);
  if (!dup) return <span className={className}>{children ?? value}</span>;
  return (
    <span className={`${className || ''} ${onDark ? '!text-red-400 dark:!text-red-600' : '!text-red-600 dark:!text-red-400'}`} title={duplicateTitle(dup)}
      data-duplicate-number="true">
      {children ?? value}
    </span>
  );
};

export default ProductNumberText;
