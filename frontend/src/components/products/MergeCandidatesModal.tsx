import React, { useEffect, useState, useCallback } from 'react';
import { Modal, Button, Spin, Empty, message, Tag } from 'antd';
import { LinkOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
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

interface Props {
    productId: number | null;
    open: boolean;
    onClose: () => void;
    onMerged?: () => void;  // callback щоб батько перезавантажив список
}

const Row: React.FC<{ label: string; np: any; sp: any }> = ({ label, np, sp }) => {
    const eq = String(np ?? '').trim() === String(sp ?? '').trim();
    const dim = (v: any) => (v == null || v === '' ? <span className="text-gray-400 italic">—</span> : v);
    return (
        <div className="grid grid-cols-[120px_1fr_1fr] gap-2 py-1 text-sm">
            <div className="text-gray-500 dark:text-gray-400">{label}</div>
            <div className={eq ? 'font-medium text-green-700 dark:text-green-400' : 'text-gray-900 dark:text-gray-100'}>{dim(np)}</div>
            <div className={eq ? 'font-medium text-green-700 dark:text-green-400' : 'text-gray-900 dark:text-gray-100'}>{dim(sp)}</div>
        </div>
    );
};

const MergeCandidatesModal: React.FC<Props> = ({ productId, open, onClose, onMerged }) => {
    const [loading, setLoading] = useState(false);
    const [acting, setActing] = useState<number | null>(null);
    const [candidates, setCandidates] = useState<MergeCandidate[]>([]);

    const load = useCallback(async () => {
        if (!productId) return;
        setLoading(true);
        try {
            const res = await axios.get(`/api/merge-candidates?product_id=${productId}`);
            setCandidates(res.data?.items ?? []);
        } catch (e: any) {
            message.error('Не вдалось завантажити кандидатів: ' + (e?.message ?? e));
        } finally {
            setLoading(false);
        }
    }, [productId]);

    useEffect(() => {
        if (open && productId) load();
    }, [open, productId, load]);

    const handleAccept = async (c: MergeCandidate) => {
        setActing(c.id);
        try {
            const res = await axios.post(`/api/merge-candidates/${c.id}/accept`);
            const nFilled = res.data?.filled_fields?.length ?? 0;
            message.success(`Об'єднано з #${c.sp_pnum.replace(/^#/, '')}${nFilled ? ` · заповнено ${nFilled} порожніх полів` : ''}`);
            onMerged?.();
            onClose();
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
            onMerged?.();
        } catch (e: any) {
            message.error('Не вдалось: ' + (e?.response?.data?.detail ?? e.message));
        } finally {
            setActing(null);
        }
    };

    return (
        <Modal
            open={open}
            onCancel={onClose}
            title={<span><LinkOutlined /> Кандидати на об'єднання</span>}
            footer={null}
            width={800}
            destroyOnClose
        >
            {loading ? (
                <div className="py-10 text-center"><Spin /></div>
            ) : candidates.length === 0 ? (
                <Empty description="Немає активних пропозицій" />
            ) : (
                <div className="space-y-4">
                    {candidates.map(c => (
                        <div key={c.id} className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 bg-white dark:bg-gray-800">
                            <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-2">
                                    <Tag color={c.score >= 70 ? 'green' : c.score >= 50 ? 'gold' : 'default'}>Збіг {c.score}%</Tag>
                                    {c.reason && <span className="text-xs text-gray-500">{c.reason}</span>}
                                </div>
                                <div className="text-xs text-gray-400">
                                    {new Date(c.created_at).toLocaleDateString('uk-UA')}
                                </div>
                            </div>

                            <div className="grid grid-cols-[120px_1fr_1fr] gap-2 py-1 text-sm font-semibold border-b border-gray-200 dark:border-gray-700 mb-2">
                                <div></div>
                                <div className="text-gray-700 dark:text-gray-200">
                                    Новий: <span className="text-blue-600 dark:text-blue-400">{c.np_pnum}</span>
                                    {c.np_clones && <span className="text-xs text-gray-500 ml-1">({c.np_clones})</span>}
                                </div>
                                <div className="text-gray-700 dark:text-gray-200">
                                    Існуючий: <span className="text-purple-600 dark:text-purple-400">{c.sp_pnum}</span>
                                    {c.sp_clones && <span className="text-xs text-gray-500 ml-1">({c.sp_clones})</span>}
                                </div>
                            </div>

                            <Row label="Бренд"      np={c.np_brand}   sp={c.sp_brand} />
                            <Row label="Тип"        np={c.np_type}    sp={c.sp_type} />
                            <Row label="Модель"     np={c.np_model}   sp={c.sp_model} />
                            <Row label="Маркування" np={c.np_marking} sp={c.sp_marking} />
                            <Row label="Розмір"     np={c.np_size}    sp={c.sp_size} />
                            <Row label="Колір"      np={c.np_color}   sp={c.sp_color} />
                            <Row label="Завоз"      np={c.np_delivery} sp={c.sp_delivery} />
                            <Row label="Продано"    np={c.np_sold}    sp={c.sp_sold} />
                            <Row label="Опис"       np={c.np_desc}    sp={c.sp_desc} />

                            <div className="flex justify-end gap-2 mt-4 pt-3 border-t border-gray-200 dark:border-gray-700">
                                <Button
                                    icon={<CloseOutlined />}
                                    onClick={() => handleDecline(c)}
                                    loading={acting === c.id}
                                >Ні, інший товар</Button>
                                <Button
                                    type="primary"
                                    icon={<CheckOutlined />}
                                    onClick={() => handleAccept(c)}
                                    loading={acting === c.id}
                                >Так, об'єднати</Button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </Modal>
    );
};

export default MergeCandidatesModal;
