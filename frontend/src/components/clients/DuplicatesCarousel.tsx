import React, { useState, useEffect, useCallback } from 'react';
import { confirmDialog } from '../../ui/feedback';
import LoadingSpinner from '../common/LoadingSpinner';

interface RecentOrder {
  id: number;
  date: string | null;
  amount: number | null;
  notes: string | null;
}

interface ContactRow {
  kind: string;
  value: string;
  is_primary: boolean;
}

interface DupClient {
  id: number;
  full_name: string;
  phone: string | null;
  facebook: string | null;
  telegram: string | null;
  instagram: string | null;
  email: string | null;
  maiden_name: string | null;
  city: string | null;
  is_locked: boolean;
  orders_count: number;
  total_amount: number;
  recent_orders: RecentOrder[];
  created_at: string | null;
  is_suggested_master: boolean;
  contacts: ContactRow[];
}

const KIND_ICONS: Record<string, string> = {
  phone: '📞', viber: '📞', email: '✉️',
  facebook: 'FB', telegram: 'TG', instagram: 'IG',
  olx: 'OLX', tiktok: 'TT', messenger: 'M',
};

interface DupGroup {
  signal: string;
  key: string;
  suggested_master_id: number;
  confidence: 'high' | 'medium' | 'low';
  confidence_reason: string;
  has_signal_conflict: boolean;
  clients: DupClient[];
}

interface Props {
  open: boolean;
  onClose: () => void;
  onMerged?: () => void;
}

function fmtMoney(n: number | null) {
  if (!n) return '—';
  return new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(n);
}

