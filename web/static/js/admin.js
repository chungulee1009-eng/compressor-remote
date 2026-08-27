async function loadPending() {
  const box = document.getElementById('pending');
  try {
    const rows = await api('/api/commands/pending');
    box.innerHTML = rows.length ? rows.map(c => `
      <div class="alarm-item warning">
        <div><b>#${c.id} [${c.unit}] ${c.action}${c.value != null ? ' → ' + c.value : ''}</b></div>
        <div class="meta"><span>요청자 ${c.requested_by}</span><span>${tstr(c.requested_ts)}</span></div>
        <div class="act">
          <button class="btn ok sm" onclick="approve(${c.id})">승인</button>
          <button class="btn danger sm" onclick="reject(${c.id})">반려</button>
        </div>
      </div>`).join('') : '<p class="note">대기 중인 명령 없음</p>';
  } catch (e) { box.textContent = '오류: ' + e.message; }
}
async function approve(id) { try { await post('/api/commands/' + id + '/approve'); loadPending(); } catch (e) { alert(e.message); } }
async function reject(id) {
  const note = prompt('반려 사유(선택)') || '';
  try { await post('/api/commands/' + id + '/reject', { note }); loadPending(); } catch (e) { alert(e.message); }
}
window.approve = approve; window.reject = reject;

const msg = document.getElementById('admin_msg');
async function addUser() {
  try {
    await post('/api/users', {
      username: document.getElementById('nu_name').value,
      password: document.getElementById('nu_pw').value,
      role: document.getElementById('nu_role').value,
    });
    location.reload();
  } catch (e) { msg.className = 'msg err'; msg.textContent = e.message; }
}
async function setRole(u, role) { try { await post('/api/users/' + u + '/role', { role }); } catch (e) { alert(e.message); } }
async function delUser(u) {
  if (!confirm(u + ' 계정을 삭제할까요?')) return;
  try { await post('/api/users/' + u + '/delete'); location.reload(); } catch (e) { alert(e.message); }
}
window.addUser = addUser; window.setRole = setRole; window.delUser = delUser;

loadPending();
setInterval(loadPending, 5000);
