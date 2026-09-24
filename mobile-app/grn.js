// GRN on the phone: open the goods receipt, break a billed bundle into the sizes
// that actually arrived, and post it. Posting is what creates one inventory
// record and one QR per size — the same server call the desktop app makes, so a
// GRN received on the floor and one received at a desk produce identical stock.
//
// The desk keeps what the desk is for: building the GRN from the invoice, and
// unposting a mistake. This screen is the part that has to happen where the
// cartons are, because only the person opening them knows the mix.
import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, FlatList, Modal,
  ActivityIndicator, Image, Alert, Linking, KeyboardAvoidingView, Platform,
} from 'react-native';
import { C, s } from './theme';
import { Select, Badge, GhostButton, PrimaryButton, CodePrompt, Notice } from './ui';
import DetailScreen from './product';

// The attributes that make a breakdown row its own stock item — the same set the
// product detail form and the QR payload carry, so a variant created here is
// already the record the label and the scanner expect.
const SPLIT_ATTRS = [
  ['size', 'Size'], ['color', 'Colour'], ['material', 'Material'],
  ['pattern', 'Pattern'], ['fit', 'Fit'], ['product_type', 'Type'], ['design_no', 'Design No'],
];
const MONEY = [['rate', 'Rate'], ['mrp', 'MRP'], ['sale_price', 'Sale price'], ['sale_discount_pct', 'Disc %']];
// quantities are floats — compare with the tolerance the server posts with
const SAME = (a, b) => Math.abs((+a || 0) - (+b || 0)) < 0.001;
const num = (v) => (v == null || v === '' ? '—' : Number(v).toFixed(2));
// Dates come off the API stored ISO and are read here DD-MM-YYYY — the same form
// the warehouse screens use, so a GRN quoted over the phone matches the one on
// the desk. Anything that is not an ISO date is shown exactly as it came.
const day = (v) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v ?? ''));
  return m ? `${m[3]}-${m[2]}-${m[1]}` : (v ?? '');
};
const qtyOf = (v) => Math.round((+v || 0) * 1000) / 1000;
const variantLabel = (r) => SPLIT_ATTRS.map(([k]) => r[k]).filter(Boolean).join(' · ');
const blankRow = (rate, category) => ({
  ...Object.fromEntries(SPLIT_ATTRS.map(([k]) => [k, ''])),
  category: category || '', qty: '', rate: rate != null ? String(rate) : '',
  mrp: '', sale_price: '', sale_discount_pct: '',
});

// --- size runs: "28-2-38" is six rows of three ---
// A garment bundle is bought as a RUN, not as a list. 28 to 38 in twos is six
// sizes with the eighteen pieces spread evenly over them — and keying that on a
// phone, standing over an open carton, is six rows, six sizes and six identical
// quantities typed on a glass keyboard. So it is typed the way the packing slip
// writes it, once. They are ordinary rows afterwards: change a quantity, drop a
// size, add one the run didn't cover.
//
// Mind the separator. In a size COLUMN "30-2" means two of size 30 — that is what
// the server's services/size_split.py reads off the supplier's own bill, and it
// is careful about it because guessing wrong invents stock. Here it cannot mean
// that: this box is only ever start-step-end. Two numbers ("28-38") step by one.
//
// The desk app has the same box (frontend/src/App.jsx) and the same arithmetic,
// because a breakdown started on the floor and one started at a desk have to come
// out as the same rows.
// A step keyed as .2 instead of 2 turns a six-size run into fifty-one rows, and
// nothing in this trade is a run of forty sizes — so that is a typo, not a run.
const SIZE_RUN_MAX = 40;
const parseSizeRun = (spec) => {
  const text = String(spec || '').trim();
  if (!text) return { sizes: [], why: '' };
  // "16*22" written with an x or a * is two numbers and NO step. On one supplier's
  // bills it is a size range — sixteen to twenty-two, in twos — and on another
  // "127 X 200" is a bedsheet. Nothing in the text itself separates them, and
  // reading either as a run off a dash would turn one line into seven sizes that
  // were never on the bill.
  //
  // So this form is never a run on its own say-so. It becomes one only where the
  // quantity proves the step (runFromSize, on the invoice grid: four pieces over
  // 16-2-22 is one of each, and the bedsheet's 73 divides by nothing) — and what
  // that produces is a SUGGESTION in this box, written out in full, for a human
  // to look at. Typed in by hand it is refused, and the message asks for the step
  // rather than pretending the run cannot be read.
  //
  // The server guards the same ambiguity from the other end, on what the supplier
  // printed: services/size_split.py, where "30x2" is refused unless the
  // arithmetic proves it.
  if (/[x×*]/i.test(text)) {
    return { sizes: [], why: `“${text}” does not say its step — write it start-step-end, like 16-2-22.` };
  }
  const nums = text.split(/[^0-9.]+/).filter(Boolean).map(Number);
  if (nums.length < 2 || nums.length > 3 || nums.some((n) => Number.isNaN(n))) {
    return { sizes: [], why: 'Write the run as start-step-end — 28-2-38.' };
  }
  const [start, step, end] = nums.length === 3 ? nums : [nums[0], 1, nums[1]];
  if (!(step > 0)) return { sizes: [], why: 'The step has to be more than zero — 28-2-38.' };
  const count = Math.floor(qtyOf(Math.abs(end - start) / step)) + 1;
  if (count > SIZE_RUN_MAX) return { sizes: [], why: `That is ${count} sizes — check the step.` };
  // 38-2-28 is the same six sizes counted down, which is how some slips write it
  const stride = end < start ? -step : step;
  return { sizes: Array.from({ length: count }, (_, i) => String(qtyOf(start + i * stride))), why: '' };
};
// 18 over 6 sizes is 3 each. 20 over 6 is 4, 4, 3, 3, 3, 3 — what will not divide
// goes to the first rows rather than being dropped, because the thing that has to
// be true before this GRN can post is that every piece is placed somewhere.
const spreadQty = (total, n) => {
  const t = Math.max(0, +total || 0);
  if (n <= 0) return [];
  if (Number.isInteger(t)) {
    const base = Math.floor(t / n);
    return Array.from({ length: n }, (_, i) => base + (i < t - base * n ? 1 : 0));
  }
  // metres and kilos divide unevenly; the rounding drift lands on the last row so
  // the rows still add up to exactly what arrived
  const each = qtyOf(t / n);
  return Array.from({ length: n }, (_, i) => (i === n - 1 ? qtyOf(t - each * (n - 1)) : each));
};

