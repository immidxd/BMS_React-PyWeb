/**
 * «📁 Папки» у шапці «Товарів»: список робочих наборів, відкрити/закрити папку,
 * створити, перейменувати, видалити. Покласти товари в папку — «Дії → У папку…».
 *
 * Відкрита папка — це ще один фільтр: решта фільтрів діє всередині неї, тож на
 * кнопці видно «9 з 12», коли частину товарів папки сховали фільтри (наприклад,
 * продані при ввімкненому «непродані»), — щоб вони не зникали непомітно.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, Popover, Tooltip, notification } from 'antd';
import type { InputRef } from 'antd';
import { CloseOutlined, DeleteOutlined, EditOutlined, FolderOpenOutlined, FolderOutlined, PlusOutlined } from '@ant-design/icons';
import { productFolderService, useProductFolders } from '../../services/productFolderService';
import { notify } from '../../ui/feedback';

interface Props {
  activeId?: number;
  /** Скільки товарів відкритої папки видно з поточними фільтрами (total списку). */
  shown?: number;
  onOpen: (id?: number) => void;
}

export const ProductFoldersButton: React.FC<Props> = ({ activeId, shown, onOpen }) => {
  const folders = useProductFolders();
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [editId, setEditId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [busy, setBusy] = useState(false);
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const active = folders.find((f) => f.id === activeId);

  const create = async () => {
    const name = newName.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      const f = await productFolderService.create(name);
      setNewName('');
      notify.success({ message: `Папку «${f.name}» створено`, description: 'Покладіть у неї товари: виділіть рядки → «Дії» → «У папку».' });
    } catch (e: any) {
      notify.error({ message: e.message });
    } finally { setBusy(false); }
  };

  const saveRename = async () => {
    if (editId == null) return;
    const name = editName.trim();
    const prev = folders.find((f) => f.id === editId);
    if (!name || name === prev?.name) { setEditId(null); return; }
    setBusy(true);
    try {
      await productFolderService.rename(editId, name);
      setEditId(null);
    } catch (e: any) {
      notify.error({ message: e.message });
    } finally { setBusy(false); }
  };

  // Папки здебільшого тимчасові — видаляємо одним кліком, без «Ви впевнені?».
  // Страховка — «Повернути» в сповіщенні: та сама назва й ті самі товари.
  const remove = async (id: number) => {
    const f = folders.find((x) => x.id === id);
    if (!f) return;
    const wasActive = activeId === id;
    try {
      const gone = await productFolderService.remove(id);
      if (wasActive) onOpen(undefined);
      const key = `folder-undo-${id}`;
      notify.info({
        key,
        message: `Папку «${gone.name}» видалено`,
        description: gone.product_ids.length ? `Товари (${gone.product_ids.length}) лишились у програмі.` : undefined,
        duration: 8,
        actions: (
          <Button size="small" onClick={async () => {
            notification.destroy(key);
            try {
              const back = await productFolderService.restore(gone.name, gone.product_ids);
              if (wasActive) onOpen(back.id);
            } catch (e: any) {
              notify.error({ message: 'Не вдалося повернути папку', description: e.message });
            }
          }}>
            Повернути
          </Button>
        ),
      });
    } catch (e: any) {
      notify.error({ message: e.message });
    }
  };

  const content = (
    <div className="w-72 max-w-[80vw]">
      {folders.length === 0 ? (
        <div className="px-1 pb-2 text-[13px] text-gray-500 dark:text-gray-400 leading-snug">
          Папок ще нема. Створіть першу — і кладіть туди товари через «Дії → У папку».
        </div>
      ) : (
        <div className="max-h-80 overflow-y-auto -mx-1 mb-2">
          {activeId != null && (
            <button
              type="button"
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-[13px] text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              onClick={() => { onOpen(undefined); setOpen(false); }}
            >
              <CloseOutlined className="text-[11px]" /> Усі товари
            </button>
          )}
          {folders.map((f) => (
            <div
              key={f.id}
              className={`group flex items-center gap-2 px-2 py-1.5 rounded text-[13px] ${
                f.id === activeId
                  ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                  : 'text-gray-800 dark:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700'
              }`}
            >
              {editId === f.id ? (
                <Input
                  size="small"
                  autoFocus
                  value={editName}
                  maxLength={120}
                  disabled={busy}
                  onChange={(e) => setEditName(e.target.value)}
                  onPressEnter={() => void saveRename()}
                  onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setEditId(null); } }}
                  onBlur={() => void saveRename()}
                />
              ) : (
                <>
                  <button
                    type="button"
                    className="flex-1 min-w-0 flex items-center gap-2 text-left"
                    onClick={(e) => {
                      // Подвійний клік — перейменування; перший клік не має встигнути закрити список.
                      if (e.detail > 1) return;
                      const target = f.id === activeId ? undefined : f.id;
                      clickTimer.current = setTimeout(() => { onOpen(target); setOpen(false); }, 220);
                    }}
                    onDoubleClick={() => {
                      if (clickTimer.current) clearTimeout(clickTimer.current);
                      setEditId(f.id); setEditName(f.name);
                    }}
                    title={`${f.id === activeId ? 'Закрити папку' : 'Відкрити папку'} · подвійний клік — перейменувати`}
                  >
                    {f.id === activeId ? <FolderOpenOutlined /> : <FolderOutlined className="text-gray-400" />}
                    <span className="truncate">{f.name}</span>
                  </button>
                  <span className={`tabular-nums text-[12px] ${f.id === activeId ? 'opacity-80' : 'text-gray-400'}`}>{f.count}</span>
                  <span className={`flex items-center gap-1 transition-opacity ${f.id === activeId ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
                    <Tooltip title="Перейменувати">
                      <button type="button" className="px-0.5 opacity-70 hover:opacity-100"
                        onClick={() => { setEditId(f.id); setEditName(f.name); }}>
                        <EditOutlined />
                      </button>
                    </Tooltip>
                    <Tooltip title="Видалити папку одразу (товари лишаться; можна повернути)">
                      <button type="button" className="px-0.5 opacity-70 hover:opacity-100" onClick={() => void remove(f.id)}>
                        <DeleteOutlined />
                      </button>
                    </Tooltip>
                  </span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-1.5">
        <Input
          size="small"
          placeholder="Нова папка…"
          value={newName}
          maxLength={120}
          disabled={busy}
          onChange={(e) => setNewName(e.target.value)}
          onPressEnter={() => void create()}
        />
        <Button size="small" icon={<PlusOutlined />} disabled={!newName.trim()} loading={busy} onClick={() => void create()}>
          Створити
        </Button>
      </div>
    </div>
  );

  const label = active
    ? `${active.name} · ${shown != null && shown < active.count ? `${shown} з ${active.count}` : active.count}`
    : `Папки${folders.length ? ` (${folders.length})` : ''}`;

  return (
    <span className="inline-flex">
      <Popover
        trigger="click"
        placement="bottomLeft"
        open={open}
        onOpenChange={(v) => { setOpen(v); if (!v) setEditId(null); }}
        content={content}
        arrow={false}
      >
        <Button
          type={active ? 'primary' : 'default'}
          icon={active ? <FolderOpenOutlined /> : <FolderOutlined />}
          className={active ? 'max-w-[260px]' : undefined}
          title={active
            ? (shown != null && shown < active.count
              ? `Відкрита папка «${active.name}». Частину її товарів (${active.count - shown}) ховають увімкнені фільтри.`
              : `Відкрита папка «${active.name}»`)
            : 'Папки — робочі набори товарів'}
        >
          <span className="truncate">{label}</span>
        </Button>
      </Popover>
      {active && (
        <Button
          type="primary"
          icon={<CloseOutlined />}
          className="ml-px"
          onClick={() => onOpen(undefined)}
          title="Закрити папку — до всіх товарів"
          aria-label="Закрити папку"
        />
      )}
    </span>
  );
};

/** Назва нової папки — для «Дії → У папку → Нова папка…». */
export const NewFolderModal: React.FC<{
  open: boolean;
  count: number;
  onCancel: () => void;
  onCreate: (name: string) => Promise<void>;
}> = ({ open, count, onCancel, onCreate }) => {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const ref = useRef<InputRef>(null);
  useEffect(() => { if (open) { setName(''); setTimeout(() => ref.current?.focus(), 50); } }, [open]);
  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try { await onCreate(name.trim()); } finally { setBusy(false); }
  };
  return (
    <Modal
      open={open}
      title="Нова папка"
      okText={`Створити й покласти (${count})`}
      cancelText="Скасувати"
      okButtonProps={{ disabled: !name.trim(), loading: busy }}
      onOk={() => void submit()}
      onCancel={onCancel}
      destroyOnClose
      width={380}
    >
      <Input
        ref={ref}
        placeholder="Наприклад: На фотосесію"
        value={name}
        maxLength={120}
        onChange={(e) => setName(e.target.value)}
        onPressEnter={() => void submit()}
      />
    </Modal>
  );
};
