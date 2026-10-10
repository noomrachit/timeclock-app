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
  if (v.status === 'work') return '<span class="badge b-ok">ทำงาน</span>';
  if (v.status === 'absent') return '<span class="badge b-no">ขาด</span>';
  if (v.status === 'leave') return '<span class="badge b-warn">ลา</span>';
  if (v.status === 'holiday' || v.holiday) return '<span class="badge b-off">วันหยุด</span>';
  return '<span class="badge b-off">-</span>';
}

// ชั่วโมงตามอัตรา เช่น "8 + 2×125%"
function hoursText(v) {
  if (v.status !== 'work') return v.status === 'leave' && v.leave_hours ? `ลาได้เงิน ${v.leave_hours} ชม.` : '';
  const h = v.hours || {};
  const parts = [];
  if (h['100']) parts.push(`${h['100']}`);
  ['125', '150', '175', '200'].forEach((t) => { if (h[t]) parts.push(`${h[t]}×${t}%`); });
  return parts.join(' + ') || '0';
}

function flags(v) {
  return (v.edited ? ' <span class="badge b-off">แก้โดยแอดมิน</span>' : '') +
    (v.self_edited ? ' <span class="badge b-in">แก้เอง</span>' : '');
}

function msg(el, text, ok) { el.textContent = text || ''; el.className = 'msg ' + (ok ? 'ok' : 'err'); }
