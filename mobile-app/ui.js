// Small shared controls. Everything here is used by both the product-detail side
// and the GRN side, so neither invents its own version of a dropdown.
import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, FlatList, Modal, ActivityIndicator,
} from 'react-native';
import { C, s } from './theme';

const match = (options, q) => {
  const t = (q || '').trim().toLowerCase();
  return t ? (options || []).filter((o) => String(o).toLowerCase().includes(t)) : (options || []);
};

export function Labeled({ label, children }) {
  return <View style={{ marginBottom: 12 }}><Text style={s.fieldLabel}>{label}</Text>{children}</View>;
}

/** Dropdown that also accepts a value that isn't in the list yet — the warehouse
 *  meets sizes and materials the option lists have never seen, and stopping to
 *  add one to a master first is how a receiving screen gets abandoned. */
export function Select({ label, value, options, onChange, placeholder, compact, allowClear, style }) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState('');
  const box = compact
    ? [s.input, { paddingVertical: 8, paddingHorizontal: 10 }]
    : s.input;
  return (
    <View style={[{ marginBottom: compact ? 0 : 12 }, style]}>
      {label ? <Text style={s.fieldLabel}>{label}</Text> : null}
      <TouchableOpacity style={box} onPress={() => setOpen(true)}>
        <Text numberOfLines={1} style={{ color: value ? C.text : C.muted, fontSize: compact ? 13 : 14 }}>
          {value || placeholder || 'Select…'}
        </Text>
      </TouchableOpacity>
      <Modal visible={open} transparent animationType="slide" onRequestClose={() => setOpen(false)}>
        <View style={s.modalWrap}>
          <View style={s.modalCard}>
            <View style={s.modalHandle} />
            <View style={s.modalHead}>
              <Text style={s.modalTitle}>{label || placeholder || 'Choose'}</Text>
              <TouchableOpacity onPress={() => setOpen(false)}><Text style={{ color: C.muted, fontSize: 20 }}>×</Text></TouchableOpacity>
            </View>
            {/* One box, two jobs: it filters the list as you type and it is also
                how a value that isn't in the list gets used. The category master
                runs to hundreds of names — scrolling that on a phone is not a
                thing anyone does twice. */}
            <View style={{ flexDirection: 'row', padding: 10, gap: 8 }}>
              <TextInput style={[s.input, { flex: 1 }]} placeholder="Type to search, or enter your own…"
                placeholderTextColor={C.muted} autoCorrect={false} value={custom} onChangeText={setCustom} />
              <TouchableOpacity style={s.btnSm} onPress={() => { if (custom.trim()) { onChange(custom.trim()); setCustom(''); setOpen(false); } }}>
                <Text style={s.btnSmText}>Use</Text></TouchableOpacity>
            </View>
            {allowClear && value ? (
              <TouchableOpacity style={s.optRow} onPress={() => { onChange(''); setCustom(''); setOpen(false); }}>
                <Text style={{ color: C.muted }}>— clear —</Text>
              </TouchableOpacity>
            ) : null}
            <FlatList data={match(options, custom)} keyExtractor={(x, i) => x + i} style={{ maxHeight: 360 }}
              keyboardShouldPersistTaps="handled"
              renderItem={({ item }) => (
                <TouchableOpacity style={s.optRow} onPress={() => { onChange(item); setCustom(''); setOpen(false); }}>
                  <Text style={{ color: item === value ? C.accent : C.text, fontSize: 14, fontWeight: item === value ? '600' : '400' }}>{item}</Text>
                </TouchableOpacity>
              )}
              ListEmptyComponent={
                <Text style={{ color: C.muted, fontSize: 13, padding: 18 }}>
                  Nothing matches “{custom.trim()}” — tap Use to enter it anyway.
                </Text>} />
          </View>
        </View>
      </Modal>
    </View>
  );
}

export function Badge({ text, tone }) {
  const bg = { ok: C.successBg, warn: C.pendingBg, err: C.dangerBg, mute: C.neutralBg }[tone] || C.neutralBg;
  const fg = { ok: C.successText, warn: C.pendingText, err: C.dangerText, mute: C.neutralText }[tone] || C.neutralText;
  const bd = { ok: C.successBorder, warn: C.pendingBorder, err: C.dangerBorder, mute: C.neutralBorder }[tone] || C.neutralBorder;
  return (
    <View style={[s.badge, { backgroundColor: bg, borderColor: bd }]}>
      <View style={[s.badgeDot, { backgroundColor: fg }]} />
      <Text style={[s.badgeText, { color: fg }]}>{text}</Text>
    </View>
  );
}

export function GhostButton({ title, onPress, disabled, style, tone }) {
  return (
    <TouchableOpacity style={[s.btnGhost, style, disabled && { opacity: 0.5 }]} onPress={onPress} disabled={disabled}>
      <Text style={[s.btnGhostText, tone === 'err' && { color: C.err }]}>{title}</Text>
    </TouchableOpacity>
  );
}

export function PrimaryButton({ title, onPress, disabled, busy, style }) {
  return (
    <TouchableOpacity style={[s.btn, { marginTop: 0 }, style, disabled && { opacity: 0.5 }]}
      onPress={onPress} disabled={disabled || busy}>
      {busy ? <ActivityIndicator color={C.onChrome} /> : <Text style={s.btnText}>{title}</Text>}
    </TouchableOpacity>
  );
}

/** Ask for a scanned/typed code. React Native has no window.prompt, and the
 *  warehouse's handhelds type into a focused field like a keyboard anyway. */
export function CodePrompt({ visible, title, onCancel, onSubmit }) {
  const [code, setCode] = useState('');
  const submit = () => { const v = code.trim(); if (v) { setCode(''); onSubmit(v); } };
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
      <View style={[s.modalWrap, { justifyContent: 'center', padding: 24 }]}>
        <View style={[s.card, { alignSelf: 'center' }]}>
          <Text style={[s.modalTitle, { marginBottom: 10 }]}>{title || 'Scan a code'}</Text>
          <TextInput style={s.input} autoFocus autoCapitalize="characters" autoCorrect={false}
            placeholder="Scan, or type the SKU…" placeholderTextColor={C.muted}
            value={code} onChangeText={setCode} onSubmitEditing={submit} returnKeyType="go" />
          <Text style={s.hint}>A scanned QR pastes its whole payload — that works too.</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <GhostButton title="Cancel" style={{ flex: 1 }} onPress={() => { setCode(''); onCancel(); }} />
            <PrimaryButton title="Link" style={{ flex: 1 }} onPress={submit} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

/** A one-line message that replaces the web app's toast. */
export function Notice({ text, tone }) {
  if (!text) return null;
  const fg = tone === 'err' ? C.err : tone === 'ok' ? C.ok : C.muted;
  return <Text style={{ color: fg, fontSize: 12, paddingHorizontal: 14, paddingBottom: 8 }}>{text}</Text>;
}
