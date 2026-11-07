const $=s=>document.querySelector(s);
let csrf='';
let currentUser=null;
let resetUserId=null;
let callFilters={};
let callOffset=0;
const callPageSize=50;
const api=async(path,opt={})=>{
  const h={'Content-Type':'application/json',...(opt.headers||{})};
  if(csrf&&opt.method&&opt.method!=='GET')h['X-Tomos-CSRF']=csrf;
  const r=await fetch('/api/v1'+path,{...opt,headers:h,credentials:'same-origin'});
  if(r.status===401){showLogin();throw Error('Session expired. Sign in again.')}
  if(!r.ok){let d={};try{d=await r.json()}catch{}throw Error(d.detail||'Request failed')}
  return r.status===204?null:r.json()
};
function showLogin(){$('#login').hidden=false;$('#app').hidden=true;$('#logout').hidden=true}
function showApp(){$('#login').hidden=true;$('#app').hidden=false;$('#logout').hidden=false;$('#change-password').hidden=false;$('#current-user').textContent=currentUser?currentUser.username+' · '+currentUser.role:'';$('#app').classList.toggle('role-viewer',currentUser?.role==='viewer');$('#app').classList.toggle('role-operator',currentUser?.role==='operator');$('#user-management').hidden=currentUser?.role!=='admin';}
function note(s){const n=$('#notice');n.textContent=s;n.style.display='block';setTimeout(()=>n.style.display='none',3200)}
function cookie(n){return document.cookie.split('; ').find(x=>x.startsWith(n+'='))?.split('=').slice(1).join('=')||''}
function secret(s){$('#secret-value').textContent=s;$('#secret-dialog').showModal()}
function queryForCalls(){const p=new URLSearchParams({limit:String(callPageSize),offset:String(callOffset)});for(const [k,v] of Object.entries(callFilters))if(v)p.set(k,v);return p.toString()}
async function loadCalls(){
  const history=await api('/calls?'+queryForCalls());
  $('#calls').replaceChildren();
  $('#calls-empty').hidden=history.calls.length>0;
  const first=history.total?history.offset+1:0, last=history.offset+history.calls.length;
  $('#calls-summary').textContent=history.total+' matching record'+(history.total===1?'':'s')+' · showing '+first+'–'+last;
  $('#calls-page').textContent=history.total?`Page ${Math.floor(history.offset/history.limit)+1} of ${Math.ceil(history.total/history.limit)}`:'Page 0 of 0';
  $('#calls-prev').disabled=history.offset===0;
  $('#calls-next').disabled=history.offset+history.limit>=history.total;
  for(const call of history.calls){
    const tr=document.createElement('tr');
    for(const value of [call.started_at,call.caller_id,call.source,call.destination,call.duration_seconds+'s',call.disposition]){
      const td=document.createElement('td');td.textContent=value;tr.append(td)
    }
    $('#calls').append(tr)
  }
}
async function refresh(){
  const [sum,rows,status,yealinkDevices,grandstreamDevices,polycomDevices]=await Promise.all([api('/dashboard/summary'),api('/extensions'),api('/dashboard/status'),api('/devices/yealink'),api('/devices/grandstream'),api('/devices/polycom')]);
  if(currentUser?.role==='admin')await loadUsers();
  $('#api-value').textContent='Online';
  $('#asterisk-value').textContent=status.status==='online'?'Online':'Offline';
  $('#uptime-value').textContent=status.uptime||'Uptime not available';
  $('#registered-value').textContent=status.registered_count;
  $('#status-updated').textContent='Updated '+new Date().toLocaleTimeString();
  $('#registrations').replaceChildren();
  $('#registrations-empty').hidden=status.registered_contacts.length>0;
  for(const contact of status.registered_contacts){
    const tr=document.createElement('tr'),ext=document.createElement('td'),state=document.createElement('td');
    ext.textContent=contact.extension;state.textContent=contact.status;tr.append(ext,state);$('#registrations').append(tr)
  }
  $('#total-value').textContent=sum.extensions_total;
  $('#enabled-value').textContent=sum.extensions_enabled;
  $('#api-status').textContent='API online';
  await loadCalls();
  $('#yealink-devices').replaceChildren();
  $('#yealink-empty').hidden=yealinkDevices.length>0;
  for(const device of yealinkDevices){
    const tr=document.createElement('tr');
    for(const value of [device.model,device.mac,device.extension,device.display_name,device.server_host]){
      const td=document.createElement('td');td.textContent=value;tr.append(td)
    }
    const actions=document.createElement('td');
    const rotate=document.createElement('button');rotate.textContent='Rotate credentials';
    rotate.onclick=async()=>{
      if(!confirm('Rotate provisioning credentials for '+device.mac+'? The old password will stop working.'))return;
      try{
        const result=await api('/devices/yealink/'+device.mac+'/credentials',{method:'POST'});
        $('#yealink-result-url').value='https://'+location.host+'/provisioning/yealink/';
        $('#yealink-result-user').value=result.provisioning_username;
        $('#yealink-result-password').value=result.provisioning_password;
        $('#yealink-result-dialog').showModal();
        note('Provisioning credentials rotated. Save the new password now.');
      }catch(error){note(error.message)}
    };
    const remove=document.createElement('button');remove.textContent='Revoke';
    remove.onclick=async()=>{
      if(!confirm('Revoke provisioning access for '+device.mac+'?'))return;
      try{await api('/devices/yealink/'+device.mac,{method:'DELETE'});await refresh();note('Yealink device revoked')}
      catch(error){note(error.message)}
    };
    actions.append(rotate,remove);tr.append(actions);$('#yealink-devices').append(tr)
  }
  $('#grandstream-devices').replaceChildren();
  $('#grandstream-empty').hidden=grandstreamDevices.length>0;
  for(const device of grandstreamDevices){
    const tr=document.createElement('tr');
    for(const value of [device.model,device.mac,device.extension,device.display_name,device.server_host]){
      const td=document.createElement('td');td.textContent=value;tr.append(td)
    }
    const actions=document.createElement('td');
    const rotate=document.createElement('button');rotate.textContent='Rotate credentials';
    rotate.onclick=async()=>{
      if(!confirm('Rotate provisioning credentials for '+device.mac+'? The old password will stop working.'))return;
      try{
        const result=await api('/devices/grandstream/'+device.mac+'/credentials',{method:'POST'});
        $('#grandstream-result-url').value='https://'+location.host+'/provisioning/grandstream/';
        $('#grandstream-result-user').value=result.provisioning_username;
        $('#grandstream-result-password').value=result.provisioning_password;
        $('#grandstream-result-dialog').showModal();
        note('Grandstream credentials rotated. Save the new password now.');
      }catch(error){note(error.message)}
    };
    const remove=document.createElement('button');remove.textContent='Revoke';
    remove.onclick=async()=>{
      if(!confirm('Revoke Grandstream provisioning access for '+device.mac+'?'))return;
      try{await api('/devices/grandstream/'+device.mac,{method:'DELETE'});await refresh();note('Grandstream device revoked')}
      catch(error){note(error.message)}
    };
    actions.append(rotate,remove);tr.append(actions);$('#grandstream-devices').append(tr)
  }
  $('#polycom-devices').replaceChildren();
  $('#polycom-empty').hidden=polycomDevices.length>0;
  for(const device of polycomDevices){
    const tr=document.createElement('tr');
    for(const value of [device.model,device.mac,device.extension,device.display_name,device.server_host]){
      const td=document.createElement('td');td.textContent=value;tr.append(td)
    }
    const actions=document.createElement('td');
    const rotate=document.createElement('button');rotate.textContent='Rotate credentials';
    rotate.onclick=async()=>{
      if(!confirm('Rotate provisioning credentials for '+device.mac+'? The old password will stop working.'))return;
      try{
        const result=await api('/devices/polycom/'+device.mac+'/credentials',{method:'POST'});
        $('#polycom-result-url').value='https://'+location.host+'/provisioning/polycom/';
        $('#polycom-result-user').value=result.provisioning_username;
        $('#polycom-result-password').value=result.provisioning_password;
        $('#polycom-result-dialog').showModal();
        note('Polycom credentials rotated. Save the new password now.');
      }catch(error){note(error.message)}
    };
    const remove=document.createElement('button');remove.textContent='Revoke';
    remove.onclick=async()=>{
      if(!confirm('Revoke Polycom provisioning access for '+device.mac+'?'))return;
      try{await api('/devices/polycom/'+device.mac,{method:'DELETE'});await refresh();note('Polycom device revoked')}
      catch(error){note(error.message)}
    };
    actions.append(rotate,remove);tr.append(actions);$('#polycom-devices').append(tr)
  }
  $('#extensions').replaceChildren();$('#empty').hidden=rows.length>0;
  for(const x of rows){
    const tr=document.createElement('tr');
    for(const v of [x.extension,x.display_name]){const td=document.createElement('td');td.textContent=v;tr.append(td)}
    const state=document.createElement('td'),pill=document.createElement('span');
    pill.className='pill '+(x.enabled?'':'off');pill.textContent=x.enabled?'Enabled':'Disabled';state.append(pill);tr.append(state);
    const actions=document.createElement('td');
    const yealink=document.createElement('button');yealink.textContent='Provision Yealink';yealink.disabled=!x.enabled;yealink.onclick=()=>openYealinkSetup(x);actions.append(yealink);
    const grandstream=document.createElement('button');grandstream.textContent='Provision Grandstream';grandstream.disabled=!x.enabled;grandstream.onclick=()=>openGrandstreamSetup(x);actions.append(grandstream);
    const polycom=document.createElement('button');polycom.textContent='Provision Polycom';polycom.disabled=!x.enabled;polycom.onclick=()=>openPolycomSetup(x);actions.append(polycom);
    for(const [label,path,method] of [['Configure phone','/extensions/'+x.extension+'/provisioning','GET'],[x.enabled?'Disable':'Enable','/extensions/'+x.extension+'/'+(x.enabled?'disable':'enable'),'POST'],['Reset password','/extensions/'+x.extension+'/password','POST'],['Delete','/extensions/'+x.extension,'DELETE']]){
      const b=document.createElement('button');b.textContent=label;
      b.onclick=async()=>{if(method==='DELETE'&&!confirm('Delete extension '+x.extension+'?'))return;try{const d=await api(path,{method});if(method==='GET'){openDeviceSetup(d);return}if(d?.sip_password)secret(d.sip_password);await refresh();note('Extension updated')}catch(e){note(e.message)}};
      actions.append(b)
    }
    tr.append(actions);$('#extensions').append(tr)
  }
}
let activeDeviceProfile=null;
function renderDeviceProfile(){
  if(!activeDeviceProfile)return;
  const host=$('#device-server').value.trim();
  const p=activeDeviceProfile;
  const lines=[
    'TomOS SIP phone setup',
    'PBX server: '+(host||'[enter your TomOS server LAN address]'),
    'SIP port: '+p.server_port,
    'Transport: '+p.transport,
    'SIP username: '+p.sip_username,
    'Authentication username: '+p.authentication_username,
    'SIP password: use the password shown when this extension was created or last reset',
    'Codecs: '+p.codecs.join(', ').toUpperCase(),
    'Outbound proxy: not configured',
    'Registration: '+(p.enabled?'enabled':'DISABLED — enable this extension in the dashboard first'),
    '',
    'Use the SIP account settings in your phone manufacturer’s account/line page.',
    'This is a generic manual SIP profile, not a vendor-specific auto-provisioning file.'
  ];
  $('#device-profile').value=lines.join('\n');
  $('#device-warning').textContent=p.enabled?'The extension is enabled. Keep the SIP password private.': 'This extension is disabled and cannot register until you enable it.';
}
function openDeviceSetup(profile){
  activeDeviceProfile=profile;
  $('#device-extension-label').textContent=profile.display_name+' · extension '+profile.extension;
  $('#device-user').textContent=profile.sip_username;
  $('#device-auth-user').textContent=profile.authentication_username;
  $('#device-server').value='';
  renderDeviceProfile();
  $('#device-dialog').showModal();
}
$('#device-server').oninput=renderDeviceProfile;
let yealinkExtension=null;
function openYealinkSetup(extension){
  yealinkExtension=extension;
  $('#yealink-extension-label').textContent=extension.display_name+' · extension '+extension.extension;
  $('#yealink-mac').value='';
  $('#yealink-model').value='SIP-T46U';
  $('#yealink-host').value='';
  $('#yealink-dialog').showModal();
}
$('#yealink-form').onsubmit=async e=>{
  e.preventDefault();
  if(!yealinkExtension)return;
  const button=$('#yealink-form button.primary');button.disabled=true;
  try{
    const device=await api('/devices/yealink',{method:'POST',body:JSON.stringify({
      extension:yealinkExtension.extension,mac:$('#yealink-mac').value.trim(),
      model:$('#yealink-model').value.trim(),server_host:$('#yealink-host').value.trim()
    })});
    $('#yealink-dialog').close();
    $('#yealink-result-url').value='https://'+location.host+'/provisioning/yealink/';
    $('#yealink-result-user').value=device.provisioning_username;
    $('#yealink-result-password').value=device.provisioning_password;
    $('#yealink-result-dialog').showModal();
    note('Yealink device registered. Save its provisioning credentials now.');
  }catch(err){note(err.message)}
  finally{button.disabled=false}
};
let grandstreamExtension=null;
function openGrandstreamSetup(extension){
  grandstreamExtension=extension;
  $('#grandstream-extension-label').textContent=extension.display_name+' · extension '+extension.extension;
  $('#grandstream-mac').value='';
  $('#grandstream-model').value='GRP2601';
  $('#grandstream-host').value='';
  $('#grandstream-dialog').showModal();
}
$('#grandstream-form').onsubmit=async e=>{
  e.preventDefault();
  if(!grandstreamExtension)return;
  const button=$('#grandstream-form button.primary');button.disabled=true;
  try{
    const device=await api('/devices/grandstream',{method:'POST',body:JSON.stringify({
      extension:grandstreamExtension.extension,mac:$('#grandstream-mac').value.trim(),
      model:$('#grandstream-model').value,server_host:$('#grandstream-host').value.trim()
    })});
    $('#grandstream-dialog').close();
    $('#grandstream-result-url').value='https://'+location.host+'/provisioning/grandstream/';
    $('#grandstream-result-user').value=device.provisioning_username;
    $('#grandstream-result-password').value=device.provisioning_password;
    $('#grandstream-result-dialog').showModal();
    note('Grandstream device registered. Save its provisioning credentials now.');
    await refresh();
  }catch(err){note(err.message)}
  finally{button.disabled=false}
};
let polycomExtension=null;
function openPolycomSetup(extension){
  polycomExtension=extension;
  $('#polycom-extension-label').textContent=extension.display_name+' · extension '+extension.extension;
  $('#polycom-mac').value='';
  $('#polycom-model').value='VVX450';
  $('#polycom-host').value='';
  $('#polycom-port').value='5060';
  $('#polycom-transport').value='UDP';
  $('#polycom-dialog').showModal();
}
$('#polycom-form').onsubmit=async e=>{
  e.preventDefault();
  if(!polycomExtension)return;
  const button=$('#polycom-form button.primary');button.disabled=true;
  try{
    const device=await api('/devices/polycom',{method:'POST',body:JSON.stringify({
      extension:polycomExtension.extension,mac:$('#polycom-mac').value.trim(),
      model:$('#polycom-model').value,server_host:$('#polycom-host').value.trim(),
      server_port:Number($('#polycom-port').value),transport:$('#polycom-transport').value
    })});
    $('#polycom-dialog').close();
    $('#polycom-result-url').value='https://'+location.host+'/provisioning/polycom/';
    $('#polycom-result-user').value=device.provisioning_username;
    $('#polycom-result-password').value=device.provisioning_password;
    $('#polycom-result-dialog').showModal();
    note('Polycom device registered. Save its provisioning credentials now.');
    await refresh();
  }catch(err){note(err.message)}
  finally{button.disabled=false}
};
$('#copy-polycom-details').onclick=async()=>{
  const details='Provisioning URL: '+$('#polycom-result-url').value+'\nUsername: '+$('#polycom-result-user').value+'\nPassword: '+$('#polycom-result-password').value;
  try{await navigator.clipboard.writeText(details);note('Polycom provisioning details copied')}
  catch(e){note('Could not copy provisioning details')}
};
$('#copy-grandstream-details').onclick=async()=>{
  const details='Provisioning URL: '+$('#grandstream-result-url').value+'\nUsername: '+$('#grandstream-result-user').value+'\nPassword: '+$('#grandstream-result-password').value;
  try{await navigator.clipboard.writeText(details);note('Grandstream provisioning details copied')}
  catch(e){note('Could not copy provisioning details')}
};
$('#copy-yealink-details').onclick=async()=>{
  const details='Provisioning URL: '+$('#yealink-result-url').value+'\nUsername: '+$('#yealink-result-user').value+'\nPassword: '+$('#yealink-result-password').value;
  try{await navigator.clipboard.writeText(details);note('Yealink provisioning details copied')}
  catch(e){note('Could not copy provisioning details')}
};
$('#copy-device-profile').onclick=async()=>{try{await navigator.clipboard.writeText($('#device-profile').value);note('Phone setup notes copied')}catch(e){note('Could not copy setup notes')}};
$('#call-filters').onsubmit=async e=>{
  e.preventDefault();
  callFilters={start_date:$('#call-start').value,end_date:$('#call-end').value,number:$('#call-number').value.trim(),disposition:$('#call-disposition').value};
  callOffset=0;
  try{await loadCalls()}catch(err){note(err.message)}
};
$('#clear-call-filters').onclick=async()=>{
  $('#call-filters').reset();callFilters={};callOffset=0;
  try{await loadCalls()}catch(err){note(err.message)}
};
$('#calls-prev').onclick=async()=>{callOffset=Math.max(0,callOffset-callPageSize);try{await loadCalls()}catch(err){note(err.message)}};
$('#calls-next').onclick=async()=>{callOffset+=callPageSize;try{await loadCalls()}catch(err){note(err.message)}};
$('#export-calls').onclick=async()=>{
  const query=new URLSearchParams();for(const [k,v] of Object.entries(callFilters))if(v)query.set(k,v);
  try{
    const r=await fetch('/api/v1/calls/export.csv'+(query.size?'?'+query.toString():''),{credentials:'same-origin'});
    if(r.status===401){showLogin();throw Error('Session expired. Sign in again.')}
    if(!r.ok){let d={};try{d=await r.json()}catch{}throw Error(d.detail||'CSV export failed')}
    const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download='tomos-call-history.csv';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url)
  }catch(err){note(err.message)}
};
$('#login-form').onsubmit=async e=>{e.preventDefault();try{const result=await api('/auth/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});currentUser=result.user;csrf=cookie('tomos_csrf');showApp();await refresh()}catch(err){$('#login-error').textContent=err.message}};
$('#logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'})}finally{csrf='';currentUser=null;$('#change-password').hidden=true;$('#current-user').textContent='';showLogin()}};
$('#change-password').onclick=()=>$('#own-password-dialog').showModal();
$('#own-password-form').onsubmit=async e=>{e.preventDefault();try{await api('/auth/password',{method:'POST',body:JSON.stringify({old_password:$('#own-old-password').value,new_password:$('#own-new-password').value})});$('#own-password-dialog').close();$('#own-password-form').reset();note('Your password has been changed')}catch(err){note(err.message)}};
async function loadUsers(){
  const users=await api('/users');
  const body=$('#users');body.replaceChildren();$('#users-empty').hidden=users.length>0;
  for(const user of users){
    const row=document.createElement("tr");
    for(const value of [user.username,user.role]){const cell=document.createElement("td");cell.textContent=value;row.append(cell)}
    const status=document.createElement("td"),pill=document.createElement("span");pill.className="pill "+(user.enabled?"":"off");pill.textContent=user.enabled?"Enabled":"Disabled";status.append(pill);row.append(status);
    const actions=document.createElement("td");
    const role=document.createElement("select");role.setAttribute("aria-label","Role for "+user.username);
    for(const [value,label] of [["admin","Administrator"],["operator","Operator"],["viewer","Read-only viewer"]]){const option=document.createElement("option");option.value=value;option.textContent=label;role.append(option)}
    role.value=user.role;role.onchange=async()=>{try{await api("/users/"+user.id,{method:"PATCH",body:JSON.stringify({role:role.value})});await loadUsers();note("Account role updated")}catch(error){note(error.message);role.value=user.role}};
    const toggle=document.createElement("button");toggle.textContent=user.enabled?"Disable":"Enable";toggle.onclick=async()=>{if(user.id===currentUser.id&&user.enabled){note("You cannot disable your own account");return}if(!confirm((user.enabled?"Disable ":"Enable ")+user.username+"?"))return;try{await api("/users/"+user.id,{method:"PATCH",body:JSON.stringify({enabled:!user.enabled})});await loadUsers();note("Account status updated")}catch(error){note(error.message)}};
    const reset=document.createElement("button");reset.textContent="Reset password";reset.onclick=()=>{resetUserId=user.id;$("#user-password-label").textContent="Set a new password for "+user.username+".";$("#user-reset-password").value="";$("#user-password-dialog").showModal()};
    const remove=document.createElement("button");remove.textContent="Delete";remove.disabled=user.id===currentUser.id;remove.onclick=async()=>{if(!confirm("Permanently delete account "+user.username+"?"))return;try{await api("/users/"+user.id,{method:"DELETE"});await loadUsers();note("Account deleted")}catch(error){note(error.message)}};
    actions.append(role,toggle,reset,remove);row.append(actions);body.append(row);
  }
}
$("#user-create-form").onsubmit=async event=>{event.preventDefault();const button=$("#user-create-form button");button.disabled=true;try{const result=await api("/users",{method:"POST",body:JSON.stringify({username:$("#new-username").value.trim(),password:$("#new-user-password").value,role:$("#new-user-role").value})});$("#user-create-form").reset();await loadUsers();note("Created "+result.role+" account "+result.username)}catch(error){note(error.message)}finally{button.disabled=false}};
$("#user-password-form").onsubmit=async event=>{event.preventDefault();if(!resetUserId)return;const button=$("#user-password-form button.primary");button.disabled=true;try{await api("/users/"+resetUserId+"/password",{method:"POST",body:JSON.stringify({password:$("#user-reset-password").value})});$("#user-password-dialog").close();$("#user-password-form").reset();note("Account password reset")}catch(error){note(error.message)}finally{button.disabled=false}};

$('#new-extension').onclick=()=>$('#create-dialog').showModal();
$('#create-form').onsubmit=async e=>{
  e.preventDefault();
  try{const d=await api('/extensions',{method:'POST',body:JSON.stringify({extension:$('#number').value,display_name:$('#display-name').value})});$('#create-dialog').close();$('#create-form').reset();secret(d.sip_password);await refresh()}
  catch(err){note(err.message)}
};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#copy-secret').onclick=async()=>{await navigator.clipboard.writeText($('#secret-value').textContent);note('Password copied')};
(async()=>{const r=await fetch('/api/v1/auth/session'),d=await r.json();if(d.authenticated){currentUser=d.user;csrf=cookie('tomos_csrf');showApp();try{await refresh()}catch(e){note(e.message)}}else showLogin()})();
setInterval(()=>{if(!$('#app').hidden)refresh().catch(e=>note(e.message))},15000);