// ---------------------------------------------------------------- GRN list ---
function GrnList({ api, onPick, onLogout }) {
  const [list, setList] = useState([]);
  const [status, setStatus] = useState('draft');
  const [drafts, setDrafts] = useState(0);        // "to receive", counted by the server
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  // The server filters and caps the list; the phone no longer fetches every GRN.
  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = await api.purchases(status);
      setList(r.rows || []);
      setDrafts((r.counts && r.counts.draft) || 0);
    }
    catch (e) { setErr(e.message); }
    setLoading(false);
  }, [api, status]);
  useEffect(() => { load(); }, [load]);

  const shown = list;
  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <Text style={s.topTitle}>Goods receipts</Text>
        <Text style={s.topCount}>{drafts} to receive</Text>
        <TouchableOpacity onPress={onLogout}><Text style={[s.link, s.topLink]}>Logout</Text></TouchableOpacity>
      </View>
      <View style={{ flexDirection: 'row', gap: 8, padding: 12 }}>
        {['draft', 'posted', 'all'].map((st) => (
          <TouchableOpacity key={st} style={[s.chip, status === st && s.chipOn]} onPress={() => setStatus(st)}>
            <Text style={[s.chipText, status === st && s.chipTextOn]}>{st}</Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity style={[s.chip, { marginLeft: 'auto' }]} onPress={load}><Text style={s.chipText}>↻</Text></TouchableOpacity>
      </View>
      <Notice text={err} tone="err" />
      {loading ? <ActivityIndicator color={C.accent} style={{ marginTop: 20 }} /> : (
        <FlatList data={shown} keyExtractor={(x) => String(x.id)} contentContainerStyle={{ padding: 12, paddingTop: 0 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.grnRow} onPress={() => onPick(item.id)}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={[s.prodName, { flex: 1 }]} numberOfLines={1}>{item.supplier_name || 'GRN #' + item.id}</Text>
                <Badge text={item.status} tone={item.status === 'posted' ? 'ok' : 'warn'} />
              </View>
              <Text style={s.prodMeta}>
                Inv {item.invoice_number || '—'}{item.invoice_date ? ' · ' + day(item.invoice_date) : ''} · ₹ {num(item.grand_total)}
              </Text>
              <Text style={s.prodMeta}>
                {item.line_count} line{item.line_count === 1 ? '' : 's'}
                {item.new_products ? ` · ${item.new_products} new product${item.new_products === 1 ? '' : 's'}` : ''}
                {item.grn_no ? ` · GRN ${item.grn_no}` : ''}
              </Text>
            </TouchableOpacity>
          )}
          ListEmptyComponent={
            <Text style={{ color: C.muted, textAlign: 'center', marginTop: 30, paddingHorizontal: 24, lineHeight: 19 }}>
              No {status === 'all' ? '' : status + ' '}GRNs. A receipt appears here once the invoice
              has been read and confirmed on the desktop app.
            </Text>} />
      )}
    </View>
  );
}

