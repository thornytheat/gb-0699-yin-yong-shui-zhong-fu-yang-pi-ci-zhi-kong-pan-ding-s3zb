const corrNotice=document.getElementById('corr-notice');

async function postCorrection(url,payload){
  const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();
  if(!response.ok)throw Error(data.message||'更正操作失败');
  return data;
}

document.querySelectorAll('[data-correction]').forEach(button=>{
  button.onclick=async()=>{
    const holder=button.closest('[data-correction-run]');
    const id=holder?holder.dataset.correctionRun:button.dataset.correctionRun;
    button.disabled=true;
    try{
      const action=button.dataset.correction;
      if(action==='create'){
        corrNotice.textContent='正在生成污染更正单…';
        await postCorrection('/api/8/correction',{id});
      }else if(action==='retry'){
        corrNotice.textContent='从最后确认批次续跑…';
        await postCorrection('/api/8/retry',{id});
      }else if(action==='retry-pass'){
        corrNotice.textContent='注入空白复核 PASS 后续跑…';
        await postCorrection('/api/8/retry',{id,override:{status:'PASS'}});
      }
      location.reload();
    }catch(err){
      corrNotice.textContent=err.message;
      button.disabled=false;
    }
  };
});
