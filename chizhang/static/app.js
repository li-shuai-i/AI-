'use strict';

const RING_CIRC = 326.7;
const MEAL_ICON = { breakfast: '早', lunch: '午', dinner: '晚', snack: '加' };

const S = {
  date: localISO(),
  data: null,
  meal: 'lunch',
  foods: [],
  category: '',
  query: '',
  picked: null,
  grams: 100,
  unit: null,
  draftProfile: null,
};

/* ---------- helpers ---------- */

function localISO(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function shiftDate(iso, delta) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + delta);
  return localISO(dt);
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function fmtDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const today = localISO();
  const yesterday = shiftDate(today, -1);
  let tag = '';
  if (iso === today) tag = '今天';
  else if (iso === yesterday) tag = '昨天';
  else if (iso === shiftDate(today, -2)) tag = '前天';
  else if (iso === shiftDate(today, 1)) tag = '明天';
  return {
    main: `${m}月${d}日`,
    sub: tag ? `${WEEKDAYS[dt.getDay()]} · ${tag}` : WEEKDAYS[dt.getDay()],
  };
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function round(n, digits = 0) {
  const f = 10 ** digits;
  return Math.round(n * f) / f;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error((data && data.error) || `请求失败 (${res.status})`);
  return data;
}

let toastTimer = null;
function toast(message) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2200);
}

/* ---------- data flow ---------- */

