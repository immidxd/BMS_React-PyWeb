import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import TelegramBatchPublishDialog from './TelegramBatchPublishDialog';

jest.mock('../common/SmartImage', () => ({
  __esModule: true,
  default: ({ src }: { src: string }) => <img src={src} alt="preview" />,
}));

jest.mock('./TelegramPublishDialog', () => ({
  __esModule: true,
  default: ({ onConfirm }: { onConfirm: (draft: any) => void }) => (
    <button onClick={() => onConfirm({
      caption: 'draft', emoji: '👟', tagline: '', features: [], search_q: '',
      size_ids: [], image_idx: [0], thread_ids: [], to_channel: false,
      channel_at: null, test_mode: false, silent: false,
    })}>
      Зберегти чернетку
    </button>
  ),
  TelegramConditionPublishConfirmation: () => null,
}));

const preview = {
  product_id: 1,
  productnumber: 'Ф1',
  brand: 'Brand',
  model: 'Model',
  type: 'Кросівки',
  emoji: '👟',
  tagline: 'кросівки',
  features: [],
  search_q: 'Brand-Model',
  condition: 'Нові',
  condition_icon: '✅',
  condition_name: 'Новий',
  condition_confirmation_required: false,
  price: '1000',
  sizes: [],
  is_bag: false,
  dimensions: null,
  caption: 'caption',
  caption_len: 7,
  caption_limit: 1024,
  image_count: 1,
  image_kind: 'official',
  image_urls: ['/product-images/test.webp?v=1'],
  image_names: ['Ф1_01.webp'],
  album_limit: 5,
  album_hard_limit: 10,
  max_threads_per_post: 6,
  default_image_idx: [0],
  archive: { configured: false, title: 'WORKSHOP' },
  threads: [],
  suggested_threads: [],
  root_topic: { thread_id: 1, thread_title: 'ВСІ ПРОПОЗИЦІЇ' },
  channel: { chat_id: 2, chat_title: 'BrandStore' },
  default_channel_at: new Date(Date.now() + 60 * 60_000).toISOString(),
  already_published: 0,
  seed_source: null,
  warnings: [],
};

const response = {
  selected_count: 1,
  unique_count: 1,
  merged_count: 0,
  batch_max_products: 10,
  items: [{
    product_id: 1,
    productnumber: 'Ф1',
    source_product_ids: [1],
    ok: true,
    preview,
  }],
};

describe('TelegramBatchPublishDialog draft state', () => {
  const fetchMock = jest.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });
    global.fetch = fetchMock as any;
  });

  it('keeps manual publish and common-settings checkboxes after editing and a same-ids rerender', async () => {
    const { rerender } = render(
      <TelegramBatchPublishDialog productIds={[1]} busy={false} onCancel={jest.fn()} onPublish={jest.fn()} />,
    );

    const checkbox = await screen.findByLabelText('Публікувати #Ф1') as HTMLInputElement;
    const commonCheckbox = screen.getByLabelText('Загальні налаштування для #Ф1') as HTMLInputElement;
    fireEvent.click(checkbox);
    fireEvent.click(commonCheckbox);
    expect(checkbox.checked).toBe(false);
    expect(commonCheckbox.checked).toBe(false);

    fireEvent.click(screen.getByText('Редагувати пост 1'));
    fireEvent.click(screen.getByText('Зберегти чернетку'));

    const afterEdit = await screen.findByLabelText('Публікувати #Ф1') as HTMLInputElement;
    expect(afterEdit.checked).toBe(false);
    expect((screen.getByLabelText('Загальні налаштування для #Ф1') as HTMLInputElement).checked).toBe(false);

    rerender(
      <TelegramBatchPublishDialog productIds={[1]} busy={false} onCancel={jest.fn()} onPublish={jest.fn()} />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect((screen.getByLabelText('Публікувати #Ф1') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText('Загальні налаштування для #Ф1') as HTMLInputElement).checked).toBe(false);
  });

  it('removes a card from the package with its close button', async () => {
    render(
      <TelegramBatchPublishDialog productIds={[1]} busy={false} onCancel={jest.fn()} onPublish={jest.fn()} />,
    );

    fireEvent.click(await screen.findByLabelText('Прибрати #Ф1 з пакета'));

    expect(await screen.findByText('Усі картки прибрано з пакета')).toBeTruthy();
    expect(screen.queryByLabelText('Публікувати #Ф1')).toBeNull();
  });
});
