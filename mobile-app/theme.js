// Colours and the shared stylesheet. Split out of App.js when the GRN screens
// arrived so both halves of the app — detailing a product and receiving a GRN —
// look like one app rather than two.
import { StyleSheet, Platform } from 'react-native';

// react-native has no cross-platform monospace alias
const MONO = Platform.OS === 'ios' ? 'Menlo' : 'monospace';

// The ESSA palette: white surfaces on a pale-green page, a dark forest-green
// header, green accents. Key names from the old theme are kept so every screen
// keeps resolving; the rest are the design's chrome and status-badge tokens.
export const C = {
  bg: '#F7F9F7', panel: '#FFFFFF', panel2: '#F7F9F7', line: '#DCE5DF',
  text: '#17221D', muted: '#68756E', accent: '#2F8F68',
  // amber-700: #D97706 is the design's warning ICON colour, but warn is used as
  // text on white here and needs AA contrast
  ok: '#2F8F68', warn: '#B45309', err: '#E11D48',

  // chrome
  primary: '#0B3D2E', primaryDeep: '#082C21', tint: '#E7F4EC',
  onChrome: '#FFFFFF', onChrome2: '#E7F4EC', onChromeMuted: 'rgba(231,244,236,0.8)',
  warnIcon: '#D97706',

  // status badges
  successBg: '#E7F4EC', successText: '#0B3D2E', successBorder: 'rgba(47,143,104,0.3)',
  pendingBg: '#FFFBEB', pendingText: '#78350F', pendingBorder: '#FDE68A',
  neutralBg: '#F7F9F7', neutralText: '#68756E', neutralBorder: '#DCE5DF',
  dangerBg: '#FFF1F2', dangerText: '#9F1239', dangerBorder: '#FECDD3',

  backdrop: 'rgba(11,61,46,0.4)',
};

// card depth: 0 1px 2px rgba(0,0,0,.03)
const CARD_SHADOW = {
  shadowColor: '#000', shadowOpacity: 0.03, shadowRadius: 2, shadowOffset: { width: 0, height: 1 }, elevation: 1,
};