function fmtDate(s: string | null) {
  if (!s) return '—';
  return new Date(s).toLocaleDateString('uk-UA', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

const ConfidenceBadge: React.FC<{ confidence: string }> = ({ confidence }) => {
  const cfg: Record<string, { label: string; cls: string; icon: string }> = {
    high: { label: 'Висока ймовірність', cls: 'bg-emerald-100 text-emerald-700 border-emerald-300 dark:bg-emerald-900/40 dark:text-emerald-300 dark:border-emerald-700', icon: '✓' },
    medium: { label: 'Середня', cls: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700', icon: '?' },
    low: { label: 'РИЗИК: різні люди', cls: 'bg-red-100 text-red-700 border-red-300 dark:bg-red-900/40 dark:text-red-300 dark:border-red-700', icon: '⚠' },
  };
  const c = cfg[confidence] || cfg.medium;
  return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${c.cls}`}>{c.icon} {c.label}</span>;
};

const DuplicatesCarousel: React.FC<Props> = ({ open, onClose, onMerged }) => {
  const [groups, setGroups] = useState<DupGroup[]>([]);
  const [totalInDb, setTotalInDb] = useState<number>(0);
  const [pageLimit, setPageLimit] = useState<number>(200);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedMaster, setSelectedMaster] = useState<number | null>(null);

  const fetchGroups = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetch('/api/client-duplicates/groups?limit=200');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      const gs: DupGroup[] = d.groups || [];
      setGroups(gs);
      setTotalInDb(d.total_in_db ?? gs.length);
      setPageLimit(d.page_limit ?? 200);
      setIdx(0);
      setSelectedMaster(gs[0]?.suggested_master_id ?? null);
    } catch (e: any) {
      setError(e.message || 'Помилка');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (open) fetchGroups(); }, [open, fetchGroups]);

  const cur = groups[idx];
  useEffect(() => {
    if (cur) setSelectedMaster(cur.suggested_master_id);
  }, [idx, cur]);

  if (!open) return null;

  const total = groups.length;
  const remaining = Math.max(0, total - idx);

  const goNext = () => {
    if (idx + 1 < total) setIdx(idx + 1);
    else onClose();
  };

  const doMerge = async () => {
    if (!cur || !selectedMaster) return;
    const sources = cur.clients.filter(c => c.id !== selectedMaster).map(c => c.id);
    if (sources.length === 0) return;
    const masterC = cur.clients.find(c => c.id === selectedMaster);
    if (!(await confirmDialog(
      `Обʼєднати ${sources.length} клієнтів у "${masterC?.full_name}" (#${selectedMaster})?\nДія НЕЗВОРОТНА.`
    ))) return;
    setBusy(true);
    try {
      const payload = { groups: [{ master_id: selectedMaster, source_ids: sources }] };
      const res = await fetch('/api/client-duplicates/merge-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      onMerged?.();
      goNext();
    } catch (e: any) {
      alert(`Помилка: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const dismissAsDifferent = async () => {
    if (!cur) return;
    setBusy(true);
    try {
      // Позначаємо ВСІ пари в групі як «різні люди»
      const ids = cur.clients.map(c => c.id);
      const pairs: Array<[number, number]> = [];
      for (let i = 0; i < ids.length; i++)
        for (let j = i + 1; j < ids.length; j++)
          pairs.push([ids[i], ids[j]]);
      await Promise.all(pairs.map(([a, b]) =>
        fetch(`/api/client-duplicates/dismiss-pair/${a}/${b}`, { method: 'POST' })
      ));
      goNext();
    } catch (e: any) {
      alert(`Помилка: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-start md:items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-5xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">⚠️ Кандидати на мердж</h2>
            <span className="text-sm text-gray-500">Група {Math.min(idx + 1, total)} з {total}</span>
            {totalInDb > total && (
              <span
                className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700"
                title={`У БД виявлено ${totalInDb} груп з потенційними дублями. Показано ТОП-${pageLimit} за частотою. Після мерджу/dismiss «знизу» підтягуються наступні.`}
              >
                ⚠ всього у БД: <strong>{totalInDb}</strong> (показано топ-{pageLimit})
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 text-xl">×</button>
        </div>

        {/* Body */}
        {loading ? (
          <LoadingSpinner variant="modal" text="Завантаження дублікатів…" />
        ) : error ? (
          <div className="h-72 flex items-center justify-center text-red-500">{error}</div>
        ) : total === 0 ? (
          <div className="h-72 flex flex-col items-center justify-center text-gray-400 gap-2">
            <span className="text-5xl">🎉</span>
            <span>Дублікатів не знайдено! Усі групи прибрані.</span>
            <button onClick={onClose} className="mt-3 px-4 py-2 rounded bg-blue-600 text-white text-sm">Закрити</button>
          </div>
        ) : !cur ? (
          <div className="h-72 flex flex-col items-center justify-center text-gray-400 gap-2">
            <span className="text-5xl">✅</span>
            <span>Усі групи перевірені!</span>
            <button onClick={onClose} className="mt-3 px-4 py-2 rounded bg-blue-600 text-white text-sm">Закрити</button>
          </div>
        ) : (
          <>
            {/* Group meta */}
            <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30">
              <div className="flex flex-wrap items-center gap-3">
                <ConfidenceBadge confidence={cur.confidence} />
                <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
                  Збіг за: <span className="font-mono bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 rounded text-xs">{cur.signal}</span>
                </span>
                <span className="text-xs text-gray-500 truncate max-w-[400px]" title={cur.key}>{cur.key}</span>
                <span className="ml-auto text-xs text-gray-500">{cur.clients.length} клієнтів</span>
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-300 mt-1.5">{cur.confidence_reason}</div>
            </div>

            {/* Clients side-by-side */}
            <div className="p-4 max-h-[60vh] overflow-y-auto">
              <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(cur.clients.length, 3)}, minmax(0, 1fr))` }}>
                {cur.clients.map(c => {
                  const isMaster = c.id === selectedMaster;
                  return (
                    <div key={c.id}
                      className={`rounded-lg border-2 p-3 transition-all cursor-pointer ${
                        isMaster
                          ? 'border-emerald-500 bg-emerald-50/50 dark:bg-emerald-900/20'
                          : 'border-gray-200 dark:border-gray-700 hover:border-gray-400'
                      }`}
                      onClick={() => setSelectedMaster(c.id)}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <input
                          type="radio"
                          checked={isMaster}
                          onChange={() => setSelectedMaster(c.id)}
                          className="cursor-pointer"
                          title="Призначити master"
                        />
                        <span className="text-xs text-gray-400 font-mono">#{c.id}</span>
                        {c.is_locked && <span title="Залочено вручну">🔒</span>}
                      </div>
                      <div className="font-semibold text-gray-900 dark:text-gray-100 text-sm mb-1">
                        {c.full_name}
                        {isMaster && <span className="ml-1 text-xs text-emerald-600">★master</span>}
                      </div>
                      {c.maiden_name && (
                        <div className="text-xs text-pink-600 dark:text-pink-300">👰 {c.maiden_name}</div>
                      )}
                      <div className="space-y-0.5 mt-2 text-xs">
                        {(c.contacts && c.contacts.length > 0 ? c.contacts : []).map((ct, i) => (
                          <div key={`${ct.kind}-${i}`}
                               className={`truncate ${ct.is_primary ? '' : 'text-gray-500 dark:text-gray-400'}`}
                               title={`${ct.kind}${ct.is_primary ? ' (primary)' : ' (secondary)'}: ${ct.value}`}>
                            <span className="inline-block w-7 text-[10px] font-mono">{KIND_ICONS[ct.kind] || ct.kind}</span>
                            {ct.value.replace('https://','').replace('www.','').replace('facebook.com/','').replace('t.me/','').replace('instagram.com/','')}
                            {!ct.is_primary && <span className="ml-1 text-[9px] text-gray-400">·alt</span>}
                          </div>
                        ))}
                        {(!c.contacts || c.contacts.length === 0) && (
                          <>
                            {c.phone && <div title="Phone">📞 {c.phone}</div>}
                            {c.email && <div>✉️ {c.email}</div>}
                            {c.facebook && <div className="truncate" title={c.facebook}>FB: {c.facebook.replace('facebook.com/','')}</div>}
                            {c.telegram && <div>TG: {c.telegram}</div>}
                            {c.instagram && <div>IG: {c.instagram}</div>}
                          </>
                        )}
                        {c.city && <div>🏙 {c.city}</div>}
                      </div>
                      <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700">
                        <div className="text-xs flex items-center justify-between">
                          <span><strong>{c.orders_count}</strong> зам.</span>
                          <span className="font-semibold">{fmtMoney(c.total_amount)}</span>
                        </div>
                        {c.recent_orders.length > 0 && (
                          <div className="mt-1.5 space-y-0.5">
                            {c.recent_orders.slice(0, 3).map(o => (
                              <div key={o.id} className="text-[10px] text-gray-600 dark:text-gray-400 truncate" title={o.notes || ''}>
                                {fmtDate(o.date)} · {fmtMoney(o.amount)}{o.notes ? ` · ${o.notes.slice(0, 30)}` : ''}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="text-[10px] text-gray-400 mt-1">створено {fmtDate(c.created_at)}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Actions */}
            <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs text-gray-500">
                Залишилось у поточному пакеті: <strong>{remaining}</strong>
                {totalInDb > total && (
                  <> · у БД ще <strong>{Math.max(0, totalInDb - total)}</strong> поза топом</>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={dismissAsDifferent}
                  disabled={busy}
                  className="px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                  title="Більше не пропонувати ці пари"
                >
                  ✅ Це різні люди
                </button>
                <button
                  onClick={goNext}
                  disabled={busy}
                  className="px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                >
                  ❓ Пропустити поки
                </button>
                <button
                  onClick={doMerge}
                  disabled={busy || !selectedMaster}
                  className={`px-4 py-1.5 text-sm font-semibold rounded text-white disabled:opacity-50 ${
                    cur.confidence === 'low'
                      ? 'bg-red-600 hover:bg-red-700'
                      : 'bg-emerald-600 hover:bg-emerald-700'
                  }`}
                >
                  {busy ? 'Обʼєдную…' : `🔀 Обʼєднати у #${selectedMaster}`}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default DuplicatesCarousel;
