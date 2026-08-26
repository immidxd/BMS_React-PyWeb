import React from 'react';
import { useRuntimeConfig, useFeatureFlag } from '../../contexts/RuntimeConfigContext';

/**
 * Діагностика збірки: версія + канал + платформа.
 * Гейтиться прапором `experimental_ui` → лише dev/beta, на stable (Windows-прод)
 * не робить нічого.
 *
 * НЕ займає місця в розкладці: обгортає знак BMS і живе у його підказці. Дві
 * попередні спроби місця не витримали — fixed-оверлей у куті накривав підпис
 * «Тільки непродані» в нижній панелі, а рядок поруч із логотипом зсував поле
 * пошуку. Діагностика не має посувати робочі елементи.
 */
const DevBadge: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const show = useFeatureFlag('experimental_ui');
  const { version, channel, platform } = useRuntimeConfig();

  if (!show) return <>{children}</>;

  return (
    <span
      title={`Збірка: v${version} · ${channel} · ${platform}`}
      style={{ display: 'inline-flex' }}
    >
      {children}
    </span>
  );
};

export default DevBadge;
