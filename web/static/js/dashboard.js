const grid = document.getElementById('grid');
const kpis = document.getElementById('kpis');
const updated = document.getElementById('updated');

function statusInfo(u) {
  if (!u.online) return ['off', '통신두절'];
  if (u.fault_alarm) return ['fault', '고장'];
  if (u.run_status) return ['run', '가동'];
  return ['stop', '정지'];
}

function card(u) {
  const [cls, label] = statusInfo(u);
  const sev = u.worst_severity ? ('sev-' + u.worst_severity) : '';
  return `<a class="card ${sev} ${u.online ? '' : 'offline'}" href="/unit/${u.unit}">
    ${u.open_alarms ? `<span class="badge">알람 ${u.open_alarms}</span>` : ''}
    <h3><span class="dot ${cls}"></span> ${u.unit}</h3>
    <div class="row"><span>상태</span><b>${label}</b></div>
    <div class="row"><span>토출압력</span><b>${fmt(u.discharge_pressure, 3)} MPa</b></div>
    <div class="row"><span>모터전류</span><b>${fmt(u.motor_current, 1)} A</b></div>
    <div class="row"><span>토출온도</span><b>${fmt(u.discharge_temp, 1)} ℃</b></div>
    <div class="row"><span>누적</span><b>${u.run_hours_accum ?? '–'} hr</b></div>
  </a>`;
}

async function tick() {
  try {
    const d = await api('/api/overview');
    grid.innerHTML = d.units.map(card).join('');
    const running = d.units.filter(u => u.run_status).length;
    const off = d.units.filter(u => !u.online).length;
    const alarms = d.units.reduce((s, u) => s + u.open_alarms, 0);
    const t = d.totals || {
      running_hp: running * 100, total_hp: d.units.length * 100,
      running_kw: +(running * 74.6).toFixed(1), total_kw: +(d.units.length * 74.6).toFixed(1)
    };
    kpis.innerHTML =
      `<span class="kpi">가동 <b>${running}/${d.units.length}</b></span>
       <span class="kpi">운영 마력 <b>${t.running_hp} / ${t.total_hp} HP</b></span>
       <span class="kpi">운영 출력 <b>${t.running_kw} / ${t.total_kw} kW</b></span>
       <span class="kpi">통신두절 <b>${off}</b></span>
       <span class="kpi">발생 알람 <b>${alarms}</b></span>`;
    updated.textContent = '업데이트: ' + tstr(d.ts);
  } catch (e) {
    updated.textContent = '갱신 오류: ' + e.message;
  }
  try { notifyNewAlarms(await api('/api/alarms?state=open')); } catch (e) {}
}
tick();
setInterval(tick, 3000);
