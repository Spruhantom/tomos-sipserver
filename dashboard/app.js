const $=s=>document.querySelector(s);
let csrf='';
let currentUser=null;
let setupPromptChecked=false;
let resetUserId=null;
let callFilters={};
let callOffset=0;
const callPageSize=50;
let webPhoneUa=null;
let webPhoneRegisterer=null;
let webPhoneSession=null;
let webPhoneSip=null;
let webPhoneConfig=null;
let autoPhoneAttempted=false;
const api=async(path,opt={})=>{
  const h={'Content-Type':'application/json',...(opt.headers||{})};
  if(csrf&&opt.method&&opt.method!=='GET')h['X-Tomos-CSRF']=csrf;
  const r=await fetch('/api/v1'+path,{...opt,headers:h,credentials:'same-origin'});
  if(r.status===401){showLogin();throw Error('Session expired. Sign in again.')}
  if(!r.ok){let d={};try{d=await r.json()}catch{}throw Error(d.detail||'Request failed')}
  return r.status===204?null:r.json()
};
function showLogin(){if(typeof webPhoneUa!=='undefined'&&webPhoneUa)disconnectWebPhone().catch(()=>{});$('#login').hidden=false;$('#app').hidden=true;$('#logout').hidden=true;$('#change-password').hidden=true;$('#current-user').textContent='';currentUser=null}
function showApp(){$('#login').hidden=true;$('#app').hidden=false;$('#logout').hidden=false;$('#change-password').hidden=false;$('#current-user').textContent=currentUser?currentUser.username+' · '+currentUser.role:'';$('#app').classList.toggle('role-viewer',currentUser?.role==='viewer');$('#app').classList.toggle('role-operator',currentUser?.role==='operator');$('#user-management').hidden=currentUser?.role!=='admin';}
function note(s){const n=$('#notice');n.textContent=s;n.style.display='block';setTimeout(()=>n.style.display='none',3200)}
async function checkFirstRunSetup(){
  if(setupPromptChecked||currentUser?.role!=='admin')return;
  const state=await api('/setup/status');
  setupPromptChecked=true;
  $('#setup-country').value=state.country_code||'GB';
  if(state.completed)return;
  $('#setup-asterisk-state').textContent=state.asterisk==='online'?'Asterisk is online':'Asterisk needs attention';
  $('#setup-extension-count').textContent=state.extensions_count+' extension'+(state.extensions_count===1?'':'s')+' configured';
  $('#first-run-setup').hidden=false;
}
async function loadTelephonySettings(){
  if(currentUser?.role!=='admin')return;
  const settings=await api('/settings/telephony');
  $('#telephony-country').value=settings.country_code||'GB';
  if($('#setup-country'))$('#setup-country').value=settings.country_code||'GB';
  $('#telephony-settings').hidden=false;
}
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
function updateWebPhoneExtensions(rows){
  const select=$('#webphone-extension');
  if(!select)return;
  const selected=select.value;
  select.replaceChildren();
  const placeholder=document.createElement('option');
  placeholder.value='';placeholder.textContent='Choose a SIP extension';select.append(placeholder);
  for(const extension of rows.filter(item=>item.protocol==='sip'&&item.enabled)){
    const option=document.createElement('option');
    option.value=extension.extension;
    option.textContent=extension.extension+' · '+extension.display_name;
    select.append(option);
  }
  if([...select.options].some(option=>option.value===selected))select.value=selected;
}
async function refresh(){
  const [sum,rows,status,yealinkDevices,grandstreamDevices,polycomDevices,ciscoDevices]=await Promise.all([api('/dashboard/summary'),api('/extensions'),api('/dashboard/status'),api('/devices/yealink'),api('/devices/grandstream'),api('/devices/polycom'),api('/devices/cisco')]);
  updateWebPhoneExtensions(rows);
  if(currentUser?.role==='admin'){await loadUsers();await loadTelephonySettings()}
  await autoProvisionWebPhone();
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
  if(!$('#first-run-setup').hidden)$('#setup-extension-count').textContent=sum.extensions_total+' extension'+(sum.extensions_total===1?'':'s')+' configured';
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
  $('#cisco-devices').replaceChildren();$('#cisco-empty').hidden=ciscoDevices.length>0;
  for(const device of ciscoDevices){
    const tr=document.createElement('tr');
    for(const value of [device.model,device.mac,device.extension,device.display_name,device.server_host]){
      const td=document.createElement('td');td.textContent=value;tr.append(td)
    }
    const profile=document.createElement('td');
    profile.textContent=device.enabled?'Generated · hardware untested':'Disabled';
    tr.append(profile);
    const actions=document.createElement('td');
    const remove=document.createElement('button');remove.textContent='Remove handset';
    remove.onclick=async()=>{
      if(!confirm('Remove Cisco handset '+device.mac+' from SCCP inventory?'))return;
      try{await api('/devices/cisco/'+encodeURIComponent(device.mac),{method:'DELETE'});await refresh();note('Cisco handset removed')}
      catch(error){note(error.message)}
    };
    actions.append(remove);tr.append(actions);$('#cisco-devices').append(tr)
  }
  $('#extensions').replaceChildren();$('#empty').hidden=rows.length>0;
  for(const x of rows){
    const tr=document.createElement('tr');
    for(const v of [x.extension,x.display_name,x.protocol.toUpperCase()]){const td=document.createElement('td');td.textContent=v;tr.append(td)}
    const state=document.createElement('td'),pill=document.createElement('span');
    pill.className='pill '+(x.enabled?'':'off');pill.textContent=x.enabled?'Enabled':'Disabled';state.append(pill);tr.append(state);
    const actions=document.createElement('td');
    if(x.protocol==='sip'){
      const yealink=document.createElement('button');yealink.textContent='Provision Yealink';yealink.disabled=!x.enabled;yealink.onclick=()=>openYealinkSetup(x);actions.append(yealink);
      const grandstream=document.createElement('button');grandstream.textContent='Provision Grandstream';grandstream.disabled=!x.enabled;grandstream.onclick=()=>openGrandstreamSetup(x);actions.append(grandstream);
      const polycom=document.createElement('button');polycom.textContent='Provision Polycom';polycom.disabled=!x.enabled;polycom.onclick=()=>openPolycomSetup(x);actions.append(polycom);
      const configure=document.createElement('button');configure.textContent='SIP settings';configure.disabled=!x.enabled;configure.onclick=async()=>{try{openDeviceSetup(await api('/extensions/'+x.extension+'/provisioning'))}catch(error){note(error.message)}};actions.append(configure);
      const reset=document.createElement('button');reset.textContent='Reset SIP password';reset.onclick=async()=>{try{const d=await api('/extensions/'+x.extension+'/password',{method:'POST'});if(d?.sip_password)secret(d.sip_password);await refresh()}catch(error){note(error.message)}};actions.append(reset);
    }
    for(const [label,path,method] of [[x.enabled?'Disable':'Enable','/extensions/'+x.extension+'/'+(x.enabled?'disable':'enable'),'POST'],['Delete','/extensions/'+x.extension,'DELETE']]){
      const b=document.createElement('button');b.textContent=label;
      b.onclick=async()=>{if(method==='DELETE'&&!confirm('Delete extension '+x.extension+'?'))return;try{await api(path,{method});await refresh();note('Extension updated')}catch(error){note(error.message)}};
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
function webPhoneStatus(message,connected=false){
  $('#webphone-status').textContent=message;
  $('#webphone-status').classList.toggle('connected',connected);
}
function webPhoneControls(registered,active=false){
  $('#webphone-call').disabled=!registered||active;
  $('#webphone-hangup').disabled=!active;
  $('#webphone-mute').disabled=!active;
  $('#webphone-connect').disabled=registered;
  $('#webphone-extension').disabled=registered;
  $('#webphone-disconnect').disabled=!registered&&!webPhoneUa;
}
function attachWebPhoneAudio(session){
  const pc=session.sessionDescriptionHandler?.peerConnection;
  if(!pc)return;
  const audio=$('#webphone-remote-audio');
  const remoteStream=new MediaStream();
  pc.getReceivers().forEach(receiver=>{
    if(receiver.track&&receiver.track.kind==='audio'&&!remoteStream.getTracks().includes(receiver.track))remoteStream.addTrack(receiver.track);
  });
  if(remoteStream.getTracks().length)audio.srcObject=remoteStream;
  pc.ontrack=event=>{
    const stream=event.streams?.[0];
    if(stream)audio.srcObject=stream;
    else if(!remoteStream.getTracks().includes(event.track))remoteStream.addTrack(event.track);
    audio.play().catch(()=>note('Tap the page to allow call audio playback'));
  };
  if(audio.srcObject)audio.play().catch(()=>note('Tap the page to allow call audio playback'));
}
function bindWebPhoneSession(session){
  webPhoneSession=session;
  webPhoneControls(true,true);
  $('#webphone-call-state').textContent='Call connecting…';
  session.stateChange.addListener(state=>{
    if(state===webPhoneSip.SessionState.Established){
      $('#webphone-call-state').textContent='Call connected · '+(session.remoteIdentity?.uri?.user||'internal extension');
      attachWebPhoneAudio(session);
      webPhoneControls(true,true);
    }else if(state===webPhoneSip.SessionState.Terminated){
      webPhoneSession=null;
      $('#webphone-incoming').hidden=true;
      $('#webphone-call-state').textContent='Call ended';
      $('#webphone-remote-audio').srcObject=null;
      $('#webphone-mute').textContent='Mute microphone';
      webPhoneControls(Boolean(webPhoneRegisterer),false);
    }
  });
  attachWebPhoneAudio(session);
}
async function connectWebPhone(){
  const extension=$('#webphone-extension').value;
  if(!extension){note('Choose an enabled SIP extension first');return}
  if(!window.isSecureContext){note('The dashboard must be opened over HTTPS with a trusted certificate');return}
  const button=$('#webphone-connect');button.disabled=true;
  try{
    const config=await api('/extensions/'+encodeURIComponent(extension)+'/webrtc');
    const SIP=await import('https://esm.sh/sip.js@0.21.2?bundle');
    const uri=SIP.UserAgent.makeURI(config.sip_uri);
    if(!uri)throw Error('The PBX returned an invalid SIP address');
    webPhoneSip=SIP;webPhoneConfig=config;
    webPhoneUa=new SIP.UserAgent({
      uri,
      authorizationUsername:config.authorization_username,
      authorizationPassword:config.authorization_password,
      transportOptions:{server:config.websocket_url},
      userAgentString:'TomOS Web Phone',
      delegate:{onInvite:invitation=>{
        if(webPhoneSession){invitation.reject();return}
        bindWebPhoneSession(invitation);
        $('#webphone-caller').textContent='Incoming call from '+(invitation.remoteIdentity?.uri?.user||'unknown');
        $('#webphone-incoming').hidden=false;
        $('#webphone-call-state').textContent='Incoming call';
      }}
    });
    webPhoneUa.transport.stateChange.addListener(state=>webPhoneStatus('WebSocket '+state, state==='Connected'));
    await webPhoneUa.start();
    webPhoneRegisterer=new SIP.Registerer(webPhoneUa);
    await webPhoneRegisterer.register();
    webPhoneStatus('Registered · '+extension,true);
    $('#webphone-call-state').textContent='Ready for calls';
    webPhoneControls(true,false);
    note('Browser phone registered. Allow microphone access when you place or answer a call.');
  }catch(error){
    if(webPhoneUa){try{await webPhoneUa.stop()}catch{}}
    webPhoneUa=null;webPhoneRegisterer=null;webPhoneSip=null;webPhoneConfig=null;
    webPhoneStatus('Connection failed');
    webPhoneControls(false,false);
    note('Browser phone: '+(error.message||'connection failed'));
  }finally{button.disabled=Boolean(webPhoneUa)}
}
async function disconnectWebPhone(){
  try{
    if(webPhoneSession){
      try{
        if(webPhoneSession.state===webPhoneSip.SessionState.Established)await webPhoneSession.bye();
        else if(typeof webPhoneSession.cancel==='function')await webPhoneSession.cancel();
        else if(typeof webPhoneSession.reject==='function')await webPhoneSession.reject();
      }catch{}
    }
    if(webPhoneRegisterer)try{await webPhoneRegisterer.unregister()}catch{}
    if(webPhoneUa)try{await webPhoneUa.stop()}catch{}
  }finally{
    webPhoneSession=null;webPhoneRegisterer=null;webPhoneUa=null;webPhoneSip=null;webPhoneConfig=null;
    $('#webphone-incoming').hidden=true;$('#webphone-remote-audio').srcObject=null;
    $('#webphone-call-state').textContent='Not connected';
    $('#webphone-mute').textContent='Mute microphone';
    webPhoneStatus('Disconnected');webPhoneControls(false,false);
  }
}
async function autoProvisionWebPhone(){
  if(autoPhoneAttempted||!currentUser||currentUser.role==='viewer')return;
  try{
    const assignment=await api('/me/webphone');
    if(!assignment.assigned)return;
    autoPhoneAttempted=true;
    const select=$('#webphone-extension');
    if(![...select.options].some(option=>option.value===assignment.extension)){
      note('Your assigned browser phone extension is currently unavailable');
      return;
    }
    select.value=assignment.extension;
    $('#webphone-call-state').textContent='Provisioning assigned extension '+assignment.extension+'…';
    await connectWebPhone();
  }catch(error){
    webPhoneStatus('Not connected');
    $('#webphone-call-state').textContent='Assigned phone could not connect';
    note('Assigned browser phone: '+error.message);
  }
}
async function placeWebPhoneCall(){
  const target=$('#webphone-dial').value.trim();
  if(!/^[1-9][0-9]{2,5}$/.test(target)){note('Enter a valid internal extension number');return}
  if(!webPhoneUa||!webPhoneRegisterer){note('Connect the browser phone first');return}
  if(webPhoneSession){note('Finish the current call first');return}
  try{
    const domain=webPhoneConfig.sip_uri.split('@').pop();
    const targetUri=webPhoneSip.UserAgent.makeURI('sip:'+target+'@'+domain);
    if(!targetUri)throw Error('Invalid destination extension');
    const inviter=new webPhoneSip.Inviter(webPhoneUa,targetUri,{
      sessionDescriptionHandlerOptions:{constraints:{audio:true,video:false}}
    });
    bindWebPhoneSession(inviter);
    await inviter.invite();
  }catch(error){webPhoneSession=null;webPhoneControls(true,false);note('Could not start call: '+error.message)}
}
$('#webphone-connect').onclick=connectWebPhone;
$('#webphone-disconnect').onclick=disconnectWebPhone;
$('#webphone-call').onclick=placeWebPhoneCall;
$('#webphone-dial').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();placeWebPhoneCall()}});
$('#webphone-answer').onclick=async()=>{
  if(!webPhoneSession||typeof webPhoneSession.accept!=='function')return;
  try{await webPhoneSession.accept({sessionDescriptionHandlerOptions:{constraints:{audio:true,video:false}}});$('#webphone-incoming').hidden=true;$('#webphone-call-state').textContent='Answering…'}
  catch(error){note('Could not answer call: '+error.message)}
};
$('#webphone-reject').onclick=async()=>{
  if(webPhoneSession&&typeof webPhoneSession.reject==='function')try{await webPhoneSession.reject()}catch(error){note(error.message)}
  $('#webphone-incoming').hidden=true;
};
$('#webphone-hangup').onclick=async()=>{
  if(!webPhoneSession)return;
  try{
    if(webPhoneSession.state===webPhoneSip.SessionState.Established)await webPhoneSession.bye();
    else if(typeof webPhoneSession.cancel==='function')await webPhoneSession.cancel();
    else if(typeof webPhoneSession.reject==='function')await webPhoneSession.reject();
  }catch(error){note('Hang-up failed: '+error.message)}
};
$('#webphone-mute').onclick=()=>{
  const pc=webPhoneSession?.sessionDescriptionHandler?.peerConnection;
  if(!pc)return;
  const tracks=pc.getSenders().map(sender=>sender.track).filter(track=>track?.kind==='audio');
  if(!tracks.length)return;
  const muted=tracks[0].enabled;
  tracks.forEach(track=>track.enabled=!muted);
  $('#webphone-mute').textContent=muted?'Unmute microphone':'Mute microphone';
};

