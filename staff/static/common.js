// ใช้ร่วมกันทั้งหน้าพนักงานและแอดมิน
const $ = (id) => document.getElementById(id);

async function api(path, data) {
  const opt = data === undefined
    ? { credentials: 'same-origin' }
    : { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'timeclock' },
        body: JSON.stringify(data) };
  let res;
  try { res = await fetch(path, opt); }
  catch (e) { return { ok: false, status: 0, data: { error: 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจอินเทอร์เน็ต' } }; }
  let body = {};
  try { body = await res.json(); } catch (e) {}
  return { ok: res.ok, status: res.status, data: body };
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function dmy(iso) { const [y, m, d] = iso.split('-'); return `${+d}/${+m}/${y}`; }
function dm(iso) { const [, m, d] = iso.split('-'); return `${+d}/${+m}`; }
function baht(n) { return '฿' + Number(n || 0).toLocaleString('th-TH', { maximumFractionDigits: 2 }); }

function statusBadge(v) {
  if (v.status === 'work') {
    if (v.in && !v.out) return '<span class="badge b-in">กำลังทำงาน</span>';
    return v.late_min ? `<span class="badge b-warn">สาย ${v.late_min} น.</span>` : '<span class="badge b-ok">ทำ</span>';
  }
  if (v.status === 'absent') return '<span class="badge b-no">ขาด</span>';
  if (v.status === 'leave') return '<span class="badge b-off">ลา</span>';
  if (!v.shift_in) return '<span class="badge b-off">หยุด</span>';
  return '<span class="badge b-off">-</span>';
}

function msg(el, text, ok) { el.textContent = text || ''; el.className = 'msg ' + (ok ? 'ok' : 'err'); }
