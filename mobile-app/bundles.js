// Bundles — the carton, its label, and the garment tags that come later.
//
// Two labels, two moments. The CARTON label is printed the moment a GRN posts and
// answers "which box is this, what's in it, where does it live" — so this screen's
// first job is scan it, put it away, find it again. The GARMENT tags print from
// here too, but only at the end: once the box has been opened and every item
// inside detailed. Tagging fifty loose garments at GRN — before anyone has looked
// at them, and before they sit in a carton for a fortnight — is the work this
// ordering exists to avoid.
import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, FlatList,
  ActivityIndicator, Image, Alert, Linking,
} from 'react-native';
import { C, s } from './theme';
import { Select, Badge, GhostButton, PrimaryButton, Notice } from './ui';
import DetailScreen from './product';

const num = (v) => (v == null || v === '' ? '—' : Number(v).toFixed(2));

function BundleList({ api, onPick, onLogout }) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('stored');
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setMsg(null);
    try { setItems(await api.bundles(status === 'all' ? '' : status)); }
    catch (e) { setMsg({ text: e.message, tone: 'err' }); }
    setLoading(false);
  }, [api, status]);
  useEffect(() => { load(); }, [load]);

  const find = async () => {
    const c = code.trim();
    if (!c) return;
    try { const b = await api.bundleLookup(c); setCode(''); onPick(b.id); }
    catch (e) { setMsg({ text: 'No bundle for that code.', tone: 'err' }); }
  };

  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <Text style={s.topTitle}>Bundles</Text>
        <Text style={s.topCount}>{items.length} shown</Text>
        <TouchableOpacity onPress={onLogout}><Text style={[s.link, s.topLink]}>Logout</Text></TouchableOpacity>
      </View>
      <View style={{ padding: 12, gap: 8 }}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput style={[s.input, { flex: 1 }]} placeholder="Scan / type a bundle code…"
            placeholderTextColor={C.muted} autoCapitalize="characters" autoCorrect={false}
            value={code} onChangeText={setCode} onSubmitEditing={find} returnKeyType="go" />
          <TouchableOpacity style={s.btnSm} onPress={find}><Text style={s.btnSmText}>Go</Text></TouchableOpacity>
        </View>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {['stored', 'opened', 'tagged', 'all'].map((st) => (
            <TouchableOpacity key={st} style={[s.chip, status === st && s.chipOn]} onPress={() => setStatus(st)}>
              <Text style={[s.chipText, status === st && s.chipTextOn]}>{st}</Text>
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={[s.chip, { marginLeft: 'auto' }]} onPress={load}>
            <Text style={s.chipText}>↻</Text></TouchableOpacity>
        </View>
      </View>
      <Notice text={msg?.text} tone={msg?.tone} />
      {loading ? <ActivityIndicator color={C.accent} style={{ marginTop: 20 }} /> : (
        <FlatList data={items} keyExtractor={(x) => String(x.id)} contentContainerStyle={{ padding: 12, paddingTop: 0 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.prodRow} onPress={() => onPick(item.id)}>
              <View style={[s.qrBox, { marginRight: 12 }]}>
                <Image source={{ uri: api.bundleQrPngUrl(item.id, 3) }} style={{ width: 48, height: 48 }} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.prodName} numberOfLines={1}>{item.description || '(unnamed)'}</Text>
                <Text style={[s.prodMeta, s.mono]}>{item.code}</Text>
                <Text style={s.prodMeta}>
                  {item.qty != null ? item.qty : '—'} {item.uom} · {item.item_count} item{item.item_count === 1 ? '' : 's'}
                </Text>
                <Text style={[s.prodMeta, !item.location && { color: C.warn }]}>
                  {item.location ? '📍 ' + item.location : 'not put away'} · GRN {item.grn_no || '—'}
                </Text>
              </View>
              <Badge text={item.status} tone={item.status === 'tagged' ? 'ok' : 'warn'} />
            </TouchableOpacity>
          )}
          ListEmptyComponent={
            <Text style={{ color: C.muted, textAlign: 'center', marginTop: 30, paddingHorizontal: 24, lineHeight: 19 }}>
              {status === 'stored'
                ? 'No cartons in store. They appear here the moment a GRN is posted.'
                : 'No bundles.'}
            </Text>} />
      )}
    </View>
  );
}

