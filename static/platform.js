'use strict';
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const plans=[['starter','Essencial · 10 acessos'],['pro','Profissional · 50 acessos'],['business','Empresa · 500 acessos']];
let user=null,companies=[];

async function api(path,body){
  const response=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw Error(result.error||'Não foi possível concluir.');
  return result;
}
async function getJson(path){
  const response=await fetch('/api/'+path,{cache:'no-store'}),result=await response.json();
  if(!response.ok)throw Error(result.error||'Não foi possível carregar os dados.');
  return result;
}
function toast(message){const element=$('#toast');element.textContent=message;element.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>element.style.display='none',5000)}
function field(name,label,type='text',extra=''){return `<label>${label}<input name="${name}" type="${type}" ${extra}></label>`}
function renderLogin(message=''){
  $('#platform-user').textContent='';
  $('#platform-logout').hidden=true;
  $('#platform-app').innerHTML=`<section class="auth-card platform-auth"><div class="brand auth-brand"><span class="mark">S</span><span>SEGURA<small>PORTAL COMERCIAL</small></span></div><p class="eyebrow">ACESSO DO VENDEDOR</p><h1>Gerencie as empresas clientes</h1><p>Este portal controla cadastros, planos e situação das empresas. Os registros de serviço e estoque ficam nos sistemas próprios das empresas.</p>${message?`<p class="helper">${esc(message)}</p>`:''}<form id="platform-login-form"><div class="fields"><label>Código da empresa (se necessário)<input name="company" value="${esc(localStorage.getItem('segura-company')||'')}" autocomplete="organization" placeholder="Ex.: minha-empresa"></label>${field('username','Usuário','text','required autocomplete="username" maxlength="80"')}${field('password','Senha','password','required autocomplete="current-password"')}</div><button type="submit">Entrar no portal</button><p class="helper">O primeiro administrador criado neste sistema também recebe o acesso do vendedor.</p></form></section>`;
  $('#platform-login-form').onsubmit=async event=>{
    event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;
    try{const form=Object.fromEntries(new FormData(event.currentTarget));const result=await api('login',form);user=result.user;if(user.organization_slug)localStorage.setItem('segura-company',user.organization_slug);await showPortal()}
    catch(error){toast(error.message)}finally{button.disabled=false}
  };
}
function showForbidden(){
  $('#platform-user').textContent='';$('#platform-logout').hidden=true;
  $('#platform-app').innerHTML='<section class="platform-notice"><p class="eyebrow">ACESSO RESTRITO</p><h1>Este portal é exclusivo do vendedor</h1><p>A conta conectada não possui permissão para administrar empresas. Entre com a conta de plataforma.</p></section>';
}
function statusName(status){return status==='active'?'Ativa':status==='trial'?'Avaliação':'Suspensa'}
function formattedDate(value){return value?new Date(value).toLocaleDateString('pt-BR'):'—'}
function renderCompanies(list){
  const rows=list.map(company=>`<tr data-company-row="${esc(company.id)}">
    <td><input class="company-name" data-name value="${esc(company.name)}" aria-label="Nome da empresa"><small>Código: ${esc(company.slug)}</small><input class="company-doc" data-document value="${esc(company.document||'')}" aria-label="CNPJ ou documento" placeholder="CNPJ / documento"></td>
    <td><select data-plan aria-label="Plano da empresa">${plans.map(([value,label])=>`<option value="${value}" ${value===company.plan?'selected':''}>${label.split(' · ')[0]}</option>`).join('')}</select></td>
    <td>${Number(company.user_count)||0} / ${Number(company.user_limit)||0}</td>
    <td><span class="badge ${company.status==='suspended'?'late':company.status==='trial'?'soon':''}">${statusName(company.status)}</span>${company.status==='trial'&&company.trial_ends_at?`<br><small>Até ${formattedDate(company.trial_ends_at)}</small>`:''}</td>
    <td><select data-status aria-label="Situação da empresa"><option value="trial" ${company.status==='trial'?'selected':''}>Avaliação</option><option value="active" ${company.status==='active'?'selected':''}>Ativa</option><option value="suspended" ${company.status==='suspended'?'selected':''}>Suspensa</option></select><button type="button" data-save="${esc(company.id)}" class="secondary small">Salvar</button></td>
  </tr>`).join('');
  $('#company-table').innerHTML=rows?`<div class="table-wrap"><table class="company-table"><thead><tr><th>Empresa / código</th><th>Plano</th><th>Acessos</th><th>Situação</th><th>Atualizar</th></tr></thead><tbody>${rows}</tbody></table></div>`:'<div class="empty">Nenhuma empresa cadastrada.</div>';
  $('#company-table').querySelectorAll('[data-save]').forEach(button=>button.onclick=async()=>{
    const row=button.closest('[data-company-row]');button.disabled=true;
    try{
      await api('platform/companies/update',{id:button.dataset.save,name:row.querySelector('[data-name]').value.trim(),document:row.querySelector('[data-document]').value.trim(),plan:row.querySelector('[data-plan]').value,status:row.querySelector('[data-status]').value});
      await loadCompanies();toast('Empresa atualizada.');
    }catch(error){toast(error.message)}finally{button.disabled=false}
  });
}
function csvExport(){
  const values=[['Empresa','Código','Documento','Plano','Situação','Acessos','Limite','Avaliação até'],...companies.map(company=>[company.name,company.slug,company.document||'',company.plan,statusName(company.status),company.user_count,company.user_limit,company.trial_ends_at||''])];
  const content='\ufeff'+values.map(row=>row.map(value=>'"'+String(value??'').replaceAll('"','""')+'"').join(';')).join('\r\n');
  const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='empresas-segura-gestao.csv';link.click();URL.revokeObjectURL(url);
}
async function loadCompanies(search=''){
  const result=await getJson('platform/companies');companies=result.companies||[];
  const needle=search.trim().toLocaleLowerCase('pt-BR');
  const filtered=companies.filter(company=>[company.name,company.slug,company.document].some(value=>String(value||'').toLocaleLowerCase('pt-BR').includes(needle)));
  $('#stat-total').textContent=companies.length;
  $('#stat-active').textContent=companies.filter(company=>company.status==='active').length;
  $('#stat-trial').textContent=companies.filter(company=>company.status==='trial').length;
  $('#stat-suspended').textContent=companies.filter(company=>company.status==='suspended').length;
  renderCompanies(filtered);
}
async function showPortal(){
  if(!user?.is_platform_admin)return showForbidden();
  $('#platform-user').textContent=`${user.name} · Vendedor`;
  $('#platform-logout').hidden=false;
  $('#platform-app').innerHTML=`<div class="page-head"><div><h1>Painel comercial</h1><p>Controle as empresas clientes e os acessos ao produto. Os dados operacionais de cada empresa não são exibidos aqui.</p></div><button id="export-companies" class="secondary">Exportar empresas CSV</button></div>
    <div class="grid stats platform-stats"><section><span class="eyebrow">Empresas cadastradas</span><div class="stat" id="stat-total">0</div></section><section><span class="eyebrow">Ativas</span><div class="stat" id="stat-active">0</div></section><section><span class="eyebrow">Em avaliação</span><div class="stat" id="stat-trial">0</div></section><section><span class="eyebrow">Suspensas</span><div class="stat" id="stat-suspended">0</div></section></div>
    <section><h2>Cadastrar empresa cliente</h2><form id="company-create-form" class="platform-company-form"><div class="fields">${field('name','Razão social / nome da empresa','text','required maxlength="180"')}${field('slug','Código para entrar (opcional)','text','maxlength="80" placeholder="Gerado pelo nome se ficar vazio"')}${field('document','CNPJ / documento','text','maxlength="40"')}<label>Plano<select name="plan">${plans.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label>${field('admin_name','Nome do primeiro administrador','text','required maxlength="150"')}${field('admin_username','Usuário do administrador','text','required maxlength="80"')}${field('admin_password','Senha inicial','password','required minlength="12" maxlength="1024" autocomplete="new-password"')}<p class="helper wide">A empresa começa com 14 dias de avaliação. Anote o código e entregue o usuário e a senha por um canal seguro.</p></div><button type="submit">Cadastrar empresa</button><p id="company-create-result" class="platform-result" role="status"></p></form></section>
    <section><div class="row split"><div><h2>Empresas clientes</h2><p>Planos e situação são controlados manualmente nesta versão.</p></div><input id="company-search" class="platform-search" type="search" placeholder="Buscar por empresa, código ou documento" aria-label="Buscar empresa"></div><div id="company-table" class="loading">Carregando empresas…</div></section>`;
  try{await loadCompanies()}catch(error){$('#company-table').textContent=error.message;return}
  $('#company-search').oninput=event=>loadCompanies(event.target.value).catch(error=>toast(error.message));
  $('#export-companies').onclick=csvExport;
  $('#company-create-form').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget,button=form.querySelector('button[type="submit"]');button.disabled=true;$('#company-create-result').textContent='';
    try{const result=await api('platform/companies',Object.fromEntries(new FormData(form)));$('#company-create-result').innerHTML=`Empresa criada. Código: <strong>${esc(result.company.slug)}</strong> · Usuário administrador: <strong>${esc(result.admin.username)}</strong>. A senha inicial foi definida no formulário.`;form.reset();await loadCompanies($('#company-search').value);toast('Empresa cadastrada.')}
    catch(error){toast(error.message)}finally{button.disabled=false}
  };
}
async function start(){
  try{
    const session=await getJson('session');
    user=session.user;
    if(user)return showPortal();
    if(session.setupRequired)renderLogin('Antes, crie a primeira empresa e o administrador na página inicial do produto.');
    else renderLogin();
  }catch(error){$('#platform-app').innerHTML=`<section class="platform-notice"><h1>Servidor indisponível</h1><p>${esc(error.message)}</p></section>`}
}
$('#platform-logout').onclick=async()=>{try{await api('logout',{});user=null;renderLogin('Sessão encerrada.')}catch(error){toast(error.message)}};
start();
