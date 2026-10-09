let offset = 0;
let serverSkew = 0; // ms: เวลาเซิร์ฟเวอร์ - เวลาเครื่อง (ใช้แสดงนาฬิกาเท่านั้น เวลาที่บันทึกมาจากเซิร์ฟเวอร์)

function show(view) {
  $('loginView').classList.toggle('hidden', view !== 'login');
  $('mainView').classList.toggle('hidden', view !== 'main');
}

function tick() {
  const d = new Date(Date.now() + serverSkew);
  $('now').textContent = d.toTimeString().slice(0, 8);
}

async function load() {
  const r = await api('/api/me?offset=' + offset);
  if (r.status === 401) { show('login'); return; }
  if (!r.ok) { msg($('punchMsg'), r.data.error || 'โหลดข้อมูลไม่ได้'); return; }
  const d = r.data;
  show('main');
  const [h, m, s] = d.now.split(':').map(Number);
  const local = new Date(); const srv = new Date(); srv.setHours(h, m, s, 0);
  serverSkew = srv - local;
  tick();

  $('who').textContent = `${d.name} (${d.code})`;
  const t = d.today;
  $('todayLabel').textContent = `${t.dow} ${dmy(t.date)} · กะ ${t.shift_in || '-'}–${t.shift_out || '-'}`;
  let txt = t.shift_in ? `กะวันนี้ ${t.shift_in}–${t.shift_out}` : 'วันนี้เป็นวันหยุด';
  if (t.status === 'work') txt = `เข้า ${t.in || '-'} · ออก ${t.out || '-'}` + (t.late_min ? ` · สาย ${t.late_min} นาที` : '');
  else if (t.status === 'absent') txt = 'แอดมินบันทึกว่าขาด';
  else if (t.status === 'leave') txt = 'แอดมินบันทึกว่าลา';
  $('todayText').textContent = txt;
  const locked = t.status === 'absent' || t.status === 'leave';
  $('btnIn').disabled = !!t.in || locked;
  $('btnOut').disabled = !t.in || !!t.out || locked;

  const p = d.period;
  $('periodLabel').textContent = `${dm(p.start)} – ${dmy(p.end)}` + (p.offset === 0 ? ' (รอบนี้)' : '');
  $('nextBtn').disabled = p.offset >= 0;
  const sm = d.summary;
  $('pay').textContent = sm.rate ? baht(sm.pay) : '';
  $('sum').innerHTML = `<div>วันคิดเงิน ${sm.paid_days}</div><div>มาทำงาน ${sm.worked} วัน</div><div>สาย ${sm.late} ครั้ง</div>` +
    (sm.rate ? `<div>${baht(sm.rate)}/วันเต็ม</div>` : '');
  $('list').innerHTML = d.days.map((v) =>
    `<div class="row"><span>${esc(v.dow)} ${dm(v.date)}</span><span>${v.status === 'work' ? `${esc(v.in || '-')} → ${esc(v.out || '-')}` : ''}</span>${statusBadge(v)}</div>`
  ).join('');
}

async function punch(kind) {
  $('btnIn').disabled = $('btnOut').disabled = true;
  const r = await api('/api/punch', { kind });
  if (r.ok) msg($('punchMsg'), `${kind === 'in' ? 'ตอกเข้า' : 'ตอกออก'}แล้ว เวลา ${r.data.time}`, true);
  else msg($('punchMsg'), r.data.error || 'ตอกไม่สำเร็จ');
  await load();
}

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  msg($('loginMsg'), '');
  const r = await api('/api/login', { code: $('code').value.trim(), pin: $('pin').value.trim() });
  if (!r.ok) { msg($('loginMsg'), r.data.error || 'เข้าสู่ระบบไม่สำเร็จ'); return; }
  $('pin').value = '';
  offset = 0;
  load();
});
$('btnIn').addEventListener('click', () => punch('in'));
$('btnOut').addEventListener('click', () => {
  if (confirm('ยืนยันตอกออก?')) punch('out');
});
$('prevBtn').addEventListener('click', () => { offset -= 1; load(); });
$('nextBtn').addEventListener('click', () => { if (offset < 0) { offset += 1; load(); } });
$('logoutBtn').addEventListener('click', async () => { await api('/api/logout', {}); show('login'); });

setInterval(tick, 1000);
setInterval(() => { if (!document.hidden && !$('mainView').classList.contains('hidden')) load(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
load();
