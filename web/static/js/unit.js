const UID = window.UID;
let range = '24h';

function pill(r) {
  const el = document.getElementById('statuspill');
  let cls = 'stop', t = '정지';
  if (!r || !r.online) { cls = 'off'; t = '통신두절'; }
  else if (r.fault_alarm) { cls = 'fault'; t = '고장(FAULT)'; }
  else if (r.run_status) { cls = 'run'; t = '가동'; }
  el.className = 'pill ' + cls;
  el.textContent = t;
}

async function refreshLatest() {
  try {
    const r = await api('/api/units/' + UID + '/latest');
    pill(r);
    document.getElementById('v_pressure').textContent = fmt(r.discharge_pressure, 3);
    document.getElementById('v_current').textContent = fmt(r.motor_current, 1);
    document.getElementById('v_temp').textContent = fmt(r.discharge_temp, 1);
    document.getElementById('v_hours').textContent = r.run_hours_accum ?? '–';
  } catch (e) {}
}

async function refreshCharts() {
  try {
    const d = await api('/api/units/' + UID + '/history?range=' + range);
    const s = d.series;
    drawChart('ch_pressure', s.map(x => ({ t: x.t, v: x.pressure })), { dec: 2 });
    drawChart('ch_current', s.map(x => ({ t: x.t, v: x.current })), { dec: 0, color: '#f5a623' });
    drawChart('ch_temp', s.map(x => ({ t: x.t, v: x.temp })), { dec: 0, color: '#ef4444' });
  } catch (e) {}
}

async function refreshAlarms() {
  try {
    const all = await api('/api/alarms?state=all');
    const mine = all.filter(a => a.unit === UID).slice(0, 12);
    document.getElementById('unit_alarms').innerHTML = mine.length
      ? mine.map(a => `<div class="alarm-item ${a.severity}">
          <div><b>${ALARM_KO[a.type] || a.type}</b> — ${a.message}</div>
          <div class="meta"><span>발생 ${tstr(a.raised_ts)}</span>
          <span>${a.cleared_ts ? '해제 ' + tstr(a.cleared_ts) : '진행 중'}</span></div>
        </div>`).join('')
      : '<p class="note">이력 없음</p>';
  } catch (e) {}
}

document.querySelectorAll('.range-tabs button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.range-tabs button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    range = b.dataset.r;
    refreshCharts();
  };
});

async function sendCmd(action) {
  const msg = document.getElementById('cmdmsg');
  msg.className = 'msg'; msg.textContent = '전송 중…';
  try {
    const r = await post('/api/units/' + UID + '/command', { action });
    msg.className = 'msg ok';
    msg.textContent = r.status === 'PENDING'
      ? '명령 #' + r.id + ' 접수됨 — 관리자 승인 대기'
      : '명령 #' + r.id + ' 접수됨 — 실행 예약(' + r.status + ')';
  } catch (e) { msg.className = 'msg err'; msg.textContent = '오류: ' + e.message; }
}
async function sendSetpoint() {
  const v = parseFloat(document.getElementById('sp').value);
  const msg = document.getElementById('cmdmsg');
  msg.className = 'msg'; msg.textContent = '전송 중…';
  try {
    const r = await post('/api/units/' + UID + '/command', { action: 'set_pressure', value: v });
    msg.className = 'msg ok'; msg.textContent = '압력설정 명령 #' + r.id + ' (' + r.status + ')';
  } catch (e) { msg.className = 'msg err'; msg.textContent = '오류: ' + e.message; }
}
window.sendCmd = sendCmd; window.sendSetpoint = sendSetpoint;

function loop() { refreshLatest(); refreshAlarms(); }
loop(); refreshCharts();
setInterval(loop, 3000);
setInterval(refreshCharts, 15000);
window.addEventListener('resize', () => { clearTimeout(window._rz); window._rz = setTimeout(refreshCharts, 200); });
