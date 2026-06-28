import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import axios from 'axios';

// Тримаємо у синхроні з backend/services/runtime_config.py (DEFAULT_FLAGS).
export interface FeatureFlags {
  olx_publishing: boolean;
  telegram_publishing: boolean;
  experimental_ui: boolean;
  [key: string]: boolean; // дозволяємо нові прапори без правок типу
}

export type Platform = 'windows' | 'darwin' | 'linux';
export type Channel = 'dev' | 'beta' | 'stable';

export interface RuntimeConfig {
  platform: Platform;
  channel: Channel;
  version: string;
  flags: FeatureFlags;
}

// ── ДЕФОЛТИ = ПОТОЧНА ПОВЕДІНКА ────────────────────────────────────────────────
// Якщо /api/runtime-config недоступний або повільний — UI працює рівно як зараз:
// усі функції увімкнені, нічого не ховається. Платформу вгадуємо з navigator,
// поки бекенд не уточнить.
function guessPlatform(): Platform {
  const ua = (navigator.userAgent || '').toLowerCase();
  if (ua.includes('win')) return 'windows';
  if (ua.includes('mac')) return 'darwin';
  return 'linux';
}

const DEFAULT_CONFIG: RuntimeConfig = {
  platform: guessPlatform(),
  channel: 'dev',
  version: 'dev',
  flags: {
    olx_publishing: true,
    telegram_publishing: true,
    experimental_ui: true,
  },
};

const RuntimeConfigContext = createContext<RuntimeConfig>(DEFAULT_CONFIG);

export const RuntimeConfigProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [config, setConfig] = useState<RuntimeConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    let cancelled = false;
    axios
      .get('/api/runtime-config')
      .then((res) => {
        if (cancelled || !res?.data) return;
        const d = res.data as Partial<RuntimeConfig>;
        setConfig((prev) => ({
          platform: (d.platform as Platform) || prev.platform,
          channel: (d.channel as Channel) || prev.channel,
          version: d.version || prev.version,
          // merge: невідомі/відсутні прапори лишаються на дефолті (увімкнені)
          flags: { ...prev.flags, ...(d.flags || {}) },
        }));
      })
      .catch(() => {
        // тихо лишаємось на дефолтах — поведінка як раніше
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // не блокуємо рендер — діти малюються одразу з дефолтами, оновляться по приходу
  return <RuntimeConfigContext.Provider value={config}>{children}</RuntimeConfigContext.Provider>;
};

export const useRuntimeConfig = (): RuntimeConfig => useContext(RuntimeConfigContext);

/** Зручний хук: чи увімкнена функція. Невідомий прапор → true (не ховаємо нове). */
export const useFeatureFlag = (name: keyof FeatureFlags | string): boolean => {
  const { flags } = useRuntimeConfig();
  const v = flags[name as string];
  return v === undefined ? true : v;
};

export const usePlatform = (): Platform => useRuntimeConfig().platform;