$('#login-form').onsubmit=async e=>{e.preventDefault();try{const result=await api('/auth/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});currentUser=result.user;csrf=cookie('tomos_csrf');setupPromptChecked=false;autoPhoneAttempted=false;showApp();await refresh();await checkFirstRunSetup()}catch(err){$('#login-error').textContent=err.message}};
$('#logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'})}finally{await disconnectWebPhone();autoPhoneAttempted=false;csrf='';currentUser=null;$('#change-password').hidden=true;$('#current-user').textContent='';showLogin()}};
$('#change-password').onclick=()=>$('#own-password-dialog').showModal();
$('#own-password-form').onsubmit=async e=>{e.preventDefault();try{await api('/auth/password',{method:'POST',body:JSON.stringify({old_password:$('#own-old-password').value,new_password:$('#own-new-password').value})});$('#own-password-dialog').close();$('#own-password-form').reset();note('Your password has been changed')}catch(err){note(err.message)}};
async function loadUsers(){
  const [users,extensions]=await Promise.all([api('/users'),api('/extensions')]);
  const assignable=extensions.filter(item=>item.protocol==='sip'&&item.enabled);
  const body=$('#users');body.replaceChildren();$('#users-empty').hidden=users.length>0;
  for(const user of users){
    const row=document.createElement("tr");
    for(const value of [user.username,user.role]){const cell=document.createElement("td");cell.textContent=value;row.append(cell)}
    const status=document.createElement("td"),pill=document.createElement("span");pill.className="pill "+(user.enabled?"":"off");pill.textContent=user.enabled?"Enabled":"Disabled";status.append(pill);row.append(status);
    const actions=document.createElement("td");
    const assignment=document.createElement("select");assignment.setAttribute("aria-label","Phone extension for "+user.username);
    const unassigned=document.createElement("option");unassigned.value="";unassigned.textContent="No phone assigned";assignment.append(unassigned);
    for(const extension of assignable){const option=document.createElement("option");option.value=extension.extension;option.textContent=extension.extension+" · "+extension.display_name;assignment.append(option)}
    assignment.value=user.assigned_extension||"";
    assignment.disabled=user.role==='viewer';
    assignment.onchange=async()=>{try{await api("/users/"+user.id,{method:"PATCH",body:JSON.stringify({assigned_extension:assignment.value||null})});await loadUsers();note("User phone assignment updated")}catch(error){note(error.message);assignment.value=user.assigned_extension||""}};
    const role=document.createElement("select");role.setAttribute("aria-label","Role for "+user.username);
    for(const [value,label] of [["admin","Administrator"],["operator","Operator"],["viewer","Read-only viewer"]]){const option=document.createElement("option");option.value=value;option.textContent=label;role.append(option)}
    role.value=user.role;role.onchange=async()=>{try{await api("/users/"+user.id,{method:"PATCH",body:JSON.stringify({role:role.value})});await loadUsers();note("Account role updated")}catch(error){note(error.message);role.value=user.role}};
    const toggle=document.createElement("button");toggle.textContent=user.enabled?"Disable":"Enable";toggle.onclick=async()=>{if(user.id===currentUser.id&&user.enabled){note("You cannot disable your own account");return}if(!confirm((user.enabled?"Disable ":"Enable ")+user.username+"?"))return;try{await api("/users/"+user.id,{method:"PATCH",body:JSON.stringify({enabled:!user.enabled})});await loadUsers();note("Account status updated")}catch(error){note(error.message)}};
    const reset=document.createElement("button");reset.textContent="Reset password";reset.onclick=()=>{resetUserId=user.id;$("#user-password-label").textContent="Set a new password for "+user.username+".";$("#user-reset-password").value="";$("#user-password-dialog").showModal()};
    const remove=document.createElement("button");remove.textContent="Delete";remove.disabled=user.id===currentUser.id;remove.onclick=async()=>{if(!confirm("Permanently delete account "+user.username+"?"))return;try{await api("/users/"+user.id,{method:"DELETE"});await loadUsers();note("Account deleted")}catch(error){note(error.message)}};
    actions.append(assignment,role,toggle,reset,remove);row.append(actions);body.append(row);
  }
  await loadAudit();
}
async function loadAudit(){
  const result=await api('/audit?limit=30');
  const body=$('#audit-events');body.replaceChildren();
  $('#audit-empty').hidden=result.events.length>0;
  for(const event of result.events){
    const row=document.createElement('tr');
    const values=[new Date(event.occurred_at).toISOString().replace('T',' ').replace('Z',' UTC'),event.actor_username,event.action,event.target_username||'—',JSON.stringify(event.details)];
    for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}
    body.append(row);
  }
}
$('#audit-refresh').onclick=()=>loadAudit().catch(error=>note(error.message));
$("#user-create-form").onsubmit=async event=>{event.preventDefault();const button=$("#user-create-form button");button.disabled=true;try{const result=await api("/users",{method:"POST",body:JSON.stringify({username:$("#new-username").value.trim(),password:$("#new-user-password").value,role:$("#new-user-role").value})});$("#user-create-form").reset();await loadUsers();note("Created "+result.role+" account "+result.username)}catch(error){note(error.message)}finally{button.disabled=false}};
$("#user-password-form").onsubmit=async event=>{event.preventDefault();if(!resetUserId)return;const button=$("#user-password-form button.primary");button.disabled=true;try{await api("/users/"+resetUserId+"/password",{method:"POST",body:JSON.stringify({password:$("#user-reset-password").value})});$("#user-password-dialog").close();$("#user-password-form").reset();note("Account password reset")}catch(error){note(error.message)}finally{button.disabled=false}};

