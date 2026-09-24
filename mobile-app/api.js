// The whole server surface the phone uses. Same endpoints the desktop app calls —
// the phone is another client of one database, not a second system.
const hdr = { 'Content-Type': 'application/json' };

// Errors come back as {detail: "..."} — surface that sentence, because the server
// writes the ones a picker needs to read ("breakdown doesn't add up — …").
async function J(r) {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Request failed (${r.status})`);
  return j;
}

export function makeApi(server, token) {
  const base = (server || '').replace(/\/+$/, '');
  const get = (path) => fetch(base + path).then(J);
  const send = (method, path, body) =>
    fetch(base + path, { method, headers: hdr, body: body === undefined ? undefined : JSON.stringify(body) }).then(J);

  return {
    base,
    ping: () => get('/api/status'),
    login: (username, password) => send('POST', '/api/auth/login', { username, password }),
    verify: () => get('/api/auth/verify?token=' + encodeURIComponent(token || '')),

    // inventory
    options: () => get('/api/inventory/product-options'),
    categories: () => get('/api/masters/categories'),
    // No search: what is in stock. A search: the whole catalogue, capped. The
    // unfiltered list is every product ever received — too big for a phone.
    products: (status, q) => get(`/api/inventory/products?status=${status}&${q ? '' : 'held=1&'}limit=500&q=${encodeURIComponent(q || '')}`),
    product: (id) => get(`/api/inventory/products/${id}`),
    detail: (id, body) => send('POST', `/api/inventory/products/${id}/detail`, body),
    summary: () => get('/api/inventory/summary'),

    // purchases / GRN
    // one filter, newest 200 — { rows, total, counts }. Every GRN ever made is
    // megabytes on a full store.
    purchases: (status) => get(`/api/purchases?limit=200&status=${encodeURIComponent(status || 'all')}`),
    purchase: (id) => get(`/api/purchases/${id}`),
    // the attribute breakdown of one billed line — [] clears it
    setSplits: (lineId, rows) => send('PUT', `/api/purchases/lines/${lineId}/splits`, { rows }),
    editLine: (lineId, fields) => send('PATCH', `/api/purchases/lines/${lineId}`, fields),
    // pin a line (or one of its variants) to an existing product by scanned code
    scanLine: (lineId, code, split_id) => send('POST', `/api/purchases/lines/${lineId}/scan`, { code, split_id }),
    postGrn: (id) => send('POST', `/api/purchases/${id}/post`),

    // bundles — the carton label, printed at GRN, and the garment tags that
    // follow once the box is opened and its items detailed
    bundles: (status) => get('/api/bundles' + (status ? `?status=${status}` : '')),
    bundle: (id) => get(`/api/bundles/${id}`),
    bundleLookup: (code) => get('/api/bundles/lookup?code=' + encodeURIComponent(code)),
    bundleLocations: () => get('/api/bundles/locations'),
    bundleLocate: (id, location, by) => send('POST', `/api/bundles/${id}/locate`,
      { location, located_by: by || 'mobile' }),
    bundleOpen: (id) => send('POST', `/api/bundles/${id}/open`),
    bundleTag: (id, by) => send('POST', `/api/bundles/${id}/tag`, { tagged_by: by || 'mobile' }),

    // PNG, not the SVG the web app uses: <Image> cannot render SVG without a
    // native renderer. Identical code, rendered for a screen.
    qrPngUrl: (productId, scale) => `${base}/api/inventory/products/${productId}/qr.png?scale=${scale || 6}`,
    bundleQrPngUrl: (id, scale) => `${base}/api/bundles/${id}/qr.png?scale=${scale || 6}`,
    // print-ready label sheets — opened in the phone browser
    labelsUrl: (ids) => `${base}/api/inventory/labels?ids=${(ids || []).join(',')}`,
    bundleLabelUrl: (id) => `${base}/api/bundles/${id}/label`,
    bundleLabelsUrl: (purchaseId) => `${base}/api/bundles/labels?purchase_id=${purchaseId}`,
    bundleItemLabelsUrl: (id) => `${base}/api/bundles/${id}/item-labels`,
  };
}
