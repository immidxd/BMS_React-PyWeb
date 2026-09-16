/**
 * Склад — коробки, їхній вміст і події. Дані живуть у хмарі (те саме, що бачить
 * Mini App працівників); ця сторінка — десктопний клієнт через /api/warehouse.
 *
 * Ліворуч — список коробок (код, назва, місце, скільки всередині, статус,
 * «перевірити»); праворуч — картка обраної коробки: вміст із фото, вийняти
 * по одному / вибрані / все, запечатати, звірено, етикетка на принтер,
 * видалити, журнал. Пошук згори — «де лежить #номер».
 *
 * Прототип карти складу (SVG-план із секторами) збережено окремою вкладкою —
 * ідея локацій коробок на плані повернеться, коли коробки матимуть зони.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dropdown, Tooltip } from 'antd';
import {
  PlusOutlined, PrinterOutlined, ReloadOutlined, SearchOutlined, DownOutlined,
  LockOutlined, UnlockOutlined, CheckOutlined, DeleteOutlined, ExportOutlined, InboxOutlined,
} from '@ant-design/icons';
import MainLayout from '../layouts/MainLayout';
import ProductDetailsModal from '../components/products/ProductDetailsModal';
import WarehouseMapPrototype from './WarehouseMapPrototype';
import {
  warehouseService as ws, whErr, KIND_UA, CATEGORIES, actorName,
  type WhBox, type WhProduct, type WhEvent, type WhStatus,
} from '../services/warehouseService';
import { isDesktopShell, saveBlob } from '../services/imageTransfer';
import { confirmDialog, notify } from '../ui/feedback';
import { useIsActivePage } from '../contexts/ActivePageContext';

const money = (v: number | null | undefined) => (v == null ? '' : `${Math.round(v).toLocaleString('uk-UA')} ₴`);
const when = (iso: string) => {
  const d = new Date(iso);
  return `${d.toLocaleDateString('uk-UA')} ${d.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' })}`;
};

const CHIP = 'inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border';
const CHIP_MUTED = `${CHIP} border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300`;
const CHIP_WARN = `${CHIP} bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800`;
const CHIP_DARK = `${CHIP} bg-black text-white border-black dark:bg-white dark:text-black dark:border-white`;
const CHIP_ERR = `${CHIP} bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-800`;

const WarehousePage: React.FC = () => {
  const isActive = useIsActivePage();
  const [tab, setTab] = useState<'boxes' | 'map'>('boxes');
  const [status, setStatus] = useState<WhStatus | null>(null);
  const [boxes, setBoxes] = useState<WhBox[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [box, setBox] = useState<WhBox | null>(null);
  const [boxLoading, setBoxLoading] = useState(false);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [searchHits, setSearchHits] = useState<WhProduct[] | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const loadedOnce = useRef(false);

  const loadBoxes = useCallback(async () => {
    setLoading(true);
    try {
      const [st, list] = await Promise.all([ws.status(), ws.boxes().catch(() => [] as WhBox[])]);
      setStatus(st); setBoxes(list);
    } catch (e: any) { notify.error({ message: 'Склад недоступний', description: whErr(e) }); }
    finally { setLoading(false); }
  }, []);

  const loadBox = useCallback(async (code: string) => {
    setBoxLoading(true);
    try { setBox(await ws.box(code)); }
    catch (e: any) { notify.error({ message: `Коробка ${code}`, description: whErr(e) }); setBox(null); }
    finally { setBoxLoading(false); }
  }, []);

  useEffect(() => {
    if (!isActive || loadedOnce.current) return;
    loadedOnce.current = true;
    void loadBoxes();
  }, [isActive, loadBoxes]);

  useEffect(() => { if (selectedCode) void loadBox(selectedCode); else setBox(null); }, [selectedCode, loadBox]);

  // Живе оновлення: у хмару пишуть телефони працівників, тож поки вкладка
  // активна — тихо перечитуємо список кожні 10 с і при поверненні у вікно.
  // Без спінера (loading не чіпаємо), щоб список не «блимав».
  const silentRefresh = useCallback(async () => {
    try {
      const list = await ws.boxes();
      setBoxes(list);
      if (selectedCode) {
        const fresh = await ws.box(selectedCode);
        setBox(prev => (prev && prev.updated_at === fresh.updated_at && prev.units === fresh.units && prev.items === fresh.items ? prev : fresh));
      }
    } catch { /* хмара тимчасово недоступна — лишаємо як є */ }
  }, [selectedCode]);
  useEffect(() => {
    if (!isActive || tab !== 'boxes') return;
    const t = setInterval(() => { void silentRefresh(); }, 10_000);
    const onFocus = () => { void silentRefresh(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onFocus);
    return () => { clearInterval(t); window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus); };
  }, [isActive, tab, silentRefresh]);

  const refreshAll = useCallback(async () => {
    await loadBoxes();
    if (selectedCode) await loadBox(selectedCode);
  }, [loadBoxes, loadBox, selectedCode]);

  const doSearch = async () => {
    const q = search.trim();
    if (!q) { setSearchHits(null); return; }
    try { setSearchHits(await ws.search(q)); }
    catch (e: any) { notify.error({ message: 'Пошук', description: whErr(e) }); }
  };

  const visibleBoxes = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = boxes.filter(b => b.status !== 'archived');
    if (!f) return list;
    return list.filter(b => `${b.code} ${b.title || ''} ${b.location || ''}`.toLowerCase().includes(f));
  }, [boxes, filter]);

  const totals = useMemo(() => ({
    boxes: visibleBoxes.length,
    units: visibleBoxes.reduce((s, b) => s + b.units, 0),
    value: visibleBoxes.reduce((s, b) => s + b.value, 0),
    check: visibleBoxes.filter(b => b.needs_check).length,
  }), [visibleBoxes]);

  return (
    <MainLayout filterPanelContent={null} onRefresh={refreshAll} isRefreshing={loading} onResetFilters={() => { setFilter(''); setSearch(''); setSearchHits(null); }}>
      <div className="p-4 pb-10 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full min-h-[70vh]">
        {/* Шапка */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Склад</h1>
          <div className="flex items-center rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden text-sm">
            <button className={`px-3 py-1.5 ${tab === 'boxes' ? 'bg-black text-white dark:bg-white dark:text-black' : 'text-gray-600 dark:text-gray-300'}`} onClick={() => setTab('boxes')}>Коробки</button>
            <button className={`px-3 py-1.5 ${tab === 'map' ? 'bg-black text-white dark:bg-white dark:text-black' : 'text-gray-600 dark:text-gray-300'}`} onClick={() => setTab('map')} title="Прототип плану складу (ще без прив'язки коробок)">Карта · прототип</button>
          </div>
          {status && (
            <span className={status.reachable ? CHIP_MUTED : CHIP_ERR} title={status.message}>
              {status.reachable ? 'хмара · онлайн' : status.configured ? 'хмара недоступна' : 'не налаштовано'}
            </span>
          )}
          <div className="flex-1" />
          {tab === 'boxes' && (
            <>
              <form className="flex items-center gap-1" onSubmit={e => { e.preventDefault(); void doSearch(); }}>
                <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Де лежить #номер…"
                  className="w-48 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900" />
                <Button htmlType="submit" icon={<SearchOutlined />} />
              </form>
              <Button icon={<ReloadOutlined />} onClick={() => void refreshAll()} loading={loading}>Оновити</Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>Нова коробка</Button>
            </>
          )}
        </div>

        {tab === 'map' ? (
          <WarehouseMapPrototype />
        ) : (
          <>
            {searchHits && (
              <div className="mb-4 rounded-xl border border-gray-200 dark:border-gray-700 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-semibold">Де лежить «{search}» · {searchHits.length}</div>
                  <button className="text-xs text-gray-400 hover:text-gray-700" onClick={() => setSearchHits(null)}>сховати</button>
                </div>
                {searchHits.length === 0 && <div className="text-sm text-gray-400">Не знайдено</div>}
                <div className="grid gap-1">
                  {searchHits.map(p => (
                    <div key={p.id} className="flex items-center gap-3 text-sm">
                      <button className="font-semibold hover:underline" onClick={() => setDetailId(p.id)}>{p.number}</button>
                      <span className="text-gray-500">{p.size}{p.insole ? ` · ${p.insole} см` : ''} · {[p.brand, p.model].filter(Boolean).join(' ')}</span>
                      <span className="flex-1" />
                      {(p.locations || []).length === 0
                        ? <span className="text-gray-400">не в коробці</span>
                        : (p.locations || []).map(l => (
                          <button key={l.box_code} className={CHIP_DARK} onClick={() => setSelectedCode(l.box_code)}>{l.box_code}{l.qty > 1 ? ` ×${l.qty}` : ''}</button>
                        ))}
                      {p.available_qty <= 0 && <span className={CHIP_ERR}>продано</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)] gap-4">
              {/* Список коробок */}
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-2">
                  <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Фільтр: код, назва, місце"
                    className="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900" />
                </div>
                <div className="text-xs text-gray-400 mb-2">
                  {totals.boxes} коробок · {totals.units} шт · {money(totals.value)}{totals.check ? ` · перевірити: ${totals.check}` : ''}
                </div>
                <div className="grid gap-1.5 max-h-[70vh] overflow-y-auto pr-1">
                  {!loading && visibleBoxes.length === 0 && (
                    <div className="text-sm text-gray-400 py-6 text-center">
                      {status && !status.reachable ? status.message : 'Коробок ще нема. Створіть першу — або працівник створить зі сканера.'}
                    </div>
                  )}
                  {visibleBoxes.map(b => (
                    <button key={b.id} onClick={() => setSelectedCode(b.code)}
                      className={`text-left rounded-xl border px-3 py-2 transition-colors ${selectedCode === b.code ? 'border-black dark:border-white bg-gray-50 dark:bg-gray-900' : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900'}`}>
                      <div className="flex items-center gap-2">
                        <span className="text-lg font-extrabold w-12">{b.code}</span>
                        <span className="flex-1 min-w-0 truncate text-sm text-gray-800 dark:text-gray-100">{b.title || <span className="text-gray-400">без назви</span>}</span>
                        <span className="text-xs text-gray-500 whitespace-nowrap">{b.units} шт</span>
                      </div>
                      <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                        {b.location && <span className="text-xs text-gray-400 truncate">{b.location}</span>}
                        {b.status === 'sealed' && <span className={CHIP_MUTED}>запечатана</span>}
                        {b.needs_check && <span className={CHIP_WARN}>перевірити</span>}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              {/* Картка коробки */}
              <div className="min-w-0">
                {!selectedCode ? (
                  <div className="h-full min-h-[300px] flex items-center justify-center text-sm text-gray-400 rounded-xl border border-dashed border-gray-200 dark:border-gray-700">
                    Оберіть коробку ліворуч
                  </div>
                ) : box ? (
                  <BoxCard box={box} loading={boxLoading}
                    onChanged={async () => { await loadBox(box.code); await loadBoxes(); }}
                    onDeleted={async () => { setSelectedCode(null); await loadBoxes(); }}
                    onOpenProduct={id => setDetailId(id)} />
                ) : (
                  <div className="text-sm text-gray-400 p-6">{boxLoading ? 'Завантаження…' : 'Коробку не знайдено'}</div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {newOpen && (
        <NewBoxDialog onClose={() => setNewOpen(false)} onCreated={async b => { setNewOpen(false); await loadBoxes(); setSelectedCode(b.code); }} />
      )}
      <ProductDetailsModal productId={detailId} open={!!detailId} onClose={() => setDetailId(null)} />
    </MainLayout>
  );
};

/* ───────────────────────────── Картка коробки ────────────────────────────── */

const BoxCard: React.FC<{
  box: WhBox; loading: boolean;
  onChanged: () => Promise<void>; onDeleted: () => Promise<void>; onOpenProduct: (id: number) => void;
}> = ({ box, loading, onChanged, onDeleted, onOpenProduct }) => {
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [title, setTitle] = useState(box.title || '');
  const [loc, setLoc] = useState(box.location || '');
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [events, setEvents] = useState<WhEvent[] | null>(null);
  const [addQ, setAddQ] = useState('');
  const [addHits, setAddHits] = useState<WhProduct[] | null>(null);
  const [labelOpen, setLabelOpen] = useState(false);
  const contents = box.contents || [];

  useEffect(() => { setTitle(box.title || ''); setLoc(box.location || ''); setEdit(false); setSel(new Set()); setEvents(null); setAddHits(null); setAddQ(''); }, [box.code, box.updated_at, box.title, box.location]);

  const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
    setBusy(true);
    try { await fn(); if (okMsg) notify.success({ message: okMsg }); await onChanged(); }
    catch (e: any) { notify.error({ message: 'Не вдалося', description: whErr(e) }); }
    finally { setBusy(false); }
  };

  const addSearch = async () => {
    const q = addQ.trim();
    if (!q) return;
    try { setAddHits(await ws.search(q)); }
    catch (e: any) { notify.error({ message: 'Пошук', description: whErr(e) }); }
  };

  const packOne = async (p: WhProduct) => {
    setBusy(true);
    try {
      try { await ws.pack(box.code, p.id, 1); }
      catch (e: any) {
        const d = e?.response?.data?.detail;
        if (e?.response?.status === 409 && d?.code === 'elsewhere') {
          if (!(await confirmDialog({ title: 'Товар уже в іншій коробці', body: `${d.message}. Перенести в ${box.code}?`, okText: 'Перенести' }))) return;
          await ws.pack(box.code, p.id, 1, true);
        } else throw e;
      }
      notify.success({ message: `${p.number} → ${box.code}` });
      setAddHits(null); setAddQ('');
      await onChanged();
    } catch (e: any) { notify.error({ message: 'Не вдалося запакувати', description: whErr(e) }); }
    finally { setBusy(false); }
  };

  const unpackSelected = async () => {
    const ids = contents.filter(c => sel.has(c.item_id));
    if (ids.length === 0) return;
    if (!(await confirmDialog({ title: `Вийняти ${ids.length} поз. з ${box.code}?`, okText: 'Вийняти' }))) return;
    await run(async () => { for (const c of ids) await ws.unpackFrom(box.code, c.product_id); }, `Вийнято ${ids.length} поз.`);
  };

  const statusChip = box.status === 'sealed' ? <span className={CHIP_DARK}><LockOutlined className="mr-1" />запечатана</span>
    : box.status === 'archived' ? <span className={CHIP_ERR}>видалена</span> : <span className={CHIP_MUTED}>відкрита</span>;

  return (
    <div className={`rounded-xl border border-gray-200 dark:border-gray-700 ${loading ? 'opacity-60' : ''}`}>
      {/* Шапка коробки */}
      <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-start gap-3">
        <div className="text-3xl font-extrabold leading-none">{box.code}</div>
        <div className="flex-1 min-w-[200px]">
          {!edit ? (
            <div className="cursor-text" onClick={() => setEdit(true)} title="Натисніть, щоб змінити назву чи місце">
              <div className="text-base font-medium text-gray-900 dark:text-gray-100">{box.title || <span className="text-gray-400">без назви</span>}</div>
              <div className="text-sm text-gray-500">{box.location || <span className="text-gray-400">місце не вказано</span>}</div>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5 max-w-md">
              <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Назва (що всередині)" className="px-2 py-1 text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900" />
              <input value={loc} onChange={e => setLoc(e.target.value)} placeholder="Де стоїть (стелаж, полиця)" className="px-2 py-1 text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900" />
              <div className="flex gap-2">
                <Button size="small" type="primary" loading={busy} onClick={() => run(() => ws.patchBox(box.code, { title, location: loc }), 'Збережено')}>Зберегти</Button>
                <Button size="small" onClick={() => setEdit(false)}>Скасувати</Button>
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {statusChip}
            <span className={CHIP_MUTED}>{box.items} поз. · {box.units} шт</span>
            {box.value > 0 && <span className={CHIP_MUTED}>{money(box.value)}</span>}
            {box.needs_check && <span className={CHIP_WARN}>перевірити</span>}
            {box.created_by && <span className="text-[11px] text-gray-400 self-center">створив: {actorName(box.created_by)} · {when(box.created_at)}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button icon={<PrinterOutlined />} onClick={() => setLabelOpen(true)}>Етикетка</Button>
          {box.needs_check && <Button icon={<CheckOutlined />} loading={busy} onClick={() => run(() => ws.check(box.code), 'Коробку звірено')}>Звірено</Button>}
          {box.status === 'sealed'
            ? <Button icon={<UnlockOutlined />} loading={busy} onClick={() => run(() => ws.open(box.code), 'Відкрито')}>Відкрити</Button>
            : <Button icon={<LockOutlined />} loading={busy} disabled={box.status === 'archived'} onClick={() => run(() => ws.seal(box.code), 'Запечатано')}>Запечатати</Button>}
          <Dropdown trigger={['click']} menu={{
            items: [
              { key: 'unpack-all', icon: <ExportOutlined />, label: 'Розпакувати все', disabled: contents.length === 0 },
              { type: 'divider' },
              { key: 'delete', icon: <DeleteOutlined />, label: 'Видалити коробку', danger: true },
            ],
            onClick: async ({ key }) => {
              if (key === 'unpack-all') {
                if (await confirmDialog({ title: `Розпакувати всю коробку ${box.code}?`, body: `${box.units} шт стануть «без коробки».`, okText: 'Розпакувати' }))
                  await run(() => ws.unpackAll(box.code), 'Коробку розпаковано');
              } else if (key === 'delete') {
                const withItems = contents.length > 0;
                if (!(await confirmDialog({ title: `Видалити коробку ${box.code}?`, body: withItems ? `У ній ще ${box.units} шт — усе стане «без коробки». Історія збережеться.` : 'Історія збережеться.', okText: 'Видалити', kind: 'delete', danger: true }))) return;
                setBusy(true);
                try { await ws.deleteBox(box.code, withItems); notify.success({ message: `Коробку ${box.code} видалено` }); await onDeleted(); }
                catch (e: any) { notify.error({ message: 'Не вдалося видалити', description: whErr(e) }); }
                finally { setBusy(false); }
              }
            },
          }}>
            <Button>Ще <DownOutlined /></Button>
          </Dropdown>
        </div>
      </div>

      {/* Додати товар у коробку */}
      <div className="px-4 py-2 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center gap-2">
        <InboxOutlined className="text-gray-400" />
        <form className="flex items-center gap-1" onSubmit={e => { e.preventDefault(); void addSearch(); }}>
          <input value={addQ} onChange={e => setAddQ(e.target.value)} placeholder="Покласти товар: #номер"
            className="w-44 px-2 py-1 text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900" />
          <Button size="small" htmlType="submit" disabled={!addQ.trim() || busy}>Знайти</Button>
        </form>
        {addHits && (
          <div className="flex flex-wrap gap-1.5 items-center">
            {addHits.length === 0 && <span className="text-xs text-gray-400">не знайдено</span>}
            {addHits.map(p => (
              <Tooltip key={p.id} title={`${[p.brand, p.model, p.color].filter(Boolean).join(' · ')} · наявно ${p.available_qty}${(p.locations || []).length ? ` · у ${(p.locations || []).map(l => l.box_code).join(', ')}` : ''}`}>
                <button disabled={busy} onClick={() => void packOne(p)}
                  className={`${CHIP} ${p.available_qty <= 0 ? 'border-rose-200 text-rose-600' : 'border-gray-300 hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black'} py-1`}>
                  {p.number} {p.size}{p.available_qty <= 0 ? ' · продано' : ''}
                </button>
              </Tooltip>
            ))}
            <button className="text-xs text-gray-400" onClick={() => setAddHits(null)}>×</button>
          </div>
        )}
        <div className="flex-1" />
        {sel.size > 0 && <Button size="small" icon={<ExportOutlined />} loading={busy} onClick={() => void unpackSelected()}>Вийняти вибрані ({sel.size})</Button>}
      </div>

      {/* Вміст */}
      <div className="overflow-x-auto">
        {contents.length === 0 ? (
          <div className="p-6 text-sm text-gray-400 text-center">Порожня</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 w-8"><input type="checkbox" className="accent-black" checked={sel.size === contents.length} onChange={e => setSel(e.target.checked ? new Set(contents.map(c => c.item_id)) : new Set())} /></th>
                <th className="px-2 py-2 text-left font-medium">Товар</th>
                <th className="px-2 py-2 text-left font-medium">Розмір</th>
                <th className="px-2 py-2 text-left font-medium">Бренд · модель</th>
                <th className="px-2 py-2 text-left font-medium">Стан</th>
                <th className="px-2 py-2 text-right font-medium">Ціна</th>
                <th className="px-2 py-2 text-right font-medium">К-сть</th>
                <th className="px-2 py-2 text-left font-medium">Поклав</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {contents.map(c => {
                const p = c.product;
                return (
                  <tr key={c.item_id} className="border-t border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900">
                    <td className="px-3 py-1.5"><input type="checkbox" className="accent-black" checked={sel.has(c.item_id)} onChange={e => setSel(s => { const n = new Set(s); if (e.target.checked) n.add(c.item_id); else n.delete(c.item_id); return n; })} /></td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center gap-2">
                        {p.image ? <img src={p.image} alt="" className="w-9 h-9 rounded-md object-cover bg-gray-100" loading="lazy" /> : <div className="w-9 h-9 rounded-md bg-gray-100 dark:bg-gray-700" />}
                        <button className="font-semibold hover:underline" onClick={() => onOpenProduct(p.id)} disabled={!!p.missing}>{p.number}</button>
                        {p.missing && <span className={CHIP_WARN}>запис зник</span>}
                        {!p.missing && p.available_qty <= 0 && <span className={CHIP_ERR}>продано</span>}
                      </div>
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{p.size}{p.insole ? <span className="text-gray-400"> · {p.insole}</span> : null}</td>
                    <td className="px-2 py-1.5 text-gray-600 dark:text-gray-300">{[p.brand, p.model].filter(Boolean).join(' · ')}{p.color ? <span className="text-gray-400"> · {p.color}</span> : null}</td>
                    <td className="px-2 py-1.5 text-gray-600 dark:text-gray-300">{p.condition || ''}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">{money(p.price)}</td>
                    <td className="px-2 py-1.5 text-right font-semibold">{c.qty}</td>
                    <td className="px-2 py-1.5 text-xs text-gray-400 whitespace-nowrap">{actorName(c.packed_by)} · {when(c.packed_at)}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      <Button size="small" loading={busy} onClick={() => run(() => ws.unpackFrom(box.code, c.product_id, c.qty > 1 ? 1 : undefined), `${p.number} вийнято`)}>Вийняти{c.qty > 1 ? ' 1' : ''}</Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Журнал */}
      <div className="px-4 py-2 border-t border-gray-100 dark:border-gray-700">
        {events === null ? (
          <button className="text-xs text-gray-400 hover:text-gray-700" onClick={async () => { try { setEvents(await ws.events({ box: box.code, limit: 40 })); } catch { setEvents([]); } }}>Показати журнал коробки</button>
        ) : (
          <div className="text-xs">
            <div className="text-gray-400 mb-1">Журнал · {events.length}</div>
            {events.length === 0 && <div className="text-gray-400">порожньо</div>}
            {events.map(e => (
              <div key={e.id} className="flex gap-2 py-0.5 border-t border-gray-50 dark:border-gray-800">
                <span className="text-gray-400 w-28 shrink-0">{when(e.at)}</span>
                <span className="w-24 shrink-0 text-gray-500">{KIND_UA[e.kind] || e.kind}</span>
                <span className="flex-1 font-medium">{e.productnumber ? e.productnumber.replace(/^#/, '') : ''}{e.qty && e.qty > 1 ? ` ×${e.qty}` : ''}{e.details && (e.details as any).from?.length ? ` з ${(e.details as any).from.join(', ')}` : ''}</span>
                <span className="text-gray-400">{actorName(e.actor)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {labelOpen && <BoxLabelDialog box={box} onClose={() => setLabelOpen(false)} />}
    </div>
  );
};

/* ───────────────────────────── Нова коробка ──────────────────────────────── */

const NewBoxDialog: React.FC<{ onClose: () => void; onCreated: (b: WhBox) => Promise<void> }> = ({ onClose, onCreated }) => {
  const [cat, setCat] = useState('Z');
  const [code, setCode] = useState('');
  const [title, setTitle] = useState('');
  const [loc, setLoc] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { ws.nextCode(cat).then(setCode).catch(() => {}); }, [cat]);
  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-md rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 text-base font-semibold">Нова коробка</div>
        <div className="px-5 py-4 space-y-3">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Категорія</div>
            <div className="flex flex-wrap gap-1.5">
              {CATEGORIES.map(c => (
                <button key={c.letter} onClick={() => setCat(c.letter)}
                  className={`${CHIP} py-1 ${cat === c.letter ? 'bg-black text-white border-black dark:bg-white dark:text-black dark:border-white' : 'border-gray-200 dark:border-gray-700'}`}>{c.letter} · {c.label}</button>
              ))}
            </div>
          </div>
          <label className="block">
            <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Код (генерується за категорією, можна змінити)</div>
            <input value={code} onChange={e => setCode(e.target.value.toUpperCase())} className="w-full px-3 py-2 text-lg font-bold rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800" />
          </label>
          <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Назва (що всередині)" className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800" />
          <input value={loc} onChange={e => setLoc(e.target.value)} placeholder="Де стоїть (стелаж, полиця)" className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800" />
        </div>
        <div className="px-5 py-3 border-t border-gray-100 dark:border-gray-800 flex justify-end gap-2">
          <Button onClick={onClose} disabled={busy}>Скасувати</Button>
          <Button type="primary" loading={busy} disabled={!code.trim()} onClick={async () => {
            setBusy(true);
            try { const b = await ws.createBox({ code: code.trim(), category: cat, title: title.trim() || undefined, location: loc.trim() || undefined }); notify.success({ message: `Коробку ${b.code} створено` }); await onCreated(b); }
            catch (e: any) { notify.error({ message: 'Не вдалося створити', description: whErr(e) }); }
            finally { setBusy(false); }
          }}>Створити {code}</Button>
        </div>
      </div>
    </div>
  );
};

/* ───────────────────────────── Етикетка коробки ──────────────────────────── */

const BoxLabelDialog: React.FC<{ box: WhBox; onClose: () => void }> = ({ box, onClose }) => {
  const [busy, setBusy] = useState<'print' | 'save' | null>(null);
  const [copies, setCopies] = useState(1);
  const [cfg, setCfg] = useState<{ desktop: boolean; can_print: boolean; printers: { name: string; label?: string; kind?: string; reachable?: boolean }[]; preferred_printer: string | null } | null>(null);
  const [printer, setPrinter] = useState('');
  useEffect(() => {
    fetch('/api/labels/config').then(r => r.json()).then(c => { setCfg(c); setPrinter(c.preferred_printer || c.printers?.[0]?.name || ''); }).catch(() => setCfg({ desktop: false, can_print: false, printers: [], preferred_printer: null }));
  }, []);
  const run = async (mode: 'print' | 'save') => {
    setBusy(mode);
    try {
      if (!(await isDesktopShell())) {
        const blob = await ws.printLabel(box.code, 'download', null, copies) as Blob;
        saveBlob(blob, `BMS коробка ${box.code}.pdf`);
        notify.success({ message: 'PDF етикетки завантажено' });
      } else {
        const r = await ws.printLabel(box.code, mode, printer || null, copies) as { path: string; printed: boolean; printer: string | null; message: string };
        if (mode === 'print' && r.printed) notify.success({ message: `Етикетку ${box.code} надіслано на ${r.printer}` });
        else if (mode === 'print') notify.warning({ message: 'PDF збережено, але не надруковано', description: `${r.message} — ${r.path}`, duration: 8 });
        else notify.success({ message: 'PDF етикетки збережено', description: r.path });
      }
      onClose();
    } catch (e: any) { notify.error({ message: 'Етикетка', description: whErr(e) }); }
    finally { setBusy(null); }
  };
  const sel = cfg?.printers.find(p => p.name === printer);
  const canPrint = !!cfg?.desktop && !!printer && (sel?.kind === 'network' ? !!sel.reachable : !!cfg?.can_print);
  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-lg rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="text-base font-semibold">Етикетка коробки {box.code}</div>
          <div className="text-xs text-gray-400">Аркуш 100×100 мм · QR bms:b:{box.code} · код великим, щоб читався й після вицвітання термопаперу</div>
        </div>
        <div className="px-5 py-4 grid grid-cols-[220px_1fr] gap-4 items-start">
          <img src={ws.labelPngUrl(box.code)} alt="Етикетка" className="w-[220px] h-[220px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white" />
          <div className="space-y-3 text-sm">
            <label className="flex items-center gap-2">Копій
              <input type="number" min={1} max={10} value={copies} onChange={e => setCopies(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} className="w-16 px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800" />
            </label>
            {cfg?.desktop && (cfg.printers.length > 0 ? (
              <select value={printer} onChange={e => setPrinter(e.target.value)} className="w-full px-2 py-1.5 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
                {cfg.printers.map(p => <option key={p.name} value={p.name}>{p.label || p.name}{p.kind === 'network' && !p.reachable ? ' — не відповідає' : ''}</option>)}
              </select>
            ) : <div className="text-xs text-gray-500">Принтер не знайдено на цій машині — PDF збережеться у «Завантаження».</div>)}
          </div>
        </div>
        <div className="px-5 py-3 border-t border-gray-100 dark:border-gray-800 flex justify-end gap-2">
          <Button onClick={onClose} disabled={!!busy}>Закрити</Button>
          <Button onClick={() => void run('save')} loading={busy === 'save'} type={canPrint ? 'default' : 'primary'}>{cfg?.desktop ? 'Зберегти PDF' : 'Завантажити PDF'}</Button>
          {canPrint && <Button type="primary" icon={<PrinterOutlined />} loading={busy === 'print'} onClick={() => void run('print')}>Друк</Button>}
        </div>
      </div>
    </div>
  );
};

export default WarehousePage;