$('#new-extension').onclick=()=>{$('#create-form').reset();$('#protocol').value='sip';$('#protocol').onchange();$('#create-dialog').showModal()};
$('#protocol').onchange=()=>{const isSccp=$('#protocol').value==='sccp';$('#sccp-fields').hidden=!isSccp;$('#cisco-mac').required=isSccp;$('#cisco-host').required=isSccp};
$('#create-form').onsubmit=async e=>{
  e.preventDefault();
  const button=$('#create-form button.primary');button.disabled=true;
  const extension=$('#number').value.trim(),protocol=$('#protocol').value;
  try{
    const payload={extension,display_name:$('#display-name').value.trim(),protocol};
    const result=await api('/extensions',{method:'POST',body:JSON.stringify(payload)});
    if(protocol==='sip'){
      $('#create-dialog').close();$('#create-form').reset();secret(result.sip_password);
      note('SIP extension created');
    }else{
      try{
        await api('/devices/cisco',{method:'POST',body:JSON.stringify({
          extension,mac:$('#cisco-mac').value.trim(),model:$('#cisco-model').value,
          server_host:$('#cisco-host').value.trim()
        })});
      }catch(error){
        try{await api('/extensions/'+extension,{method:'DELETE'})}catch(rollback){note('Cisco setup failed and the new extension could not be rolled back: '+rollback.message)}
        throw error;
      }
      $('#create-dialog').close();$('#create-form').reset();
      note('SCCP extension and Cisco profile created. Handset firmware and physical compatibility still need testing.');
    }
    await refresh();
  }catch(err){note(err.message)}
  finally{button.disabled=false}
};
$('#setup-add-phone').onclick=()=>$('#new-extension').click();
$('#setup-later').onclick=()=>{$('#first-run-setup').hidden=true;note('Setup reminder saved for your next sign-in')};
$('#setup-complete').onclick=async()=>{
  const button=$('#setup-complete');button.disabled=true;
  try{
    const result=await api('/setup/complete',{method:'POST',body:JSON.stringify({country_code:$('#setup-country').value||'GB'})});
    $('#telephony-country').value=result.country_code;
    $('#first-run-setup').hidden=true;
    note('First-run setup completed. '+(result.country_code==='GB'?'United Kingdom dial plan applied.':'International/manual profile applied.'));
  }catch(error){note(error.message)}
  finally{button.disabled=false}
};
$('#telephony-settings-form').onsubmit=async event=>{
  event.preventDefault();
  const button=$('#telephony-settings-form button');button.disabled=true;
  try{
    const result=await api('/settings/telephony',{method:'PUT',body:JSON.stringify({country_code:$('#telephony-country').value})});
    $('#setup-country').value=result.country_code;
    note('Country dial-plan profile saved and Asterisk configuration reloaded.');
  }catch(error){note(error.message)}
  finally{button.disabled=false}
};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#copy-secret').onclick=async()=>{await navigator.clipboard.writeText($('#secret-value').textContent);note('Password copied')};
(async()=>{const r=await fetch('/api/v1/auth/session'),d=await r.json();if(d.authenticated){currentUser=d.user;csrf=cookie('tomos_csrf');showApp();try{await refresh();await checkFirstRunSetup()}catch(e){note(e.message)}}else showLogin()})();
setInterval(()=>{if(!$('#app').hidden)refresh().catch(e=>note(e.message))},15000);