// ------------------------------------------------------------ one GRN ---------
function GrnDetail({ api, grnId, cats, flash, onBack, onBreakdown, onPosted }) {
  const [grn, setGrn] = useState(null);
  const [busy, setBusy] = useState(false);
  // remounted after a breakdown is saved, so the confirmation arrives as `flash`
  const [msg, setMsg] = useState(flash ? { text: flash, tone: 'ok' } : null);
  const [scanFor, setScanFor] = useState(null);    // {line, splitId} awaiting a code

  const load = useCallback(async () => {
    try { setGrn(await api.purchase(grnId)); }
    catch (e) { setMsg({ text: e.message, tone: 'err' }); }
  }, [api, grnId]);
  useEffect(() => { load(); }, [load]);

  const setCategory = async (line, name) => {
    try { await api.editLine(line.id, { category: name }); await load(); }
    catch (e) { setMsg({ text: e.message, tone: 'err' }); }
  };
  const linkCode = async (code) => {
    const target = scanFor;
    setScanFor(null);
    try {
      await api.scanLine(target.line.id, code, target.splitId ?? null);
      await load();
      setMsg({ text: '✓ Linked to that product', tone: 'ok' });
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); }
  };

  const post = async () => {
    setBusy(true);
    try {
      const r = await api.postGrn(grnId);
      const fresh = await api.purchase(grnId);     // now carries each variant's product
      onPosted(fresh, r);
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); }
    setBusy(false);
  };
  const confirmPost = () => {
    const rows = grn.lines.reduce((n, l) => n + (l.splits.length || 1), 0);
    Alert.alert('Post this GRN?',
      `${rows} item${rows === 1 ? '' : 's'} go into stock. Each new one gets its own `
      + 'SKU and QR code, and its cost is averaged into inventory.\n\n'
      + 'Correcting a posted GRN means unposting it on the desktop app.',
      [{ text: 'Cancel', style: 'cancel' }, { text: 'Post', onPress: post }]);
  };

  if (!grn) return <View style={s.center}><ActivityIndicator color={C.accent} /></View>;
  const editable = grn.status !== 'posted';
  const unbalanced = (grn.unbalanced_splits || []).length;

  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={[s.link, s.topLink]}>‹ GRNs</Text></TouchableOpacity>
        <Text style={s.topTitle} numberOfLines={1}>{grn.supplier_name || 'GRN #' + grn.id}</Text>
        <Badge text={grn.status} tone={grn.status === 'posted' ? 'ok' : 'warn'} />
      </View>
      <Notice text={msg?.text} tone={msg?.tone} />

      <ScrollView contentContainerStyle={{ padding: 12 }}>
        <View style={[s.grnRow, { marginBottom: 14 }]}>
          <Text style={s.prodMeta}>Invoice {grn.invoice_number || '—'}{grn.invoice_date ? ' · ' + day(grn.invoice_date) : ''}</Text>
          <Text style={s.prodMeta}>
            GRN No {grn.grn_no || '—'} · {grn.line_count} line{grn.line_count === 1 ? '' : 's'}
          </Text>
          <Text style={[s.prodMeta, { color: C.text, marginTop: 6 }]}>Grand total ₹ {num(grn.grand_total)}</Text>
        </View>

        <Text style={s.sectionLabel}>Lines</Text>
        {grn.lines.map((l) => {
          // the server says so outright; falling back to the rows keeps an older
          // server from making a broken-down bundle read as a plain new product
          const isSplit = l.is_split != null ? l.is_split : l.splits.length > 0;
          return (
          <View key={l.id} style={s.lineCard}>
            <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8 }}>
              <Text style={s.lineTitle}>{l.description || '(unnamed)'}</Text>
              {isSplit
                ? <Badge text={`split · ${l.splits.length}${l.split_balanced ? '' : ' ⚠'}`}
                    tone={l.split_balanced ? 'ok' : 'warn'} />
                : <Badge text={l.is_new_product ? 'new' : 'matched'} tone={l.is_new_product ? 'warn' : 'ok'} />}
            </View>
            <Text style={s.prodMeta}>
              HSN {l.hsn || '—'} · {qtyOf(l.qty)} {l.uom} × ₹{num(l.rate)} = ₹{num(l.amount)}
            </Text>
            {/* the bundle is what the supplier billed; it never becomes stock itself */}
            {isSplit && (
              <Text style={s.prodMeta}>
                Bundle split into {l.splits.length} item{l.splits.length === 1 ? '' : 's'} — the rows
                below carry the stock, not this line.
              </Text>
            )}
            {l.splits.length > 0 && !l.split_balanced && (
              <Text style={[s.prodMeta, { color: C.warn }]}>
                {qtyOf(l.split_remainder)} of {qtyOf(l.qty)} still to break down
              </Text>
            )}

            {/* Category here means the products are born mapped instead of landing
                "unmapped" in Inventory for someone to fix one by one. */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10 }}>
              <Text style={[s.fieldLabel, { marginBottom: 0, width: 72 }]}>Category</Text>
              {editable ? (
                <Select compact allowClear style={{ flex: 1 }} value={l.category || ''} options={cats}
                  placeholder={l.category_suggestion?.best || 'auto'}
                  onChange={(v) => setCategory(l, v)} />
              ) : (
                <Text style={[s.prodMeta, { flex: 1, marginTop: 0 }]}>{l.category || l.product_category || 'auto'}</Text>
              )}
            </View>
            {/* One tap to accept what the classifier read from the description —
                and whichever category ends up here is remembered for this wording,
                so the next invoice that says it this way arrives already mapped. */}
            {editable && !l.category && l.category_suggestion?.best && (
              <TouchableOpacity onPress={() => setCategory(l, l.category_suggestion.best)}>
                <Text style={[s.hint, { color: C.accent, marginTop: 6 }]}>
                  use {l.category_suggestion.best}
                  {l.category_suggestion.via === 'alias' ? ' · learned'
                    : l.category_suggestion.confident ? '' : ' ?'}
                </Text>
              </TouchableOpacity>
            )}

            {/* saved variants — one product each, once posted */}
            {l.splits.map((sp) => (
              <View key={sp.id} style={s.varRow}>
                <View style={{ flex: 1 }}>
                  <Text style={s.varLabel}>↳ {sp.label}</Text>
                  <Text style={s.prodMeta}>
                    {qtyOf(sp.qty)} × ₹{num(sp.rate)} = ₹{num(sp.amount)}
                    {sp.mrp ? ` · MRP ${num(sp.mrp)}` : ''}
                  </Text>
                  <Text style={[s.prodMeta, s.mono]}>
                    {sp.product_sku || (grn.status === 'posted' ? '—' : 'SKU + QR on post')}
                  </Text>
                </View>
                {editable && !sp.product_id && (
                  <GhostButton title="⌗ QR" onPress={() => setScanFor({ line: l, splitId: sp.id })} />
                )}
              </View>
            ))}

            {editable && (
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
                <GhostButton style={{ flex: 1 }} onPress={() => onBreakdown(l)}
                  title={l.splits.length ? 'Edit breakdown' : 'Break down into sizes'} />
                {!l.splits.length && <GhostButton title="⌗ QR" onPress={() => setScanFor({ line: l, splitId: null })} />}
              </View>
            )}
            {!editable && !l.splits.length && (
              <Text style={[s.prodMeta, s.mono]}>{l.product_sku || '—'}</Text>
            )}
          </View>
          );
        })}
        <View style={{ height: 8 }} />
      </ScrollView>

      <View style={s.actionbar}>
        <Text style={s.hint}>
          {grn.status === 'posted'
            ? 'Posted — stock is updated. Correct it by unposting on the desktop app.'
            : unbalanced
              ? `⚠ ${unbalanced} line${unbalanced === 1 ? '' : 's'} broken down to the wrong total — fix before posting.`
              : 'Posting creates a product and a QR for every size, adds the stock, and averages the cost.'}
        </Text>
        {grn.status === 'posted'
          ? <PrimaryButton title="Show QR codes" onPress={() => onPosted(grn, null)} />
          : <PrimaryButton title="Post to inventory" busy={busy} disabled={unbalanced > 0} onPress={confirmPost} />}
      </View>

      <CodePrompt visible={!!scanFor} title="Link to an existing product"
        onCancel={() => setScanFor(null)} onSubmit={linkCode} />
    </View>
  );
}

