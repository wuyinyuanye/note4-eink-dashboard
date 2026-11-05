'use strict';
const $ = selector => document.querySelector(selector);
const dirty = new Set();
let revision = null, albumKey = '', albumState = null, albumBusy = false, albumMessage = '';
let marketKey = '', marketCandidate = null, marketBusy = false, marketState = null;
let currentState = null, refreshPromise = null, actionMessage = '';
let portfolioBusy = false, portfolioMessage = '';
let weatherBusy = false, weatherMessage = '', weatherMatches = [];
let deviceBusy = false, deviceMessage = '';
const pendingActions = new Set();
const dateTime = value => new Date(value).toLocaleString('zh-CN', {hour12: false});

async function request(path, body) {
  const options = {signal: AbortSignal.timeout(20000)};
  if (body !== undefined) Object.assign(options, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const response = await fetch(path, options);
  let result;
  try { result = await response.json(); }
  catch { throw Error('服务返回异常，请稍后重试'); }
  if (!response.ok) throw Error(result.error || '操作失败，请稍后重试');
  return result;
}
const albumRequest = (path, body) => request('/api/album/' + path, body);
const marketRequest = (path, body) => request('/api/market/' + path, body);

for (const id of ['album-source', 'album-fit', 'album-interval', 'market-interval', 'portfolio-code', 'portfolio-quantity', 'portfolio-cost',
  'weather-city', 'weather-latitude', 'weather-longitude', 'device-target', 'device-id']) {
  const field = $('#' + id);
  field.addEventListener('input', () => dirty.add(id));
  field.addEventListener('change', () => dirty.add(id));
}
function syncField(id, value) {
  if (!dirty.has(id) && document.activeElement !== $('#' + id)) $('#' + id).value = String(value);
}
async function guard(fn) {
  if (albumBusy) return;
  albumBusy = true; albumMessage = '';
  if (currentState) showAlbum(currentState);
  try { await fn(); await refresh(true); }
  catch (error) { albumMessage = error.message; }
  finally { albumBusy = false; if (currentState) showAlbum(currentState); }
}
function showAlbum(s) {
  const a = s.album;
  albumState = a;
  $('#album-count').textContent = `· ${a.photos.length} 张`;
  syncField('album-source', a.source);
  syncField('album-fit', a.fit);
  syncField('album-interval', a.interval);
  const daily = a.daily_date ? `今日照片 ${a.daily_date} · ` : '';
  $('#album-status').textContent = albumMessage || (albumBusy ? '正在处理照片…' : s.album_syncing ? '正在读取今日横屏照片…' : s.album_error ||
    (a.photos.length ? `${daily}${a.current + 1} / ${a.photos.length} · ${a.playing && s.page === 'album' ? '轮播中' : '已暂停'}` : '上传照片或连接电子相框服务开始'));
  $('#source-save').disabled = albumBusy || s.album_syncing;
  $('#album-sync').disabled = albumBusy || s.album_syncing || (!a.source && !a.daily_available);
  $('#album-settings').disabled = albumBusy;
  $('#upload-button').disabled = albumBusy;
  $('#album-play').disabled = albumBusy || !a.photos.length;
  $('#album-play').textContent = a.playing ? '暂停轮播' : '开始轮播';
  const key = JSON.stringify([a.photos, a.current]);
  if (key !== albumKey) {
    albumKey = key;
    $('#photo-list').replaceChildren();
    a.photos.forEach((photo, index) => {
      const tile = document.createElement('div'), button = document.createElement('button');
      const img = document.createElement('img'), remove = document.createElement('button');
      button.style.cssText = 'padding:3px;width:100%;border-width:2px';
      button.classList.toggle('active', a.current === index);
      button.setAttribute('aria-pressed', String(a.current === index));
      img.src = '/album/photo/' + encodeURIComponent(photo.id);
      img.loading = 'lazy'; img.alt = photo.name;
      img.style.cssText = 'width:100%;height:82px;object-fit:contain;display:block;background:white';
      button.append(img); button.title = photo.name;
      button.onclick = () => guard(() => albumRequest('select', {index}));
      remove.textContent = '移除缓存';
      remove.setAttribute('aria-label', '移除缓存：' + photo.name);
      remove.style.cssText = 'font-size:11px;padding:4px;margin-top:4px';
      remove.onclick = () => guard(() => albumRequest('delete', {id: photo.id}));
      tile.append(button, remove); $('#photo-list').append(tile);
    });
  }
  document.querySelectorAll('#photo-list button').forEach(button => button.disabled = albumBusy);
}
$('#source-save').onclick = () => guard(async () => {
  const source = $('#album-source').value.trim();
  await albumRequest('source', {source});
  if ($('#album-source').value.trim() === source) dirty.delete('album-source');
});
$('#album-sync').onclick = () => guard(() => albumRequest('sync', {}));
$('#album-settings').onclick = () => guard(async () => {
  const fit = $('#album-fit').value, interval = Number($('#album-interval').value);
  await albumRequest('settings', {fit, interval});
  if ($('#album-fit').value === fit) dirty.delete('album-fit');
  if (Number($('#album-interval').value) === interval) dirty.delete('album-interval');
});
$('#upload-button').onclick = () => $('#photos').click();
$('#photos').onchange = () => guard(async () => {
  for (const file of $('#photos').files) {
    if (file.size > 20 * 1024 * 1024) throw Error('每张照片最多20MB');
    const response = await fetch('/api/album/upload', {method: 'POST',
      headers: {'Content-Type': 'application/octet-stream', 'X-Photo-Name': encodeURIComponent(file.name)},
      body: file, signal: AbortSignal.timeout(30000)});
    if (!response.ok) throw Error((await response.json()).error || '照片上传失败');
  }
  $('#photos').value = '';
});
$('#album-play').onclick = () => guard(async () => {
  if (!albumState) return;
  await request('/api/action', {action: albumState.playing ? 'album_pause' : 'album_play'});
  setTimeout(refresh, 300);
});

function marketButtons() {
  const saving = marketBusy || marketState?.fetching;
  $('#market-lookup').disabled = marketBusy;
  $('#market-code').disabled = marketBusy;
  $('#market-add').disabled = saving || !marketCandidate || marketState?.codes.includes(marketCandidate.code);
  $('#market-save').disabled = saving;
  $('#market-refresh').disabled = saving || pendingActions.has('market_refresh');
  document.querySelectorAll('#market-list button').forEach(button => button.disabled = saving || button.dataset.edge === 'true');
}
async function marketGuard(fn) {
  if (marketBusy) return;
  marketBusy = true; $('#market-message').textContent = ''; marketButtons();
  try { await fn(); await refresh(true); }
  catch (error) { $('#market-message').textContent = error.message; }
  finally { marketBusy = false; marketButtons(); }
}
$('#market-code').oninput = () => {
  marketCandidate = null; $('#market-candidate').hidden = true;
  $('#market-message').textContent = ''; marketButtons();
};
$('#market-preset-apply').onclick = () => {
  const value = $('#market-preset').value;
  if (!value) { $('#market-message').textContent = '请先选择一个常用代码'; return; }
  $('#market-code').value = value;
  $('#market-code').oninput();
  $('#market-add-form').requestSubmit();
};
$('#market-add-form').onsubmit = event => {
  event.preventDefault();
  marketGuard(async () => {
    marketCandidate = null; $('#market-candidate').hidden = true;
    const result = await marketRequest('lookup', {code: $('#market-code').value.trim()});
    marketCandidate = result.quote;
    $('#market-candidate-name').textContent = marketCandidate.name + ' · ' + marketCandidate.code;
    $('#market-candidate-detail').textContent = '源报价 ' + marketCandidate.price + ' · ' + dateTime(marketCandidate.stamp);
    $('#market-candidate').hidden = false;
    if (marketState?.codes.includes(marketCandidate.code)) $('#market-message').textContent = '这只证券已在自选中';
  });
};
$('#market-add').onclick = () => marketGuard(async () => {
  if (!marketCandidate) return;
  await marketRequest('add', {code: marketCandidate.code});
  $('#market-message').textContent = '已添加 ' + marketCandidate.name;
  marketCandidate = null; $('#market-candidate').hidden = true; $('#market-code').value = '';
});
$('#market-save').onclick = () => marketGuard(async () => {
  const interval = Number($('#market-interval').value);
  await marketRequest('settings', {interval});
  if (Number($('#market-interval').value) === interval) dirty.delete('market-interval');
  $('#market-message').textContent = '更新间隔已保存';
});
function showMarket(s) {
  const m = s.market;
  marketState = m;
  $('#key-status').textContent = (s.last_key ? '最后按键 ' + ({Confirm: '确认', Up: '上', Down: '下'}[s.last_key.key] || s.last_key.key) +
    ' · ' + s.last_key.action + ' · ' + dateTime(s.last_key.at) : '尚未收到设备按键') +
    (m.last_refresh_trigger ? '；最近更新：' + ({confirm: '确认操作', manual: '手动', auto: '自动', open: '进入页面'}[m.last_refresh_trigger] || m.last_refresh_trigger) : '');
  const key = JSON.stringify([m.codes, m.rows.map(row => [row.code, row.name, row.stamp])]);
  if (key !== marketKey) {
    marketKey = key; $('#market-list').replaceChildren();
    const quotes = new Map(m.rows.map(row => [row.code, row]));
    m.codes.forEach((code, index) => {
      const row = document.createElement('div'), label = document.createElement('div'), controls = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #e7e7df;padding:10px 0';
      label.style.cssText = 'min-width:0;overflow-wrap:anywhere';
      const title = document.createElement('strong'), detail = document.createElement('div'), quote = quotes.get(code);
      title.textContent = quote?.name || code;
      detail.textContent = code + (quote ? ' · 报价 ' + dateTime(quote.stamp) : ' · 暂无有效报价');
      detail.className = 'muted'; label.append(title, detail);
      controls.style.cssText = 'display:flex;gap:4px;flex-shrink:0';
      for (const [text, path, value, edge] of [['↑', 'move', -1, index === 0], ['↓', 'move', 1, index === m.codes.length - 1], ['删除', 'remove', null, m.codes.length === 1]]) {
        const button = document.createElement('button');
        button.textContent = text; button.title = (text === '↑' ? '上移 ' : text === '↓' ? '下移 ' : '删除 ') + code;
        button.setAttribute('aria-label', button.title);
        button.style.cssText = 'padding:6px 8px;font-size:12px'; button.dataset.edge = String(edge);
        button.onclick = () => marketGuard(async () => {
          await marketRequest(path, {code, ...(value === null ? {} : {direction: value})});
          $('#market-message').textContent = path === 'remove' ? '已删除 ' + code : '顺序已保存';
        });
        controls.append(button);
      }
      row.append(label, controls); $('#market-list').append(row);
    });
  }
  syncField('market-interval', m.interval); marketButtons();
  $('#market-status').textContent = m.fetching ? '正在获取行情…' : m.error || m.session + ' · ' +
    (m.fetched_at ? '最近获取 ' + dateTime(m.fetched_at) : '尚未获取行情');
}
function showNews(s) {
  const n = s.news;
  $('#news-refresh').disabled = n.fetching || pendingActions.has('news_refresh');
  $('#news-status').textContent = n.fetching ? '正在获取国内和国际新闻…' : n.error ||
    (n.fetched_at ? '最近获取 ' + dateTime(n.fetched_at) + `，共${n.rows.length}条` : '尚未获取新闻');
}
function showGold(s) {
  const g = s.gold;
  $('#gold-refresh').disabled = g.fetching || pendingActions.has('gold_refresh');
  $('#gold-status').textContent = g.fetching ? '正在获取三种黄金报价…' : g.error ||
    (g.requested_at ? '最近请求 ' + dateTime(g.requested_at) : '尚未请求报价');
}
function showPortfolio(s) {
  const p = s.portfolio, config = p.config || {};
  syncField('portfolio-code', config.code || '');
  syncField('portfolio-quantity', config.quantity || '');
  syncField('portfolio-cost', config.cost_price || '');
  $('#portfolio-save').disabled = portfolioBusy || p.fetching;
  $('#portfolio-refresh').disabled = portfolioBusy || p.fetching || !p.config || pendingActions.has('portfolio_refresh');
  $('#portfolio-status').textContent = portfolioMessage || (p.fetching ? '正在获取持仓行情…' : p.error ||
    (p.quote ? '最近报价 ' + dateTime(p.quote.stamp) : p.config ? '等待有效行情' : '尚未设置持仓'));
}
function showWeatherSettings(s) {
  syncField('weather-city', s.city || '');
  syncField('weather-latitude', s.latitude ?? '');
  syncField('weather-longitude', s.longitude ?? '');
  $('#weather-lookup').disabled = weatherBusy;
  $('#weather-save').disabled = weatherBusy || s.fetching;
  $('#weather-settings-status').textContent = weatherMessage ||
    (s.fetching ? '正在获取地点天气…' : `当前：${s.city} · ${Number(s.latitude).toFixed(4)}, ${Number(s.longitude).toFixed(4)}`);
  const key = JSON.stringify(weatherMatches);
  if ($('#weather-matches').dataset.key !== key) {
    $('#weather-matches').dataset.key = key;
    $('#weather-matches').replaceChildren();
    weatherMatches.forEach(match => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = match.label;
      button.onclick = () => {
        $('#weather-city').value = match.name;
        $('#weather-latitude').value = String(match.latitude);
        $('#weather-longitude').value = String(match.longitude);
        for (const id of ['weather-city', 'weather-latitude', 'weather-longitude']) dirty.add(id);
        weatherMatches = [];
        weatherMessage = '已填入坐标，点击“保存并更新天气”生效';
        showWeatherSettings(currentState);
      };
      $('#weather-matches').append(button);
    });
  }
}
async function weatherGuard(fn) {
  if (weatherBusy) return;
  weatherBusy = true; weatherMessage = '';
  if (currentState) showWeatherSettings(currentState);
  try { await fn(); await refresh(true); }
  catch (error) { weatherMessage = error.message; }
  finally { weatherBusy = false; if (currentState) showWeatherSettings(currentState); }
}
$('#weather-lookup').onclick = () => weatherGuard(async () => {
  const query = $('#weather-city').value.trim();
  const result = await request('/api/weather/lookup', {query});
  weatherMatches = result.results || [];
  weatherMessage = weatherMatches.length ? `找到 ${weatherMatches.length} 个地点，请点选一个` : '没有找到地点';
});
$('#weather-save').onclick = () => weatherGuard(async () => {
  const value = {city: $('#weather-city').value.trim(), latitude: $('#weather-latitude').value.trim(), longitude: $('#weather-longitude').value.trim()};
  await request('/api/weather/settings', value);
  for (const [id, key] of [['weather-city','city'], ['weather-latitude','latitude'], ['weather-longitude','longitude']])
    if ($('#'+id).value.trim() === String(value[key])) dirty.delete(id);
  weatherMatches = [];
  weatherMessage = '地点已保存，正在获取新天气；成功后会更新屏幕';
});
function showDeviceSettings(s) {
  syncField('device-target', s.target || '');
  syncField('device-id', s.expected_id || '');
  const configured = s.target !== '192.0.2.100' && s.expected_id !== '0123456789ab';
  $('#device-save').disabled = deviceBusy || ['已连接', '正在连接', '正在关闭'].includes(s.connection);
  $('#device-settings-status').textContent = deviceMessage || (configured ? '设备信息已配置，保存在本机。' : '请填写自己的设备地址和 ID 后再连接。');
  const connect = $('[data-action=connect]');
  connect.disabled = !configured || deviceBusy || pendingActions.has('connect') || ['已连接', '正在连接', '正在关闭'].includes(s.connection);
}
$('#device-save').onclick = async () => {
  if (deviceBusy) return;
  deviceBusy = true; deviceMessage = ''; if (currentState) showDeviceSettings(currentState);
  const value = {target: $('#device-target').value.trim(), expected_id: $('#device-id').value.trim()};
  try {
    await request('/api/device/settings', value);
    for (const [id, key] of [['device-target','target'], ['device-id','expected_id']])
      if ($('#'+id).value.trim() === value[key]) dirty.delete(id);
    deviceMessage = '设备信息已保存';
    await refresh(true);
  } catch (error) { deviceMessage = error.message; }
  finally { deviceBusy = false; if (currentState) showDeviceSettings(currentState); }
};
$('#portfolio-save').onclick = async () => {
  if (portfolioBusy) return;
  portfolioBusy = true; portfolioMessage = ''; if (currentState) showPortfolio(currentState);
  const value = {code: $('#portfolio-code').value.trim(), quantity: $('#portfolio-quantity').value.trim(), cost_price: $('#portfolio-cost').value.trim()};
  try {
    await request('/api/portfolio/settings', value);
    for (const [id, key] of [['portfolio-code','code'], ['portfolio-quantity','quantity'], ['portfolio-cost','cost_price']])
      if ($('#'+id).value.trim() === value[key]) dirty.delete(id);
    await refresh(true);
  } catch (error) { portfolioMessage = error.message; }
  finally { portfolioBusy = false; if (currentState) showPortfolio(currentState); }
};
function showNavigation(s) {
  const labels = {menu: ['← 上一个', '进入应用', '下一个 →', '上键：左侧应用移入中间；下键：右侧应用移入中间。确认进入，长按上/下键返回。'],
    album: ['↑ 上一张', s.album.playing ? '暂停轮播' : '开始轮播', '↓ 下一张', '上下翻照片，确认暂停或继续轮播。'],
    market: ['↑ 上一页', '更新行情', '↓ 下一页', '上下切换行情页，确认获取最新报价。'],
    sectors: ['↑', '更新板块', '↓', '十四个板块代理，交易时段每 5 分钟更新；确认键手动更新。'],
    news: ['↑', '更新新闻', '↓', '国内、国际各五条，确认更新新闻。'],
    gold: ['↑', '更新黄金', '↓', '确认获取最新有效报价；不自动更新。'],
    portfolio: ['↑', '更新估值', '↓', '每 5 分钟获取有效报价，确认键可手动更新。'],
    home: ['↑ 天气总览', '更新天气', '↓ 天气总览', '上下切换天气页面，确认获取最新天气。'],
    details: ['↑ 天气首页', '更新天气', '↓ 天气首页', '上下返回天气首页，确认更新天气并保留当前页面。']}[s.page];
  if (s.page === 'menu' && s.menu) {
    labels[0] = '← ' + s.menu.previous;
    labels[1] = '进入' + s.menu.title;
    labels[2] = s.menu.next + ' →';
  }
  const busy = s.page === 'sectors' ? s.sectors.fetching : s.page === 'portfolio' ? s.portfolio.fetching : s.page === 'gold' ? s.gold.fetching : s.page === 'news' ? s.news.fetching : s.page === 'market' ? s.market.fetching : ['home', 'details'].includes(s.page) && s.fetching;
  const buttons = ['up', 'confirm', 'down'].map(action => $('.device-keys [data-action=' + action + ']'));
  buttons.forEach((button, index) => {
    button.textContent = index === 1 && busy ? '正在更新…' : labels[index];
    button.disabled = pendingActions.has(button.dataset.action) || (index === 1 ? busy || (s.page === 'album' && !s.album.photos.length) : ['news', 'gold', 'portfolio', 'sectors'].includes(s.page));
  });
  $('#navigation-help').textContent = labels[3];
  document.querySelectorAll('nav button').forEach(button => {
    const active = button.dataset.action === s.page;
    if (button.dataset.action === 'menu') button.textContent = s.page === 'menu' ? '返回原应用' : '切换应用';
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  });
}
function showState(s) {
  currentState = s;
  showAlbum(s); showMarket(s); showNews(s); showGold(s); showPortfolio(s); showWeatherSettings(s); showDeviceSettings(s); showNavigation(s);
  if (s.sectors) $('#sectors-status').textContent = s.sectors.fetching ? '正在获取板块行情…' : s.sectors.error || (s.sectors.fetched_at ? '更新于 ' + dateTime(s.sectors.fetched_at) : '尚无有效报价');
  $('#page-title').textContent = ({menu: '切换应用', market: '自选行情', sectors: '板块行情', news: '即时新闻', gold: '黄金看板', portfolio: '持仓账本', album: '照片相册'}[s.page] || s.city + ' · 天气');
  $('#connection').textContent = s.connection + (s.recording ? ' · 录音中' : '');
  $('#connection').dataset.tone = s.connection === '已连接' ? 'ready' : s.error ? 'error' : 'offline';
  $('#target').textContent = s.target;
  $('#displayed').textContent = s.displayed_at ? dateTime(s.displayed_at) : '尚未显示';
  $('#battery').textContent = s.status?.battery_percent != null ? s.status.battery_percent + '%' : '未知';
  const format = {Mono: '黑白 · 1bpp', Gray4: '四阶灰 · 2bpp', Gray16: '十六阶灰 · 4bpp'}[s.image_format] || '等待页面';
  $('#page-format').textContent = format;
  $('#palette').textContent = format + (s.connection === '已连接' ? '' : ' · ' + s.connection);
  $('#freshness').textContent = s.freshness + (s.fetching ? ' · 正在取数' : '');
  $('#error').textContent = [actionMessage, s.weather_error, s.error].filter(Boolean).join('\n');
  $('#retry').hidden = !s.display_blocked;
  if (s.revision !== revision) {
    $('#preview').src = '/preview.png?t=' + s.revision; revision = s.revision;
  }
  showDeviceSettings(s);
  $('[data-action=disconnect]').disabled = pendingActions.has('disconnect') || !['已连接', '正在连接'].includes(s.connection);
}
function refresh(afterMutation = false) {
  if (refreshPromise) return afterMutation ? refreshPromise.then(() => refresh()) : refreshPromise;
  refreshPromise = request('/api/state').then(state => {
    if (state.mode === 'night') { window.location.reload(); return; }
    showState(state);
  }).catch(() => {
    $('#connection').textContent = '暂时无法读取状态'; $('#connection').dataset.tone = 'error';
  }).finally(() => { refreshPromise = null; });
  return refreshPromise;
}
document.querySelectorAll('button[data-action]').forEach(button => button.onclick = async () => {
  const action = button.dataset.action;
  if (pendingActions.has(action)) return;
  actionMessage = '';
  pendingActions.add(action); button.disabled = true;
  try {
    await request('/api/action', {action});
    await new Promise(resolve => setTimeout(resolve, 300));
    await refresh(true);
  } catch (error) { actionMessage = error.message; $('#error').textContent = actionMessage; }
  finally {
    pendingActions.delete(action); button.disabled = false;
    if (currentState) {
      showNavigation(currentState); marketButtons(); showNews(currentState);
      showDeviceSettings(currentState);
      $('[data-action=disconnect]').disabled = !['已连接', '正在连接'].includes(currentState.connection);
    }
  }
});
refresh(); setInterval(refresh, 2000);