function BundleDetail({ api, bundleId, onBack, onDetailItem, flash }) {
  const [b, setB] = useState(null);
  const [loc, setLoc] = useState('');
  const [locs, setLocs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(flash ? { text: flash, tone: 'ok' } : null);

  const load = useCallback(async () => {
    try {
      const d = await api.bundle(bundleId);
      setB(d); setLoc(d.location || '');
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); }
  }, [api, bundleId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.bundleLocations().then(setLocs).catch(() => {}); }, [api]);

  const putAway = async () => {
    if (!loc.trim()) { setMsg({ text: 'Enter where it went.', tone: 'err' }); return; }
    setBusy(true);
    try {
      await api.bundleLocate(bundleId, loc.trim());
      await load();
      api.bundleLocations().then(setLocs).catch(() => {});
      setMsg({ text: '✓ ' + loc.trim(), tone: 'ok' });
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); }
    setBusy(false);
  };

  const openItem = (it) => {
    // opening the box is what detailing its contents means, so record it
    if (b.status === 'stored') api.bundleOpen(bundleId).catch(() => {});
    onDetailItem(it.id);
  };

  const printItemTags = () => Linking.openURL(api.bundleItemLabelsUrl(bundleId)).catch(() =>
    Alert.alert('Could not open', 'Labels open in the phone browser — check the server address.'));

  const tag = async () => {
    setBusy(true);
    try {
      const r = await api.bundleTag(bundleId);
      await load();
      setMsg({ text: `✓ Tagged · ${r.label_ids.length} garment label(s)`, tone: 'ok' });
      printItemTags();
    } catch (e) { setMsg({ text: e.message, tone: 'err' }); }
    setBusy(false);
  };

  if (!b) return <View style={s.center}><ActivityIndicator color={C.accent} /></View>;
  const pending = b.items_pending_detail || 0;
  const tagged = b.status === 'tagged';

  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={[s.link, s.topLink]}>‹ Bundles</Text></TouchableOpacity>
        <Text style={[s.topTitle, s.mono]} numberOfLines={1}>{b.code}</Text>
        <Badge text={b.status} tone={tagged ? 'ok' : 'warn'} />
      </View>
      <Notice text={msg?.text} tone={msg?.tone} />

      <ScrollView contentContainerStyle={{ padding: 12 }}>
        <View style={[s.grnRow, { flexDirection: 'row', gap: 12 }]}>
          <View style={s.qrBox}>
            <Image source={{ uri: api.bundleQrPngUrl(bundleId, 4) }} style={{ width: 84, height: 84 }} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={s.prodName} numberOfLines={2}>{b.description || '(unnamed)'}</Text>
            <Text style={s.prodMeta}>
              {b.qty != null ? b.qty : '—'} {b.uom} · {b.item_count} item{b.item_count === 1 ? '' : 's'}
            </Text>
            {(b.mix || []).length > 0 && (
              <Text style={s.prodMeta}>{b.mix.map((m) => `${m.qty} ${m.label}`).join(' · ')}</Text>
            )}
            <Text style={s.prodMeta}>GRN {b.grn_no || '—'} · Inv {b.invoice_number || '—'}</Text>
            <Text style={s.prodMeta}>{b.supplier_name || ''}</Text>
          </View>
        </View>

        <Text style={s.sectionLabel}>Where is it?</Text>
        <View style={[s.lineCard, { marginBottom: 14 }]}>
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-end' }}>
            <View style={{ flex: 1 }}>
              <TextInput style={s.input} value={loc} onChangeText={setLoc} autoCapitalize="characters"
                placeholder="e.g. RACK A3" placeholderTextColor={C.muted} />
            </View>
            <GhostButton title={b.location ? 'Move' : 'Put away'} onPress={putAway} disabled={busy}
              style={{ paddingVertical: 13 }} />
          </View>
          {locs.length > 0 && (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
              {locs.slice(0, 6).map((l) => (
                <TouchableOpacity key={l} style={s.chip} onPress={() => setLoc(l)}>
                  <Text style={[s.chipText, { textTransform: 'none' }]}>{l}</Text>
                </TouchableOpacity>
              ))}
            </View>
          )}
          <Text style={s.hint}>
            {b.location
              ? `Put away${b.located_by ? ' by ' + b.located_by : ''} — scan the carton and change this to move it.`
              : 'The carton label is for exactly this: scan it, then say where it went.'}
          </Text>
        </View>

        <Text style={s.sectionLabel}>Inside this carton</Text>
        <Text style={[s.prodMeta, { marginBottom: 8, color: pending ? C.warn : C.ok }]}>
          {pending
            ? `${pending} of ${b.items.length} still to detail — tap one to record size, colour, fit and pricing.`
            : `All ${b.items.length} detailed ✓ — ready to tag.`}
        </Text>
        {(b.items || []).map((it) => (
          <TouchableOpacity key={it.id} style={s.prodRow} onPress={() => openItem(it)}>
            <View style={{ flex: 1 }}>
              <Text style={s.prodName} numberOfLines={1}>
                {[it.size, it.color, it.material].filter(Boolean).join(' · ') || it.description || '—'}
              </Text>
              <Text style={[s.prodMeta, s.mono]}>
                {it.sku || '—'} · stock {it.stock_qty}{it.mrp ? ` · MRP ${num(it.mrp)}` : ''}
              </Text>
            </View>
            <Badge text={it.detailed ? 'detailed' : 'to detail'} tone={it.detailed ? 'ok' : 'warn'} />
          </TouchableOpacity>
        ))}
        <View style={{ height: 8 }} />
      </ScrollView>

      <View style={s.actionbar}>
        <Text style={s.hint}>
          {tagged
            ? `Tagged${b.tagged_by ? ' by ' + b.tagged_by : ''} — every item carries its own label now.`
            : pending
              ? 'Garment tags print once every item has been detailed — that is what the second label is for.'
              : 'Tag the items: prints one garment label each and closes the carton off.'}
        </Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <GhostButton title="Carton label" style={{ flex: 1, paddingVertical: 13 }}
            onPress={() => Linking.openURL(api.bundleLabelUrl(bundleId)).catch(() => {})} />
          {tagged
            ? <GhostButton title="Reprint tags" style={{ flex: 1, paddingVertical: 13 }} onPress={printItemTags} />
            : <PrimaryButton title="Tag & print" style={{ flex: 1 }} busy={busy} disabled={pending > 0} onPress={tag} />}
        </View>
      </View>
    </View>
  );
}