// -------------------------------------------------------- breakdown editor ----
function Breakdown({ api, line, options, cats, onBack, onSaved }) {
  const [rows, setRows] = useState(() => {
    if (line.splits.length) {
      return line.splits.map((sp) => {
        const r = blankRow(line.rate);
        Object.keys(r).forEach((k) => { if (sp[k] != null) r[k] = String(sp[k]); });
        return r;
      });
    }
    // a first row inherits the line's category (or the mapping it would get), so
    // the common case — one category, several sizes — needs no repetition
    return [blankRow(line.rate, line.category || line.category_suggestion?.best)];
  });
  const [open, setOpen] = useState({});            // row index -> details expanded
  const [runSpec, setRunSpec] = useState('');      // "28-2-38", the run being typed
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const upd = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const drop = (i) => setRows(rows.filter((_, j) => j !== i));
  const assigned = rows.reduce((n, r) => n + (+r.qty || 0), 0);
  const billed = +line.qty || 0;
  const left = qtyOf(billed - assigned);
  const lastCat = rows[rows.length - 1]?.category || line.category || line.category_suggestion?.best;
  const run = parseSizeRun(runSpec);
  const runQtys = run.sizes.length ? spreadQty(billed, run.sizes.length) : [];
  const runEven = runQtys.length > 0 && runQtys.every((q) => q === runQtys[0]);
  const runListed = run.sizes.length > 10
    ? `${run.sizes.slice(0, 9).join(', ')}, … ${run.sizes[run.sizes.length - 1]}`
    : run.sizes.join(', ');

  const addRow = (size) => {
    if (size && rows.some((r) => r.size === size)) {
      setMsg({ text: `${size} is already a row — set its quantity below.`, tone: 'err' });
      return;
    }
    setMsg(null);
    // A size chip fills the empty row the editor opens with rather than adding a
    // second one below it: an untouched row left above the one being typed into
    // is both clutter and a trap — it fails validation at save.
    const blank = rows.findIndex((r) => !variantLabel(r) && !r.qty);
    if (size && blank > -1) {
      setRows(rows.map((r, i) => (i === blank ? { ...r, size } : r)));
      return;
    }
    setRows([...rows, { ...blankRow(line.rate, lastCat), ...(size ? { size } : {}) }]);
  };

  // Fill the rows from the run. Whatever the rows already share stays on them — a
  // bundle in six sizes is one garment six times, and only the size and the count
  // differ — so a colour and a category keyed once are not keyed again.
  const applyRun = () => {
    if (!run.sizes.length) {
      setMsg({ text: run.why || 'Write the run as start-step-end — 28-2-38.', tone: 'err' });
      return;
    }
    const fill = () => {
      const shared = {};
      SPLIT_ATTRS.forEach(([k]) => {
        if (k === 'size') return;
        const v = rows.map((r) => r[k]).find(Boolean);
        if (v) shared[k] = v;
      });
      const cat = rows.map((r) => r.category).find(Boolean) || lastCat;
      setRows(run.sizes.map((size, i) => ({
        ...blankRow(line.rate, cat), ...shared, size, qty: String(runQtys[i]),
      })));
      setOpen({});
      // more sizes than pieces: the empty ones stay on the list rather than being
      // dropped, because which of them did not come is the receiver's to say
      const empty = runQtys.some((q) => !q);
      setMsg({
        text: empty
          ? `${run.sizes.length} sizes · only ${qtyOf(billed)} billed — set or remove the rows left at zero`
          : `${run.sizes.length} sizes · ${qtyOf(billed)} spread `
            + (runEven ? `${runQtys[0]} each` : 'as evenly as it divides'),
        tone: empty ? 'err' : 'ok',
      });
    };
    const typed = rows.filter((r) => variantLabel(r) || r.qty);
    if (!typed.length) { fill(); return; }
    Alert.alert('Replace the rows below?',
      `${typed.length} row${typed.length === 1 ? '' : 's'} go, and the ${run.sizes.length} sizes `
      + `of ${runSpec} take their place — ${run.sizes.join(', ')}.`,
      [{ text: 'Keep them', style: 'cancel' },
       { text: 'Replace', style: 'destructive', onPress: fill }]);
  };

  // Same rules the server enforces, checked here so a picker is told before the
  // round trip rather than after it.
  const save = async (out) => {
    setBusy(true); setMsg(null);
    try {
      await api.setSplits(line.id, out);
      onSaved(out.length
        ? `✓ Broken into ${out.length} item${out.length === 1 ? '' : 's'}`
        : '✓ Breakdown cleared');
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); setBusy(false); }
  };
  const submit = () => {
    const kept = rows.filter((r) => variantLabel(r) || r.qty);
    for (const r of kept) {
      if (!variantLabel(r)) {
        setMsg({ text: 'Every row needs at least one attribute — a size, colour, material…', tone: 'err' }); return;
      }
      if (!(+r.qty > 0)) {
        setMsg({ text: `“${variantLabel(r)}”: enter a quantity greater than zero.`, tone: 'err' }); return;
      }
    }
    // identity is the WHOLE attribute tuple, exactly as the server compares it —
    // not the label, which two different tuples can share
    const keys = kept.map((r) => SPLIT_ATTRS.map(([k]) => (r[k] || '').trim().toLowerCase()).join(' '));
    const dupAt = keys.findIndex((x, i) => keys.indexOf(x) !== i);
    if (dupAt > -1) {
      setMsg({ text: `“${variantLabel(kept[dupAt])}” appears twice — merge those rows.`, tone: 'err' }); return;
    }
    if (kept.length && !SAME(assigned, billed)) {
      Alert.alert('Saved, but it won’t post yet',
        `The rows add up to ${qtyOf(assigned)} of ${qtyOf(billed)} billed. That's fine to save `
        + 'and come back to — the GRN just can’t be posted until it balances.',
        [{ text: 'Keep editing', style: 'cancel' }, { text: 'Save anyway', onPress: () => save(kept) }]);
      return;
    }
    save(kept);
  };
  const clear = () => Alert.alert('Clear the breakdown?',
    'The line goes back to one product for the whole billed quantity.',
    [{ text: 'Cancel', style: 'cancel' }, { text: 'Clear', style: 'destructive', onPress: () => save([]) }]);

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={[s.link, s.topLink]}>‹ Cancel</Text></TouchableOpacity>
        <Text style={s.topTitle} numberOfLines={1}>Break down</Text>
      </View>

      <View style={{ paddingHorizontal: 14, paddingTop: 12 }}>
        <Text style={s.detName} numberOfLines={2}>{line.description || '(unnamed)'}</Text>
        <Text style={[s.prodMeta, { color: SAME(assigned, billed) ? C.ok : C.muted }]}>
          {qtyOf(assigned)} of {qtyOf(billed)} {line.uom} assigned
          {SAME(assigned, billed) ? ' ✓' : ` · ${left} left`}
        </Text>
        <View style={s.bar}>
          <View style={[s.barFill, {
            width: `${Math.min(100, billed ? (assigned / billed) * 100 : 0)}%`,
            backgroundColor: SAME(assigned, billed) ? C.ok : C.accent,
          }]} />
        </View>
        <Text style={[s.hint, { marginTop: 8 }]}>
          Fill only what differs. Each row becomes its own product with its own SKU and QR.
        </Text>
      </View>

      <ScrollView contentContainerStyle={{ padding: 14, paddingTop: 6 }} keyboardShouldPersistTaps="handled">
        <Text style={s.sectionLabel}>Size run</Text>
        <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
          <TextInput style={[s.input, { flex: 1, paddingVertical: 9 }]} value={runSpec}
            placeholder="28-2-38" placeholderTextColor={C.muted}
            autoCapitalize="none" autoCorrect={false} returnKeyType="go"
            onChangeText={setRunSpec} onSubmitEditing={applyRun} />
          <GhostButton title="Generate" disabled={!run.sizes.length}
            style={{ paddingVertical: 12 }} onPress={applyRun} />
        </View>
        <Text style={[s.hint, { marginTop: 6 }, run.why ? { color: C.warn } : null]}>
          {run.why || (run.sizes.length
            ? `${runListed} — ${run.sizes.length} sizes, ${qtyOf(billed)} to place, `
              + (runEven ? `${runQtys[0]} each` : `${Math.min(...runQtys)}–${Math.max(...runQtys)} each`)
            : 'Start–step–end, the way the packing slip writes it. 28-2-38 makes 28, 30, 32, '
              + '34, 36, 38 and spreads what arrived over them; every row stays editable.')}
        </Text>

        <Text style={s.sectionLabel}>Quick add a size</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
          {(options.size || []).slice(0, 8).map((sz) => (
            <TouchableOpacity key={sz} style={s.chip} onPress={() => addRow(sz)}>
              <Text style={[s.chipText, { textTransform: 'none' }]}>{sz}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {rows.map((r, i) => (
          <View key={i} style={s.rowCard}>
            <View style={s.rowHead}>
              <Text style={s.rowIndex}>{i + 1}</Text>
              <Text style={[s.varLabel, { flex: 1 }]} numberOfLines={1}>{variantLabel(r) || 'new item'}</Text>
              <TouchableOpacity onPress={() => drop(i)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <Text style={{ color: C.muted, fontSize: 18 }}>×</Text>
              </TouchableOpacity>
            </View>

            <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 8 }}>
              <View style={{ flex: 1.2 }}>
                <Text style={s.fieldLabel}>Size</Text>
                <Select compact allowClear value={r.size} options={options.size || []}
                  placeholder="Size" onChange={(v) => upd(i, 'size', v)} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.fieldLabel}>Qty</Text>
                <TextInput style={[s.input, { paddingVertical: 8 }]} keyboardType="numeric" value={r.qty}
                  placeholder="0" placeholderTextColor={C.muted} onChangeText={(v) => upd(i, 'qty', v)} />
              </View>
              <GhostButton title="rest" style={{ paddingVertical: 10 }}
                onPress={() => {
                  const others = rows.reduce((n, x, j) => n + (j === i ? 0 : (+x.qty || 0)), 0);
                  upd(i, 'qty', String(qtyOf(Math.max(0, billed - others))));
                }} />
            </View>

            <TouchableOpacity onPress={() => setOpen({ ...open, [i]: !open[i] })} style={{ paddingVertical: 10 }}>
              <Text style={{ color: C.accent, fontSize: 12 }}>
                {open[i] ? '▾ fewer' : '▸ colour, material, category, pricing'}
              </Text>
            </TouchableOpacity>

            {open[i] && (
              <View>
                {SPLIT_ATTRS.filter(([k]) => k !== 'size').map(([k, label]) => (
                  <View key={k} style={{ marginBottom: 10 }}>
                    <Text style={s.fieldLabel}>{label}</Text>
                    {k === 'design_no' ? (
                      <TextInput style={[s.input, { paddingVertical: 8 }]} value={r[k]}
                        placeholder="e.g. SH-05" placeholderTextColor={C.muted}
                        onChangeText={(v) => upd(i, k, v)} />
                    ) : (
                      <Select compact allowClear value={r[k]} options={options[k] || []}
                        placeholder={label} onChange={(v) => upd(i, k, v)} />
                    )}
                  </View>
                ))}
                <View style={{ marginBottom: 10 }}>
                  <Text style={s.fieldLabel}>Category</Text>
                  <Select compact allowClear value={r.category} options={cats}
                    placeholder={line.category || 'auto'} onChange={(v) => upd(i, 'category', v)} />
                </View>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  {MONEY.map(([k, label]) => (
                    <View key={k} style={{ flex: 1 }}>
                      <Text style={s.fieldLabel}>{label}</Text>
                      <TextInput style={[s.input, { paddingVertical: 8, fontSize: 13 }]} keyboardType="numeric"
                        value={r[k]} placeholder={k === 'rate' ? String(line.rate ?? 0) : '—'}
                        placeholderTextColor={C.muted} onChangeText={(v) => upd(i, k, v)} />
                    </View>
                  ))}
                </View>
              </View>
            )}
          </View>
        ))}

        <View style={{ flexDirection: 'row', gap: 8, marginBottom: 10 }}>
          <GhostButton title="+ add row" style={{ flex: 1 }} onPress={() => addRow(null)} />
          <GhostButton title="⧉ duplicate last" style={{ flex: 1 }} disabled={!rows.length}
            onPress={() => setRows([...rows, { ...rows[rows.length - 1], qty: '' }])} />
        </View>
        {line.splits.length > 0 && <GhostButton title="Clear breakdown" tone="err" onPress={clear} />}
      </ScrollView>

      <View style={s.actionbar}>
        <Notice text={msg?.text} tone={msg?.tone} />
        <PrimaryButton title="Save breakdown" busy={busy} onPress={submit} />
      </View>
    </KeyboardAvoidingView>
  );
}

