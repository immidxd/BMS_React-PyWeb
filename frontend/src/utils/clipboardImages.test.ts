import { isEditableTarget, readClipboardPhotos } from './clipboardImages';

// jsdom не має DataTransfer — достатньо того, що читає розбір.
const clip = (o: { files?: File[]; items?: File[]; text?: string; html?: string }): DataTransfer => ({
  files: (o.files || []) as unknown as FileList,
  items: (o.items || []).map((f) => ({ kind: 'file', type: f.type, getAsFile: () => new File([f], f.name, { type: f.type }) })) as unknown as DataTransferItemList,
  getData: (t: string) => (t === 'text/plain' ? o.text || '' : t === 'text/html' ? o.html || '' : ''),
} as unknown as DataTransfer);

const png = (name = 'image.png') => new File(['x'], name, { type: 'image/png' });

test('screenshot: one image, taken from files — the same one in items is not duplicated', () => {
  const shot = png();
  const r = readClipboardPhotos(clip({ files: [shot], items: [shot] }));
  expect(r.images).toHaveLength(1);
  expect(r.images[0]).toBe(shot);
  expect(r.hasText).toBe(false);
});

test('falls back to items when files is empty (some webviews)', () => {
  const r = readClipboardPhotos(clip({ items: [png(), new File(['x'], 'a.pdf', { type: 'application/pdf' })] }));
  expect(r.images.map((f) => f.name)).toEqual(['image.png']);
  expect(r.otherFiles).toBe(1);
});

test('Finder copy: images kept in order, non-images counted, extension-only HEIC accepted', () => {
  const r = readClipboardPhotos(clip({
    files: [new File(['x'], 'IMG_1.HEIC', { type: '' }), new File(['x'], 'notes.pdf', { type: 'application/pdf' }), png('b.png')],
    text: 'IMG_1.HEIC',
  }));
  expect(r.images.map((f) => f.name)).toEqual(['IMG_1.HEIC', 'b.png']);
  expect(r.otherFiles).toBe(1);
  expect(r.hasText).toBe(true);
});

test('empty (0-byte) image is not a photo', () => {
  const r = readClipboardPhotos(clip({ files: [new File([], 'image.png', { type: 'image/png' }), png('ok.png')] }));
  expect(r.images.map((f) => f.name)).toEqual(['ok.png']);
  expect(r.otherFiles).toBe(1);
});

test('nameless image gets a name with an extension from its type', () => {
  const r = readClipboardPhotos(clip({ files: [new File(['x'], '', { type: 'image/jpeg' })] }));
  expect(r.images[0].name).toBe('з-буфера-1.jpg');
  expect(r.images[0].type).toBe('image/jpeg');
});

test('page fragment with <img> but no picture is reported, plain text is not', () => {
  expect(readClipboardPhotos(clip({ html: '<p>x</p><img src="https://x/y.jpg">', text: 'x' })).htmlImage).toBe(true);
  expect(readClipboardPhotos(clip({ text: 'just text' }))).toEqual({ images: [], hasText: true, otherFiles: 0, htmlImage: false });
  expect(readClipboardPhotos(null).images).toEqual([]);
});

test('editable targets: text inputs and textarea yes; buttons, checkboxes, divs no', () => {
  const mk = (tag: string, type?: string) => { const el = document.createElement(tag); if (type) el.setAttribute('type', type); return el; };
  expect(isEditableTarget(mk('input'))).toBe(true);
  expect(isEditableTarget(mk('input', 'number'))).toBe(true);
  expect(isEditableTarget(mk('textarea'))).toBe(true);
  expect(isEditableTarget(mk('input', 'checkbox'))).toBe(false);
  expect(isEditableTarget(mk('button'))).toBe(false);
  expect(isEditableTarget(mk('div'))).toBe(false);
  expect(isEditableTarget(document)).toBe(false);
  expect(isEditableTarget(null)).toBe(false);
});
