import React, { useEffect, useState, useCallback } from 'react';
import { Button, Spin, Empty, message, Tag, Popconfirm } from 'antd';
import { LinkOutlined, CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import axios from 'axios';

interface MergeCandidate {
    id: number;
    new_product_id: number;
    suggested_id: number;
    score: number;
    reason: string | null;
    created_at: string;
    np_pnum: string;
    np_clones: string | null;
    np_brand: string | null;
    np_type: string | null;
    np_model: string | null;
    np_size: string | null;
    np_marking: string | null;
    np_color: string | null;
    np_desc: string | null;
    np_delivery: string | null;
    np_delivery_date: string | null;
    np_sold: number;
    sp_pnum: string;
    sp_clones: string | null;
    sp_brand: string | null;
    sp_type: string | null;
    sp_model: string | null;
    sp_size: string | null;
    sp_marking: string | null;
    sp_color: string | null;
    sp_desc: string | null;
    sp_delivery: string | null;
    sp_delivery_date: string | null;
    sp_sold: number;
}

// Колір бейджа впевненості: зелений ≥70, жовтий ≥50, сірий нижче
const scoreColor = (s: number) => (s >= 70 ? 'green' : s >= 50 ? 'gold' : 'default');

const FieldRow: React.FC<{ label: string; np: any; sp: any }> = ({ label, np, sp }) => {
    const eq = String(np ?? '').trim() === String(sp ?? '').trim() && String(np ?? '').trim() !== '';
    const dim = (v: any) => (v == null || v === '' ? <span className="text-gray-400 italic">—</span> : v);
    return (
        <div className="grid grid-cols-[110px_1fr_1fr] gap-2 py-0.5 text-sm">
            <div className="text-gray-500 dark:text-gray-400">{label}</div>
            <div className={eq ? 'font-medium text-green-700 dark:text-green-400' : 'text-gray-900 dark:text-gray-100'}>{dim(np)}</div>
            <div className={eq ? 'font-medium text-green-700 dark:text-green-400' : 'text-gray-900 dark:text-gray-100'}>{dim(sp)}</div>
        </div>
    );
};

const MergeQueuePage: React.FC = () => {
    const [loading, setLoading] = useState(false);
    const [scanning, setScanning] = useState(false);
    const [acting, setActing] = useState<number | null>(null);
    const [candidates, setCandidates] = useState<MergeCandidate[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await axios.get('/api/merge-candidates');
            setCandidates(res.data?.items ?? []);
        } catch (e: any) {
            message.error('Не вдалось завантажити кандидатів: ' + (e?.message ?? e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const handleScan = async () => {
        setScanning(true);
        try {
            const res = await axios.post('/api/merge-candidates/scan?reset=true');
            const d = res.data ?? {};
            message.success(
                `Сканування: ${d.scanned_lost ?? 0} загублених → ${d.candidates_created ?? 0} пропозицій`
            );
            await load();
        } catch (e: any) {
            message.error('Сканування не вдалось: ' + (e?.response?.data?.detail ?? e.message));
        } finally {
            setScanning(false);
        }
    };

    const handleAccept = async (c: MergeCandidate) => {
        setActing(c.id);
        try {
            const res = await axios.post(`/api/merge-candidates/${c.id}/accept`);
            const nFilled = res.data?.filled_fields?.length ?? 0;
            message.success(`Об'єднано з #${c.sp_pnum.replace(/^#/, '')}${nFilled ? ` · заповнено ${nFilled} порожніх полів` : ''}`);
            // Прибираємо всі пропозиції цього ж загубленого товару (його видалено при merge)
            setCandidates(prev => prev.filter(x => x.new_product_id !== c.new_product_id));
        } catch (e: any) {
            message.error('Не вдалось об\'єднати: ' + (e?.response?.data?.detail ?? e.message));
        } finally {
            setActing(null);
        }
    };

    const handleDecline = async (c: MergeCandidate) => {
        setActing(c.id);
        try {
            await axios.post(`/api/merge-candidates/${c.id}/decline`);
            message.info('Пропозицію відхилено');
            setCandidates(prev => prev.filter(x => x.id !== c.id));
        } catch (e: any) {
            message.error('Не вдалось: ' + (e?.response?.data?.detail ?? e.message));
        } finally {
            setActing(null);
        }
    };

    // Групуємо пропозиції за загубленим товаром (new_product_id)
    const groups = React.useMemo(() => {
        const m = new Map<number, MergeCandidate[]>();
        for (const c of candidates) {
            if (!m.has(c.new_product_id)) m.set(c.new_product_id, []);
            m.get(c.new_product_id)!.push(c);
        }
        // Сортуємо групи за найкращим score; всередині — теж за score
        return Array.from(m.values())
            .map(arr => arr.sort((a, b) => b.score - a.score))
            .sort((a, b) => b[0].score - a[0].score);
    }, [candidates]);

    return (
        <div className="p-4 max-w-5xl mx-auto">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <LinkOutlined className="text-lg" />
                    <h2 className="text-lg font-semibold m-0">Можливі збіги (загублені товари)</h2>
                    <Tag>{candidates.length} пропозицій</Tag>
                </div>
                <div className="flex gap-2">
                    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Оновити</Button>
                    <Popconfirm
                        title="Пересканувати загублені товари?"
                        description="Перерахує пропозиції зважено (top-5 на товар). Декланутi/прийняті не чіпає."
                        onConfirm={handleScan}
                        okText="Так, сканувати"
                        cancelText="Скасувати"
                    >
                        <Button type="primary" loading={scanning}>Сканувати</Button>
                    </Popconfirm>
                </div>
            </div>

            {loading ? (
                <div className="py-16 text-center"><Spin /></div>
            ) : groups.length === 0 ? (
                <Empty description="Немає пропозицій. Натисніть «Сканувати», щоб знайти оригінали для загублених товарів." />
            ) : (
                <div className="space-y-6">
                    {groups.map(group => {
                        const first = group[0];
                        return (
                            <div key={first.new_product_id} className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                                <div className="px-4 py-2 bg-gray-50 dark:bg-gray-800/60 border-b border-gray-200 dark:border-gray-700">
                                    <span className="text-sm text-gray-500">Загублений товар:</span>{' '}
                                    <span className="font-semibold text-blue-600 dark:text-blue-400">{first.np_pnum}</span>
                                    {first.np_clones && <span className="text-xs text-gray-500 ml-1">({first.np_clones})</span>}
                                    <span className="text-xs text-gray-500 ml-2">
                                        {[first.np_brand, first.np_type, first.np_model, first.np_size].filter(Boolean).join(' · ')}
                                    </span>
                                </div>
                                <div className="divide-y divide-gray-100 dark:divide-gray-700">
                                    {group.map(c => (
                                        <div key={c.id} className="p-4">
                                            <div className="flex items-center justify-between mb-2">
                                                <Tag color={scoreColor(c.score)}>Збіг {c.score}%</Tag>
                                                {c.reason && <span className="text-xs text-gray-500">{c.reason}</span>}
                                            </div>
                                            <div className="grid grid-cols-[110px_1fr_1fr] gap-2 text-xs font-semibold border-b border-gray-200 dark:border-gray-700 pb-1 mb-1">
                                                <div></div>
                                                <div className="text-gray-600 dark:text-gray-300">Загублений</div>
                                                <div className="text-purple-600 dark:text-purple-400">
                                                    Оригінал: {c.sp_pnum}
                                                    {c.sp_clones && <span className="text-gray-500 ml-1">({c.sp_clones})</span>}
                                                </div>
                                            </div>
                                            <FieldRow label="Бренд" np={c.np_brand} sp={c.sp_brand} />
                                            <FieldRow label="Тип" np={c.np_type} sp={c.sp_type} />
                                            <FieldRow label="Модель" np={c.np_model} sp={c.sp_model} />
                                            <FieldRow label="Маркування" np={c.np_marking} sp={c.sp_marking} />
                                            <FieldRow label="Розмір" np={c.np_size} sp={c.sp_size} />
                                            <FieldRow label="Колір" np={c.np_color} sp={c.sp_color} />
                                            <FieldRow label="Завіз" np={c.np_delivery} sp={c.sp_delivery} />
                                            <FieldRow label="Продано" np={c.np_sold} sp={c.sp_sold} />
                                            <FieldRow label="Опис" np={c.np_desc} sp={c.sp_desc} />
                                            <div className="flex justify-end gap-2 mt-3">
                                                <Button size="small" icon={<CloseOutlined />} onClick={() => handleDecline(c)} loading={acting === c.id}>
                                                    Ні, інший
                                                </Button>
                                                <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => handleAccept(c)} loading={acting === c.id}>
                                                    Так, об'єднати
                                                </Button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default MergeQueuePage;