// ------------------------------------------------- what posting produced ------
// Every size that just became a stock record, with the QR that will be stuck to
// it. Shown straight after posting because that is the moment the labels get
// printed and the cartons get marked.
function Posted({ api, grn, result, onBack, onDetail }) {
  const [zoom, setZoom] = useState(null);          // product being viewed large
  const items = [];
  grn.lines.forEach((l) => {
    if (l.splits.length) {
      l.splits.forEach((sp) => items.push({
        key: 'sp' + sp.id, id: sp.product_id, sku: sp.product_sku,
        title: sp.label, sub: l.description, qty: sp.qty, isNew: sp.is_new_product,
        detailed: sp.product_detailed,
      }));
    } else {
      items.push({
        key: 'l' + l.id, id: l.product_id, sku: l.product_sku,
        title: l.description, sub: null, qty: l.qty, isNew: l.is_new_product,
        detailed: l.product_detailed,
      });
    }
  });
  const ids = items.filter((x) => x.id).map((x) => x.id);
  const todo = items.filter((x) => x.id && !x.detailed).length;
  // the CARTON labels are what this moment needs — the goods are on the floor and
  // have to go on a rack. Garment tags come later, from Bundles, once the box is
  // opened and its items detailed.
  const printCartons = () => Linking.openURL(api.bundleLabelsUrl(grn.id)).catch(() =>
    Alert.alert('Could not open', 'The label sheet opens in the phone browser — check the server address.'));

  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={[s.link, s.topLink]}>‹ GRNs</Text></TouchableOpacity>
        <Text style={s.topTitle}>In inventory</Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: 12 }}>
        <View style={[s.grnRow, { marginBottom: 14 }]}>
          <Text style={[s.prodName, { color: C.ok }]}>✓ Posted to inventory</Text>
          <Text style={s.prodMeta}>
            {result
              ? `${result.products_created} new product${result.products_created === 1 ? '' : 's'} · `
                + `${result.products_updated} restocked`
                + (result.size_rows ? ` · ${result.size_rows} size row${result.size_rows === 1 ? '' : 's'}` : '')
              : `${items.length} item${items.length === 1 ? '' : 's'} from this GRN`}
          </Text>
          <Text style={[s.prodMeta, { marginTop: 6 }]}>
            Each one carries its own SKU and QR, and is tracked on its own from here.
            Scan it anywhere in the system — the code resolves to the live record, and
            still reads the item when there's no network.
          </Text>
          <Text style={[s.prodMeta, { marginTop: 6, color: todo ? C.warn : C.ok }]}>
            {todo
              ? `${todo} of ${items.length} still to detail — tap one to record size, colour, `
                + 'fit and pricing.'
              : `All ${items.length} detailed ✓`}
          </Text>
        </View>

        {items.map((it) => (
          <TouchableOpacity key={it.key} style={s.prodRow} disabled={!it.id}
            onPress={() => onDetail(it.id)}>
            {it.id ? (
              <TouchableOpacity style={[s.qrBox, { marginRight: 12 }]} onPress={() => setZoom(it)}>
                <Image source={{ uri: api.qrPngUrl(it.id, 3) }} style={{ width: 54, height: 54 }} />
              </TouchableOpacity>
            ) : null}
            <View style={{ flex: 1 }}>
              <Text style={s.prodName} numberOfLines={1}>{it.title || '(unnamed)'}</Text>
              {it.sub ? <Text style={s.prodMeta} numberOfLines={1}>{it.sub}</Text> : null}
              <Text style={[s.prodMeta, s.mono]}>{it.sku || '—'} · {qtyOf(it.qty)} in</Text>
            </View>
            <Badge text={it.detailed ? 'detailed' : 'to detail'} tone={it.detailed ? 'ok' : 'warn'} />
          </TouchableOpacity>
        ))}
      </ScrollView>

      <View style={s.actionbar}>
        <Text style={s.hint}>
          Print the carton labels and stick one on each box — that is what gets it onto a rack.
          The garment tags come later, from <Text style={{ color: C.text }}>Bundles</Text>, once
          the box is opened and its items detailed.
        </Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <GhostButton title="Carton labels" style={{ flex: 1, paddingVertical: 13 }} onPress={printCartons} />
          <PrimaryButton title="Done" style={{ flex: 1 }} onPress={onBack} />
        </View>
      </View>

      <Modal visible={!!zoom} transparent animationType="fade" onRequestClose={() => setZoom(null)}>
        <TouchableOpacity style={[s.modalWrap, { justifyContent: 'center', alignItems: 'center' }]}
          activeOpacity={1} onPress={() => setZoom(null)}>
          <View style={[s.card, { alignItems: 'center' }]}>
            <View style={s.qrBox}>
              <Image source={{ uri: api.qrPngUrl(zoom?.id, 8) }} style={{ width: 230, height: 230 }} />
            </View>
            <Text style={[s.prodName, { marginTop: 12 }]}>{zoom?.title}</Text>
            <Text style={[s.prodMeta, s.mono]}>{zoom?.sku}</Text>
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

