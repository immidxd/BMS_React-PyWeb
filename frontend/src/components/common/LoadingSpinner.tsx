import React from 'react';

type LoadingSpinnerVariant = 'inline' | 'section' | 'page' | 'modal' | 'overlay';
type LoadingSpinnerSize = 'small' | 'medium' | 'large';

interface LoadingSpinnerProps {
  text?: string | null;
  variant?: LoadingSpinnerVariant;
  size?: LoadingSpinnerSize;
  className?: string;
}

/**
 * Єдиний індикатор завантаження BMS.
 *
 * Контейнер завжди центрує саме внутрішній вузол, а overlay позиціонується
 * відносно видимого батьківського блоку. Це не дає індикатору «поїхати» вбік
 * услід за широким вмістом таблиці з горизонтальним скролом.
 */
const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  text = 'Завантаження…',
  variant = 'section',
  size = 'medium',
  className = '',
}) => (
  <span
    className={`bms-loading bms-loading--${variant} bms-loading--${size} ${className}`.trim()}
    role="status"
    aria-live="polite"
    aria-label={text || 'Завантаження'}
  >
    <span className="bms-loading__content">
      <span className="bms-loading__spinner" aria-hidden="true" />
      {text && <span className="bms-loading__label">{text}</span>}
    </span>
  </span>
);

export default LoadingSpinner;
