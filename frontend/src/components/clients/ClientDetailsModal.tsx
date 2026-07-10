import React, { useEffect, useState } from 'react';
import {
  fetchClient,
  updateClient,
  fetchClientAddresses,
  createClientAddress,
  updateClientAddress,
  deleteClientAddress,
  setPrimaryClientAddress,
  importClientAddressesFromOrders,
  ClientAddress,
  fetchClientRelations,
  createClientRelation,
  updateClientRelation,
  deleteClientRelation,
  importClientRelationsFromOrders,
  fetchClients,
  ClientRelation,
  RelationType,
  fetchClientAliases,
  createClientAlias,
  deleteClientAlias,
  fetchClientFlags,
  dismissClientFlag,
  mergeClients,
  ClientAlias,
  ClientFlag,
} from '../../services/referenceService';
import ProductDetailsModal from '../products/ProductDetailsModal';
import ProductNumberLink from '../products/ProductNumberLink';
import { toast } from 'react-toastify';
import { CopyOnClick, OrderStatusBadge, PaymentStatusBadge } from '../common/displayHelpers';
import { DeliveryBadge } from '../common/DeliveryBadge';
import { confirmDialog } from '../../ui/feedback';

/* ── Типи ─────────────────────────────────────────────────────────────────── */
interface RecentOrder {
  id: number;
  order_date: string | null;
  total_amount: number | null;
  tracking_number: string | null;
  notes: string | null;
  sales_channel: string | null;
  order_status: string | null;
  payment_status: string | null;
  delivery_method: string | null;
  product_numbers: string;
  item_count: number;
}

interface ClientFull {
  id: number;
  first_name: string;
  last_name: string | null;
  middle_name: string | null;
  maiden_name: string | null;
  full_name: string;
  nickname: string | null;
  gender_id: number | null;
  phone_number: string | null;
  email: string | null;
  facebook: string | null;
  instagram: string | null;
  telegram: string | null;
  viber: string | null;
  olx: string | null;
  messenger: string | null;
  tiktok: string | null;
  city_of_residence: string | null;
  notes: string | null;
  // Статистика
  total_orders: number;
  confirmed_orders: number;
  cancelled_count: number;
  ignored_count: number;
  return_exchange_count: number;
  queue_count: number;
  gift_count: number;
  clarify_count: number;
  purchased_models: number;
  has_deferred: boolean;
  computed_total_amount: number;
  computed_avg_amount: number;
  computed_max_amount: number;
  computed_first_order: string | null;
  computed_last_order: string | null;
  rating: number | null;
  registration_date: string | null;
  created_at: string | null;
  client_discount: number | null;
  bonus_account: number | null;
  // Уподобання (агреговано з історії)
  top_brands: { name: string; cnt: number }[];
  top_types: { name: string; cnt: number }[];
  top_colors: { name: string; cnt: number }[];
  top_sizes_eu: { name: string; cnt: number }[];
  payment_split: { paid: number; unpaid: number; partial: number; total: number };
  // Адреси
  addresses?: ClientAddress[];
  // Звʼязки
  relations?: ClientRelation[];
  // Identity (Step 4)
  aliases?: ClientAlias[];
  flags?: ClientFlag[];
  has_active_flags?: boolean;
  manually_edited_at?: string | null;
  manually_edited_fields?: string | null;
  // Замовлення
  recent_orders: RecentOrder[];
}

interface Props {
  clientId: number | null;
  open: boolean;
  onClose: () => void;
}

/* ── Хелпери ──────────────────────────────────────────────────────────────── */
const fmtMoney = (n: number | null | undefined) => {
  if (!n) return '—';
  return new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(n);
};

const fmtDate = (d: string | null | undefined) => {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
};

const ratingColor = (r: number) => {
  if (r >= 7) return 'text-green-600 dark:text-green-400';
  if (r >= 4) return 'text-yellow-600 dark:text-yellow-400';
  return 'text-red-600 dark:text-red-400';
};

const ratingBg = (r: number) => {
  if (r >= 7) return 'bg-green-100 dark:bg-green-900/30 border-green-200 dark:border-green-700';
  if (r >= 4) return 'bg-yellow-100 dark:bg-yellow-900/30 border-yellow-200 dark:border-yellow-700';
  return 'bg-red-100 dark:bg-red-900/30 border-red-200 dark:border-red-700';
};

const orderStatusColor = (s: string | null) => {
  if (!s) return 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300';
  const low = s.toLowerCase();
  if (low.includes('підтвердж')) return 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300';
  if (low.includes('відміна')) return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300';
  if (low.includes('ігнор')) return 'bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-300';
  if (low.includes('поверн') || low.includes('обмін')) return 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300';
  if (low.includes('черз')) return 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300';
  if (low.includes('подарун')) return 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300';
  if (low.includes('уточн') || low.includes('фото')) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300';
  return 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300';
};

