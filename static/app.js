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
    if(!apiBase) throw new Error("الخادم غير مربوط بعد.");
    const response = await fetch(apiBase+path,{...options,headers:{...headers,...options.headers}});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.detail || "حدث خطأ بالاتصال");
    return data;
  }
  function escapeHtml(s){return String(s ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
  async function showActivity(id, target) {
    try {
      const entries = await api("/api/bots/"+id+"/activity");
      target.innerHTML = entries.length ? entries.map(x=>'<div class="activity-row"><b>'+escapeHtml(x.action)+'</b><span>'+escapeHtml(x.details)+'</span><small>'+new Date(x.created_at*1000).toLocaleString('ar-IQ')+'</small></div>').join("") : '<p class="muted">لا يوجد نشاط مسجل.</p>';
    } catch(e) { target.textContent=e.message; }
  }
  async function load() {
    if(!initData){$("botList").innerHTML='<p class="muted">افتح المنصة من زر القائمة داخل البوت حتى يتم التحقق من حسابك.</p>';return;}
    try {
      const [dash,bots]=await Promise.all([api("/api/dashboard"),api("/api/bots")]);
      $("botsCount").textContent=dash.bots_count;
      $("plan").textContent=dash.subscription.plan;
      $("botList").innerHTML=bots.length?bots.map(b=>{
        const liveStatus = b.status === "running" ? '<span class="pill status-running">يعمل الآن</span>' :
          b.status === "stopped" ? '<span class="pill status-stopped">متوقف</span>' :
          '<span class="pill status-unknown">مرتبط • فحص التشغيل غير متاح بعد</span>';
        return '<article class="managed-bot" data-bot="'+b.id+'"><div class="managed-bot-head"><div><b>@'+escapeHtml(b.username)+'</b><small>نوع الربط: توكن خارجي</small></div>'+liveStatus+'</div><p class="hint">حالة الربط تؤكد حفظ البوت في المنصة فقط؛ لن نعرضه كأنه يعمل حتى يصل تأكيد مباشر من عامل التشغيل.</p><label>الاسم داخل المنصة</label><input class="bot-name" maxlength="80" value="'+escapeHtml(b.display_name)+'" placeholder="اسم مخصص للبوت"><label>ملاحظات</label><textarea class="bot-notes" maxlength="1000" rows="2" placeholder="اكتب ملاحظاتك عن هذا البوت">'+escapeHtml(b.notes)+'</textarea><div class="bot-actions"><button data-save="'+b.id+'">💾 حفظ</button><button class="secondary" data-activity="'+b.id+'">📜 سجل النشاط</button><button class="danger" data-delete="'+b.id+'">🗑 إزالة</button></div><div class="activity-list" hidden></div></article>';
      }).join(""):'<p class="muted">ما عندك بوتات مرتبطة حالياً.</p>';
    } catch(e){notice(e.message,true);}
  }
  $("addBotForm").addEventListener("submit",async e=>{
    e.preventDefault(); const token=$("token").value.trim(); if(!token)return;
    try{const result=await api("/api/bots",{method:"POST",body:JSON.stringify({token})});$("token").value="";notice("تم ربط @"+result.username+" بنجاح");await load();}
    catch(err){notice(err.message,true);}
  });
  $("botList").addEventListener("click",async e=>{
    const card=e.target.closest("[data-bot]");
    if(!card)return;
    const id=card.dataset.bot;
    if(e.target.dataset.save){
      try {
        await api("/api/bots/"+id,{method:"PUT",body:JSON.stringify({display_name:card.querySelector(".bot-name").value,notes:card.querySelector(".bot-notes").value})});
        notice("تم حفظ إعدادات البوت بنجاح");
      } catch(err){notice(err.message,true);}
    }
    if(e.target.dataset.activity){
      const panel=card.querySelector(".activity-list");
      panel.hidden=!panel.hidden;
      if(!panel.hidden) await showActivity(id,panel);
    }
    if(e.target.dataset.delete){
      if(!confirm("تأكيد إزالة البوت من حسابك؟ هذا يحذف الربط من Bot Center فقط ولا يحذف البوت من تيليجرام."))return;
      try{await api("/api/bots/"+id,{method:"DELETE"});notice("تمت إزالة البوت من المنصة");await load();}
      catch(err){notice(err.message,true);}
    }
  });
  document.querySelectorAll("[data-method]").forEach(button=>button.addEventListener("click",async()=>{
    const amount=prompt("أدخل قيمة طلب الدفع:");if(!amount)return;
    try{const result=await api("/api/payments",{method:"POST",body:JSON.stringify({method:button.dataset.method,amount})});notice("تم إرسال طلب الدفع رقم "+result.id);}
    catch(err){notice(err.message,true);}
  }));
  load();
})();