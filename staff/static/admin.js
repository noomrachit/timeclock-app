let offset = 0;
let tab = 'today';
let todayRows = [];
let emps = [];
let dlg = { id: null, name: '', date: null };

function show(view) {
  $('loginView').classList.toggle('hidden', view !== 'login');
  $('mainView').classList.toggle('hidden', view !== 'main');
}

function setTab(t) {
  tab = t;
  document.querySelectorAll('#mainTabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === t));
  ['today', 'report', 'emps'].forEach((x) => $('tab-' + x).classList.toggle('hidden', x !== t));
  refresh();
}

async function guard(r) {
  if (r.status === 401) { show('login'); return false; }
  return true;
}

function refresh() {
  if (tab === 'today') loadToday();
  else if (tab === 'report') loadReport();
  else loadEmps();
}

// ---------- วันนี้ ----------
async function loadToday() {
  const r = await api('/api/admin/today');
  if (!(await guard(r)) || !r.ok) return;
  show('main');
  const d = r.data;
  todayRows = d.rows;
  $('todayTitle').textContent = `${d.dow} ${dmy(d.date)} · ` + (d.holiday ? 'วันหยุด' : `กะ ${d.shift_in}–${d.shift_out}`);
  const c = (st) => d.rows.filter((x) => x.status === st).length;
  const none = d.rows.filter((x) => !x.status).length;
  $('todayStat').innerHTML = `<div><b>${d.rows.length}</b>พนักงาน</div><div><b>${c('work')}</b>ทำงาน</div><div><b>${c('leave')}</b>ลา</div><div><b>${c('absent')}</b>ขาด</div><div><b>${none}</b>ยังไม่ลง</div>`;
  renderToday();
}

function match(x, q) { q = q.trim().toLowerCase(); return !q || x.code.toLowerCase().includes(q) || x.name.toLowerCase().includes(q); }

function renderToday() {
  const q = $('todayFilter').value;
  $('todayBody').innerHTML = todayRows.filter((x) => match(x, q)).map((x) =>
    `<tr><td>${esc(x.code)}</td><td><a href="#" data-emp="${x.id}" data-name="${esc(x.name)}">${esc(x.name)}</a></td><td>${x.status ? statusBadge(x) : '<span class="badge b-off">ยังไม่ลง</span>'}${flags(x)}</td><td>${x.status === 'work' ? `${esc(x.in)}–${esc(x.out)}` : ''}</td><td>${esc(hoursText(x))}</td><td>${esc(x.note || '')}</td></tr>`
  ).join('') || '<tr><td colspan="6" class="sub">ไม่มีข้อมูล</td></tr>';
}

// ---------- สรุปรอบ ----------
async function loadReport() {
  const r = await api('/api/admin/report?offset=' + offset);
  if (!(await guard(r)) || !r.ok) return;
  show('main');
  const d = r.data;
  $('periodLabel').textContent = `${dm(d.period.start)} – ${dmy(d.period.end)}` + (offset === 0 ? ' (รอบนี้)' : '');
  $('nextBtn').disabled = offset >= 0;
  if (document.activeElement !== $('rate')) $('rate').value = d.rate || '';
  const total = d.rows.reduce((a, x) => a + x.pay, 0);
  const miss = d.rows.reduce((a, x) => a + x.missing, 0);
  $('reportStat').innerHTML = `<div><b>${d.rows.length}</b>คน</div><div><b>${baht(total)}</b>ยอดรวม</div><div><b>${miss}</b>วันที่ไม่ได้ลง</div>`;
  $('reportBody').innerHTML = d.rows.map((x) => {
    const h = x.hours;
    return `<tr><td>${esc(x.code)}</td><td>${esc(x.name)}${x.active ? '' : ' <span class="badge b-off">ปิด</span>'}</td><td>${x.worked}</td><td>${h['100']}</td><td>${h['125']}</td><td>${h['150']}</td><td>${h['175']}</td><td>${h['200']}</td><td>${x.leave_hours}</td><td>${x.absent}</td><td>${x.leave}</td><td>${x.missing ? `<span class="badge b-warn">${x.missing}</span>` : 0}</td><td>${baht(x.pay)}</td><td><button class="small" data-emp="${x.id}" data-name="${esc(x.name)}">ดู/แก้</button></td></tr>`;
  }).join('') || '<tr><td colspan="14" class="sub">ยังไม่มีพนักงาน</td></tr>';
}

// ---------- พนักงาน ----------
async function loadEmps() {
  const r = await api('/api/admin/employees');
  if (!(await guard(r)) || !r.ok) return;
  show('main');
  emps = r.data.employees;
  $('empCount').textContent = emps.filter((e) => e.active).length;
  renderEmps();
}

function renderEmps() {
  const q = $('empFilter').value;
  $('empBody').innerHTML = emps.filter((x) => match(x, q)).map((e) =>
    `<tr><td>${esc(e.code)}</td><td>${esc(e.name)}</td><td>${e.active ? (e.locked ? '<span class="badge b-warn">ล็อก PIN</span>' : '<span class="badge b-ok">ใช้งาน</span>') : '<span class="badge b-off">ปิด</span>'}</td>
     <td><button class="small" data-act="reset_pin" data-id="${e.id}" data-name="${esc(e.name)}">รีเซ็ต PIN</button>
     <button class="small" data-act="rename" data-id="${e.id}" data-name="${esc(e.name)}">แก้ชื่อ</button>
     <button class="small ${e.active ? 'danger' : ''}" data-act="${e.active ? 'deactivate' : 'activate'}" data-id="${e.id}" data-name="${esc(e.name)}">${e.active ? 'ปิดใช้งาน' : 'เปิดใช้งาน'}</button></td></tr>`
  ).join('') || '<tr><td colspan="4" class="sub">ยังไม่มีพนักงาน</td></tr>';
}

function showPin(name, code, pin) {
  const el = $('pinShow');
  el.classList.remove('hidden');
  el.innerHTML = `<div class="sub" style="margin-top:10px">PIN ของ ${esc(name)} (${esc(code)}) — แสดงครั้งเดียว จดหรือส่งให้พนักงานตอนนี้</div><div class="pinbox">${esc(pin)}</div>`;
}

// ---------- กล่องแก้วัน ----------
let edAct = 'work';
let edDay = null;

async function openDays(id, name) {
  dlg = { id, name, date: null };
  $('dlgTitle').textContent = name;
  $('editBox').classList.add('hidden');
  if (!$('dayDlg').open) $('dayDlg').showModal();
  const r = await api(`/api/admin/days?id=${id}&offset=${offset}`);
  if (!(await guard(r)) || !r.ok) return;
  window._days = r.data.days;
  $('dlgList').innerHTML = r.data.days.map((v, i) =>
    `<div class="row"><span>${esc(v.dow)} ${dm(v.date)}</span><span>${v.status === 'work' ? `${esc(v.in)}–${esc(v.out)} · ${esc(hoursText(v))}` : esc(hoursText(v) || v.note || '')}${flags(v)}</span>${statusBadge(v)}<button class="small" data-i="${i}">แก้</button></div>`
  ).join('');
}

function setEdAct(a) {
  edAct = a;
  document.querySelectorAll('#edActs button').forEach((b) => b.classList.toggle('on', b.dataset.a === a));
  const sat = edDay && edDay.holiday;
  $('edTimes').classList.toggle('hidden', a !== 'edit');
  $('edLeave').classList.toggle('hidden', a !== 'leave');
  $('edHint').textContent =
    a === 'work' ? (sat ? 'วันเสาร์ลงทำงานไม่ได้' : `ลงเวลาตามกะอัตโนมัติ ${edDay.shift_in}–${edDay.shift_out}`) :
    a === 'edit' ? (sat ? 'วันเสาร์ลงทำงานไม่ได้' : 'ใส่เวลาเข้า–ออกเอง (คิดชั่วโมงปัดลงทีละ 30 นาที)') :
    a === 'clear' ? 'ลบข้อมูลวันนี้ทั้งหมด' : 'ใส่หมายเหตุได้';
}

$('dlgList').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-i]');
  if (!b) return;
  const v = edDay = window._days[+b.dataset.i];
  dlg.date = v.date;
  $('editTitle').textContent = `แก้วันที่ ${v.dow} ${dmy(v.date)}`;
  $('edIn').value = v.in || v.shift_in || '';
  $('edOut').value = v.out || v.shift_out || '';
  $('edLeaveH').value = v.leave_hours || 0;
  $('edNote').value = v.note || '';
  msg($('edMsg'), '');
  setEdAct(v.status === 'work' ? (v.in === v.shift_in && v.out === v.shift_out ? 'work' : 'edit') : (v.status || (v.holiday ? 'holiday' : 'work')));
  $('editBox').classList.remove('hidden');
  $('editBox').scrollIntoView({ behavior: 'smooth' });
});
$('edActs').addEventListener('click', (e) => { const b = e.target.closest('button[data-a]'); if (b) setEdAct(b.dataset.a); });
$('edSave').addEventListener('click', async () => {
  if (edAct === 'clear' && !confirm('ลบข้อมูลวันนี้ของพนักงานคนนี้?')) return;
  const r = await api('/api/admin/day', { id: dlg.id, date: dlg.date, status: edAct, in: $('edIn').value, out: $('edOut').value, leave_hours: $('edLeaveH').value, note: $('edNote').value });
  if (!r.ok) { msg($('edMsg'), r.data.error || 'บันทึกไม่สำเร็จ'); return; }
  await openDays(dlg.id, dlg.name);
  refresh();
});
$('dlgClose').addEventListener('click', () => $('dayDlg').close());

