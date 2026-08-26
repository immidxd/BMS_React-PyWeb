import React from 'react';
import { useRuntimeConfig, useFeatureFlag } from '../../contexts/RuntimeConfigContext';

/**
 * Діагностичний бейдж: версія + канал + платформа.
 * Гейтиться прапором `experimental_ui` → видно лише на dev/beta, приховано на
 * stable (Windows-прод). Демонструє ланцюг platform → feature-flag → UI.
 *
 * Живе В ШАПЦІ, поруч зі знаком BMS, а не як fixed-оверлей у куті: раніше він
 * висів поверх нижньої панелі й накривав собою підпис «Тільки непродані».
 */
const DevBadge: React.FC = () => {
  const show = useFeatureFlag('experimental_ui');
  const { version, channel, platform } = useRuntimeConfig();

  if (!show) return null; // на stable/Windows — нічого не рендеримо

  return (
    <span
      title="Діагностика збірки (видно лише на dev/beta)"
      style={{
        fontFamily: 'var(--bms-font-mono)',
        fontSize: 10,
        lineHeight: 1,
        letterSpacing: '0.02em',
        color: 'var(--bms-fg-faint)',
        whiteSpace: 'nowrap',
        userSelect: 'none',
      }}
    >
      {`v${version} · ${channel} · ${platform}`}
    </span>
  );
};

export default DevBadge;