export const s = StyleSheet.create({
  center: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', padding: 24 },
  brand: { color: C.text, fontSize: 22, fontWeight: '700', letterSpacing: -0.3, marginTop: 10 },
  logoAI: {
    color: C.onChrome, backgroundColor: C.accent, fontSize: 26, fontWeight: '800', letterSpacing: 1,
    width: 64, height: 64, lineHeight: 64, textAlign: 'center', borderRadius: 12, overflow: 'hidden',
  },
  sub: { color: C.muted, fontSize: 13, marginTop: 4, marginBottom: 18 },
  card: { width: '100%', maxWidth: 420, backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 20, ...CARD_SHADOW },
  fieldLabel: { color: C.muted, fontSize: 11, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 5 },
  input: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 11, color: C.text, fontSize: 14 },
  hint: { color: C.muted, fontSize: 11, lineHeight: 16, marginTop: 2, marginBottom: 8 },
  err: { color: C.err, fontSize: 13, marginBottom: 8, textAlign: 'center' },
  btn: { backgroundColor: C.primary, borderColor: C.primary, borderWidth: 1, borderRadius: 8, minHeight: 44, paddingVertical: 11, alignItems: 'center', justifyContent: 'center', marginTop: 6, ...CARD_SHADOW },
  btnText: { color: C.onChrome, fontWeight: '600', fontSize: 14 },
  btnSm: { backgroundColor: C.primary, borderRadius: 8, minHeight: 44, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center' },
  btnSmText: { color: C.onChrome, fontWeight: '600', fontSize: 14 },
  link: { color: C.accent, fontSize: 13, fontWeight: '600', textAlign: 'center', marginTop: 12 },
  topbar: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 56, paddingHorizontal: 14, paddingVertical: 12, borderBottomColor: C.primaryDeep, borderBottomWidth: 1, backgroundColor: C.primary },
  topTitle: { color: C.onChrome, fontSize: 18, fontWeight: '700', letterSpacing: -0.2, flex: 1 },
  topCount: { color: C.onChromeMuted, fontSize: 12 },
  // a text link sitting on the green header (Logout, ‹ back)
  topLink: { color: C.onChrome2, fontSize: 13, fontWeight: '600', marginTop: 0 },
  chip: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 8, minHeight: 36, paddingHorizontal: 14, paddingVertical: 7, alignItems: 'center', justifyContent: 'center' },
  chipOn: { backgroundColor: C.primary, borderColor: C.primary },
  chipText: { color: C.muted, fontSize: 13, fontWeight: '500', textTransform: 'capitalize' },
  chipTextOn: { color: C.onChrome, fontWeight: '600' },
  prodRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 8, ...CARD_SHADOW },
  prodName: { color: C.text, fontSize: 14, fontWeight: '600' },
  prodMeta: { color: C.muted, fontSize: 12, marginTop: 3 },
  badge: { flexDirection: 'row', alignItems: 'center', borderRadius: 6, borderWidth: 1, borderColor: C.neutralBorder, backgroundColor: C.neutralBg, paddingHorizontal: 8, paddingVertical: 3 },
  badgeDone: { backgroundColor: C.successBg, borderColor: C.successBorder },
  badgePend: { backgroundColor: C.pendingBg, borderColor: C.pendingBorder },
  badgeText: { color: C.neutralText, fontFamily: MONO, fontSize: 10, fontWeight: '500', textTransform: 'uppercase', letterSpacing: 0.5 },
  badgeDoneText: { color: C.successText },
  badgePendText: { color: C.pendingText },
  badgeDot: { width: 6, height: 6, borderRadius: 3, marginRight: 5, opacity: 0.8, backgroundColor: C.neutralText },
  detName: { color: C.text, fontSize: 20, fontWeight: '700', letterSpacing: -0.3 },
  modalWrap: { flex: 1, backgroundColor: C.backdrop, justifyContent: 'flex-end' },
  modalCard: {
    backgroundColor: C.panel, borderTopLeftRadius: 16, borderTopRightRadius: 16, borderColor: C.line, borderWidth: 1,
    paddingBottom: 24,
    shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 25, shadowOffset: { width: 0, height: -4 }, elevation: 12,
  },
  modalHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: C.line, alignSelf: 'center', marginTop: 8, marginBottom: 8 },
  modalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 14, backgroundColor: C.panel2, borderTopColor: C.line, borderTopWidth: 1, borderBottomColor: C.line, borderBottomWidth: 1 },
  modalTitle: { color: C.text, fontSize: 16, fontWeight: '700' },
  optRow: { paddingHorizontal: 18, paddingVertical: 14, borderBottomColor: C.line, borderBottomWidth: 1 },

  // ---- shared small pieces ----
  mono: { fontFamily: MONO, letterSpacing: 0.2 },
  tabbar: { flexDirection: 'row', backgroundColor: C.panel, borderBottomColor: C.line, borderBottomWidth: 1 },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center', minHeight: 44, paddingVertical: 12, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  tabOn: { borderBottomColor: C.accent },
  tabText: { color: C.muted, fontSize: 14, fontWeight: '500' },
  tabTextOn: { color: C.primary, fontWeight: '600' },
  btnGhost: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 8, minHeight: 40, paddingHorizontal: 12, paddingVertical: 9, alignItems: 'center', justifyContent: 'center', ...CARD_SHADOW },
  btnGhostText: { color: C.text, fontSize: 13, fontWeight: '600' },
  actionbar: { borderTopColor: C.line, borderTopWidth: 1, backgroundColor: C.panel, padding: 12, gap: 8 },

  // ---- GRN ----
  grnRow: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 8, ...CARD_SHADOW },
  lineCard: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 10, ...CARD_SHADOW },
  lineTitle: { color: C.text, fontSize: 14, fontWeight: '600', flex: 1 },
  varRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, borderTopColor: C.line, borderTopWidth: 1 },
  varLabel: { color: C.text, fontSize: 13, fontWeight: '600' },
  rowCard: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 10, ...CARD_SHADOW },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 10 },
  rowIndex: { color: C.primary, backgroundColor: C.tint, fontSize: 11, fontWeight: '700', minWidth: 22, textAlign: 'center', paddingHorizontal: 5, paddingVertical: 2, borderRadius: 6, overflow: 'hidden' },
  bar: { height: 6, borderRadius: 3, backgroundColor: C.line, overflow: 'hidden', marginTop: 8 },
  barFill: { height: 6, borderRadius: 3, backgroundColor: C.accent },
  qrBox: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 8, padding: 3 },
  sectionLabel: { color: C.muted, fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6, marginTop: 4, marginBottom: 8 },
});
