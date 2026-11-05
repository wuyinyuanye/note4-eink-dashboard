const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  const elements = new Map(), actions = ['menu', 'home', 'details', 'album', 'market', 'news', 'gold', 'portfolio', 'up', 'confirm', 'down', 'connect', 'disconnect', 'market_refresh', 'news_refresh', 'gold_refresh', 'portfolio_refresh'];
  class Element {
    constructor() { this.value = ''; this.textContent = ''; this.children = []; this.dataset = {}; this.style = {}; this.disabled = false; this.listeners = {}; this.attributes = {}; this.classes = new Set(); this.classList = {toggle: (key, yes) => yes ? this.classes.add(key) : this.classes.delete(key)}; }
    addEventListener(key, fn) { this.listeners[key] = fn; }
    setAttribute(key, value) { this.attributes[key] = value; }
    append(...items) { this.children.push(...items); }
    replaceChildren() { this.children = []; }
  }
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const actionElements = actions.map(action => { const element = get('[data-action=' + action + ']'); element.dataset.action = action; return element; });
  const navigation = actionElements.slice(0, 8);
  const all = selector => selector === 'button[data-action]' ? actionElements : selector === 'nav button' ? navigation : [];
  const document = {activeElement: null, querySelector: selector => selector.startsWith('.device-keys ') ? get(selector.slice(13)) : get(selector), querySelectorAll: all, createElement: () => new Element()};
  const state = {page: 'news', revision: 1, connection: '未连接', target: 'test', expected_id: '0123456789ab', city: '上海', latitude: 31.2304, longitude: 121.4737, image_format: 'Gray16', freshness: '暂无数据',
    album: {photos: [], source: 'http://old.test', fit: 'contain', interval: 300, current: 0, playing: false},
    gold: {fetching: false, requested_at: null},
    portfolio: {fetching: false, config: null, quote: null},
    news: {rows: Array(10).fill({}), fetched_at: '2026-10-24T08:00:00+08:00', fetching: false},
    market: {codes: ['sh000001'], rows: [{code: 'sh000001', name: '上证指数', stamp: '2026-10-24T08:00:00+08:00'}], interval: 300, session: '休市', fetching: false}};
  const requests = [];
  const browser = {reloads: 0, location: {reload: () => browser.reloads++}};
  let responder = async (url, options) => ({ok: true, json: async () => url === '/api/state' ? structuredClone(state) : {ok: true}});
  const context = vm.createContext({document, window: browser, Date, Map, Set, Promise, JSON, String, Number, Error, AbortSignal,
    fetch: async (url, options) => { requests.push([url, options]); return responder(url, options); },
    setTimeout: fn => { queueMicrotask(fn); return 1; }, setInterval: () => 1});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'control.js'), 'utf8'), context);
  return {get, state, requests, browser, run: code => vm.runInContext(code, context), responder: fn => { responder = fn; }};
}

test('polling preserves unsaved source and interval after focus leaves', async () => {
  const f = fixture(); await f.run('refresh()');
  for (const [id, value] of [['album-source', 'http://draft.test'], ['album-fit', 'cover'], ['album-interval', '900'], ['market-interval', '60'],
    ['weather-city', '北京'], ['weather-latitude', '39.9'], ['weather-longitude', '116.4'], ['device-target', '192.0.2.44'], ['device-id', 'abcdef123456']]) {
    const field = f.get('#' + id); field.value = value; field.listeners.change();
  }
  await f.run('refresh()');
  assert.equal(f.get('#album-source').value, 'http://draft.test');
  assert.equal(f.get('#album-fit').value, 'cover');
  assert.equal(f.get('#album-interval').value, '900');
  assert.equal(f.get('#market-interval').value, '60');
  assert.equal(f.get('#weather-city').value, '北京');
  assert.equal(f.get('#weather-latitude').value, '39.9');
  assert.equal(f.get('#device-target').value, '192.0.2.44');
  assert.equal(f.get('#device-id').value, 'abcdef123456');
});

test('weather lookup fills coordinates and saves through the settings endpoint', async () => {
  const f = fixture(); await f.run('refresh()');
  f.responder(async (url, options) => {
    if (url === '/api/weather/lookup') return {ok: true, json: async () => ({results: [
      {name: '北京', label: '北京 · 北京市 · 中国', latitude: 39.9042, longitude: 116.4074}
    ]})};
    return {ok: true, json: async () => url === '/api/state' ? f.state : {ok: true}};
  });
  f.get('#weather-city').value = '北京';
  await f.get('#weather-lookup').onclick();
  const match = f.get('#weather-matches').children[0];
  assert.equal(match.textContent, '北京 · 北京市 · 中国');
  match.onclick();
  assert.equal(String(f.get('#weather-latitude').value), '39.9042');
  assert.equal(String(f.get('#weather-longitude').value), '116.4074');
  await f.get('#weather-save').onclick();
  assert.ok(f.requests.some(([url, options]) => url === '/api/weather/settings' &&
    JSON.parse(options.body).latitude === '39.9042'));
});

test('device settings are saved and placeholder devices cannot connect', async () => {
  const f = fixture();
  f.state.target = '192.0.2.100'; f.state.expected_id = '0123456789ab';
  await f.run('refresh()');
  assert.equal(f.get('[data-action=connect]').disabled, true);
  f.get('#device-target').value = '192.0.2.44';
  f.get('#device-id').value = 'abcdef123456';
  f.get('#device-target').listeners.input();
  f.get('#device-id').listeners.input();
  await f.get('#device-save').onclick();
  assert.ok(f.requests.some(([url, options]) => url === '/api/device/settings' &&
    JSON.parse(options.body).target === '192.0.2.44'));
});

