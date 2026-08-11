import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import TelegramPublishDialog, { type TelegramPreview } from './TelegramPublishDialog';
import { productService } from '../../services/productService';
import { emitProductPhotosChanged, taskManager } from '../../services/taskManager';

jest.mock('../common/SmartImage', () => ({
  __esModule: true,
  default: ({ src }: { src: string }) => <img src={src} alt="photo" />,
}));

jest.mock('../../services/productService', () => ({
  productService: { transformProductPhoto: jest.fn() },
}));

jest.mock('../../services/taskManager', () => ({
  taskManager: { run: jest.fn((_label: string, fn: () => Promise<any>) => fn()) },
  emitProductPhotosChanged: jest.fn(),
}));

const preview: TelegramPreview = {
  product_id: 42,
  productnumber: 'Ф42',
  brand: 'Brand',
  model: 'Model',
  type: 'Кросівки',
  emoji: '👟',
  tagline: 'кросівки',
  features: ['Матеріал'],
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
  image_urls: ['/product-images/Ф42_01.webp?v=old'],
  image_names: ['Ф42_01.webp'],
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

describe('TelegramPublishDialog photo editing', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (taskManager.run as jest.Mock).mockImplementation(
      (_label: string, fn: () => Promise<any>) => fn(),
    );
    (productService.transformProductPhoto as jest.Mock).mockResolvedValue({
      transformed: 'Ф42_01.webp',
      operation: 'flip_horizontal',
      width: 100,
      height: 100,
      version: 'fresh-version',
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ caption: 'caption', caption_len: 7, problem: null }),
    }) as any;
  });

  it('mirrors the canonical image and immediately switches preview to its new version', async () => {
    const onPreviewChange = jest.fn();
    render(
      <TelegramPublishDialog
        data={preview}
        busy={false}
        onCancel={jest.fn()}
        onConfirm={jest.fn()}
        onPreviewChange={onPreviewChange}
      />,
    );

    const mirrorButton = screen.getByLabelText('Віддзеркалити фото 1') as HTMLButtonElement;
    expect(mirrorButton.disabled).toBe(false);
    fireEvent.click(mirrorButton);

    expect(taskManager.run).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(productService.transformProductPhoto).toHaveBeenCalledWith(
      42, 'Ф42_01.webp', 'flip_horizontal',
    ));
    await waitFor(() => expect((screen.getByAltText('photo') as HTMLImageElement).src).toContain('v=fresh-version'));
    expect(onPreviewChange.mock.calls[0][0].image_urls[0]).toContain('v=fresh-version');
    expect(emitProductPhotosChanged).toHaveBeenCalledWith(42);
  });
});
