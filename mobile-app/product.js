// Detailing one product: what the person holding it can see and the invoice could
// not say. Split out of App.js so the Receive tab can open it directly on an item
// the GRN just created — the picker details each size where they are standing,
// instead of finding it again later in the Products list.
import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, Image,
  ActivityIndicator, KeyboardAvoidingView, Platform, Alert,
} from 'react-native';
import { C, s } from './theme';
import { Select, Labeled } from './ui';

export default function DetailScreen({ api, product, options, onBack, onSaved, employee, backLabel }) {
  const [f, setF] = useState({
    color: product.color || '', size: product.size || '', pattern: product.pattern || '',
    fit: product.fit || '', product_type: product.product_type || '', material: product.material || '',
    design_no: product.design_no || '', mrp: product.mrp != null ? String(product.mrp) : '',
    sale_price: product.sale_price != null ? String(product.sale_price) : '',
    sale_discount_pct: product.sale_discount_pct != null ? String(product.sale_discount_pct) : '',
  });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((o) => ({ ...o, [k]: v }));
  const num = (v) => (v === '' ? null : Number(v));
  const save = async () => {
    setBusy(true);
    try {
      const saved = await api.detail(product.id, {
        ...f, mrp: num(f.mrp), sale_price: num(f.sale_price),
        sale_discount_pct: num(f.sale_discount_pct),
        detailed_by: employee || 'mobile',
      });
      onSaved(saved);
    } catch (e) { Alert.alert('Save failed', e.message); setBusy(false); }
  };
  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={[s.link, s.topLink]}>‹ {backLabel || 'Back'}</Text></TouchableOpacity>
        <Text style={s.topTitle} numberOfLines={1}> </Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <View style={{ flex: 1 }}>
            <Text style={s.detName}>{product.description}</Text>
            <Text style={[s.prodMeta, s.mono]}>{product.sku || 'no SKU yet'}</Text>
            <Text style={s.prodMeta}>HSN {product.hsn || '—'} · stock {product.stock_qty} {product.uom}</Text>
            <Text style={s.prodMeta}>from {product.supplier_name || '—'} · cost ₹{product.avg_cost}</Text>
          </View>
          {/* the code this item is tracked by, so it can be checked against the
              label on the item in hand before its details are written */}
          {product.sku ? (
            <View style={s.qrBox}>
              <Image source={{ uri: api.qrPngUrl(product.id, 3) }} style={{ width: 66, height: 66 }} />
            </View>
          ) : null}
        </View>
        <View style={{ height: 14 }} />

        <Select label="Color" value={f.color} options={options.color || []} onChange={(v) => set('color', v)} />
        <Select label="Size" value={f.size} options={options.size || []} onChange={(v) => set('size', v)} />
        <Select label="Pattern" value={f.pattern} options={options.pattern || []} onChange={(v) => set('pattern', v)} />
        <Select label="Fit" value={f.fit} options={options.fit || []} onChange={(v) => set('fit', v)} />
        <Select label="Type" value={f.product_type} options={options.product_type || []} onChange={(v) => set('product_type', v)} />
        <Select label="Material" value={f.material} options={options.material || []} onChange={(v) => set('material', v)} />
        <Labeled label="Design No">
          <TextInput style={s.input} value={f.design_no} onChangeText={(v) => set('design_no', v)}
            placeholder="e.g. SH-05" placeholderTextColor={C.muted} />
        </Labeled>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <View style={{ flex: 1 }}><Labeled label="MRP"><TextInput style={s.input} keyboardType="numeric" value={f.mrp} onChangeText={(v) => set('mrp', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
          <View style={{ flex: 1 }}><Labeled label="Sale price"><TextInput style={s.input} keyboardType="numeric" value={f.sale_price} onChangeText={(v) => set('sale_price', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
          <View style={{ flex: 1 }}><Labeled label="Discount %"><TextInput style={s.input} keyboardType="numeric" value={f.sale_discount_pct} onChangeText={(v) => set('sale_discount_pct', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
        </View>
        {product.detailed
          ? <Text style={[s.hint, { color: C.ok }]}>Already detailed by {product.detailed_by || '—'}. Saving updates it.</Text>
          : <Text style={s.hint}>Saving marks this item detailed and confirms its QR code.</Text>}
        <TouchableOpacity style={[s.btn, { marginTop: 8 }]} onPress={save} disabled={busy}>
          {busy ? <ActivityIndicator color={C.onChrome} /> : <Text style={s.btnText}>Save details</Text>}
        </TouchableOpacity>
        <View style={{ height: 20 }} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
