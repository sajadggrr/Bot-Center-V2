(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready(); tg?.expand();
  const initData = tg?.initData || "";
  const apiBase = window.BOT_CENTER_API || "";
  const headers = {"Content-Type":"application/json","X-Telegram-Init-Data":initData};
  const $ = (id) => document.getElementById(id);
  const notice = (message, error=false) => { $("notice").textContent=message; $("notice").style.color=error?"#ff8c9b":"#43d6a0"; };
  $("connection").textContent = initData ? "تيليجرام متصل" : "افتح من تيليجرام";
  async function api(path, options={}) {
    if(!apiBase) throw new Error("الخادم غير مربوط بعد. نحتاج رابط Backend بعد نشره.");
    const response = await fetch(apiBase+path,{...options,headers:{...headers,...options.headers}});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.detail || "حدث خطأ بالاتصال");
    return data;
  }
  async function load() {
    if(!initData){$("botList").innerHTML='<p class="muted">افتح المنصة من زر القائمة داخل البوت حتى يتم التحقق من حسابك.</p>';return;}
    try {
      const [dash,bots]=await Promise.all([api("/api/dashboard"),api("/api/bots")]);
      $("botsCount").textContent=dash.bots_count;
      $("plan").textContent=dash.subscription.plan;
      $("botList").innerHTML=bots.length?bots.map(b=>'<div class="bot-item"><a target="_blank" rel="noopener">@'+escapeHtml(b.username)+'</a><button data-delete="'+b.id+'">إزالة</button></div>').join(""):'<p class="muted">ما عندك بوتات مرتبطة حالياً.</p>';
    } catch(e){notice(e.message,true);}
  }
  function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
  $("addBotForm").addEventListener("submit",async e=>{
    e.preventDefault(); const token=$("token").value.trim(); if(!token)return;
    try{const result=await api("/api/bots",{method:"POST",body:JSON.stringify({token})});$("token").value="";notice("تم ربط @"+result.username+" بنجاح");await load();}
    catch(err){notice(err.message,true);}
  });
  $("botList").addEventListener("click",async e=>{
    const id=e.target.dataset.delete;if(!id)return;
    if(!confirm("تأكيد إزالة البوت من حسابك؟"))return;
    try{await api("/api/bots/"+id,{method:"DELETE"});notice("تمت إزالة البوت");await load();}catch(err){notice(err.message,true);}
  });
  document.querySelectorAll("[data-method]").forEach(button=>button.addEventListener("click",async()=>{
    const amount=prompt("أدخل قيمة طلب الدفع:");if(!amount)return;
    try{const result=await api("/api/payments",{method:"POST",body:JSON.stringify({method:button.dataset.method,amount})});notice("تم إرسال طلب الدفع رقم "+result.id);}
    catch(err){notice(err.message,true);}
  }));
  load();
})();