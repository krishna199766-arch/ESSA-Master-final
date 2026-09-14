// ESSA Warehouse — the phone app.
//
// Three jobs, one per tab, in the order the goods move:
//   Receive  — open a GRN, break a billed bundle into the sizes that arrived,
//              post it. Posting creates a product + QR per size, and a carton
//              label per line (grn.js).
//   Bundles  — the cartons: scan one, put it away, and later open it and tag its
//              items for sale (bundles.js).
//   Products — record what each item physically is (colour, fit, pricing).
//
// Everything writes to the same ESSA database over WiFi; there is no local store
// beyond the server address and the login token.
import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, FlatList,
  ActivityIndicator, KeyboardAvoidingView, Platform, Alert,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { C, s } from './theme';
import { Labeled } from './ui';
import { makeApi } from './api';
import GrnModule from './grn';
import BundleModule from './bundles';
import DetailScreen from './product';

// ---------------- screens ----------------
function ServerScreen({ initial, onSave }) {
  const [url, setUrl] = useState(initial || 'http://192.168.1.5:8000');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const save = async () => {
    setErr(''); setBusy(true);
    try {
      const api = makeApi(url.trim());
      await api.ping();               // reachability check
      await onSave(url.trim());
    } catch (e) { setErr('Could not reach that server. Check the IP, port and same-WiFi.'); }
    setBusy(false);
  };
  return (
    <View style={s.center}>
      <Text style={s.brand}>ESSA <Text style={{ color: C.accent }}>·</Text> Warehouse</Text>
      <Text style={s.sub}>Connect to your ESSA server</Text>
      <View style={s.card}>
        <Labeled label="Server address (from run.sh / run.bat machine)">
          <TextInput style={s.input} autoCapitalize="none" autoCorrect={false} keyboardType="url"
            value={url} onChangeText={setUrl} placeholder="http://192.168.1.5:8000" placeholderTextColor={C.muted} />
        </Labeled>
        <Text style={s.hint}>Find it on that computer: the IP shown by ipconfig/ifconfig, port 8000. Phone must be on the same WiFi.</Text>
        {err ? <Text style={s.err}>{err}</Text> : null}
        <TouchableOpacity style={s.btn} onPress={save} disabled={busy}>
          {busy ? <ActivityIndicator color={C.onChrome} /> : <Text style={s.btnText}>Connect</Text>}
        </TouchableOpacity>
      </View>
    </View>
  );
}

function LoginScreen({ api, onLogin, onChangeServer }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async () => {
    setErr(''); setBusy(true);
    try { const r = await api.login(username.trim(), password); onLogin(r.token, r.user); }
    catch (e) { setErr(e.message); setBusy(false); }
  };
  return (
    <View style={s.center}>
      <Text style={s.logoAI}>AI</Text>
      <Text style={s.brand}>ESSA <Text style={{ color: C.accent }}>·</Text> Warehouse</Text>
      <Text style={s.sub}>Sign in to receive goods and detail products</Text>
      <View style={s.card}>
        <Labeled label="Username"><TextInput style={s.input} autoCapitalize="none" value={username} onChangeText={setUsername} /></Labeled>
        <Labeled label="Password"><TextInput style={s.input} secureTextEntry value={password} onChangeText={setPassword} placeholder="••••••••" placeholderTextColor={C.muted} /></Labeled>
        {err ? <Text style={s.err}>{err}</Text> : null}
        <TouchableOpacity style={s.btn} onPress={submit} disabled={busy}>
          {busy ? <ActivityIndicator color={C.onChrome} /> : <Text style={s.btnText}>Sign in</Text>}
        </TouchableOpacity>
        <TouchableOpacity onPress={onChangeServer}><Text style={s.link}>Change server</Text></TouchableOpacity>
      </View>
    </View>
  );
}