export default function BundleModule({ api, options, employee, onLogout }) {
  const [view, setView] = useState('list');     // list | bundle | detail
  const [bundleId, setBundleId] = useState(null);
  const [item, setItem] = useState(null);
  const [stamp, setStamp] = useState(0);
  const [flash, setFlash] = useState('');

  const openDetail = async (productId) => {
    if (!productId) return;
    try { setItem(await api.product(productId)); setView('detail'); }
    catch (e) { Alert.alert('Could not open', e.message); }
  };

  if (view === 'detail' && item) {
    return (
      <DetailScreen api={api} product={item} options={options} employee={employee}
        backLabel="Bundle" onBack={() => { setItem(null); setView('bundle'); }}
        onSaved={() => {
          setItem(null); setFlash('✓ Saved · QR confirmed');
          setStamp((n) => n + 1); setView('bundle');
        }} />
    );
  }
  if (view === 'bundle' && bundleId) {
    return (
      <BundleDetail key={bundleId + ':' + stamp} api={api} bundleId={bundleId} flash={flash}
        onBack={() => { setBundleId(null); setFlash(''); setView('list'); }}
        onDetailItem={openDetail} />
    );
  }
  return <BundleList api={api} onLogout={onLogout}
    onPick={(id) => { setBundleId(id); setFlash(''); setView('bundle'); }} />;
}