// ---------- ปุ่มต่าง ๆ ----------
document.querySelectorAll('#mainTabs button').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
document.addEventListener('click', (e) => {
  const a = e.target.closest('[data-emp]');
  if (a) { e.preventDefault(); openDays(+a.dataset.emp, a.dataset.name); }
});
$('todayFilter').addEventListener('input', renderToday);
$('empFilter').addEventListener('input', renderEmps);
$('prevBtn').addEventListener('click', () => { offset -= 1; loadReport(); });
$('nextBtn').addEventListener('click', () => { if (offset < 0) { offset += 1; loadReport(); } });
$('rateBtn').addEventListener('click', async () => {
  const r = await api('/api/admin/rate', { rate: $('rate').value });
  msg($('reportMsg'), r.ok ? 'บันทึกค่าแรงแล้ว' : (r.data.error || 'บันทึกไม่สำเร็จ'), r.ok);
  if (r.ok) loadReport();
});
$('csvBtn').addEventListener('click', () => { location.href = '/api/admin/report.csv?offset=' + offset; });

$('addForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await api('/api/admin/employees', { code: $('newCode').value, name: $('newName').value });
  if (!r.ok) { msg($('empMsg'), r.data.error || 'เพิ่มไม่สำเร็จ'); return; }
  msg($('empMsg'), 'เพิ่มพนักงานแล้ว', true);
  showPin($('newName').value, r.data.code, r.data.pin);
  $('newCode').value = ''; $('newName').value = '';
  loadEmps();
});
$('empBody').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  const id = +b.dataset.id, name = b.dataset.name, act = b.dataset.act;
  const payload = { id, action: act };
  if (act === 'reset_pin' && !confirm(`รีเซ็ต PIN ของ ${name}? PIN เดิมจะใช้ไม่ได้ และมือถือที่เข้าระบบอยู่จะถูกออก`)) return;
  if (act === 'deactivate' && !confirm(`ปิดใช้งาน ${name}? จะตอกเวลาไม่ได้ (ข้อมูลเดิมยังอยู่)`)) return;
  if (act === 'rename') { const n = prompt('ชื่อใหม่', name); if (!n) return; payload.name = n; }
  const r = await api('/api/admin/employee', payload);
  if (!r.ok) { msg($('empMsg'), r.data.error || 'ไม่สำเร็จ'); return; }
  if (act === 'reset_pin') { const e2 = emps.find((x) => x.id === id); showPin(name, e2 ? e2.code : '', r.data.pin); }
  loadEmps();
});

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await api('/api/admin/login', { password: $('pw').value });
  if (!r.ok) { msg($('loginMsg'), r.data.error || 'เข้าสู่ระบบไม่สำเร็จ'); return; }
  $('pw').value = '';
  setTab('today');
});
$('logoutBtn').addEventListener('click', async () => { await api('/api/logout', {}); show('login'); });

setInterval(() => { if (!document.hidden && tab === 'today' && !$('mainView').classList.contains('hidden')) loadToday(); }, 30000);

(async () => {
  const r = await api('/api/admin/me');
  if (r.ok) setTab('today'); else show('login');
})();
