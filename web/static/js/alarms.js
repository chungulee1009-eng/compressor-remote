let state = 'open';

async function loadAlarms() {
  const box = document.getElementById('alarm_table');
  try {
    const rows = await api('/api/alarms?state=' + state);
    notifyNewAlarms(rows.filter(a => !a.cleared_ts));
    box.innerHTML = rows.length ? rows.map(a => `
      <div class="alarm-item ${a.severity}">
        <div><b>[${a.unit}] ${ALARM_KO[a.type] || a.type}</b> — ${a.message}</div>
        <div class="meta">
          <span>발생 ${tstr(a.raised_ts)}</span>
          <span>${a.cleared_ts ? '해제 ' + tstr(a.cleared_ts) : '진행 중'}</span>
          <span>${a.acknowledged ? '확인: ' + a.ack_by : '미확인'}</span>
        </div>
        ${a.cleared_ts || a.acknowledged ? '' :
          `<div class="act"><button class="btn sm" onclick="ackAlarm(${a.id})">확인</button></div>`}
      </div>`).join('') : '<p class="note">알람 없음</p>';
  } catch (e) { box.textContent = '오류: ' + e.message; }
}
async function ackAlarm(id) { try { await post('/api/alarms/' + id + '/ack'); loadAlarms(); } catch (e) {} }
window.ackAlarm = ackAlarm;

async function loadNotif() {
  try {
    const rows = await api('/api/notifications/recent');
    document.getElementById('notif_table').innerHTML = rows.length ? rows.map(n => `
      <div class="alarm-item info">
        <div><b>${n.channel.toUpperCase()}</b> → ${n.target} · ${n.subject}</div>
        <div class="meta"><span>${tstr(n.ts)}</span><span>${n.body}</span><span>${n.status}</span></div>
      </div>`).join('') : '<p class="note">이력 없음</p>';
  } catch (e) {}
}

document.querySelectorAll('#alarm-tabs button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('#alarm-tabs button').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); state = b.dataset.s; loadAlarms();
  };
});
loadAlarms(); loadNotif();
setInterval(loadAlarms, 4000);
setInterval(loadNotif, 8000);