function ListScreen({ api, onPick, onLogout }) {
  const [items, setItems] = useState([]);
  const [q, setQ] = useState('');
  const [barcode, setBarcode] = useState('');
  const [status, setStatus] = useState('pending');
  const [loading, setLoading] = useState(false);
  const [counts, setCounts] = useState(null);
  const load = useCallback(async (query) => {
    setLoading(true);
    try {
      const data = await api.products(status, query != null ? query : q);
      setItems(data);
      setCounts(await api.summary());
    } catch (e) { Alert.alert('Error', 'Could not load products.'); }
    setLoading(false);
  }, [api, status, q]);
  useEffect(() => { load(); }, [status]); // eslint-disable-line
  const findBarcode = async () => {
    if (!barcode.trim()) return;
    const data = await api.products('all', barcode.trim());
    const exact = data.find((p) => (p.barcode || '') === barcode.trim()) || data[0];
    if (exact) { setBarcode(''); onPick(exact); }
    else Alert.alert('Not found', 'No product with that barcode.');
  };
  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <Text style={s.topTitle}>Products</Text>
        {counts && <Text style={s.topCount}>{counts.pending_detail} pending · {counts.detailed} done</Text>}
        <TouchableOpacity onPress={onLogout}><Text style={[s.link, s.topLink]}>Logout</Text></TouchableOpacity>
      </View>
      <View style={{ padding: 12, gap: 8 }}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput style={[s.input, { flex: 1 }]} placeholder="Enter / scan barcode…" placeholderTextColor={C.muted}
            autoCapitalize="characters" value={barcode} onChangeText={setBarcode} onSubmitEditing={findBarcode} returnKeyType="go" />
          <TouchableOpacity style={s.btnSm} onPress={findBarcode}><Text style={s.btnSmText}>Go</Text></TouchableOpacity>
        </View>
        <TextInput style={s.input} placeholder="Search SKU, description…" placeholderTextColor={C.muted}
          value={q} onChangeText={setQ} onSubmitEditing={() => load()} returnKeyType="search" />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {['pending', 'all', 'detailed'].map((st) => (
            <TouchableOpacity key={st} style={[s.chip, status === st && s.chipOn]} onPress={() => setStatus(st)}>
              <Text style={[s.chipText, status === st && s.chipTextOn]}>{st}</Text>
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={[s.chip, { marginLeft: 'auto' }]} onPress={() => load()}>
            <Text style={s.chipText}>↻</Text></TouchableOpacity>
        </View>
      </View>
      {loading ? <ActivityIndicator color={C.accent} style={{ marginTop: 20 }} /> : (
        <FlatList data={items} keyExtractor={(x) => String(x.id)} contentContainerStyle={{ padding: 12, paddingTop: 0 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.prodRow} onPress={() => onPick(item)}>
              <View style={{ flex: 1 }}>
                <Text style={s.prodName}>{item.description}</Text>
                <Text style={s.prodMeta}>{item.sku}{item.barcode ? ' · ' + item.barcode : ''} · stock {item.stock_qty} {item.uom}</Text>
                {(item.size || item.color) ? (
                  <Text style={s.prodMeta}>{[item.size, item.color].filter(Boolean).join(' · ')}</Text>
                ) : null}
              </View>
              <View style={[s.badge, item.detailed ? s.badgeDone : s.badgePend]}>
                <View style={[s.badgeDot, { backgroundColor: item.detailed ? C.successText : C.pendingText }]} />
                <Text style={[s.badgeText, item.detailed ? s.badgeDoneText : s.badgePendText]}>{item.detailed ? 'detailed' : 'pending'}</Text>
              </View>
            </TouchableOpacity>
          )}
          ListEmptyComponent={<Text style={{ color: C.muted, textAlign: 'center', marginTop: 30 }}>No products.</Text>} />
      )}
    </View>
  );
}


// ---------------- root ----------------
export default function App() {
  const [ready, setReady] = useState(false);
  const [server, setServer] = useState('');
  const [token, setToken] = useState('');
  const [user, setUser] = useState('');
  const [screen, setScreen] = useState('server');   // server | login | app | detail
  const [tab, setTab] = useState('grn');            // grn | bundles | products
  const [options, setOptions] = useState({});
  const [cats, setCats] = useState([]);
  const [selected, setSelected] = useState(null);

  const api = makeApi(server, token);

  useEffect(() => {
    (async () => {
      const srv = await AsyncStorage.getItem('essa_server');
      const tok = await AsyncStorage.getItem('essa_token');
      const usr = await AsyncStorage.getItem('essa_user');
      if (srv) setServer(srv);
      if (tok) setToken(tok);
      if (usr) setUser(usr);
      if (!srv) setScreen('server');
      else if (!tok) setScreen('login');
      else {
        try {
          const r = await makeApi(srv, tok).verify();
          if (r.ok) { setScreen('app'); loadMasters(srv); }
          else setScreen('login');
        } catch { setScreen('login'); }
      }
      setReady(true);
    })();
  }, []); // eslint-disable-line

  // the attribute option lists and the category master — both ends of the app use
  // one vocabulary, so a size chosen at GRN is the same string the detail form offers
  const loadMasters = async (srv) => {
    const a = makeApi(srv || server);
    try { setOptions(await a.options()); } catch {}
    try { const c = await a.categories(); setCats((c.items || []).map((i) => i.name)); } catch {}
  };
  const saveServer = async (url) => { await AsyncStorage.setItem('essa_server', url); setServer(url); setScreen('login'); };
  const onLogin = async (tok, usr) => {
    await AsyncStorage.setItem('essa_token', tok); await AsyncStorage.setItem('essa_user', usr || '');
    setToken(tok); setUser(usr); loadMasters(); setScreen('app');
  };
  const logout = async () => { await AsyncStorage.removeItem('essa_token'); setToken(''); setScreen('login'); };

  if (!ready) return <View style={[s.center, { backgroundColor: C.bg }]}><ActivityIndicator color={C.accent} /></View>;

  let body;
  if (screen === 'server') body = <ServerScreen initial={server} onSave={saveServer} />;
  else if (screen === 'login') body = <LoginScreen api={api} onLogin={onLogin} onChangeServer={() => setScreen('server')} />;
  else if (screen === 'detail') body = (
    <DetailScreen api={api} product={selected} options={options} employee={user}
      onBack={() => setScreen('app')} onSaved={() => setScreen('app')} />
  );
  else body = (
    <View style={{ flex: 1 }}>
      <View style={s.tabbar}>
        {[['grn', 'Receive'], ['bundles', 'Bundles'], ['products', 'Products']].map(([k, label]) => (
          <TouchableOpacity key={k} style={[s.tab, tab === k && s.tabOn]} onPress={() => setTab(k)}>
            <Text style={[s.tabText, tab === k && s.tabTextOn]}>{label}</Text>
          </TouchableOpacity>
        ))}
      </View>
      {tab === 'grn'
        ? <GrnModule api={api} options={options} cats={cats} employee={user} onLogout={logout} />
        : tab === 'bundles'
          ? <BundleModule api={api} options={options} employee={user} onLogout={logout} />
          : <ListScreen api={api} onLogout={logout}
              onPick={(p) => { setSelected(p); setScreen('detail'); }} />}
    </View>
  );

  // The status-bar strip takes the colour of whatever sits directly under it: the
  // green header on the product detail screen (light icons), the white tab bar in
  // the app, and the page itself on the server / login screens (dark icons).
  const onChrome = screen === 'detail';
  const stripBg = onChrome ? C.primary : (screen === 'server' || screen === 'login') ? C.bg : C.panel;
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar style={onChrome ? 'light' : 'dark'} />
      <View style={{ height: Platform.OS === 'ios' ? 44 : 28, backgroundColor: stripBg }} />
      {body}
    </View>
  );
}