test('loading takes priority over old errors, empty album and news navigation are disabled', async () => {
  const f = fixture(); await f.run('refresh()');
  f.state.news.error = '旧错误'; f.state.news.fetching = true;
  f.state.market.error = '旧错误'; f.state.market.fetching = true;
  await f.run('refresh()');
  assert.match(f.get('#news-status').textContent, /正在获取国内和国际新闻/);
  assert.match(f.get('#market-status').textContent, /正在获取/);
  assert.equal(f.get('[data-action=confirm]').textContent, '正在更新…');
  assert.equal(f.get('[data-action=confirm]').disabled, true);
  assert.equal(f.get('[data-action=up]').disabled, true);
  assert.equal(f.get('#album-play').disabled, true);
  f.state.page = 'menu'; await f.run('refresh()');
  assert.equal(f.get('[data-action=confirm]').textContent, '进入应用');
  assert.equal(f.get('[data-action=up]').disabled, false);
  assert.equal(f.get('[data-action=menu]').attributes['aria-pressed'], 'true');
});

test('HTTP failure on album action remains visible through polling', async () => {
  const f = fixture(); await f.run('refresh()');
  f.state.album.photos = [{id: 'test', name: '测试照片'}]; await f.run('refresh()');
  f.responder(async url => url === '/api/action' ? {ok: false, json: async () => ({error: '队列繁忙'})} : {ok: true, json: async () => f.state});
  await f.get('#album-play').onclick();
  await f.run('refresh()');
  assert.match(f.get('#album-status').textContent, /队列繁忙/);
});

test('polls share in-flight GET and retain last state during temporary outage', async () => {
  const f = fixture(); await f.run('refresh()');
  let resolve;
  f.responder(() => new Promise(done => { resolve = done; }));
  const before = f.requests.length;
  const first = f.run('refresh()'), second = f.run('refresh()');
  assert.equal(f.requests.length, before + 1);
  resolve({ok: true, json: async () => f.state}); await Promise.all([first, second]);
  f.responder(async () => { throw Error('network'); });
  await f.run('refresh()');
  assert.equal(f.get('#connection').textContent, '暂时无法读取状态');
  assert.equal(f.get('#page-title').textContent, '即时新闻');
});

test('saving interval submits draft then accepts authoritative server setting', async () => {
  const f = fixture(); await f.run('refresh()');
  f.get('#market-interval').value = '60'; f.get('#market-interval').listeners.change();
  f.responder(async (url, options) => {
    if (url === '/api/market/settings') f.state.market.interval = JSON.parse(options.body).interval;
    return {ok: true, json: async () => url === '/api/state' ? f.state : {ok: true}};
  });
  await f.get('#market-save').onclick();
  assert.equal(f.state.market.interval, 60);
  f.state.market.interval = 900; await f.run('refresh()');
  assert.equal(f.get('#market-interval').value, '900');
});

test('focus switcher shows the actual neighbours and return control', async () => {
  const f = fixture(); await f.run('refresh()');
  f.state.page = 'menu';
  f.state.menu = {title: '相册', previous: '天气', next: '行情', return_page: 'news'};
  await f.run('refresh()');
  assert.equal(f.get('[data-action=up]').textContent, '← 天气');
  assert.equal(f.get('[data-action=confirm]').textContent, '进入相册');
  assert.equal(f.get('[data-action=down]').textContent, '行情 →');
  assert.equal(f.get('[data-action=menu]').textContent, '返回原应用');
  assert.equal(f.get('#page-title').textContent, '切换应用');
  f.state.page = 'news'; await f.run('refresh()');
  assert.equal(f.get('[data-action=menu]').textContent, '切换应用');
});

test('gold page reports loading and does not offer navigation keys', async () => {
  const f = fixture();
  f.state.page = 'gold'; f.state.gold.fetching = true;
  await f.run('refresh()');
  assert.equal(f.get('#page-title').textContent, '黄金看板');
  assert.match(f.get('#gold-status').textContent, /正在获取/);
  assert.equal(f.get('#gold-refresh').disabled, true);
  assert.equal(f.get('[data-action=confirm]').disabled, true);
  assert.equal(f.get('[data-action=up]').disabled, true);
  assert.equal(f.get('[data-action=gold]').attributes['aria-pressed'], 'true');
  f.state.gold.fetching = false; f.state.gold.error = 'COMEX 获取失败';
  await f.run('refresh()');
  assert.match(f.get('#gold-status').textContent, /COMEX/);
  assert.equal(f.get('#gold-refresh').disabled, false);
});


test('control page switches to the night status page at midnight', async () => {
  const f = fixture(); await f.run('refresh()');
  f.responder(async () => ({ok: true, json: async () => ({mode: 'night'})}));
  await f.run('refresh()');
  assert.equal(f.browser.reloads, 1);
});


test('portfolio draft survives polling and page exposes update status', async () => {
  const f = fixture(); f.state.page = 'portfolio'; await f.run('refresh()');
  f.get('#portfolio-code').value='518880'; f.get('#portfolio-code').listeners.change();
  await f.run('refresh()');
  assert.equal(f.get('#portfolio-code').value, '518880');
  assert.equal(f.get('#page-title').textContent, '持仓账本');
  assert.equal(f.get('#portfolio-refresh').disabled, true);
  f.state.portfolio.config={code:'sh518880', quantity:'10', cost_price:'8.8'};
  f.state.portfolio.fetching=true; await f.run('refresh()');
  assert.match(f.get('#portfolio-status').textContent, /正在获取/);
  assert.equal(f.get('[data-action=confirm]').disabled, true);
});
