import React from 'react';
import { useRuntimeConfig, useFeatureFlag } from '../../contexts/RuntimeConfigContext';

/**
 * Діагностичний бейдж: версія + канал + платформа.
 * Гейтиться прапором `experimental_ui` → видно лише на dev/beta, приховано на
 * stable (Windows-прод). Демонструє ланцюг platform → feature-flag → UI.
 * Чисто додатковий, фіксована позиція — наявну розкладку не чіпає.
 */
const DevBadge: React.FC = () => {
  const show = useFeatureFlag('experimental_ui');
  const { version, channel, platform } = useRuntimeConfig();

  if (!show) return null; // на stable/Windows — нічого не рендеримо

  return (
    <div
      title="Діагностика збірки (видно лише на dev/beta)"
      style={{
        position: 'fixed',
        bottom: 8,
        left: 8,
        zIndex: 9999,
        padding: '2px 8px',
        fontSize: 11,
        lineHeight: 1.4,
        fontFamily: 'monospace',
        borderRadius: 6,
        background: 'rgba(0,0,0,0.6)',
        color: '#9ae6b4',
        pointerEvents: 'none',
        userSelect: 'none',
      }}
    >
      {`v${version} · ${channel} · ${platform}`}
    </div>
  );
};

export default DevBadge;