// ---------------------------------------------------------------- module ------
export default function GrnModule({ api, options, cats, employee, onLogout }) {
  const [view, setView] = useState('list');        // list | grn | breakdown | posted | detail
  const [grnId, setGrnId] = useState(null);
  const [line, setLine] = useState(null);
  const [posted, setPosted] = useState(null);      // {grn, result}
  const [item, setItem] = useState(null);          // product being detailed
  // remounts GrnDetail after a breakdown is saved, so it reloads from the server
  const [stamp, setStamp] = useState(0);
  const [flash, setFlash] = useState('');

  // Detailing happens right here rather than back in the Products tab: the picker
  // has the item in their hands, and the posted screen is the list of exactly the
  // items that still need it. Returning re-reads the GRN so the row's status moves
  // to "detailed" without a manual refresh.
  const openDetail = async (productId) => {
    if (!productId) return;
    try {
      setItem(await api.product(productId));
      setView('detail');
    } catch (e) { Alert.alert('Could not open', e.message); }
  };
  const afterDetail = async () => {
    try {
      const fresh = await api.purchase(grnId);
      setPosted((p) => ({ grn: fresh, result: p ? p.result : null }));
    } catch { /* keep what is on screen; the row just won't tick over yet */ }
    setItem(null);
    setView('posted');
  };

  if (view === 'detail' && item) {
    return (
      <DetailScreen api={api} product={item} options={options} employee={employee}
        backLabel="Items" onBack={() => { setItem(null); setView('posted'); }}
        onSaved={afterDetail} />
    );
  }

  if (view === 'breakdown' && line) {
    return (
      <Breakdown api={api} line={line} options={options} cats={cats}
        onBack={() => setView('grn')}
        onSaved={(text) => { setFlash(text); setStamp((n) => n + 1); setView('grn'); }} />
    );
  }
  if (view === 'posted' && posted && posted.grn) {
    return <Posted api={api} grn={posted.grn} result={posted.result} onDetail={openDetail}
      onBack={() => { setPosted(null); setGrnId(null); setView('list'); }} />;
  }
  if (view === 'grn' && grnId) {
    return (
      <GrnDetail key={grnId + ':' + stamp} api={api} grnId={grnId} cats={cats} flash={flash}
        onBack={() => { setGrnId(null); setFlash(''); setView('list'); }}
        onBreakdown={(l) => { setLine(l); setView('breakdown'); }}
        onPosted={(grn, result) => { setPosted({ grn, result }); setView('posted'); }} />
    );
  }
  return <GrnList api={api} onLogout={onLogout} onPick={(id) => { setGrnId(id); setView('grn'); }} />;
}