async function load(date = S.date) {
  try {
    S.date = date;
    S.data = await api(`/api/state?date=${date}`);
    render();
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- rendering ---------- */

function render() {
  if (!S.data) return;
  renderDate();
  renderSummary();
  renderMeals();
  renderInsights();
  renderWeek();
  renderWeight();
  renderStats();
}

function renderDate() {
  const info = fmtDate(S.date);
  document.getElementById('dayText').textContent = info.main;
  document.getElementById('daySub').textContent = info.sub;
  document.getElementById('nextDay').disabled = S.date >= S.data.today;
}

function renderSummary() {
  const { targets, day } = S.data;
  const eaten = day.totals.kcal;
  const pct = targets.kcal ? Math.min(eaten / targets.kcal, 1) : 0;
  const bar = document.getElementById('ringBar');
  bar.style.strokeDashoffset = String(RING_CIRC * (1 - pct));
  bar.classList.toggle('over', eaten > targets.kcal);

  const remain = Math.round(targets.kcal - eaten);
  const remainKcal = document.getElementById('remainKcal');
  const remainLabel = document.getElementById('remainLabel');
  if (remain >= 0) {
    remainKcal.textContent = remain;
    remainLabel.textContent = '还能吃 kcal';
  } else {
    remainKcal.textContent = '+' + Math.abs(remain);
    remainLabel.textContent = '超出 kcal';
  }

  document.getElementById('eatenKcal').textContent = Math.round(eaten);
  document.getElementById('targetKcal').textContent = targets.kcal;

  const note = document.getElementById('deficitNote');
  if (targets.floored) {
    note.textContent = `缺口调至 ${targets.deficit} kcal（安全下限）`;
    note.className = 'deficit-note warn';
    note.title = `按你设的缺口会低于安全下限 ${targets.floor} kcal，已自动上调`;
  } else if (targets.deficit > 0) {
    note.textContent = `每天缺口 ${targets.deficit} kcal`;
    note.className = 'deficit-note';
  } else {
    note.textContent = '维持当前体重';
    note.className = 'deficit-note';
  }

  const macros = [
    { key: 'protein', label: '蛋白质', cls: 'protein' },
    { key: 'carb', label: '碳水', cls: 'carb' },
    { key: 'fat', label: '脂肪', cls: 'fat' },
  ];
  document.getElementById('macros').innerHTML = macros.map((m) => {
    const p = day.progress[m.key];
    const w = p.target ? Math.min((p.value / p.target) * 100, 100) : 0;
    const over = p.value > p.target * 1.05;
    return `
      <div class="macro">
        <div class="macro-head">
          <span class="macro-name">${m.label}</span>
          <span class="macro-value"><b>${round(p.value)}</b> / ${round(p.target)} g</span>
        </div>
        <div class="macro-bar"><div class="macro-fill ${m.cls}${over ? ' over' : ''}" style="width:${w}%"></div></div>
      </div>`;
  }).join('');
}

function portionText(entry) {
  if (entry.unit_grams && entry.unit_grams > 0) {
    const count = entry.grams / entry.unit_grams;
    if (Math.abs(count - Math.round(count)) < 0.02 && Math.round(count) >= 1) {
      return `${Math.round(count)} ${entry.unit_label} · ${round(entry.grams)} g`;
    }
  }
  return `${round(entry.grams)} g`;
}

function renderMeals() {
  const { day } = S.data;
  const host = document.getElementById('meals');
  host.innerHTML = day.meals.map((meal) => {
    const items = meal.items.length
      ? meal.items.map((item) => `
          <div class="entry" data-id="${escapeHtml(item.id)}">
            <div>
              <div class="entry-name">${escapeHtml(item.name)}</div>
              <div class="entry-grams">${portionText(item)}</div>
            </div>
            <span class="entry-kcal">${Math.round(item.kcal)} kcal</span>
            <button class="entry-del" data-del="${escapeHtml(item.id)}" aria-label="删除">×</button>
          </div>`).join('')
      : `<div class="meal-empty">还没记，点右边的 ＋</div>`;

    return `
      <section class="card meal">
        <div class="meal-head">
          <h3 class="meal-name">${meal.label}</h3>
          <span class="meal-kcal">${Math.round(meal.totals.kcal)} kcal</span>
          <button class="add-btn" data-add="${meal.key}" aria-label="添加${meal.label}">＋</button>
        </div>
        ${items}
      </section>`;
  }).join('');
}

function renderInsights() {
  const host = document.getElementById('insightCard');
  const list = S.data.day.insights || [];
  if (!list.length) { host.hidden = true; host.innerHTML = ''; return; }
  host.hidden = false;
  host.innerHTML = list.map((item) => `
    <div class="insight ${item.level}">
      <span class="insight-dot"></span>
      <span>${escapeHtml(item.text)}</span>
    </div>`).join('');
}

function renderWeek() {
  const { week, targets } = S.data;
  const max = Math.max(targets.kcal, ...week.days.map((d) => d.kcal)) || 1;
  const bars = week.days.map((d) => {
    const h = d.logged ? Math.max((d.kcal / max) * 100, 4) : 2;
    const isToday = d.date === S.date;
    const cls = ['week-bar'];
    if (d.logged) cls.push('logged');
    if (isToday) cls.push('today');
    if (d.kcal > targets.kcal) cls.push('over');
    return `
      <div class="week-col" title="${d.label} · ${d.kcal} kcal">
        <div class="${cls.join(' ')}" style="height:${h}%"></div>
        <span class="week-label${isToday ? ' today' : ''}">${d.label}</span>
      </div>`;
  }).join('');

  document.getElementById('weekCard').innerHTML = `
    <div class="side-title"><h3>最近 7 天</h3><span>目标 ${targets.kcal}</span></div>
    <div class="week-chart">${bars}</div>`;
}

function renderWeight() {
  const { weight, targets } = S.data;
  const host = document.getElementById('weightCard');
  const latest = weight.latest;
  const delta = weight.change_7d;

  let spark = '';
  if (weight.series.length > 1) {
    const values = weight.series.map((p) => p.kg);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo || 1;
    const pts = weight.series.map((p, i) => {
      const x = (i / (weight.series.length - 1)) * 100;
      const y = 42 - ((p.kg - lo) / span) * 34;
      return `${round(x, 2)},${round(y, 2)}`;
    });
    const last = pts[pts.length - 1].split(',');
    spark = `
      <svg class="spark" viewBox="0 0 100 46" preserveAspectRatio="none" aria-hidden="true">
        <polygon class="area" points="0,46 ${pts.join(' ')} 100,46"></polygon>
        <polyline points="${pts.join(' ')}"></polyline>
        <circle cx="${last[0]}" cy="${last[1]}" r="1.8"></circle>
      </svg>`;
  }

  const deltaHtml = delta === null || delta === undefined
    ? '<span class="weight-delta">还没有对比数据</span>'
    : `<span class="weight-delta ${delta < 0 ? 'down' : delta > 0 ? 'up' : ''}">${
        delta === 0 ? '持平' : (delta > 0 ? '+' : '') + delta + ' kg'} / 7天</span>`;

  host.innerHTML = `
    <div class="side-title"><h3>体重</h3><span>BMI ${targets.bmi}</span></div>
    <div class="weight-body">
      <div class="weight-top">
        <span class="weight-value">${latest ? latest.kg : '—'}</span>
        <span class="weight-unit">kg</span>
        ${deltaHtml}
      </div>
      ${spark}
      <div class="weight-form">
        <input type="number" id="weightInput" inputmode="decimal" step="0.1" min="30" max="300"
               placeholder="今天早上多少 kg" value="${latest ? latest.kg : ''}">
        <button class="mini-btn" id="saveWeight">记下</button>
      </div>
    </div>`;

  document.getElementById('saveWeight').addEventListener('click', saveWeight);
  document.getElementById('weightInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveWeight();
  });
}

function renderStats() {
  const { week, targets } = S.data;
  const host = document.getElementById('disciplineCard');
  const avgDelta = week.avg_delta;
  let note;
  if (!week.logged_days) {
    note = '记满 3 天就能看出你的饮食规律了。';
  } else if (avgDelta > 150) {
    note = `日均比目标高 ${avgDelta} kcal。少一勺炒菜油（约 90 kcal）或半碗饭，就能拉回来。`;
  } else if (avgDelta < -400) {
    note = '吃得比目标少很多。长期这样基础代谢会下降，建议吃够目标。';
  } else {
    note = '日均摄入在目标附近，这个节奏是对的，继续保持。';
  }

  host.innerHTML = `
    <h3>这周</h3>
    <div class="discipline-line"><span>记录天数</span><b>${week.logged_days} / 7</b></div>
    <div class="discipline-line"><span>日均摄入</span><b>${week.avg_kcal || '—'} kcal</b></div>
    <div class="discipline-line"><span>蛋白质达标</span><b>${week.protein_hit_days} 天</b></div>
    <p class="discipline-note">${escapeHtml(note)}</p>`;
}

/* ---------- sheets ---------- */

function openSheet(id) {
  document.getElementById(id).hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeSheet(id) {
  document.getElementById(id).hidden = true;
  document.body.style.overflow = '';
}

/* ---------- search sheet ---------- */

async function openSearch(meal) {
  S.meal = meal;
  S.query = '';
  S.category = '';
  const label = (S.data.meals.find((m) => m.key === meal) || {}).label || '这一餐';
  document.getElementById('sheetTitle').textContent = `添加到${label}`;
  document.getElementById('foodQuery').value = '';
  document.getElementById('customForm').hidden = true;
  document.getElementById('customToggle').textContent = '没找到？手动记一笔';

  document.getElementById('catChips').innerHTML =
    ['', ...S.data.categories].map((c) => `
      <button class="chip${c === '' ? ' on' : ''}" data-cat="${escapeHtml(c)}">${c || '全部'}</button>`).join('');

  const recent = S.data.recent || [];
  document.getElementById('recentRow').hidden = !recent.length;
  document.getElementById('recentChips').innerHTML = recent.map((f) => `
    <button class="chip" data-recent="${escapeHtml(f.id)}">${escapeHtml(f.name)}</button>`).join('');

  openSheet('searchSheet');
  await runSearch();
  setTimeout(() => document.getElementById('foodQuery').focus(), 120);
}

async function runSearch() {
  const params = new URLSearchParams({ limit: S.query ? '30' : '12' });
  if (S.query) params.set('q', S.query);
  if (S.category) params.set('category', S.category);
  try {
    const data = await api('/api/foods?' + params.toString());
    S.foods = data.items;
    renderResults();
  } catch (err) {
    toast(err.message);
  }
}

function renderResults() {
  const host = document.getElementById('results');
  if (!S.foods.length) {
    host.innerHTML = `<div class="results-empty">没找到。<br>试试更短的关键词，或者手动记一笔。</div>`;
    return;
  }
  host.innerHTML = S.foods.map((f) => `
    <button class="food-row" data-food="${f.id}">
      <div>
        <div class="food-name">${escapeHtml(f.name)}</div>
        <div class="food-cat">${escapeHtml(f.category)}</div>
      </div>
      <span class="food-kcal">${Math.round(f.per100.kcal)} kcal/100g</span>
    </button>`).join('');
}

/* ---------- portion sheet ---------- */

function openPortion(food) {
  S.picked = food;
  S.unit = food.units.length ? food.units[0].label : null;
  S.grams = food.units.length ? food.units[0].grams : 100;

  document.getElementById('portionTitle').textContent = food.name;
  document.getElementById('portionMeta').textContent =
    `每 100 g 约 ${Math.round(food.per100.kcal)} kcal · 蛋白质 ${food.per100.protein} g · 碳水 ${food.per100.carb} g · 脂肪 ${food.per100.fat} g`;

  document.getElementById('portionChips').innerHTML = food.units.map((u) => `
    <button class="portion-chip${u.label === S.unit ? ' on' : ''}" data-unit="${escapeHtml(u.label)}" data-grams="${u.grams}">
      ${escapeHtml(u.label)}<small>${u.grams} g</small>
    </button>`).join('');

  document.getElementById('gramsInput').value = S.grams;
  updatePortionPreview();
  openSheet('portionSheet');
  closeSheet('searchSheet');
}

function updatePortionPreview() {
  if (!S.picked) return;
  const food = S.picked;
  const grams = Math.max(1, Number(S.grams) || 1);
  const k = grams / 100;
  document.getElementById('portionPreview').innerHTML = `
    <span><b>${Math.round(food.per100.kcal * k)}</b> kcal</span>
    <span>蛋白 <b>${round(food.per100.protein * k, 1)}</b> g</span>
    <span>碳水 <b>${round(food.per100.carb * k, 1)}</b> g</span>
    <span>脂肪 <b>${round(food.per100.fat * k, 1)}</b> g</span>`;
}

async function confirmPortion() {
  if (!S.picked) return;
  const grams = Math.max(1, Math.min(5000, Number(S.grams) || 100));
  try {
    const res = await api('/api/entries', {
      method: 'POST',
      body: JSON.stringify({
        date: S.date,
        meal: S.meal,
        food_id: S.picked.id,
        grams,
        unit_label: S.unit,
      }),
    });
    S.data = res.state;
    render();
    closeSheet('portionSheet');
    toast(`已记下 ${res.entry.name} ${Math.round(res.entry.kcal)} kcal`);
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- settings ---------- */

function openSettings() {
  const p = S.data.profile;
  S.draftProfile = { ...p };
  document.getElementById('pAge').value = p.age;
  document.getElementById('pHeight').value = p.height_cm;
  document.getElementById('pWeight').value = p.weight_kg;
  document.getElementById('pDeficit').value = p.deficit;

  document.querySelectorAll('#sexSeg button').forEach((b) => {
    b.classList.toggle('on', b.dataset.value === p.sex);
  });

  document.getElementById('activityList').innerHTML = S.data.activity_options.map((o) => `
    <button class="radio-item${o.key === p.activity ? ' on' : ''}" data-activity="${o.key}">
      <span class="dot"></span><span>${escapeHtml(o.label)}</span>
    </button>`).join('');

  document.getElementById('goalList').innerHTML = S.data.goal_options.map((o) => `
    <button class="radio-item${o.key === p.goal ? ' on' : ''}" data-goal="${o.key}">
      <span class="dot"></span><span>${escapeHtml(o.label)}</span>
    </button>`).join('');

  updateCalcPreview();
  openSheet('settingsSheet');
}

const ACTIVITY_FACTOR = { sedentary: 1.2, light: 1.375, moderate: 1.55, active: 1.725 };
const GOAL_PROTEIN = { lose: 1.6, keep: 1.3, gain: 1.8 };

function readDraft() {
  const p = S.draftProfile;
  p.age = Number(document.getElementById('pAge').value) || 30;
  p.height_cm = Number(document.getElementById('pHeight').value) || 165;
  p.weight_kg = Number(document.getElementById('pWeight').value) || 60;
  p.deficit = Number(document.getElementById('pDeficit').value) || 0;
  return p;
}

function updateCalcPreview() {
  const p = readDraft();
  const base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age + (p.sex === 'male' ? 5 : -161);
  const total = base * ACTIVITY_FACTOR[p.activity];
  let raw = p.goal === 'lose' ? total - p.deficit : p.goal === 'gain' ? total + 300 : total;
  const floor = Math.max(base * 1.1, p.sex === 'female' ? 1200 : 1500);
  const floored = p.goal === 'lose' && raw < floor;
  const kcal = floored ? floor : raw;
  const protein = GOAL_PROTEIN[p.goal] * p.weight_kg;

  document.getElementById('calcPreview').innerHTML = `
    基础代谢 <b>${Math.round(base)}</b> kcal · 每日消耗 <b>${Math.round(total)}</b> kcal<br>
    目标热量 <b>${Math.round(kcal)}</b> kcal · 蛋白质 <b>${Math.round(protein)}</b> g
    ${floored ? `<br><span class="flag">缺口会把你压到安全下限以下，已自动上调到 ${Math.round(floor)} kcal</span>` : ''}`;
}

async function saveProfile() {
  const p = readDraft();
  try {
    S.data = await api('/api/profile', {
      method: 'POST',
      body: JSON.stringify({ ...p, date: S.date }),
    });
    render();
    closeSheet('settingsSheet');
    toast('目标已更新');
  } catch (err) {
    toast(err.message);
  }
}

async function saveWeight() {
  const input = document.getElementById('weightInput');
  const kg = Number(input.value);
  if (!kg || kg < 30 || kg > 300) { toast('请输入 30–300 之间的体重'); return; }
  try {
    S.data = await api('/api/weights', {
      method: 'POST',
      body: JSON.stringify({ date: S.date, kg }),
    });
    render();
    toast(`已记录 ${kg} kg`);
  } catch (err) {
    toast(err.message);
  }
}

async function deleteEntry(id) {
  try {
    S.data = await api(`/api/entries/${id}`, { method: 'DELETE' });
    render();
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- wiring ---------- */

function wire() {
  document.getElementById('prevDay').addEventListener('click', () => load(shiftDate(S.date, -1)));
  document.getElementById('nextDay').addEventListener('click', () => {
    if (S.date < S.data.today) load(shiftDate(S.date, 1));
  });
  document.getElementById('todayBtn').addEventListener('click', () => load(localISO()));
  document.getElementById('settingsBtn').addEventListener('click', openSettings);

  document.getElementById('meals').addEventListener('click', (e) => {
    const add = e.target.closest('[data-add]');
    if (add) { openSearch(add.dataset.add); return; }
    const del = e.target.closest('[data-del]');
    if (del) { deleteEntry(del.dataset.del); }
  });

  document.getElementById('closeSheet').addEventListener('click', () => closeSheet('searchSheet'));
  document.getElementById('closePortion').addEventListener('click', () => closeSheet('portionSheet'));
  document.getElementById('closeSettings').addEventListener('click', () => closeSheet('settingsSheet'));

  ['searchSheet', 'portionSheet', 'settingsSheet'].forEach((id) => {
    document.getElementById(id).addEventListener('click', (e) => {
      if (e.target.id === id) closeSheet(id);
    });
  });

  let searchTimer = null;
  document.getElementById('foodQuery').addEventListener('input', (e) => {
    S.query = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 180);
  });

  document.getElementById('catChips').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-cat]');
    if (!chip) return;
    S.category = chip.dataset.cat;
    document.querySelectorAll('#catChips .chip').forEach((c) => c.classList.toggle('on', c === chip));
    runSearch();
  });

  document.getElementById('results').addEventListener('click', (e) => {
    const row = e.target.closest('[data-food]');
    if (!row) return;
    const food = S.foods.find((f) => f.id === row.dataset.food);
    if (food) openPortion(food);
  });

  document.getElementById('recentChips').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-recent]');
    if (!chip) return;
    const food = (S.data.recent || []).find((f) => f.id === chip.dataset.recent);
    if (food) openPortion(food);
  });

  document.getElementById('portionChips').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-unit]');
    if (!chip) return;
    S.unit = chip.dataset.unit;
    S.grams = Number(chip.dataset.grams);
    document.getElementById('gramsInput').value = S.grams;
    document.querySelectorAll('.portion-chip').forEach((c) => c.classList.toggle('on', c === chip));
    updatePortionPreview();
  });

  document.getElementById('gramsInput').addEventListener('input', (e) => {
    S.grams = e.target.value;
    S.unit = null;
    document.querySelectorAll('.portion-chip').forEach((c) => c.classList.remove('on'));
    updatePortionPreview();
  });

  document.getElementById('confirmPortion').addEventListener('click', confirmPortion);

  document.getElementById('customToggle').addEventListener('click', (e) => {
    const form = document.getElementById('customForm');
    form.hidden = !form.hidden;
    e.target.textContent = form.hidden ? '没找到？手动记一笔' : '收起';
    if (!form.hidden) document.getElementById('cName').focus();
  });

  document.getElementById('customSave').addEventListener('click', async () => {
    const name = document.getElementById('cName').value.trim();
    if (!name) { toast('先填个名字'); return; }
    try {
      const res = await api('/api/entries', {
        method: 'POST',
        body: JSON.stringify({
          date: S.date,
          meal: S.meal,
          custom: {
            name,
            grams: Number(document.getElementById('cGrams').value) || 100,
            kcal: Number(document.getElementById('cKcal').value) || 0,
            protein: Number(document.getElementById('cProtein').value) || 0,
            carb: Number(document.getElementById('cCarb').value) || 0,
            fat: Number(document.getElementById('cFat').value) || 0,
          },
        }),
      });
      S.data = res.state;
      render();
      closeSheet('searchSheet');
      ['cName', 'cKcal', 'cProtein', 'cCarb', 'cFat'].forEach((id) => {
        document.getElementById(id).value = '';
      });
      toast(`已记下 ${name}`);
    } catch (err) {
      toast(err.message);
    }
  });

  document.querySelectorAll('#sexSeg button').forEach((btn) => {
    btn.addEventListener('click', () => {
      S.draftProfile.sex = btn.dataset.value;
      document.querySelectorAll('#sexSeg button').forEach((b) => b.classList.toggle('on', b === btn));
      updateCalcPreview();
    });
  });

  document.getElementById('activityList').addEventListener('click', (e) => {
    const item = e.target.closest('[data-activity]');
    if (!item) return;
    S.draftProfile.activity = item.dataset.activity;
    document.querySelectorAll('#activityList .radio-item')
      .forEach((b) => b.classList.toggle('on', b === item));
    updateCalcPreview();
  });

  document.getElementById('goalList').addEventListener('click', (e) => {
    const item = e.target.closest('[data-goal]');
    if (!item) return;
    S.draftProfile.goal = item.dataset.goal;
    document.querySelectorAll('#goalList .radio-item')
      .forEach((b) => b.classList.toggle('on', b === item));
    updateCalcPreview();
  });

  ['pAge', 'pHeight', 'pWeight', 'pDeficit'].forEach((id) => {
    document.getElementById(id).addEventListener('input', updateCalcPreview);
  });

  document.getElementById('saveProfile').addEventListener('click', saveProfile);

  document.getElementById('exportBtn').addEventListener('click', async () => {
    try {
      const data = await api('/api/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `chizhang-backup-${localISO()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById('clearDayBtn').addEventListener('click', async () => {
    if (!confirm(`确定清空 ${S.date} 的全部记录？这一步不能撤销。`)) return;
    try {
      S.data = await api(`/api/day/${S.date}`, { method: 'DELETE' });
      render();
      toast('已清空');
    } catch (err) {
      toast(err.message);
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    ['searchSheet', 'portionSheet', 'settingsSheet'].forEach((id) => {
      if (!document.getElementById(id).hidden) closeSheet(id);
    });
  });
}

/* ---------- boot ---------- */

async function boot() {
  wire();
  await load(S.date);
  if (S.data && !S.data.has_data) {
    setTimeout(() => {
      openSettings();
      toast('先填一下身高体重，我才能算出你的目标');
    }, 400);
  }
}

boot();
