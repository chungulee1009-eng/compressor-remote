// 공통 유틸
async function api(url, opts) {
  const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  let data = null;
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
  return data;
}
function post(url, body) {
  return api(url, { method: 'POST', body: JSON.stringify(body || {}) });
}
function fmt(v, d) {
  if (v === null || v === undefined) return '–';
  return Number(v).toFixed(d === undefined ? 1 : d);
}
function ago(ts) {
  if (!ts) return '–';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return s + '초 전';
  if (s < 3600) return Math.floor(s / 60) + '분 전';
  if (s < 86400) return Math.floor(s / 3600) + '시간 전';
  return Math.floor(s / 86400) + '일 전';
}
function tstr(ts) {
  if (!ts) return '–';
  return new Date(ts * 1000).toLocaleString('ko-KR', { hour12: false });
}
const ALARM_KO = {
  FAULT: '고장(FAULT)', HIGH_TEMP: '토출온도 상한', HIGH_CURRENT: '모터전류 상한',
  LEAK_SUSPECT: '누설 의심', COMM_LOSS: '통신 두절'
};

// 새 알람 브라우저 알림 (푸시 대체)
let _seenAlarms = null;
function notifyNewAlarms(list) {
  if (!('Notification' in window)) return;
  const ids = new Set(list.map(a => a.id));
  if (_seenAlarms === null) { _seenAlarms = ids; return; }
  for (const a of list) {
    if (!_seenAlarms.has(a.id)) {
      if (Notification.permission === 'granted') {
        new Notification('[' + a.unit + '] ' + (ALARM_KO[a.type] || a.type), { body: a.message });
      }
    }
  }
  _seenAlarms = ids;
}
if ('Notification' in window && Notification.permission === 'default') {
  document.addEventListener('click', function once() {
    Notification.requestPermission(); document.removeEventListener('click', once);
  }, { once: true });
}