/* ── Компонент ────────────────────────────────────────────────────────────── */
const ClientDetailsModal: React.FC<Props> = ({ clientId, open, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [client, setClient] = useState<ClientFull | null>(null);
  const [activeTab, setActiveTab] = useState<'info' | 'orders'>('info');
  const [cardProductId, setCardProductId] = useState<number | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Partial<ClientFull>>({});
  // Адреси
  const [addresses, setAddresses] = useState<ClientAddress[]>([]);
  const [addrBusy, setAddrBusy] = useState(false);
  const [addrEditingId, setAddrEditingId] = useState<number | 'new' | null>(null);
  const [addrDraft, setAddrDraft] = useState<Partial<ClientAddress>>({});
  // Звʼязки
  const [relations, setRelations] = useState<ClientRelation[]>([]);
  const [relBusy, setRelBusy] = useState(false);
  const [relEditingId, setRelEditingId] = useState<number | null>(null);
  const [relDraft, setRelDraft] = useState<{ relation_type: RelationType; label: string }>({ relation_type: 'together', label: '' });
  // Додавання нового звʼязку — autocomplete по клієнтах
  const [showAddRel, setShowAddRel] = useState(false);
  const [newRelQuery, setNewRelQuery] = useState('');
  const [newRelResults, setNewRelResults] = useState<Array<{ id: number; full_name: string; phone_number?: string | null }>>([]);
  const [newRelSelected, setNewRelSelected] = useState<{ id: number; full_name: string } | null>(null);
  const [newRelDraft, setNewRelDraft] = useState<{ relation_type: RelationType; label: string; inverse_label: string }>({ relation_type: 'family', label: '', inverse_label: '' });
  const [newRelSearching, setNewRelSearching] = useState(false);
  // Identity (Step 4)
  const [aliases, setAliases] = useState<ClientAlias[]>([]);
  const [flags, setFlags] = useState<ClientFlag[]>([]);
  const [aliasBusy, setAliasBusy] = useState(false);
  const [aliasDraft, setAliasDraft] = useState<{ first_name: string; last_name: string; nickname: string }>({ first_name: '', last_name: '', nickname: '' });
  const [showAddAlias, setShowAddAlias] = useState(false);

  useEffect(() => {
    if (!open || !clientId) return;
    setLoading(true);
    setClient(null);
    setActiveTab('info');
    setEditMode(false);
    setDraft({});
    setAddresses([]);
    setAddrEditingId(null);
    setAddrDraft({});
    setRelations([]);
    setRelEditingId(null);
    setAliases([]);
    setFlags([]);
    setShowAddAlias(false);
    setAliasDraft({ first_name: '', last_name: '', nickname: '' });
    fetchClient(clientId)
      .then((data: any) => {
        setClient(data as ClientFull);
        if (Array.isArray(data?.addresses)) setAddresses(data.addresses as ClientAddress[]);
        if (Array.isArray(data?.relations)) setRelations(data.relations as ClientRelation[]);
        if (Array.isArray(data?.aliases)) setAliases(data.aliases as ClientAlias[]);
        if (Array.isArray(data?.flags)) setFlags(data.flags as ClientFlag[]);
      })
      .finally(() => setLoading(false));
  }, [open, clientId]);

  // ── Relations helpers ──────────────────────────────────────────────────────
  const reloadRelations = async () => {
    if (!clientId) return;
    try { setRelations(await fetchClientRelations(clientId)); }
    catch (e) { console.error(e); }
  };

  const startEditRel = (r: ClientRelation) => {
    setRelDraft({ relation_type: r.relation_type, label: r.label || '' });
    setRelEditingId(r.id);
  };
  const cancelEditRel = () => { setRelEditingId(null); };

  const saveRel = async () => {
    if (!clientId || relEditingId == null) return;
    setRelBusy(true);
    try {
      await updateClientRelation(clientId, relEditingId, {
        relation_type: relDraft.relation_type,
        label: relDraft.label || undefined,
        confirmed: true,
      });
      await reloadRelations();
      cancelEditRel();
      toast.success('Звʼязок оновлено');
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setRelBusy(false); }
  };

  const confirmRel = async (r: ClientRelation) => {
    if (!clientId) return;
    setRelBusy(true);
    try {
      await updateClientRelation(clientId, r.id, { confirmed: true });
      await reloadRelations();
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setRelBusy(false); }
  };

  const removeRel = async (r: ClientRelation) => {
    if (!clientId) return;
    if (!(await confirmDialog(`Видалити звʼязок з "${r.related_full_name || '#' + r.related_id}"? Дзеркальний звʼязок теж буде видалено.`))) return;
    setRelBusy(true);
    try {
      await deleteClientRelation(clientId, r.id, true);
      await reloadRelations();
      toast.success('Звʼязок видалено');
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setRelBusy(false); }
  };

  // Debounced пошук клієнтів для autocomplete
  useEffect(() => {
    if (!showAddRel) return;
    const q = newRelQuery.trim();
    if (q.length < 2) { setNewRelResults([]); return; }
    let cancelled = false;
    setNewRelSearching(true);
    const t = setTimeout(async () => {
      try {
        const data = await fetchClients(q, undefined, 1, 8);
        if (cancelled) return;
        // Виключаємо поточного клієнта та вже звʼязаних
        const existing = new Set(relations.map(r => r.related_id));
        const items = (data.items || [])
          .filter((c: any) => c.id !== clientId && !existing.has(c.id))
          .map((c: any) => ({
            id: c.id,
            full_name: [c.first_name, c.last_name].filter(Boolean).join(' ').trim() || c.nickname || `#${c.id}`,
            phone_number: c.phone_number,
          }));
        setNewRelResults(items);
      } catch (e) {
        if (!cancelled) setNewRelResults([]);
      } finally {
        if (!cancelled) setNewRelSearching(false);
      }
    }, 250);
    return () => { cancelled = true; clearTimeout(t); };
  }, [newRelQuery, showAddRel, clientId, relations]);

  const resetNewRel = () => {
    setShowAddRel(false);
    setNewRelQuery('');
    setNewRelResults([]);
    setNewRelSelected(null);
    setNewRelDraft({ relation_type: 'family', label: '', inverse_label: '' });
  };

  const submitNewRel = async () => {
    if (!clientId || !newRelSelected) return;
    setRelBusy(true);
    try {
      await createClientRelation(clientId, {
        related_id: newRelSelected.id,
        relation_type: newRelDraft.relation_type,
        label: newRelDraft.label || undefined,
        inverse_label: newRelDraft.inverse_label || undefined,
      });
      await reloadRelations();
      toast.success(`Звʼязок з "${newRelSelected.full_name}" створено`);
      resetNewRel();
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setRelBusy(false); }
  };

  const importRelations = async () => {
    if (!clientId) return;
    setRelBusy(true);
    try {
      const r = await importClientRelationsFromOrders(clientId);
      toast.success(`Імпорт: ${r.matches} збігів, ${r.pairs_processed} пар`);
      await reloadRelations();
    } catch (e: any) {
      toast.error(`Помилка імпорту: ${e?.response?.data?.detail || e.message}`);
    } finally { setRelBusy(false); }
  };

  // Клік по імені партнера → перейти в його картку
  const openPartner = (partnerId: number) => {
    // Перепризначаємо clientId через хак: емітимо подію через onClose+reopen?
    // Простіше — onClose, потім батьківський компонент сам відкриє нову. Поки що: просто toast.
    // Для UX мінімального етапу: відкриваємо в новій вкладці модального API через подію
    window.dispatchEvent(new CustomEvent('bms:open-client-card', { detail: { clientId: partnerId } }));
  };

  // ── Identity (Step 4) handlers ────────────────────────────────────────────
  const reloadAliasesFlags = async () => {
    if (!clientId) return;
    try {
      const [a, f] = await Promise.all([
        fetchClientAliases(clientId),
        fetchClientFlags(clientId, false),
      ]);
      setAliases(a);
      setFlags(f);
    } catch (e) { console.error(e); }
  };

  const addAlias = async () => {
    if (!clientId) return;
    const f = aliasDraft.first_name.trim();
    const l = aliasDraft.last_name.trim();
    const n = aliasDraft.nickname.trim();
    if (!f && !l && !n) {
      toast.warn('Введіть хоча б одне з полів');
      return;
    }
    setAliasBusy(true);
    try {
      await createClientAlias(clientId, {
        first_name: f || null,
        last_name: l || null,
        nickname: n || null,
      });
      setAliasDraft({ first_name: '', last_name: '', nickname: '' });
      setShowAddAlias(false);
      await reloadAliasesFlags();
      toast.success('Псевдонім додано');
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setAliasBusy(false); }
  };

  const removeAlias = async (a: ClientAlias) => {
    if (!clientId) return;
    if (!(await confirmDialog(`Видалити цей варіант імені? «${a.full_raw || a.nickname || a.first_name || ''}»`))) return;
    setAliasBusy(true);
    try {
      await deleteClientAlias(clientId, a.id);
      await reloadAliasesFlags();
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally { setAliasBusy(false); }
  };

  const dismissFlag = async (flag: ClientFlag, note: string = 'manual_dismiss') => {
    if (!clientId) return;
    try {
      await dismissClientFlag(clientId, flag.id, note);
      await reloadAliasesFlags();
      toast.success('Прапорець знято');
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    }
  };

  const mergeWith = async (peerId: number) => {
    if (!clientId) return;
    if (!(await confirmDialog(
      `Об'єднати клієнта #${clientId} → #${peerId}?\n\n` +
      `Усі замовлення, адреси, звʼязки та псевдоніми клієнта #${clientId} переїдуть до #${peerId}.\n` +
      `Клієнт #${clientId} буде ВИДАЛЕНО. Дія НЕ зворотна.`
    ))) return;
    try {
      const r = await mergeClients(clientId, peerId);
      toast.success(`Об'єднано: orders=${r.moved.orders}, addr=${r.moved.addresses}, rel=${r.moved.relations}, aliases=${r.moved.aliases}`);
      // Закриваємо картку source — він видалений, відкриваємо target
      onClose();
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('bms:open-client-card', { detail: { clientId: peerId } }));
      }, 150);
    } catch (e: any) {
      toast.error(`Merge failed: ${e?.response?.data?.detail || e.message}`);
    }
  };

  // ── Address helpers ────────────────────────────────────────────────────────
  const reloadAddresses = async () => {
    if (!clientId) return;
    try {
      const list = await fetchClientAddresses(clientId);
      setAddresses(list);
    } catch (e: any) {
      console.error(e);
    }
  };

  const startAddAddress = () => {
    setAddrDraft({ delivery_type: 'np_warehouse', is_primary: addresses.length === 0, is_active: true });
    setAddrEditingId('new');
  };

  const startEditAddress = (a: ClientAddress) => {
    setAddrDraft({ ...a });
    setAddrEditingId(a.id);
  };

  const cancelAddressEdit = () => { setAddrEditingId(null); setAddrDraft({}); };

  const saveAddress = async () => {
    if (!clientId) return;
    setAddrBusy(true);
    try {
      const payload: any = {};
      Object.entries(addrDraft).forEach(([k, v]) => {
        if (v === '' || v === undefined) payload[k] = null;
        else payload[k] = v;
      });
      if (addrEditingId === 'new') {
        await createClientAddress(clientId, payload);
        toast.success('Адресу додано');
      } else if (typeof addrEditingId === 'number') {
        await updateClientAddress(clientId, addrEditingId, payload);
        toast.success('Адресу оновлено');
      }
      await reloadAddresses();
      cancelAddressEdit();
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally {
      setAddrBusy(false);
    }
  };

  const removeAddress = async (a: ClientAddress) => {
    if (!clientId) return;
    if (!(await confirmDialog(`Видалити адресу "${a.label || a.city || '#' + a.id}"?`))) return;
    setAddrBusy(true);
    try {
      await deleteClientAddress(clientId, a.id);
      await reloadAddresses();
      toast.success('Адресу видалено');
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally {
      setAddrBusy(false);
    }
  };

  const makePrimary = async (a: ClientAddress) => {
    if (!clientId || a.is_primary) return;
    setAddrBusy(true);
    try {
      await setPrimaryClientAddress(clientId, a.id);
      await reloadAddresses();
    } catch (e: any) {
      toast.error(`Помилка: ${e?.response?.data?.detail || e.message}`);
    } finally {
      setAddrBusy(false);
    }
  };

  const importFromOrders = async () => {
    if (!clientId) return;
    setAddrBusy(true);
    try {
      const r = await importClientAddressesFromOrders(clientId);
      toast.success(`Імпортовано: ${r.imported}, пропущено дублів: ${r.skipped}`);
      await reloadAddresses();
    } catch (e: any) {
      toast.error(`Помилка імпорту: ${e?.response?.data?.detail || e.message}`);
    } finally {
      setAddrBusy(false);
    }
  };

  const startEdit = () => {
    if (!client) return;
    setDraft({
      first_name: client.first_name || '',
      last_name: client.last_name || '',
      middle_name: client.middle_name || '',
      maiden_name: client.maiden_name || '',
      nickname: client.nickname || '',
      phone_number: client.phone_number || '',
      email: client.email || '',
      city_of_residence: client.city_of_residence || '',
      notes: client.notes || '',
      client_discount: client.client_discount ?? null,
      bonus_account: client.bonus_account ?? null,
      facebook: client.facebook || '',
      instagram: client.instagram || '',
      telegram: client.telegram || '',
      viber: client.viber || '',
      messenger: client.messenger || '',
      tiktok: client.tiktok || '',
      olx: client.olx || '',
    });
    setEditMode(true);
  };

  const cancelEdit = () => { setDraft({}); setEditMode(false); };

  const saveEdit = async () => {
    if (!client || !clientId) return;
    setSaving(true);
    try {
      // Чистимо порожні рядки в null, щоб не сетити "" замість справжнього null
      const payload: any = {};
      Object.entries(draft).forEach(([k, v]) => {
        if (v === '' || v === undefined) payload[k] = null;
        else payload[k] = v;
      });
      await updateClient(clientId, payload);
      // Перезавантажуємо повну картку (з агрегатами)
      const fresh = await fetchClient(clientId);
      setClient(fresh as any);
      setEditMode(false);
      setDraft({});
      toast.success('Дані клієнта збережено');
    } catch (e: any) {
      console.error(e);
      toast.error(`Помилка збереження: ${e?.response?.data?.detail || e.message || 'unknown'}`);
    } finally {
      setSaving(false);
    }
  };

  const setDraftField = (k: keyof ClientFull, v: any) => setDraft(d => ({ ...d, [k]: v }));

  // Закриття по Escape. Гасимо подію (preventDefault+stopPropagation), щоб Esc ЛИШЕ
  // закривав картку, а не доходив до вебв'ю/ОС і «зменшував» вікно. Якщо відкрита
  // вкладена картка товару — її власний (capture) хендлер обробить Esc першим.
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (cardProductId !== null) return;   // вкладена картка товару сама обробить Esc
      e.preventDefault();
      e.stopPropagation();
      if (editMode) { cancelEdit(); return; }
      onClose();
    };
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [open, onClose, cardProductId, editMode]);

  if (!open) return null;

  const c = client;

  /* ── Соцмережі: збираємо список непорожніх каналів ── */
  const socialChannels = c ? [
    c.facebook && { icon: '📘', label: 'Facebook', value: c.facebook, href: c.facebook.startsWith('http') ? c.facebook : `https://${c.facebook}` },
    c.telegram && { icon: '✈️', label: 'Telegram', value: c.telegram, href: c.telegram.startsWith('http') || c.telegram.startsWith('@') ? (c.telegram.startsWith('@') ? `https://t.me/${c.telegram.slice(1)}` : c.telegram) : undefined },
    c.viber && { icon: '💜', label: 'Viber', value: c.viber },
    c.instagram && { icon: '📷', label: 'Instagram', value: c.instagram, href: c.instagram.startsWith('http') ? c.instagram : `https://instagram.com/${c.instagram}` },
    c.olx && { icon: '🛒', label: 'OLX', value: c.olx, href: c.olx.startsWith('http') ? c.olx : undefined },
    c.messenger && { icon: '💬', label: 'Messenger', value: c.messenger },
    c.tiktok && { icon: '🎵', label: 'TikTok', value: c.tiktok },
  ].filter(Boolean) as { icon: string; label: string; value: string; href?: string }[] : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-5xl mx-4 max-h-[92vh] overflow-hidden flex flex-col">

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
          </div>
        )}

        {/* Not found */}
        {!loading && !c && (
          <div className="flex items-center justify-center py-20 text-gray-400">Клієнт не знайдено</div>
        )}

        {/* Content */}
        {!loading && c && (
          <>
            {/* ── Header ── */}
            <div className="flex items-start justify-between px-6 pt-5 pb-3 border-b border-gray-100 dark:border-gray-700">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-3">
                  {/* Аватар-ініціали: реальне імʼя пріоритетне; nickname — fallback */}
                  {(() => {
                    const realName = [c.first_name, c.last_name].filter(Boolean).join(' ').trim();
                    const hasReal = !!realName;
                    const titleText = hasReal ? realName : (c.nickname || c.full_name?.trim() || 'Невідомий');
                    const initials = hasReal
                      ? `${(c.first_name?.[0] || '').toUpperCase()}${(c.last_name?.[0] || '').toUpperCase()}`
                      : (c.nickname?.[0] || '?').toUpperCase();
                    return (
                      <>
                        <div className={`shrink-0 w-12 h-12 rounded-full flex items-center justify-center text-white text-lg font-bold shadow-md ${hasReal ? 'bg-gradient-to-br from-blue-500 to-indigo-600' : 'bg-gradient-to-br from-gray-500 to-gray-600'}`}>
                          {initials}
                        </div>
                        <div className="min-w-0">
                          <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100 truncate">
                            {titleText}
                          </h2>
                          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                            <span className="text-xs text-gray-400 font-mono">ID: <CopyOnClick value={c.id} /></span>
                            {hasReal && c.nickname && (
                              <span className="text-xs text-gray-400">
                                нік: <span className="italic">«{c.nickname}»</span>
                              </span>
                            )}
                      {c.maiden_name && (
                        <span
                          className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-pink-50 text-pink-700 border border-pink-200 dark:bg-pink-900/30 dark:text-pink-300 dark:border-pink-800"
                          title="Дівоче прізвище — використовується для пошуку"
                        >
                          👰 {c.maiden_name}
                        </span>
                      )}
                      {!hasReal && c.nickname && (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-500 border border-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:border-gray-600">нікнейм</span>
                      )}
                      {c.rating != null && (
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold border ${ratingBg(c.rating)}`}>
                          <span className={ratingColor(c.rating)}>★ {c.rating.toFixed(1)}</span>
                        </span>
                      )}
                          </div>
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>
              <div className="shrink-0 ml-4 flex items-center gap-2">
                {!editMode ? (
                  <button
                    onClick={startEdit}
                    className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                  >
                    ✎ Редагувати
                  </button>
                ) : (
                  <>
                    <button
                      onClick={saveEdit}
                      disabled={saving}
                      className="px-3 py-1.5 text-sm rounded-lg bg-gray-900 text-white hover:bg-black disabled:opacity-50 transition-colors"
                    >
                      {saving ? 'Збереження…' : '✓ Зберегти'}
                    </button>
                    <button
                      onClick={cancelEdit}
                      disabled={saving}
                      className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                      Скасувати
                    </button>
                  </>
                )}
                <button
                  onClick={onClose}
                  className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors text-xl"
                  aria-label="Закрити"
                  tabIndex={0}
                >
                  ✕
                </button>
              </div>
            </div>

            {/* ── Tabs ── */}
            <div className="flex gap-1 px-6 pt-3 border-b border-gray-100 dark:border-gray-700">
              {(['info', 'orders'] as const).map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
                    activeTab === tab
                      ? 'bg-white dark:bg-gray-700 text-blue-600 dark:text-blue-400 border border-b-0 border-gray-200 dark:border-gray-600 -mb-px'
                      : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                  }`}
                  tabIndex={0}
                >
                  {tab === 'info' ? '👤 Профіль' : `📦 Замовлення (${c.total_orders})`}
                </button>
              ))}
            </div>

            {/* ── Body ── */}
            <div className="overflow-y-auto flex-1 p-6">

              {activeTab === 'info' && (
                <div className="space-y-6">

                  {/* ── Статистика: картки-лічильники ── */}
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Статистика замовлень</h3>
                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                      <StatCard label="Підтверджено" value={c.confirmed_orders} color="green" />
                      <StatCard label="В черзі" value={c.queue_count} color="blue" />
                      <StatCard label="Відміна" value={c.cancelled_count} color="red" />
                      <StatCard label="Ігнорування" value={c.ignored_count} color="gray" />
                      <StatCard label="Повернення / Обмін" value={c.return_exchange_count} color="orange" />
                      <StatCard label="Подарунки" value={c.gift_count} color="purple" />
                      <StatCard label="Уточнити" value={c.clarify_count} color="yellow" />
                      <StatCard label="Куплено моделей" value={c.purchased_models} color="indigo" />
                      <StatCard label="Всього замовлень" value={c.total_orders} color="slate" />
                    </div>
                  </div>

                  {/* ── Фінанси ── */}
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Фінанси</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="bg-gray-50 dark:bg-gray-700/40 rounded-lg px-4 py-3">
                        <div className="text-xs text-gray-400 mb-1">Загальна сума</div>
                        <div className="text-lg font-bold text-gray-900 dark:text-gray-100">{fmtMoney(c.computed_total_amount)}</div>
                      </div>
                      <div className="bg-gray-50 dark:bg-gray-700/40 rounded-lg px-4 py-3">
                        <div className="text-xs text-gray-400 mb-1">Середній чек</div>
                        <div className="text-lg font-bold text-gray-900 dark:text-gray-100">{fmtMoney(c.computed_avg_amount)}</div>
                      </div>
                      <div className="bg-gray-50 dark:bg-gray-700/40 rounded-lg px-4 py-3">
                        <div className="text-xs text-gray-400 mb-1">Макс. покупка</div>
                        <div className="text-lg font-bold text-gray-900 dark:text-gray-100">{fmtMoney(c.computed_max_amount)}</div>
                      </div>
                    </div>
                  </div>

                  {/* ── FLAGS банер (Step 4) ─────────────────────────────── */}
                  {flags && flags.length > 0 && (
                    <div className="space-y-2">
                      {flags.map(f => (
                        <FlagBanner
                          key={f.id}
                          flag={f}
                          onDismiss={() => dismissFlag(f, 'manual_dismiss')}
                          onMerge={(peerId) => mergeWith(peerId)}
                          onOpenPeer={openPartner}
                        />
                      ))}
                    </div>
                  )}

                  {/* ── УПОДОБАННЯ (auto-derived з історії) ── */}
                  {(c.top_brands?.length || c.top_types?.length || c.top_sizes_eu?.length || c.top_colors?.length) ? (
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">
                        Уподобання <span className="normal-case font-normal text-gray-400">— з історії замовлень</span>
                      </h3>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <PrefBlock title="Бренди"  items={c.top_brands} />
                        <PrefBlock title="Типи"    items={c.top_types} />
                        <PrefBlock title="Розміри (EU)" items={c.top_sizes_eu} />
                        <PrefBlock title="Кольори" items={c.top_colors} />
                      </div>
                      {c.payment_split && c.payment_split.total > 0 && (
                        <div className="mt-3 flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                          <span>💳 Оплати:</span>
                          <span className="text-green-600 dark:text-green-400">{c.payment_split.paid} оплачено</span>
                          {c.payment_split.partial > 0 && (
                            <span className="text-amber-600 dark:text-amber-400">{c.payment_split.partial} частково</span>
                          )}
                          <span className="text-red-500 dark:text-red-400">{c.payment_split.unpaid} не оплачено</span>
                          <span className="text-gray-400">з {c.payment_split.total}</span>
                        </div>
                      )}
                    </div>
                  ) : null}

                  {/* ── Двоколонковий layout: Контакти + Хронологія ── */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Контакти */}
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Контактна інформація</h3>
                      <div className="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        {editMode ? (
                          <>
                            <EditRow label="Імʼя" value={draft.first_name as string ?? ''} onChange={v => setDraftField('first_name', v)} />
                            <EditRow label="Прізвище" value={draft.last_name as string ?? ''} onChange={v => setDraftField('last_name', v)} />
                            <EditRow label="По батькові" value={draft.middle_name as string ?? ''} onChange={v => setDraftField('middle_name', v)} />
                            {/* Дівоче прізвище — для жіночих профілів. Показуємо
                                завжди, бо gender_id може бути не виставлений; пошук
                                по цьому полю працює незалежно від статі. */}
                            <EditRow label="👰 Дівоче прізвище" value={draft.maiden_name as string ?? ''} onChange={v => setDraftField('maiden_name', v)} />
                            <EditRow label="Нікнейм" value={draft.nickname as string ?? ''} onChange={v => setDraftField('nickname', v)} />
                            <EditRow label="📞 Телефон" value={draft.phone_number as string ?? ''} onChange={v => setDraftField('phone_number', v)} />
                            <EditRow label="✉️ Email"   value={draft.email as string ?? ''}        onChange={v => setDraftField('email', v)} />
                            <EditRow label="🏙️ Місто"  value={draft.city_of_residence as string ?? ''} onChange={v => setDraftField('city_of_residence', v)} />
                          </>
                        ) : (
                          <>
                            <InfoRow label="📞 Телефон" value={c.phone_number ? <CopyOnClick value={c.phone_number} /> : null} />
                            <InfoRow label="✉️ Email" value={c.email} />
                            <InfoRow label="🏙️ Місто" value={c.city_of_residence} />
                            {(c.client_discount != null || c.bonus_account != null) && (
                              <>
                                {c.client_discount != null && <InfoRow label="🎯 Знижка" value={`${c.client_discount}%`} />}
                                {c.bonus_account != null && <InfoRow label="💰 Бонуси" value={fmtMoney(c.bonus_account)} />}
                              </>
                            )}
                          </>
                        )}
                      </div>
                      {editMode && (
                        <div className="mt-3 grid grid-cols-2 gap-3">
                          <div className="bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
                            <label className="text-xs text-gray-400 dark:text-gray-500 block mb-1">🎯 Знижка %</label>
                            <input type="number" step="0.1" value={draft.client_discount ?? ''}
                              onChange={e => setDraftField('client_discount', e.target.value === '' ? null : Number(e.target.value))}
                              className="w-full px-2 py-1 text-sm border rounded bg-white dark:bg-gray-800 dark:border-gray-600" />
                          </div>
                          <div className="bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
                            <label className="text-xs text-gray-400 dark:text-gray-500 block mb-1">💰 Бонуси</label>
                            <input type="number" step="1" value={draft.bonus_account ?? ''}
                              onChange={e => setDraftField('bonus_account', e.target.value === '' ? null : Number(e.target.value))}
                              className="w-full px-2 py-1 text-sm border rounded bg-white dark:bg-gray-800 dark:border-gray-600" />
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Дати */}
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Хронологія</h3>
                      <div className="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        <InfoRow label="Перше замовлення" value={fmtDate(c.computed_first_order)} />
                        <InfoRow label="Останнє замовлення" value={fmtDate(c.computed_last_order)} />
                        <InfoRow label="Дата реєстрації" value={fmtDate(c.registration_date || c.created_at)} />
                        {c.has_deferred && (
                          <div className="flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 mt-1">
                            <span>⏳</span> <span>Є відкладені замовлення</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* ── Адреси доставки ── */}
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                        Адреси доставки <span className="normal-case font-normal text-gray-400">— для НП / Укрпошти</span>
                      </h3>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={importFromOrders}
                          disabled={addrBusy}
                          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                          title="Зібрати адреси з історії замовлень"
                        >
                          ↻ Імпорт з історії
                        </button>
                        <button
                          onClick={startAddAddress}
                          disabled={addrBusy || addrEditingId !== null}
                          className="text-xs px-2 py-1 rounded bg-gray-900 text-white hover:bg-black disabled:opacity-50"
                        >
                          + Додати
                        </button>
                      </div>
                    </div>

                    {addresses.length === 0 && addrEditingId !== 'new' && (
                      <div className="text-sm text-gray-400 italic bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        Немає збережених адрес. Натисніть «Імпорт з історії», щоб зібрати з минулих замовлень,
                        або «Додати», щоб ввести вручну.
                      </div>
                    )}

                    <div className="space-y-2">
                      {addresses.map(a => (
                        addrEditingId === a.id ? (
                          <AddressEditor
                            key={a.id}
                            value={addrDraft}
                            onChange={setAddrDraft}
                            onSave={saveAddress}
                            onCancel={cancelAddressEdit}
                            saving={addrBusy}
                          />
                        ) : (
                          <AddressRow
                            key={a.id}
                            a={a}
                            onEdit={() => startEditAddress(a)}
                            onDelete={() => removeAddress(a)}
                            onMakePrimary={() => makePrimary(a)}
                            disabled={addrBusy || addrEditingId !== null}
                          />
                        )
                      ))}
                      {addrEditingId === 'new' && (
                        <AddressEditor
                          value={addrDraft}
                          onChange={setAddrDraft}
                          onSave={saveAddress}
                          onCancel={cancelAddressEdit}
                          saving={addrBusy}
                        />
                      )}
                    </div>
                  </div>

                  {/* ── Звʼязки (родичі / друзі / разом замовляють) ── */}
                  <div>
                    <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-2">
                        Звʼязки <span className="normal-case font-normal text-gray-400">— замовляють разом / родичі / друзі</span>
                        {relations.filter(r => !r.confirmed).length > 0 && (
                          <span
                            className="normal-case text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700"
                            title="Звʼязки, авто-знайдені парсером з нотаток. Підтвердьте або видаліть, якщо помилково."
                          >
                            {relations.filter(r => !r.confirmed).length} непідтверджених
                          </span>
                        )}
                      </h3>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setShowAddRel(s => !s)}
                          disabled={relBusy}
                          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                        >
                          {showAddRel ? '✕ Скасувати' : '+ Звʼязати з клієнтом'}
                        </button>
                        <button
                          onClick={importRelations}
                          disabled={relBusy}
                          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                          title="Зібрати звʼязки з нотаток типу 'разом з …'"
                        >
                          ↻ Імпорт з історії
                        </button>
                      </div>
                    </div>

                    {showAddRel && (
                      <div className="mb-3 p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/40 space-y-2">
                        {!newRelSelected ? (
                          <>
                            <input
                              type="text"
                              autoFocus
                              placeholder="Імʼя, прізвище, нік, телефон…"
                              value={newRelQuery}
                              onChange={e => setNewRelQuery(e.target.value)}
                              className="w-full px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                            />
                            {newRelQuery.trim().length < 2 ? (
                              <div className="text-[11px] text-gray-400">Введіть мін. 2 символи для пошуку</div>
                            ) : newRelSearching ? (
                              <div className="text-[11px] text-gray-400">Пошук…</div>
                            ) : newRelResults.length === 0 ? (
                              <div className="text-[11px] text-gray-400">Нічого не знайдено</div>
                            ) : (
                              <ul className="max-h-48 overflow-y-auto rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 divide-y divide-gray-100 dark:divide-gray-700">
                                {newRelResults.map(r => (
                                  <li key={r.id}>
                                    <button
                                      type="button"
                                      onClick={() => setNewRelSelected({ id: r.id, full_name: r.full_name })}
                                      className="w-full text-left px-3 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-700"
                                    >
                                      <span className="font-medium">{r.full_name}</span>
                                      <span className="text-[11px] text-gray-400 ml-2">#{r.id}</span>
                                      {r.phone_number && <span className="text-[11px] text-gray-500 ml-2">📞 {r.phone_number}</span>}
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </>
                        ) : (
                          <>
                            <div className="text-sm flex items-center justify-between">
                              <span>Звʼязати з: <strong>{newRelSelected.full_name}</strong> <span className="text-[11px] text-gray-400">#{newRelSelected.id}</span></span>
                              <button type="button" onClick={() => setNewRelSelected(null)} className="text-[11px] text-gray-500 hover:text-gray-900 dark:hover:text-gray-200 underline">змінити</button>
                            </div>
                            <label className="block">
                              <span className="text-[11px] text-gray-400">Тип звʼязку</span>
                              <select
                                value={newRelDraft.relation_type}
                                onChange={e => setNewRelDraft(d => ({ ...d, relation_type: e.target.value as RelationType }))}
                                className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                              >
                                <option value="family">👨‍👩‍👧 Родичі</option>
                                <option value="spouse">💍 Подружжя</option>
                                <option value="friend">👯 Друзі</option>
                                <option value="together">🤝 Разом замовляють</option>
                                <option value="other">📌 Інше</option>
                              </select>
                            </label>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                              <label className="block">
                                <span className="text-[11px] text-gray-400">
                                  Хто <strong>{newRelSelected.full_name.split(' ')[0]}</strong> для цього клієнта?
                                </span>
                                <input
                                  type="text"
                                  placeholder="напр. «син», «подруга»"
                                  value={newRelDraft.label}
                                  onChange={e => setNewRelDraft(d => ({ ...d, label: e.target.value }))}
                                  className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                />
                              </label>
                              <label className="block">
                                <span className="text-[11px] text-gray-400">
                                  …і навпаки, хто цей клієнт для <strong>{newRelSelected.full_name.split(' ')[0]}</strong>?
                                </span>
                                <input
                                  type="text"
                                  placeholder="напр. «мати», «друг»"
                                  value={newRelDraft.inverse_label}
                                  onChange={e => setNewRelDraft(d => ({ ...d, inverse_label: e.target.value }))}
                                  className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                />
                              </label>
                            </div>
                            <div className="text-[10px] text-gray-400 italic">
                              Ярлики асиметричні: «син» Людмили — це «мати» для Івана. Якщо лишите порожнім — дзеркальний бік буде без ярлика, його можна заповнити пізніше в картці партнера.
                            </div>
                            <div className="flex justify-end gap-2 pt-1">
                              <button onClick={resetNewRel} disabled={relBusy} className="px-3 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700">Скасувати</button>
                              <button onClick={submitNewRel} disabled={relBusy} className="px-3 py-1 text-sm rounded bg-gray-900 text-white hover:bg-black disabled:opacity-50">{relBusy ? '…' : 'Створити звʼязок'}</button>
                            </div>
                          </>
                        )}
                      </div>
                    )}

                    {relations.length === 0 ? (
                      <div className="text-sm text-gray-400 italic bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        Немає виявлених звʼязків. Натисніть «Імпорт з історії», щоб знайти партнерів зі спільних замовлень («разом з …»).
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {relations.map(r => (
                          relEditingId === r.id ? (
                            <div key={r.id} className="p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 space-y-2">
                              <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                                {r.related_full_name || `#${r.related_id}`}
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                <label className="block">
                                  <span className="text-[11px] text-gray-400">Тип звʼязку</span>
                                  <select
                                    value={relDraft.relation_type}
                                    onChange={e => setRelDraft(d => ({ ...d, relation_type: e.target.value as RelationType }))}
                                    className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                  >
                                    <option value="together">🤝 Разом замовляють</option>
                                    <option value="family">👨‍👩‍👧 Родичі</option>
                                    <option value="friend">👯 Друзі</option>
                                    <option value="spouse">💍 Подружжя</option>
                                    <option value="other">📌 Інше</option>
                                  </select>
                                </label>
                                <label className="block">
                                  <span className="text-[11px] text-gray-400">Ярлик (напр. «мама», «подруга»)</span>
                                  <input
                                    type="text"
                                    value={relDraft.label}
                                    onChange={e => setRelDraft(d => ({ ...d, label: e.target.value }))}
                                    className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                  />
                                </label>
                              </div>
                              <div className="flex justify-end gap-2 pt-1">
                                <button onClick={cancelEditRel} disabled={relBusy} className="px-3 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700">Скасувати</button>
                                <button onClick={saveRel} disabled={relBusy} className="px-3 py-1 text-sm rounded bg-gray-900 text-white hover:bg-black disabled:opacity-50">{relBusy ? '…' : 'Зберегти'}</button>
                              </div>
                            </div>
                          ) : (
                            <RelationRow
                              key={r.id}
                              r={r}
                              onOpen={() => openPartner(r.related_id)}
                              onEdit={() => startEditRel(r)}
                              onConfirm={() => confirmRel(r)}
                              onDelete={() => removeRel(r)}
                              disabled={relBusy || relEditingId !== null}
                            />
                          )
                        ))}
                      </div>
                    )}
                  </div>

                  {/* ── Псевдоніми / Історія імен (Step 4) ── */}
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                        Псевдоніми <span className="normal-case font-normal text-gray-400">— всі варіанти імені, які пам'ятає система</span>
                      </h3>
                      <div className="flex items-center gap-2">
                        {(c.manually_edited_at) && (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700"
                            title={`Поля редаговані вручну: ${c.manually_edited_fields || '(невідомо)'}\nПарсер їх не перезатре.`}
                          >
                            🔒 Залочено
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => setShowAddAlias(s => !s)}
                          disabled={aliasBusy}
                          className="px-2 py-0.5 text-[11px] rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                        >
                          {showAddAlias ? '✕ Скасувати' : '+ Додати варіант'}
                        </button>
                      </div>
                    </div>

                    {showAddAlias && (
                      <div className="mb-3 p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/40 space-y-2">
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <input
                            type="text" placeholder="Ім'я"
                            value={aliasDraft.first_name}
                            onChange={e => setAliasDraft(d => ({ ...d, first_name: e.target.value }))}
                            className="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                          />
                          <input
                            type="text" placeholder="Прізвище"
                            value={aliasDraft.last_name}
                            onChange={e => setAliasDraft(d => ({ ...d, last_name: e.target.value }))}
                            className="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                          />
                          <input
                            type="text" placeholder="Нікнейм"
                            value={aliasDraft.nickname}
                            onChange={e => setAliasDraft(d => ({ ...d, nickname: e.target.value }))}
                            className="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                          />
                        </div>
                        <div className="flex justify-end">
                          <button
                            onClick={addAlias} disabled={aliasBusy}
                            className="px-3 py-1 text-sm rounded bg-gray-900 text-white hover:bg-black disabled:opacity-50"
                          >
                            {aliasBusy ? '…' : 'Додати'}
                          </button>
                        </div>
                      </div>
                    )}

                    {aliases.length === 0 ? (
                      <div className="text-sm text-gray-400 italic bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
                        Поки що тільки поточне ім'я. Парсер автоматично запам'ятовуватиме нові варіанти при майбутніх імпортах.
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {aliases.map(a => (
                          <div
                            key={a.id}
                            className="group inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm bg-gray-50 dark:bg-gray-700/40 rounded-lg border border-gray-200 dark:border-gray-600"
                            title={`Бачено: ${a.seen_count}× • Джерело: ${a.source}\nОстаннє: ${a.last_seen_at ? fmtDate(a.last_seen_at) : '—'}`}
                          >
                            <span className="font-medium text-gray-700 dark:text-gray-200">
                              {a.full_raw || [a.first_name, a.last_name].filter(Boolean).join(' ') || a.nickname || '—'}
                            </span>
                            {a.seen_count > 1 && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300">
                                ×{a.seen_count}
                              </span>
                            )}
                            <span className="text-[10px] text-gray-400">
                              {a.source === 'parser' ? '🔄' :
                               a.source === 'manual_edit_history' ? '✏️' :
                               a.source === 'merge' ? '🔗' :
                               a.source === 'initial_backfill' ? '📥' : ''}
                            </span>
                            {a.source !== 'initial_backfill' && (
                              <button
                                onClick={() => removeAlias(a)}
                                disabled={aliasBusy}
                                className="ml-1 opacity-0 group-hover:opacity-100 text-red-500 hover:text-red-700 text-xs transition-opacity"
                                title="Видалити цей варіант"
                              >
                                ✕
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* ── Соцмережі / Канали зв'язку ── */}
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Канали зв'язку</h3>
                    {editMode ? (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        <EditRow label="📘 Facebook"  value={draft.facebook as string ?? ''}  onChange={v => setDraftField('facebook', v)} />
                        <EditRow label="📷 Instagram" value={draft.instagram as string ?? ''} onChange={v => setDraftField('instagram', v)} />
                        <EditRow label="✈️ Telegram"  value={draft.telegram as string ?? ''}  onChange={v => setDraftField('telegram', v)} />
                        <EditRow label="💜 Viber"     value={draft.viber as string ?? ''}     onChange={v => setDraftField('viber', v)} />
                        <EditRow label="💬 Messenger" value={draft.messenger as string ?? ''} onChange={v => setDraftField('messenger', v)} />
                        <EditRow label="🎵 TikTok"    value={draft.tiktok as string ?? ''}    onChange={v => setDraftField('tiktok', v)} />
                        <EditRow label="🛒 OLX"       value={draft.olx as string ?? ''}       onChange={v => setDraftField('olx', v)} />
                      </div>
                    ) : socialChannels.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {socialChannels.map((ch, idx) => (
                          <div key={idx} className="inline-flex items-center gap-1.5 px-3 py-2 bg-gray-50 dark:bg-gray-700/40 rounded-lg border border-gray-200 dark:border-gray-600 text-sm max-w-[320px]">
                            <span>{ch.icon}</span>
                            <span className="font-medium text-gray-700 dark:text-gray-300">{ch.label}</span>
                            {ch.href ? (
                              <a href={ch.href} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:text-blue-600 truncate text-xs ml-1" title={ch.value}>
                                {ch.value.length > 35 ? ch.value.slice(0, 35) + '…' : ch.value}
                              </a>
                            ) : (
                              <span className="text-gray-500 dark:text-gray-400 truncate text-xs ml-1" title={ch.value}>
                                {ch.value.length > 35 ? ch.value.slice(0, 35) + '…' : ch.value}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-gray-400 italic">Немає прив'язаних соцмереж чи месенджерів</p>
                    )}
                  </div>

                  {/* ── Нотатки ── */}
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">Нотатки</h3>
                    {editMode ? (
                      <textarea
                        value={(draft.notes as string) ?? ''}
                        onChange={e => setDraftField('notes', e.target.value)}
                        rows={4}
                        className="w-full text-sm px-3 py-2 rounded-lg bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 focus:outline-none"
                        placeholder="Особливі побажання, історія взаємодії, нагадування…"
                      />
                    ) : c.notes ? (
                      <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap bg-amber-50 dark:bg-amber-900/20 rounded-lg px-4 py-3 border border-amber-100 dark:border-amber-800/30">
                        {c.notes}
                      </p>
                    ) : (
                      <p className="text-sm text-gray-400 italic">Немає нотаток</p>
                    )}
                  </div>
                </div>
              )}

              {/* ── Tab: Замовлення ── */}
              {activeTab === 'orders' && (
                <div>
                  {c.recent_orders.length === 0 ? (
                    <div className="text-center py-12 text-gray-400">Замовлень не знайдено</div>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
                      <table className="w-full text-sm [&_th]:text-center [&_td]:text-center">
                        <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                          <tr>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Дата</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Статус</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Товари</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Сума</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Оплата</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">Доставка</th>
                            <th className="px-3 py-2 font-semibold text-gray-600 dark:text-gray-300">ТТН</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                          {c.recent_orders.map(o => (
                            <tr key={o.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                              <td className="px-3 py-2 whitespace-nowrap text-gray-600 dark:text-gray-300 text-xs">
                                {fmtDate(o.order_date)}
                              </td>
                              <td className="px-3 py-2">
                                {o.order_status ? <OrderStatusBadge name={o.order_status} /> : <span className="text-gray-300">—</span>}
                              </td>
                              <td className="px-3 py-2 text-gray-700 dark:text-gray-300 max-w-[200px]">
                                <span className="block truncate text-xs font-mono" title={o.product_numbers}>
                                  {o.product_numbers ? (
                                    o.product_numbers.split(',').map((raw, i, arr) => {
                                      const num = raw.trim();
                                      return num ? (
                                        <React.Fragment key={`${o.id}-${i}`}>
                                          <ProductNumberLink productNumber={num} onOpen={setCardProductId} />
                                          {i < arr.length - 1 && <span>, </span>}
                                        </React.Fragment>
                                      ) : null;
                                    })
                                  ) : '—'}
                                </span>
                                {o.item_count > 1 && (
                                  <span className="text-[10px] text-gray-400">({o.item_count} шт.)</span>
                                )}
                              </td>
                              <td className="px-3 py-2 font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">
                                {o.total_amount != null
                                  ? <CopyOnClick value={String(o.total_amount)} display={<>{fmtMoney(o.total_amount)}</>} />
                                  : <span className="text-gray-300">—</span>}
                              </td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                                <PaymentStatusBadge name={o.payment_status} />
                              </td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400"><DeliveryBadge name={o.delivery_method} height={18} /></td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 font-mono max-w-[140px]" title={o.tracking_number || ''}>
                                {o.tracking_number ? <CopyOnClick value={o.tracking_number} groupDigits /> : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
      <ProductDetailsModal
        productId={cardProductId}
        open={cardProductId !== null}
        onClose={() => setCardProductId(null)}
      />
    </div>
  );
};

/* ── Допоміжні підкомпоненти ──────────────────────────────────────────────── */
const InfoRow: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <div className="flex items-baseline gap-2 py-0.5">
    <span className="text-xs text-gray-400 dark:text-gray-500 min-w-[130px] shrink-0">{label}</span>
    <span className="text-sm text-gray-800 dark:text-gray-200 break-words">{value || <span className="text-gray-300 dark:text-gray-600">—</span>}</span>
  </div>
);

const EditRow: React.FC<{ label: string; value: string; onChange: (v: string) => void; placeholder?: string }> = ({ label, value, onChange, placeholder }) => (
  <div className="flex items-center gap-2 py-0.5">
    <span className="text-xs text-gray-400 dark:text-gray-500 min-w-[130px] shrink-0">{label}</span>
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={e => onChange(e.target.value)}
      className="flex-1 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 focus:outline-none focus:border-gray-500 dark:focus:border-gray-400"
    />
  </div>
);

const PrefBlock: React.FC<{ title: string; items?: { name: string; cnt: number }[] }> = ({ title, items }) => {
  if (!items || items.length === 0) {
    return (
      <div className="bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
        <div className="text-xs text-gray-400 dark:text-gray-500 mb-1.5">{title}</div>
        <div className="text-xs italic text-gray-300 dark:text-gray-600">— немає даних</div>
      </div>
    );
  }
  const max = Math.max(...items.map(i => i.cnt));
  return (
    <div className="bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
      <div className="text-xs text-gray-400 dark:text-gray-500 mb-1.5">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => {
          // Чим більший cnt — тим темніший пілюль
          const intensity = Math.round((it.cnt / max) * 100);
          const cls = intensity >= 67
            ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
            : intensity >= 34
              ? 'bg-gray-300 text-gray-800 dark:bg-gray-600 dark:text-gray-100'
              : 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300';
          return (
            <span key={i} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
              <span>{it.name}</span>
              <span className="opacity-70 text-[10px]">×{it.cnt}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
};

const colorMap: Record<string, string> = {
  green: 'bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-300 dark:border-green-800',
  blue: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/20 dark:text-blue-300 dark:border-blue-800',
  red: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800',
  gray: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-700/30 dark:text-gray-300 dark:border-gray-600',
  orange: 'bg-orange-50 text-orange-700 border-orange-200 dark:bg-orange-900/20 dark:text-orange-300 dark:border-orange-800',
  purple: 'bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-900/20 dark:text-purple-300 dark:border-purple-800',
  yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-300 dark:border-yellow-800',
  indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/20 dark:text-indigo-300 dark:border-indigo-800',
  slate: 'bg-slate-50 text-slate-700 border-slate-200 dark:bg-slate-700/30 dark:text-slate-300 dark:border-slate-600',
};

const StatCard: React.FC<{ label: string; value: number; color: string }> = ({ label, value, color }) => (
  <div className={`rounded-lg border px-3 py-2 ${colorMap[color] || colorMap.gray}`}>
    <div className="text-2xl font-bold">{value}</div>
    <div className="text-[11px] leading-tight mt-0.5 opacity-80">{label}</div>
  </div>
);

/* ── Адреси: типи + UI ────────────────────────────────────────────────────── */
const DELIVERY_TYPES: { value: string; label: string; icon: string }[] = [
  { value: 'np_warehouse', label: 'Нова Пошта — відділення', icon: '📦' },
  { value: 'np_postomat',  label: 'Нова Пошта — поштомат',   icon: '🗄️' },
  { value: 'np_courier',   label: 'Нова Пошта — кур’єр',     icon: '🚚' },
  { value: 'up_warehouse', label: 'Укрпошта — відділення',   icon: '✉️' },
  { value: 'up_courier',   label: 'Укрпошта — кур’єр',       icon: '📮' },
  { value: 'pickup',       label: 'Самовивіз',               icon: '🏬' },
  { value: 'other',        label: 'Інше',                    icon: '📍' },
];

const deliveryLabel = (t?: string | null) => DELIVERY_TYPES.find(d => d.value === t) || DELIVERY_TYPES[6];

const formatAddress = (a: ClientAddress): string => {
  const t = a.delivery_type || '';
  const parts: string[] = [];
  if (a.city) parts.push(a.city);
  if (t.includes('warehouse') || t.includes('postomat')) {
    if (a.warehouse_number) parts.push(`відд. №${a.warehouse_number}`);
  } else if (a.street || a.building) {
    const street = [a.street, a.building, a.apartment ? `кв. ${a.apartment}` : null].filter(Boolean).join(' ');
    if (street) parts.push(street);
  }
  if (a.postal_code) parts.push(a.postal_code);
  return parts.join(', ') || '—';
};

const AddressRow: React.FC<{
  a: ClientAddress;
  onEdit: () => void;
  onDelete: () => void;
  onMakePrimary: () => void;
  disabled?: boolean;
}> = ({ a, onEdit, onDelete, onMakePrimary, disabled }) => {
  const dt = deliveryLabel(a.delivery_type);
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border ${a.is_primary ? 'bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800/40' : 'bg-gray-50 dark:bg-gray-700/30 border-gray-200 dark:border-gray-600'} ${!a.is_active ? 'opacity-60' : ''}`}>
      <button
        onClick={onMakePrimary}
        disabled={disabled || a.is_primary}
        title={a.is_primary ? 'Основна адреса' : 'Зробити основною'}
        className={`shrink-0 text-xl leading-none mt-0.5 ${a.is_primary ? 'text-amber-500' : 'text-gray-300 hover:text-amber-400'} disabled:cursor-default`}
      >
        ★
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center flex-wrap gap-2">
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{dt.icon} {dt.label}</span>
          {a.label && <span className="text-xs px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200">{a.label}</span>}
          {!a.is_active && <span className="text-xs px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-500">архів</span>}
          {a.usage_count > 0 && <span className="text-[11px] text-gray-400">×{a.usage_count}</span>}
          {a.source && a.source !== 'manual' && (
            <span className="text-[11px] text-gray-400" title={`Джерело: ${a.source}`}>auto</span>
          )}
        </div>
        <div className="text-sm text-gray-700 dark:text-gray-300 mt-0.5">{formatAddress(a)}</div>
        {(a.recipient_name || a.recipient_phone) && (
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {a.recipient_name || '—'}{a.recipient_phone ? `, ${a.recipient_phone}` : ''}
          </div>
        )}
        {a.notes && <div className="text-xs italic text-gray-500 dark:text-gray-400 mt-0.5">{a.notes}</div>}
      </div>
      <div className="shrink-0 flex flex-col gap-1">
        <button
          onClick={onEdit}
          disabled={disabled}
          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
        >
          ✎
        </button>
        <button
          onClick={onDelete}
          disabled={disabled}
          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
        >
          ✕
        </button>
      </div>
    </div>
  );
};

const AddressEditor: React.FC<{
  value: Partial<ClientAddress>;
  onChange: (v: Partial<ClientAddress>) => void;
  onSave: () => void;
  onCancel: () => void;
  saving?: boolean;
}> = ({ value, onChange, onSave, onCancel, saving }) => {
  const set = (k: keyof ClientAddress, v: any) => onChange({ ...value, [k]: v });
  const t = value.delivery_type || 'np_warehouse';
  const isWarehouse = t.includes('warehouse') || t.includes('postomat');
  const isCourier = t.includes('courier') || t === 'other';

  return (
    <div className="p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <label className="block">
          <span className="text-[11px] text-gray-400">Тип доставки</span>
          <select
            value={t}
            onChange={e => set('delivery_type', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          >
            {DELIVERY_TYPES.map(d => (
              <option key={d.value} value={d.value}>{d.icon} {d.label}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-[11px] text-gray-400">Підпис (напр. «Дім», «Офіс»)</span>
          <input
            type="text"
            value={value.label || ''}
            onChange={e => set('label', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          />
        </label>
        <label className="block">
          <span className="text-[11px] text-gray-400">Імʼя одержувача</span>
          <input
            type="text"
            value={value.recipient_name || ''}
            onChange={e => set('recipient_name', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          />
        </label>
        <label className="block">
          <span className="text-[11px] text-gray-400">Телефон одержувача</span>
          <input
            type="text"
            value={value.recipient_phone || ''}
            onChange={e => set('recipient_phone', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-[11px] text-gray-400">Місто</span>
          <input
            type="text"
            value={value.city || ''}
            onChange={e => set('city', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          />
        </label>
        {isWarehouse && (
          <label className="block sm:col-span-2">
            <span className="text-[11px] text-gray-400">Номер відділення / поштомату</span>
            <input
              type="text"
              value={value.warehouse_number || ''}
              onChange={e => set('warehouse_number', e.target.value)}
              className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
            />
          </label>
        )}
        {isCourier && (
          <>
            <label className="block">
              <span className="text-[11px] text-gray-400">Вулиця</span>
              <input
                type="text"
                value={value.street || ''}
                onChange={e => set('street', e.target.value)}
                className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-[11px] text-gray-400">Будинок</span>
                <input
                  type="text"
                  value={value.building || ''}
                  onChange={e => set('building', e.target.value)}
                  className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                />
              </label>
              <label className="block">
                <span className="text-[11px] text-gray-400">Квартира</span>
                <input
                  type="text"
                  value={value.apartment || ''}
                  onChange={e => set('apartment', e.target.value)}
                  className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                />
              </label>
            </div>
          </>
        )}
        <label className="block">
          <span className="text-[11px] text-gray-400">Поштовий індекс</span>
          <input
            type="text"
            value={value.postal_code || ''}
            onChange={e => set('postal_code', e.target.value)}
            className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
          />
        </label>
      </div>
      <label className="block">
        <span className="text-[11px] text-gray-400">Нотатки</span>
        <textarea
          value={value.notes || ''}
          onChange={e => set('notes', e.target.value)}
          rows={2}
          className="w-full mt-0.5 px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
        />
      </label>
      <div className="flex items-center gap-4 text-sm">
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={!!value.is_primary}
            onChange={e => set('is_primary', e.target.checked)}
          />
          <span>Основна</span>
        </label>
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={value.is_active !== false}
            onChange={e => set('is_active', e.target.checked)}
          />
          <span>Активна</span>
        </label>
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onCancel}
          disabled={saving}
          className="px-3 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          Скасувати
        </button>
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-1 text-sm rounded bg-gray-900 text-white hover:bg-black disabled:opacity-50"
        >
          {saving ? 'Збереження…' : 'Зберегти'}
        </button>
      </div>
    </div>
  );
};

/* ── Relations: row component ─────────────────────────────────────────────── */
const RELATION_LABELS: Record<RelationType, { icon: string; label: string }> = {
  together: { icon: '🤝', label: 'Разом замовляють' },
  family:   { icon: '👨‍👩‍👧', label: 'Родичі' },
  friend:   { icon: '👯', label: 'Друзі' },
  spouse:   { icon: '💍', label: 'Подружжя' },
  other:    { icon: '📌', label: 'Інше' },
};

const fmtRelDate = (d?: string | null) => {
  if (!d) return '';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
};

const RelationRow: React.FC<{
  r: ClientRelation;
  onOpen: () => void;
  onEdit: () => void;
  onConfirm: () => void;
  onDelete: () => void;
  disabled?: boolean;
}> = ({ r, onOpen, onEdit, onConfirm, onDelete, disabled }) => {
  const t = RELATION_LABELS[r.relation_type] || RELATION_LABELS.other;
  const isAuto = r.source === 'order_import' && !r.confirmed;
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border ${isAuto ? 'bg-gray-50 dark:bg-gray-700/30 border-gray-200 dark:border-gray-600 border-dashed' : 'bg-gray-50 dark:bg-gray-700/30 border-gray-200 dark:border-gray-600'}`}>
      <div className="shrink-0 text-xl leading-none mt-0.5">{t.icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center flex-wrap gap-2">
          <button
            onClick={onOpen}
            className="text-sm font-semibold text-gray-800 dark:text-gray-100 hover:underline"
            title="Відкрити картку"
          >
            {r.related_full_name || `#${r.related_id}`}
          </button>
          {r.label && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200">{r.label}</span>
          )}
          <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">{t.label}</span>
          {r.joint_orders > 0 && (
            <span className="text-[11px] text-gray-400">×{r.joint_orders} замовл.</span>
          )}
          {isAuto && (
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300" title="Авто з парсера, ще не підтверджено">
              auto
            </span>
          )}
        </div>
        {r.last_order_date && (
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Останнє спільне: {fmtRelDate(r.last_order_date)}
          </div>
        )}
        {r.notes && <div className="text-xs italic text-gray-500 dark:text-gray-400 mt-0.5">{r.notes}</div>}
      </div>
      <div className="shrink-0 flex flex-col gap-1">
        {isAuto && (
          <button
            onClick={onConfirm}
            disabled={disabled}
            className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 disabled:opacity-50"
            title="Підтвердити цей звʼязок"
          >
            ✓
          </button>
        )}
        <button
          onClick={onEdit}
          disabled={disabled}
          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
          title="Редагувати тип/ярлик"
        >
          ✎
        </button>
        <button
          onClick={onDelete}
          disabled={disabled}
          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
          title="Видалити"
        >
          ✕
        </button>
      </div>
    </div>
  );
};

/* ── Flag banner (Step 4) ─────────────────────────────────────────────────── */
const FLAG_LABELS: Record<string, { icon: string; title: string; tone: 'warn' | 'error' | 'info' }> = {
  possible_duplicate:        { icon: '⚠️', title: 'Можливий дублікат',  tone: 'warn' },
  ambiguous_name_at_parse:   { icon: '❓', title: 'Неоднозначне ім\'я при парсингу', tone: 'warn' },
  phone_mismatch_with_alias: { icon: '☎️', title: 'Конфлікт сильного сигналу',     tone: 'error' },
  merged_into:               { icon: '🔗', title: 'Об\'єднано з іншим клієнтом',   tone: 'info' },
};

const FlagBanner: React.FC<{
  flag: ClientFlag;
  onDismiss: () => void;
  onMerge: (peerId: number) => void;
  onOpenPeer: (peerId: number) => void;
}> = ({ flag, onDismiss, onMerge, onOpenPeer }) => {
  const meta = FLAG_LABELS[flag.flag_type] || { icon: '🚩', title: flag.flag_type, tone: 'warn' as const };
  const tones: Record<string, string> = {
    warn:  'bg-amber-50 dark:bg-amber-900/20 border-amber-300 dark:border-amber-700 text-amber-900 dark:text-amber-200',
    error: 'bg-red-50 dark:bg-red-900/20 border-red-300 dark:border-red-700 text-red-900 dark:text-red-200',
    info:  'bg-blue-50 dark:bg-blue-900/20 border-blue-300 dark:border-blue-700 text-blue-900 dark:text-blue-200',
  };
  const peers = flag.peer_clients || [];
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border ${tones[meta.tone]}`}>
      <div className="text-xl shrink-0 leading-none mt-0.5">{meta.icon}</div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">
          {meta.title}
        </div>
        {flag.details && (
          <div className="text-xs opacity-80 mt-0.5">{flag.details}</div>
        )}
        {peers.length > 0 && (
          <div className="mt-2 space-y-1.5">
            <span className="text-xs opacity-70">Перевір з:</span>
            <div className="flex flex-col gap-1.5">
              {peers.map((p: any) => {
                const channels: string[] = [];
                if (p.phone) channels.push(`📞 ${p.phone}`);
                if (p.facebook) channels.push(`FB: ${(p.facebook || '').replace(/^https?:\/\//,'').replace(/^www\./,'').replace('facebook.com/','').slice(0, 40)}`);
                if (p.telegram) channels.push(`TG: ${p.telegram}`);
                if (p.instagram) channels.push(`IG: ${(p.instagram || '').replace('instagram.com/','')}`);
                if (p.email) channels.push(`✉️ ${p.email}`);
                const fmt = (s: string | null | undefined) => s ? new Date(s).toLocaleDateString('uk-UA', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—';
                const money = p.total_amount ? new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(p.total_amount) : null;
                return (
                  <div key={p.id} className="rounded border border-current/20 bg-white/60 dark:bg-gray-800/60 p-2">
                    <div className="flex items-start justify-between gap-2">
                      <button
                        onClick={() => onOpenPeer(p.id)}
                        className="text-xs font-semibold hover:underline text-left"
                        title="Відкрити картку"
                      >
                        {p.full_name || (p.nickname ? `«${p.nickname}»` : `#${p.id}`)}
                        <span className="ml-1 opacity-60 font-normal">#{p.id}</span>
                        {p.nickname && p.full_name && <span className="ml-1 opacity-70 font-normal">({p.nickname})</span>}
                      </button>
                      {flag.flag_type !== 'merged_into' && (
                        <button
                          onClick={() => onMerge(p.id)}
                          className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-current/10 hover:bg-current/20"
                          title="Об'єднати поточного клієнта в цього (поточний буде видалено)"
                        >
                          🔗 Об'єднати
                        </button>
                      )}
                    </div>
                    {channels.length > 0 && (
                      <div className="text-[10px] opacity-80 mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
                        {channels.map((c, i) => <span key={i}>{c}</span>)}
                      </div>
                    )}
                    <div className="text-[10px] opacity-70 mt-0.5">
                      <strong>{p.orders_count ?? 0}</strong> зам.
                      {money && <> · {money}</>}
                      {p.last_order_date && <> · ост. {fmt(p.last_order_date)}</>}
                      {p.created_at && <> · створено {fmt(p.created_at)}</>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="shrink-0 text-xs px-2 py-1 rounded border border-current/30 hover:bg-current/10"
        title="Зняти прапорець (це різні люди / перевірив)"
      >
        ✓ Це різні люди
      </button>
    </div>
  );
};

export default ClientDetailsModal;
