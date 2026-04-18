import React, { useEffect, useState } from 'react';
import { fetchClient } from '../../services/referenceService';
import ProductDetailsModal from '../products/ProductDetailsModal';
import ProductNumberLink from '../products/ProductNumberLink';

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
  full_name: string;
  nickname: string | null;
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

  useEffect(() => {
    if (!open || !clientId) return;
    setLoading(true);
    setClient(null);
    setActiveTab('info');
    fetchClient(clientId)
      .then((data: any) => setClient(data as ClientFull))
      .finally(() => setLoading(false));
  }, [open, clientId]);

  // Закриття по Escape
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

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
                  {/* Аватар-ініціали */}
                  <div className={`shrink-0 w-12 h-12 rounded-full flex items-center justify-center text-white text-lg font-bold shadow-md ${c.nickname ? 'bg-gradient-to-br from-gray-500 to-gray-600' : 'bg-gradient-to-br from-blue-500 to-indigo-600'}`}>
                    {c.nickname
                      ? (c.nickname[0] || '?').toUpperCase()
                      : `${(c.first_name?.[0] || '').toUpperCase()}${(c.last_name?.[0] || '').toUpperCase()}`}
                  </div>
                  <div className="min-w-0">
                    <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100 truncate">
                      {c.nickname
                        ? c.nickname
                        : c.full_name?.trim() || 'Невідомий'}
                    </h2>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs text-gray-400 font-mono">ID: {c.id}</span>
                      {c.nickname && (c.first_name || c.last_name) && (
                        <span className="text-xs text-gray-400">({[c.first_name, c.last_name].filter(Boolean).join(' ')})</span>
                      )}
                      {c.nickname && (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-500 border border-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:border-gray-600">нікнейм</span>
                      )}
                      {c.rating != null && (
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold border ${ratingBg(c.rating)}`}>
                          <span className={ratingColor(c.rating)}>★ {c.rating.toFixed(1)}</span>
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
              <button
                onClick={onClose}
                className="shrink-0 ml-4 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors text-xl"
                aria-label="Закрити"
                tabIndex={0}
              >
                ✕
              </button>
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

                  {/* ── Двоколонковий layout: Контакти + Додатково ── */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Контакти */}
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Контактна інформація</h3>
                      <div className="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-4">
                        <InfoRow label="📞 Телефон" value={c.phone_number} />
                        <InfoRow label="✉️ Email" value={c.email} />
                        <InfoRow label="🏙️ Місто" value={c.city_of_residence} />
                      </div>
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

                  {/* ── Соцмережі / Канали зв'язку ── */}
                  {socialChannels.length > 0 && (
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Канали зв'язку</h3>
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
                    </div>
                  )}
                  {socialChannels.length === 0 && (
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-3">Канали зв'язку</h3>
                      <p className="text-sm text-gray-400 italic">Немає прив'язаних соцмереж чи месенджерів</p>
                    </div>
                  )}

                  {/* ── Нотатки ── */}
                  {c.notes && (
                    <div>
                      <h3 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">Нотатки</h3>
                      <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap bg-amber-50 dark:bg-amber-900/20 rounded-lg px-4 py-3 border border-amber-100 dark:border-amber-800/30">
                        {c.notes}
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* ── Tab: Замовлення ── */}
              {activeTab === 'orders' && (
                <div>
                  {c.recent_orders.length === 0 ? (
                    <div className="text-center py-12 text-gray-400">Замовлень не знайдено</div>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                          <tr>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">Дата</th>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">Статус</th>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">Товари</th>
                            <th className="px-3 py-2 text-right font-semibold text-gray-600 dark:text-gray-300">Сума</th>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">Оплата</th>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">Доставка</th>
                            <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">ТТН</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                          {c.recent_orders.map(o => (
                            <tr key={o.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                              <td className="px-3 py-2 whitespace-nowrap text-gray-600 dark:text-gray-300 text-xs">
                                {fmtDate(o.order_date)}
                              </td>
                              <td className="px-3 py-2">
                                <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${orderStatusColor(o.order_status)}`}>
                                  {o.order_status || '—'}
                                </span>
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
                              <td className="px-3 py-2 text-right font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">
                                {fmtMoney(o.total_amount)}
                              </td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{o.payment_status || '—'}</td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{o.delivery_method || '—'}</td>
                              <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 font-mono max-w-[120px] truncate" title={o.tracking_number || ''}>
                                {o.tracking_number || '—'}
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

export default ClientDetailsModal;
