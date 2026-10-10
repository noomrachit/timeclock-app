let offset = 0;
let serverSkew = 0; // ms: เวลาเซิร์ฟเวอร์ - เวลาเครื่อง (ใช้แสดงนาฬิกาเท่านั้น)
let today = null;
let pickAct = null;
let autoPrompted = false;

function show(view) {
  $('loginView').classList.toggle('hidden', view !== 'login');
  $('mainView').classList.toggle('hidden', view !== 'main');
}

function tick() {
  const d = new Date(Date.now() + serverSkew);
  $('now').textContent = d.toTimeString().slice(0, 8);
}

function todayText(t) {
  if (t.holiday) return 'วันเสาร์ วันหยุด';
  if (t.status === 'work') return `ทำงาน ${t.in} – ${t.out} · ${hoursText(t)} ชม.`;
  if (t.status === 'leave') return 'ลา' + (t.note ? ` · ${t.note}` : '');
  if (t.status === 'absent') return 'ขาด' + (t.note ? ` · ${t.note}` : '');
  return `ยังไม่ได้ลง · กะวันนี้ ${t.shift_in}–${t.shift_out}`;
}

async function load() {
  const r = await api('/api/me?offset=' + offset);
  if (r.status === 401) { show('login'); return; }
  if (!r.ok) { msg($('dayMsg'), r.data.error || 'โหลดข้อมูลไม่ได้'); return; }
  const d = r.data;
  show('main');
  const [h, m, s] = d.now.split(':').map(Number);
  const srv = new Date(); srv.setHours(h, m, s, 0);
  serverSkew = srv - new Date();
  tick();

  $('who').textContent = `${d.name} (${d.code})`;
  const t = today = d.today;
  $('todayLabel').textContent = `${t.dow} ${dmy(t.date)}` + (t.holiday ? ' · วันหยุด' : ` · กะ ${t.shift_in}–${t.shift_out}`);
  $('todayText').innerHTML = esc(todayText(t)) + flags(t);
  const locked = t.holiday || t.edited;
  $('openPick').disabled = locked;
  $('openPick').textContent = t.status ? 'แก้ไขวันนี้' : 'ลงเวลาวันนี้';
  if (t.edited && !t.holiday) msg($('dayMsg'), 'แอดมินแก้วันนี้แล้ว หากต้องการเปลี่ยนให้ติดต่อแอดมิน');

  const p = d.period;
  $('periodLabel').textContent = `${dm(p.start)} – ${dmy(p.end)}` + (p.offset === 0 ? ' (รอบนี้)' : '');
  $('nextBtn').disabled = p.offset >= 0;
  const sm = d.summary, hs = sm.hours;
  $('pay').textContent = sm.rate ? baht(sm.pay) : '';
  $('sum').innerHTML =
    `<div>มาทำงาน ${sm.worked} วัน</div><div>ขาด ${sm.absent} · ลา ${sm.leave}</div>` +
    `<div>100%: ${hs['100']} ชม.</div><div>125%: ${hs['125']} ชม.</div>` +
    `<div>150%: ${hs['150']} ชม.</div><div>175%: ${hs['175']} ชม.</div>` +
    `<div>200%: ${hs['200']} ชม.</div>` + (sm.leave_hours ? `<div>ลาได้เงิน: ${sm.leave_hours} ชม.</div>` : '<div></div>') +
    (sm.rate ? `<div>${baht(sm.rate)}/ชั่วโมง</div>` : '');
  $('list').innerHTML = d.days.map((v) =>
    `<div class="row"><span>${esc(v.dow)} ${dm(v.date)}</span><span>${v.status === 'work' ? `${esc(v.in)}–${esc(v.out)}` : esc(v.note || '')}</span>${statusBadge(v)}</div>`
  ).join('');

  // แจ้งเตือนเด้งขึ้นเองครั้งแรก ถ้าวันนี้ยังไม่ได้ลง
  if (!autoPrompted && offset === 0 && !t.status && !t.holiday) { autoPrompted = true; openPick(); }
}

function openPick() {
  if (!today || today.holiday) return;
  pickAct = null;
  $('pickSub').textContent = `${today.dow} ${dmy(today.date)} · กด "ทำงาน" = ลงเวลาตามกะ ${today.shift_in}–${today.shift_out}`;
  $('pickBtns').classList.remove('hidden');
  ['otherBox', 'noteBox', 'confirmBox'].forEach((x) => $(x).classList.add('hidden'));
  $('pNote').value = ''; msg($('pMsg'), '');
  if (!$('pick').open) $('pick').showModal();
}

async function save(action) {
  const payload = { action, note: $('pNote').value };
  if (action === 'other') { payload.in = $('oIn').value; payload.out = $('oOut').value; }
  const r = await api('/api/day', payload);
  if (!r.ok) { msg($('pMsg'), r.data.error || 'บันทึกไม่สำเร็จ'); return; }
  $('pick').close();
  msg($('dayMsg'), 'บันทึกแล้ว', true);
  load();
}

$('pickBtns').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  pickAct = b.dataset.act;
  if (pickAct === 'work') { save('work'); return; } // กดครั้งเดียว
  $('pickBtns').classList.add('hidden');
  $('noteBox').classList.remove('hidden');
  $('confirmBox').classList.remove('hidden');
  $('pickTitle').textContent = { leave: 'ลา', absent: 'ขาด', other: 'ใส่เวลาเอง' }[pickAct];
  if (pickAct === 'other') {
    $('otherBox').classList.remove('hidden');
    $('oIn').value = (today.status === 'work' && today.in) || today.shift_in;
    $('oOut').value = (today.status === 'work' && today.out) || today.shift_out;
  }
});
$('pSave').addEventListener('click', () => save(pickAct));
$('pClose').addEventListener('click', () => { $('pick').close(); $('pickTitle').textContent = 'วันนี้ลงเวลาแบบไหน'; });
$('pick').addEventListener('close', () => { $('pickTitle').textContent = 'วันนี้ลงเวลาแบบไหน'; });
$('openPick').addEventListener('click', openPick);

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  msg($('loginMsg'), '');
  const r = await api('/api/login', { code: $('code').value.trim(), pin: $('pin').value.trim() });
  if (!r.ok) { msg($('loginMsg'), r.data.error || 'เข้าสู่ระบบไม่สำเร็จ'); return; }
  $('pin').value = '';
  offset = 0;
  load();
});
$('prevBtn').addEventListener('click', () => { offset -= 1; load(); });
$('nextBtn').addEventListener('click', () => { if (offset < 0) { offset += 1; load(); } });
$('logoutBtn').addEventListener('click', async () => { await api('/api/logout', {}); autoPrompted = false; show('login'); });

setInterval(tick, 1000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('pick').open) load(); });
load();
