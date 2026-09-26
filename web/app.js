'use strict';
const $=(q,root=document)=>root.querySelector(q), $$=(q,root=document)=>[...root.querySelectorAll(q)];
const esc=(s)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={page:'overview',nodes:[],rules:[],accounts:[],tunnels:[],forwardings:[],events:[],revision:0,retention_days:30,search:'',group:'',nodeFilter:'',pageNum:1,probes:{},user:''};
const titles={overview:'总览',forwardings:'转发管理',tunnels:'隧道管理',nodes:'节点监控',accounts:'账号与配额',rules:'Realm 直连',diagnostic:'连通诊断',events:'操作记录',settings:'设置'};
async function api(path,method='GET',data){const options={method,headers:{'X-Nexus-Request':'panel'}};if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}const response=await fetch('/api'+path,options);let body;try{body=await response.json()}catch{throw new Error('服务器响应无效')}if(!response.ok)throw new Error(body.error||`请求失败 (${response.status})`);return body}
let toastTimer;function toast(message,error=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.add('hidden'),3200)}
function showLogin(){ $('#app').classList.add('hidden');$('#login').classList.remove('hidden') }
function showApp(){ $('#login').classList.add('hidden');$('#app').classList.remove('hidden') }
async function load(silent=false){try{const data=await api('/dashboard');Object.assign(state,data);$('#sync-time').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});if(!silent||!['INPUT','SELECT'].includes(document.activeElement.tagName)&&$('#modal').classList.contains('hidden'))render()}catch(e){if(!silent)toast(e.message,true);if(e.message.includes('登录'))showLogin()}}
function nodeName(id){return state.nodes.find(n=>n.id===id)?.name||'已删除服务器'}
function online(n){return Date.now()/1000-n.last_seen<20}
function nodeStatus(n){const hasTunnel=state.tunnels.some(t=>t.enabled&&[t.entry_id,t.relay_id,t.exit_id].includes(n.id)&&(t.mode==='legacy'||state.forwardings.some(f=>f.tunnel_id===t.id&&f.enabled)));const hasRealm=state.rules.some(r=>r.node_id===n.id&&r.enabled);return !online(n)?'<span class="status failed">离线</span>':n.error?'<span class="status failed">异常</span>':hasRealm&&!n.realm_running||hasTunnel&&!n.tunnel_running?'<span class="status warning">进程未启动</span>':n.applied_revision===state.revision?'<span class="status online">运行中</span>':'<span class="status warning">下发中</span>'}
function metric(title,value,sub,color){return `<div class="metric"><span>${title}</span><strong>${value}</strong><small>${sub}</small></div>`}
function intro(text,button=''){return `<div class="intro"><p>${text}</p>${button}</div>`}
function panel(title,body,sub=''){return `<section class="panel"><header class="panel-head"><div><h2>${title}</h2>${sub?`<p>${sub}</p>`:''}</div></header>${body}</section>`}
function empty(title,subtitle){return `<div class="empty"><strong>${title}</strong>${subtitle}</div>`}
function overview(){
  const active=state.nodes.filter(online).length;
  const healthy=state.forwardings.filter(f=>forwardState(f).label==='运行中').length;
  const issues=state.nodes.filter(n=>n.error).length;
  let body=intro('先安装节点，建立三节点隧道，再在隧道下添加多个转发。',`<button class="primary" data-action="new-forward">＋ 新增转发</button>`);
  body+=`<div class="metrics">${metric('节点',state.nodes.length,`${active} 台在线`)}${metric('隧道',state.tunnels.length,'前置 · 中转 · 落地')}${metric('转发',state.forwardings.length,`${healthy} 条运行中`)}${metric('待处理异常',issues,'点击节点查看完整原因')}</div>`;
  const recent=state.nodes.slice(0,6).map(n=>`<div class="line-item"><div><strong>${esc(n.name)}</strong><small>${esc(n.address||'未填写地址')}${n.error?` · ${esc(n.error)}`:''}</small></div>${nodeStatus(n)}</div>`).join('')||empty('先添加一台节点','服务器页面会生成单条 SSH 安装命令。');
  const paths=state.tunnels.slice(0,6).map(t=>`<div class="line-item"><div><strong>${esc(t.name)}</strong><small>${esc(nodeName(t.entry_id))} → ${esc(nodeName(t.relay_id))} → ${esc(nodeName(t.exit_id))} · ${state.forwardings.filter(f=>f.tunnel_id===t.id).length} 条转发</small></div><span class="status ${t.enabled?'online':''}">${t.enabled?'已启用':'已停用'}</span></div>`).join('')||empty('还没有隧道','选择三个节点组成链路，目标地址在转发中填写。');
  return body+`<div class="split">${panel('节点状态',`<div class="panel-body">${recent}</div>`)}${panel('转发链路',`<div class="panel-body">${paths}</div>`)}</div>`;
}
function date(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—'}
function filtered(items){const q=state.search.toLowerCase();return items.filter(item=>{if(state.group&&item.grp!==state.group)return false;if(state.nodeFilter&&String(item.node_id)!==state.nodeFilter)return false;return !q||[item.name,item.address,item.grp,item.remote_host,item.extra_remotes,item.listen_port,item.remote_port,nodeName(item.node_id)].some(x=>String(x??'').toLowerCase().includes(q))})}
function paging(items){const count=Math.max(1,Math.ceil(items.length/30));state.pageNum=Math.min(state.pageNum,count);const slice=items.slice((state.pageNum-1)*30,state.pageNum*30);return {slice,footer:`<div class="pager"><span>共 ${items.length} 条 · 第 ${state.pageNum}/${count} 页</span><button data-action="prev" ${state.pageNum===1?'disabled':''}>上一页</button><button data-action="next" ${state.pageNum===count?'disabled':''}>下一页</button></div>`}}
function toolbar(kind){const items=kind==='nodes'?state.nodes:state.rules;const groups=[...new Set(items.map(x=>x.grp).filter(Boolean))].sort();return `<div class="toolbar"><input id="search" aria-label="搜索" placeholder="搜索名称、地址或端口" value="${esc(state.search)}"><select id="group-filter" aria-label="分组"><option value="">全部分组</option>${groups.map(x=>`<option value="${esc(x)}" ${x===state.group?'selected':''}>${esc(x)}</option>`).join('')}</select>${kind==='rules'?`<select id="node-filter" aria-label="服务器"><option value="">全部服务器</option>${state.nodes.map(n=>`<option value="${n.id}" ${String(n.id)===state.nodeFilter?'selected':''}>${esc(n.name)}</option>`).join('')}</select>`:''}<span class="spacer"></span><button class="primary" data-action="new-${kind==='nodes'?'node':'rule'}">＋ 添加${kind==='nodes'?'服务器':'规则'}</button></div>`}
function nodes(){
  const {slice,footer}=paging(filtered(state.nodes));
  const rows=slice.map(n=>`<tr><td><strong>${esc(n.name)}</strong><small>${esc(n.grp||'未分组')}</small></td><td class="mono">${esc(n.address||'—')}</td><td>${nodeStatus(n)}${n.error?`<small class="error-detail">${esc(n.error)}</small>`:''}</td><td>${n.last_seen?date(n.last_seen):'从未连接'}</td><td class="actions"><button data-action="edit-node" data-id="${n.id}">编辑</button><button data-action="enroll-node" data-id="${n.id}">安装/升级</button><button class="danger" data-action="delete-node" data-id="${n.id}">删除</button></td></tr>`).join('');
  return intro('新增节点后复制单条命令到目标服务器 SSH 执行。错误会完整显示在状态列。',`<button class="primary" data-action="new-node">＋ 添加节点</button>`)+`<section class="panel"><div class="toolbar"><input id="search" aria-label="搜索节点" placeholder="搜索名称、地址或分组" value="${esc(state.search)}"></div><div class="table-wrap"><table class="data-table"><thead><tr><th>节点</th><th>节点地址</th><th>实际运行状态</th><th>最后心跳</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="5">${empty('没有节点','点击“添加节点”开始。')}</td></tr>`}</tbody></table></div>${footer}</section>`;
}
function ruleState(r){
  if(!r.enabled)return '<span class="status">已停用</span>';
  const n=state.nodes.find(n=>n.id===r.node_id);
  if(!n||!online(n))return '<span class="status warning">等待节点</span>';
  if(n.error||!n.realm_running)return '<span class="status failed">启动失败</span>';
  return n.applied_revision===state.revision?'<span class="status online">运行中</span>':'<span class="status warning">下发中</span>';
}
function rules(){
  const {slice,footer}=paging(filtered(state.rules));
  const rows=slice.map(r=>`<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.grp||'未分组')}</small></td><td>${esc(nodeName(r.node_id))}</td><td class="mono">${esc(r.listen_host)}:${r.listen_port}</td><td class="mono">${esc(r.remote_host)}:${r.remote_port}${r.extra_remotes?.length?` · +${r.extra_remotes.length} 目标`:''}</td><td>${esc(r.protocol.toUpperCase())}</td><td>${ruleState(r)}</td><td class="actions"><button data-action="toggle-rule" data-id="${r.id}">${r.enabled?'停用':'启用'}</button><button data-action="edit-rule" data-id="${r.id}">编辑</button><button class="danger" data-action="delete-rule" data-id="${r.id}">删除</button></td></tr>`).join('');
  return intro('直接使用 Realm 的 TCP/UDP 端口转发。状态表示节点实际运行情况。',`<button class="primary" data-action="new-rule">＋ 添加直连</button>`)+`<section class="panel"><div class="toolbar"><input id="search" aria-label="搜索规则" placeholder="搜索规则或端口" value="${esc(state.search)}"></div><div class="table-wrap"><table class="data-table"><thead><tr><th>规则</th><th>节点</th><th>监听</th><th>目标</th><th>协议</th><th>运行状态</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="7">${empty('暂无直连规则','三节点链路请从“隧道管理”开始。')}</td></tr>`}</tbody></table></div>${footer}</section>`;
}
function gb(bytes){return (bytes/1024**3).toFixed(2)+' GB'}
function forwardState(f){
  if(!f.enabled)return {label:'已停用',kind:''};
  const t=state.tunnels.find(t=>t.id===f.tunnel_id);
  if(!t||!t.enabled)return {label:'隧道已停用',kind:'warning'};
  const nodes=[t.entry_id,t.relay_id,t.exit_id].map(id=>state.nodes.find(n=>n.id===id));
  if(nodes.some(n=>!n||!online(n)))return {label:'等待节点',kind:'warning'};
  if(nodes.some(n=>n.error||!n.tunnel_running))return {label:'节点异常',kind:'failed'};
  if(nodes.some(n=>n.applied_revision!==state.revision))return {label:'下发中',kind:'warning'};
  return {label:'运行中',kind:'online'};
}
function forwardings(){
  const query=state.search.toLowerCase();
  const items=state.forwardings.filter(f=>!query||[f.name,f.entry_port,...f.targets,state.tunnels.find(t=>t.id===f.tunnel_id)?.name].some(x=>String(x??'').toLowerCase().includes(query)));
  const {slice,footer}=paging(items);
  const rows=slice.map(f=>{
    const t=state.tunnels.find(t=>t.id===f.tunnel_id),n=state.nodes.find(n=>n.id===t?.entry_id),s=forwardState(f);
    return `<tr><td><strong>${esc(f.name)}</strong><small>${esc(t?.name||'已删除隧道')}</small></td><td class="mono">${esc(n?.address||'未设置入口地址')}:${f.entry_port}<small>中转 TCP ${f.relay_port} · 落地 TCP ${f.exit_port}</small></td><td class="mono">${esc(f.targets.join(' · '))}</td><td><span class="tag">TCP</span></td><td><span class="status ${s.kind}">${s.label}</span>${s.kind==='failed'?`<small class="error-detail">${esc([t?.entry_id,t?.relay_id,t?.exit_id].map(id=>state.nodes.find(n=>n.id===id)?.error).filter(Boolean).join(' · '))}</small>`:''}</td><td class="actions"><button data-action="toggle-forward" data-id="${f.id}">${f.enabled?'停用':'启用'}</button><button data-action="edit-forward" data-id="${f.id}">编辑</button><button class="danger" data-action="delete-forward" data-id="${f.id}">删除</button></td></tr>`;
  }).join('');
  return intro('选隧道，填入口端口与落地目标即可。中转与落地端口自动分配，状态展示实际节点运行情况。',`<button class="primary" data-action="new-forward">＋ 新增转发</button>`)+`<section class="panel"><div class="toolbar"><input id="search" aria-label="搜索转发" placeholder="搜索名称、隧道、地址或端口" value="${esc(state.search)}"><span class="spacer"></span><span class="muted small">共 ${items.length} 条</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>转发 / 隧道</th><th>入口地址</th><th>落地目标</th><th>协议</th><th>运行状态</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="6">${empty('暂无转发','先创建隧道，再添加第一条转发。')}</td></tr>`}</tbody></table></div>${footer}</section><p class="footnote">“已创建”不代表端口可用；只有三台节点全部上线并启动转发进程后才显示“运行中”。</p>`;
}
function accounts(){const rows=state.accounts.map(a=>`<tr><td><strong>${esc(a.name)}</strong></td><td>${gb(a.used_bytes)} / ${a.quota_bytes?gb(a.quota_bytes):'不限'}</td><td>${gb(a.up_bytes)} ↑ / ${gb(a.down_bytes)} ↓</td><td>${a.speed_bps?a.speed_bps*8/1e6+' Mbps':'不限'}</td><td>${{up:'上行',down:'下行',both:'双向'}[a.billing]} × ${a.ratio_bp/10000}</td><td>¥${(a.amount_cents/100).toFixed(2)}<small>¥${(a.price_cents_per_gb/100).toFixed(2)}/GB</small></td><td><span class="status ${a.enabled?'online':''}">${a.enabled?'启用':'停用'}</span></td><td class="actions"><button data-action="edit-account" data-id="${a.id}">编辑</button><button class="danger" data-action="delete-account" data-id="${a.id}">删除</button></td></tr>`).join('');return intro('每个账号绑定一条三节点隧道。入口执行 TCP 流量配额、倍率与共享限速。',`<button class="primary" data-action="new-account">＋ 添加账号</button>`)+`<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>账号</th><th>已计费 / 配额</th><th>实际流量</th><th>限速</th><th>计费方向</th><th>累计金额</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="8">${empty('暂无隧道账号','先创建账号，再创建三节点隧道。')}</td></tr>`}</tbody></table></div></section>`}
function tunnels(){
  const rows=state.tunnels.map(t=>{const count=state.forwardings.filter(f=>f.tunnel_id===t.id).length;return `<tr><td><strong>${esc(t.name)}</strong><small>${esc(state.accounts.find(a=>a.id===t.account_id)?.name||'—')}${t.mode==='legacy'?' · 旧版单目标':''}</small></td><td class="path-cell">${esc(nodeName(t.entry_id))}<span>→</span>${esc(nodeName(t.relay_id))}<span>→</span>${esc(nodeName(t.exit_id))}</td><td>${count} 条</td><td><span class="status ${t.enabled?'online':''}">${t.enabled?'已启用':'已停用'}</span></td><td class="actions">${t.mode==='chain'?`<button data-action="add-forward" data-id="${t.id}">＋ 转发</button>`:''}<button data-action="toggle-tunnel" data-id="${t.id}">${t.enabled?'停用':'启用'}</button><button data-action="edit-tunnel" data-id="${t.id}">编辑</button><button class="danger" data-action="delete-tunnel" data-id="${t.id}">删除</button></td></tr>`}).join('');
  return intro('隧道只定义三台机器的链路；每条隧道可以承载多个不同入口端口和目标。',`<button class="primary" data-action="new-tunnel">＋ 新增隧道</button>`)+`<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>隧道 / 账号</th><th>前置 → 中转 → 落地</th><th>转发数</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="5">${empty('暂无隧道','添加三个节点和一个账号后，创建隧道。')}</td></tr>`}</tbody></table></div></section><p class="footnote">隧道转发支持 TCP；UDP 可在“Realm 直连”中配置。中转和落地的监听端口由面板为每条转发分配。</p>`;
}
function diagnostic(){const rows=state.rules.filter(r=>r.enabled).map(r=>{const p=state.probes[r.id];const status=p===undefined?'—':p===null?'测试中…':p.reachable?`<span class="latency ${p.ms<130?'good':'slow'}">${p.ms} ms</span>`:'<span class="latency bad">不可达</span>';return `<tr><td><strong>${esc(r.name)}</strong><small>${esc(nodeName(r.node_id))}</small></td><td class="mono">${esc(r.remote_host)}:${r.remote_port}</td><td>${status}</td><td class="actions"><button data-action="probe-rule" data-id="${r.id}" ${r.protocol==='udp'?'disabled title="TCP 探测不适用于 UDP"':''}>测试</button></td></tr>`}).join('');return intro('TCP 测试从面板服务器发起，耗时直接显示在行内；UDP 无法通过此方法判断。')+`<div class="panel"><div class="toolbar"><strong>目标连通性</strong><span class="spacer"></span><button data-action="probe-all">测试全部 TCP</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>规则</th><th>目标</th><th>延迟</th><th style="text-align:right">操作</th></tr></thead><tbody>${rows||`<tr><td colspan="4">${empty('暂无启用的规则','添加转发规则后在这里检查目标端口。')}</td></tr>`}</tbody></table></div></div><p class="footnote">这里显示面板 → 目标的 TCP 建连时间，不等于用户 → 中转 → 目标的完整链路延迟；绿色 &lt;130 ms，黄色 ≥130 ms。</p>`}
function events(){return intro('记录管理员的配置变更，默认保留 30 天。')+panel('操作记录',`<div class="table-wrap"><table class="data-table"><thead><tr><th>操作</th><th>详情</th><th>账号</th><th>时间</th></tr></thead><tbody>${state.events.map(e=>`<tr><td><strong>${esc(e.action)}</strong></td><td>${esc(e.detail)}</td><td>${esc(e.actor)}</td><td>${date(e.created_at)}</td></tr>`).join('')||`<tr><td colspan="4">${empty('暂无记录','配置变更会显示在这里。')}</td></tr>`}</tbody></table></div>`) }
function settings(){return intro('管理员账号、审计记录与安装操作。')+`<div class="settings-grid">${panel('修改密码',`<form id="password-form" class="panel-body"><label>当前密码<input name="current" type="password" required></label><label>新密码（至少 12 位）<input name="password" type="password" minlength="12" required></label><button class="primary">保存密码</button></form>`)}${panel('修改账号',`<form id="username-form" class="panel-body"><label>新账号名<input name="username" value="${esc(state.user)}" minlength="3" maxlength="32" required></label><label>当前密码<input name="password" type="password" required></label><button class="primary">保存账号</button></form>`)}${panel('记录保留',`<form id="retention-form" class="panel-body"><p class="muted small">操作记录自动清理；登录会话到期后自动删除。数据库备份与恢复在 SSH 菜单中操作。</p><label>保留天数（1–365）<input name="retention_days" type="number" min="1" max="365" value="${state.retention_days}" required></label><button class="primary">保存设置</button></form>`)}${panel('SSH 管理菜单',`<div class="panel-body"><p class="muted small">在面板服务器的 SSH 终端运行：</p><code class="secret">sudo nexus-panel</code><p class="footnote">可检查服务、更新、修改域名、重置密码、备份与恢复。安装脚本支持 Debian / Ubuntu。</p></div>`)}</div>`}
function render(){const page=state.page;$$('#nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===page));$('#title').textContent=titles[page];$('#breadcrumb').textContent='WORKSPACE / '+page.toUpperCase();$('#content').innerHTML=({overview,forwardings,nodes,rules,tunnels,accounts,diagnostic,events,settings})[page]();$('#username').textContent=state.user||'管理员'}
function modal(title,body){$('#modal-title').textContent=title;$('#modal-body').innerHTML=body;$('#modal').classList.remove('hidden');setTimeout(()=>$('#modal input')?.focus(),0)}function closeModal(){$('#modal').classList.add('hidden');$('#modal-body').innerHTML=''}
function nodeForm(n){modal(n?'编辑服务器':'添加服务器',`<form id="node-form" data-id="${n?.id||''}" class="form-grid"><label>服务器名称<input name="name" maxlength="80" value="${esc(n?.name||'')}" required></label><label>分组（可选）<input name="grp" maxlength="60" value="${esc(n?.grp||'')}"></label><label class="wide">节点地址（隧道上游可连接的 IP 或域名）<input name="address" value="${esc(n?.address||'')}" placeholder="例如 relay.example.com"></label><div class="hint wide">创建后会生成有效期 30 分钟的一次性安装命令。三节点隧道需要填写三个节点之间可互通的地址。</div><div class="form-actions wide"><button type="button" data-close="1">取消</button><button class="primary">${n?'保存修改':'创建服务器'}</button></div></form>`)}
function ruleForm(r){if(!state.nodes.length)return toast('请先添加服务器',true);modal(r?'编辑规则':'创建转发规则',`<form id="rule-form" data-id="${r?.id||''}" class="form-grid"><label>规则名称<input name="name" value="${esc(r?.name||'')}" maxlength="80" required></label><label>所属服务器<select name="node_id" ${r?'disabled':''}>${state.nodes.map(n=>`<option value="${n.id}" ${r?.node_id===n.id?'selected':''}>${esc(n.name)}</option>`).join('')}</select></label><label>分组（可选）<input name="grp" maxlength="60" value="${esc(r?.grp||'')}"></label><label>协议<select name="protocol">${['tcp','udp','both'].map(p=>`<option value="${p}" ${(r?.protocol||'tcp')===p?'selected':''}>${p.toUpperCase()}</option>`).join('')}</select></label><label>监听 IP<input name="listen_host" value="${esc(r?.listen_host||'0.0.0.0')}" required></label><label>监听端口<input name="listen_port" type="number" min="1" max="65535" value="${r?.listen_port||''}" required></label><label>目标域名或 IP<input name="remote_host" value="${esc(r?.remote_host||'')}" placeholder="example.com" required></label><label>目标端口<input name="remote_port" type="number" min="1" max="65535" value="${r?.remote_port||''}" required></label><label class="wide">附加目标（可选，每行一个 域名:端口）<textarea name="extra_remotes" rows="3" placeholder="backup.example.com:443">${esc((r?.extra_remotes||[]).join("\n"))}</textarea></label><label class="wide">分配策略<select name="balance"><option value="off" ${(r?.balance||"off")==="off"?"selected":""}>单一目标</option><option value="roundrobin" ${r?.balance==="roundrobin"?"selected":""}>轮询</option><option value="iphash" ${r?.balance==="iphash"?"selected":""}>IP 哈希</option></select></label><label class="wide"><input name="enabled" type="checkbox" class="check-input" ${!r||r.enabled?'checked':''}>启用此规则</label><div class="form-actions wide"><button type="button" data-close="1">取消</button><button class="primary">保存规则</button></div></form>`)}
function accountForm(a){modal(a?'编辑隧道账号':'新建隧道账号',`<form id="account-form" data-id="${a?.id||''}" class="form-grid"><label class="wide">账号名称<input name="name" value="${esc(a?.name||'')}" maxlength="80" required></label><label>配额 GB（0 不限）<input name="quota_gb" type="number" min="0" max="100000" value="${a?.quota_bytes/1024**3||0}"></label><label>共享限速 Mbps（0 不限）<input name="speed_mbps" type="number" min="0" max="100000" value="${a?.speed_bps*8/1e6||0}"></label><label>计费方向<select name="billing">${['both','up','down'].map((x,i)=>`<option value="${x}" ${x===(a?.billing||'both')?'selected':''}>${['双向','上行','下行'][i]}</option>`).join('')}</select></label><label>计费倍率（0.1–10）<input name="ratio" type="number" step="0.1" min="0.1" max="10" value="${a?.ratio_bp/10000||1}"></label><label>参考单价 ¥/GB（0 免费）<input name="price_per_gb" type="number" step="0.01" min="0" max="100000" value="${a?.price_cents_per_gb/100||0}"></label><label class="wide"><input name="enabled" type="checkbox" class="check-input" ${!a||a.enabled?'checked':''}>启用账号</label><div class="form-actions wide"><button type="button" data-close="1">取消</button><button class="primary">保存账号</button></div></form>`)}
function tunnelForm(t){
  if(state.nodes.length<3)return toast('请先添加三台节点',true);
  const accounts=state.accounts.filter(a=>!state.tunnels.some(x=>x.account_id===a.id&&x.id!==t?.id));
  const select=(name,label,items,value)=>`<label>${label}<select name="${name}">${items.map(x=>`<option value="${x.id}" ${x.id===value?'selected':''}>${esc(x.name)}${x.address?' · '+esc(x.address):''}</option>`).join('')}</select></label>`;
  modal(t?'编辑隧道':'新增三节点隧道',`<form id="tunnel-form" data-id="${t?.id||''}" class="form-grid"><label class="wide">隧道名称<input name="name" maxlength="80" value="${esc(t?.name||'')}" placeholder="例如 CNIX → 香港 → 落地" required></label><label>隧道账号<select name="account_id" ${t?'disabled':''}>${!t?'<option value="auto">自动创建（不限额、不限速）</option>':''}${accounts.map(a=>`<option value="${a.id}" ${t?.account_id===a.id?'selected':''}>${esc(a.name)}</option>`).join('')}</select></label><div></div>${select('entry_id','① 前置入口',state.nodes,t?.entry_id||state.nodes[0].id)}${select('relay_id','② 中转节点',state.nodes,t?.relay_id||state.nodes[1].id)}${select('exit_id','③ 落地节点',state.nodes,t?.exit_id||state.nodes[2].id)}<div></div><label class="wide"><input name="enabled" type="checkbox" class="check-input" ${!t||t.enabled?'checked':''}>启用隧道</label><div class="hint wide">目标地址和入口端口在下一步“新增转发”里配置；内部监听端口自动分配。账号配额、倍率及限速可随后在“账号与配额”修改。${t?.mode==='legacy'?'保存后会将旧版单目标配置迁移成一条转发。':''}</div><div class="form-actions wide"><button type="button" data-close="1">取消</button><button class="primary">${t?'保存隧道':'创建隧道'}</button></div></form>`);
}
function forwardForm(f,selectedTunnel){
  const available=state.tunnels.filter(t=>t.mode==='chain');
  if(!available.length)return toast('请先创建三节点隧道',true);
  const chosen=f?.tunnel_id||selectedTunnel||available[0].id;
  modal(f?'编辑转发':'新增转发',`<form id="forward-form" data-id="${f?.id||''}" class="form-grid"><label class="wide">转发名称<input name="name" maxlength="80" value="${esc(f?.name||'')}" placeholder="例如 香港落地 · SSH" required></label><label class="wide">选择隧道<select name="tunnel_id" ${f?'disabled':''}>${available.map(t=>`<option value="${t.id}" ${chosen===t.id?'selected':''}>${esc(t.name)} · ${esc(nodeName(t.entry_id))} → ${esc(nodeName(t.relay_id))} → ${esc(nodeName(t.exit_id))}</option>`).join('')}</select></label><label>入口端口（留空自动分配）<input name="entry_port" type="number" min="1" max="65535" value="${f?.entry_port||''}" placeholder="自动分配"></label><label>多个目标的策略<select name="strategy"><option value="first" ${(!f||f.strategy==='first')?'selected':''}>单一目标</option><option value="roundrobin" ${f?.strategy==='roundrobin'?'selected':''}>轮询</option></select></label><label class="wide">落地目标（每行一个 域名:端口）<textarea name="targets" rows="4" placeholder="example.com:443" required>${esc((f?.targets||[]).join('\n'))}</textarea></label><label class="wide"><input name="enabled" type="checkbox" class="check-input" ${!f||f.enabled?'checked':''}>启用转发</label><div class="hint wide">客户端连接前置节点地址:入口端口。多目标时请选择轮询；防火墙需要放行入口和自动分配的两个节点间 TCP 端口。</div><div class="form-actions wide"><button type="button" data-close="1">取消</button><button class="primary">${f?'保存转发':'创建转发'}</button></div></form>`);
}
function tokenModal(ticket){const command=`curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/scripts/install-agent.sh -o /tmp/nexus-agent-install.sh && sudo bash /tmp/nexus-agent-install.sh --panel '${location.origin}' --ticket '${ticket}'`;state.installCommand=command;modal('一条命令安装节点',`<div class="stack"><p class="hint">在目标服务器的 SSH 终端粘贴下方整条命令即可。安装码 30 分钟内有效，且只能使用一次。</p><code class="secret">${esc(command)}</code><div class="form-actions"><button data-action="copy-install">复制命令</button><button data-close="1">完成</button></div><p class="footnote">请确认目标服务器可访问面板域名和 GitHub。运行后节点通常约 10 秒显示在线。</p></div>`)}

function formData(form){return Object.fromEntries(new FormData(form))}
async function withBusy(button,fn){button.disabled=true;try{await fn()}catch(e){toast(e.message,true)}finally{button.disabled=false}}
async function probe(r){state.probes[r.id]=null;render();try{state.probes[r.id]=await api('/probe','POST',{host:r.remote_host,port:r.remote_port});render()}catch(e){delete state.probes[r.id];toast(e.message,true);render()}}
async function action(kind,id){
  const collections={node:state.nodes,rule:state.rules,tunnel:state.tunnels,account:state.accounts,forward:state.forwardings};
  if(kind==='new-node')return nodeForm();
  if(kind==='new-rule')return ruleForm();
  if(kind==='new-account')return accountForm();
  if(kind==='new-tunnel')return tunnelForm();
  if(kind==='new-forward')return forwardForm();
  if(kind==='add-forward')return forwardForm(null,id);
  if(kind.startsWith('edit-')){
    const type=kind.slice(5),item=collections[type]?.find(x=>x.id===id);
    return {node:nodeForm,rule:ruleForm,account:accountForm,tunnel:tunnelForm,forward:forwardForm}[type](item);
  }
  if(kind==='prev'||kind==='next'){state.pageNum+=kind==='next'?1:-1;return render()}
  if(kind==='probe-rule')return probe(state.rules.find(x=>x.id===id));
  if(kind==='probe-all'){
    const work=state.rules.filter(x=>x.enabled&&x.protocol!=='udp');let cursor=0;
    await Promise.all(Array.from({length:Math.min(6,work.length)},async()=>{while(cursor<work.length)await probe(work[cursor++])}));return;
  }
  if(kind==='enroll-node'){const result=await api(`/nodes/${id}/enrollment`,'POST',{});tokenModal(result.ticket);return}
  if(kind==='copy-install'){await navigator.clipboard.writeText(state.installCommand);toast('安装命令已复制');return}
  if(kind.startsWith('delete-')){
    const type=kind.slice(7),item=collections[type]?.find(x=>x.id===id);
    if(!item||!confirm(`确定删除「${item.name}」？`))return;
    const path={node:'nodes',rule:'rules',account:'accounts',tunnel:'tunnels',forward:'forwardings'}[type];
    await api(`/${path}/${id}`,'DELETE');toast('已删除');return load();
  }
  if(kind.startsWith('toggle-')){
    const type=kind.slice(7),path={rule:'rules',tunnel:'tunnels',forward:'forwardings'}[type];
    const item=collections[type]?.find(x=>x.id===id);
    if(!item)return;
    await api(`/${path}/${id}`,'PUT',{...item,enabled:!item.enabled});
    toast(item.enabled?'已停用':'已启用');return load();
  }
}
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const btn=$('button',e.target);await withBusy(btn,async()=>{try{const form=formData(e.target);await api('/login','POST',form);state.user=form.username;$('#login-error').textContent='';showApp();await load()}catch(err){$('#login-error').textContent=err.message}})});
$('#nav').addEventListener('click',e=>{const b=e.target.closest('[data-page]');if(!b)return;state.page=b.dataset.page;state.search='';state.group='';state.nodeFilter='';state.pageNum=1;render();$('.sidebar').classList.remove('open');$('#scrim').classList.add('hidden')});
$('#mobile-menu').onclick=()=>{$('.sidebar').classList.add('open');$('#scrim').classList.remove('hidden')};$('#scrim').onclick=()=>{$('.sidebar').classList.remove('open');$('#scrim').classList.add('hidden')};$('#refresh').onclick=()=>load();$('#logout').onclick=async()=>{await api('/logout','POST',{});showLogin()};
document.addEventListener('click',async e=>{if(e.target.closest('[data-close]'))return closeModal();const b=e.target.closest('[data-action]');if(!b)return;await withBusy(b,()=>action(b.dataset.action,Number(b.dataset.id)))});
$('#content').addEventListener('input',e=>{if(e.target.id==='search'){state.search=e.target.value;state.pageNum=1;const pos=e.target.selectionStart;render();const search=$('#search');search.focus();search.setSelectionRange(pos,pos)}});
$('#content').addEventListener('change',e=>{if(e.target.id==='group-filter'||e.target.id==='node-filter'){state[e.target.id==='group-filter'?'group':'nodeFilter']=e.target.value;state.pageNum=1;render()}});
document.addEventListener('submit',async e=>{if(e.target.id==='login-form')return;e.preventDefault();const f=e.target,button=$('button[type=submit],button.primary',f);await withBusy(button,async()=>{const data=formData(f);
if(f.id==='node-form'){const id=f.dataset.id;const result=await api(id?`/nodes/${id}`:'/nodes',id?'PUT':'POST',data);closeModal();await load();if(!id)tokenModal(result.ticket);else toast('服务器已更新')}
else if(f.id==='rule-form'){const id=f.dataset.id;const body={...data,enabled:!!f.elements.enabled.checked};if(id)body.node_id=state.rules.find(r=>r.id===Number(id)).node_id;await api(id?`/rules/${id}`:'/rules',id?'PUT':'POST',body);closeModal();toast(id?'规则已更新':'规则已创建');await load()}
else if(f.id==='account-form'||f.id==='tunnel-form'){
  const id=f.dataset.id,collection=f.id==='account-form'?'accounts':'tunnels';
  if(id&&collection==='tunnels')data.account_id=state.tunnels.find(t=>t.id===Number(id)).account_id;
  await api(id?`/${collection}/${id}`:`/${collection}`,id?'PUT':'POST',{...data,mode:collection==='tunnels'?'chain':undefined,enabled:!!f.elements.enabled.checked});
  closeModal();toast(id?'已更新':'已创建');await load();
}
else if(f.id==='forward-form'){
  const id=f.dataset.id;
  if(id)data.tunnel_id=state.forwardings.find(x=>x.id===Number(id)).tunnel_id;
  const result=await api(id?`/forwardings/${id}`:'/forwardings',id?'PUT':'POST',{...data,enabled:!!f.elements.enabled.checked});
  closeModal();await load();toast(id?'转发已更新':`转发已创建，入口端口 ${result.entry_port}`);
}
else if(f.id==='password-form'){await api('/password','POST',data);toast('密码已更新，请重新登录');showLogin()}
else if(f.id==='username-form'){await api('/username','POST',data);state.user=data.username;toast('账号已更新');render()}
else if(f.id==='retention-form'){await api('/settings','POST',data);toast('设置已保存');await load()}
})});
(async()=>{try{const me=await api('/me');state.user=me.username;showApp();await load()}catch{showLogin()}})();
setInterval(()=>{if(!$('#app').classList.contains('hidden'))load(true)},10000);
