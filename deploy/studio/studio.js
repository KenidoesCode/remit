/* The studio. Every value rendered here came out of the Python engine in this
   repository; this file only lays it out. Nothing is computed in the browser
   except which pre-computed decision to show you. */
(function(){
var D=window.D,CL=D.CL,cur=0,red=matchMedia("(prefers-reduced-motion: reduce)").matches;
var $=function(s){return document.querySelector(s)};
var R=function(p){return"₹"+(p/100).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2})};
var R0=function(p){return"₹"+Math.round(p/100).toLocaleString("en-IN")};
var esc=function(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})};
function clauses(mask){return'<div class="cls">'+CL.map(function(id,i){
  var ok=mask.charAt(i)==="1";
  return'<span class="cl'+(ok?"":" x")+'"><b>'+(ok?"✓":"✕")+"</b>"+id+"</span>"}).join("")+"</div>"}
function dims(dr){var h='<div class="dims"><span class="dm">drift '+dr.s+"</span>";
  for(var k in dr.d)h+='<span class="dm h">'+esc(k)+" "+dr.d[k]+"</span>";
  if(dr.ne&&dr.ne.length)h+='<span class="dm">not evaluable: '+dr.ne.join(", ")+"</span>";
  return h+"</div>"}
function panel(j){
  var a=j.au;
  return'<div class="panel"><div class="vr"><span class="bg '+a.v+'">'+a.v.replace("_"," ")+"</span>"+
  '<span class="mn">'+esc(j.cat)+" × "+j.qty+" · "+esc(j.obj)+" · parse confidence "+j.conf+"</span></div>"+
  '<p class="said">'+esc(a.r)+"</p>"+
  (a.c?'<p class="ml">↳ '+esc(a.c)+"</p>":"")+
  clauses(a.p)+dims(j.dr)+
  (j.dr.why&&j.dr.why.length?'<p class="ml">'+j.dr.why.map(esc).join("<br>")+"</p>":"")+
  '<p class="ml">risk '+j.rk.l+" · expected loss "+R(j.rk.e)+" vs cost of asking "+R(j.rk.f)+
  " · p(wrong) "+j.rk.p+" · decided in "+j.ms+"ms · payment "+j.pay+"</p></div>"}
function render(i){
  cur=i;var j=D.j[i];
  [].forEach.call(document.querySelectorAll("#ch button"),function(b,k){b.dataset.on=k===i?"1":"0"});
  var h=panel(j);
  if(j.sel)h+='<div style="margin-top:26px;border-top:1px solid var(--ln)">'+
    '<article class="row pk"><span class="t">the agent’s pick</span><div class="n">'+esc(j.sel.n)+
    '</div><div class="p">'+R0(j.sel.p)+"<s>"+R0(j.sel.m)+'</s></div><div class="sb"><span>'+j.sel.r+
    "★ ("+j.sel.rv+")</span><span>"+j.sel.d+"d delivery</span></div></article>"+
    "</div>";
  if(j.off&&j.off.length){h+='<p class="ml" style="margin-top:30px;letter-spacing:.2em;text-transform:uppercase;color:var(--sig)">what the merchant would like to add</p>';
    h+=j.off.map(function(o){return'<div class="of'+(o.h?" nd":"")+'" style="margin-top:10px"><div class="r1"><strong>'+
      esc(o.n)+'</strong><span class="dl">+'+R(o.x)+'</span></div><div class="rs">'+esc(o.r)+
      '</div><div class="tl">'+esc(o.k)+" · relevance "+o.v+" · "+
      (o.h?"would cross the line — needs you":"fits inside the line")+(o.a?" · ADDED":"")+"</div></div>"}).join("")}
  h+='<div class="tw"><table><tr><td>subtotal</td><td class="n">'+R(j.tot.s)+
    '</td></tr><tr><td>shipping</td><td class="n">'+R(j.tot.sh)+
    '</td></tr><tr><td><strong>total</strong></td><td class="n"><strong>'+R(j.tot.t)+
    '</strong></td></tr><tr><td style="color:var(--i4)">merchant margin</td><td class="n" style="color:var(--i4)">'+
    R(j.tot.m)+"</td></tr></table></div>";
  $("#out").innerHTML=h;
  if(window.GLF)GLF(j.au.v!=="AUTO");
  boundary(Math.max(0,Math.min(j.sw.length-1,Math.round(j.ceil/j.top*j.sw.length)-1)));
}
function boundary(idx){
  var j=D.j[cur],sw=j.sw,s=sw[idx],ceil=s[0],v=s[1],dv=s[2],us=s[3],tot=j.tot.t,top=j.top;
  var fp=Math.min(100,tot/top*100),lp=Math.min(100,ceil/top*100),op=Math.max(0,fp-lp),room=ceil-tot;
  $("#bd").innerHTML='<div class="bnd"><div class="tp"><strong>the property line</strong><span'+
    (room<0?' style="color:var(--sig)"':"")+">"+(room<0?"over the line":room<tot*.08?"on the line":"inside the line")+
    '</span></div><div class="trk"><div class="fl" style="width:'+Math.min(fp,lp)+'%"></div>'+
    '<div class="ov" style="left:'+lp+"%;width:"+op+'%"></div><div class="ln" style="left:'+lp+
    '%"><s></s><u>authorised</u></div></div>'+
    '<input id="sl" type="range" min="0" max="'+(sw.length-1)+'" value="'+idx+'">'+
    '<div class="figs"><div class="fig"><span class="k">authorised</span><span class="v">'+R(ceil)+
    '</span></div><div class="fig"><span class="k">about to charge</span><span class="v'+(room<0?" o":"")+'">'+R(tot)+
    '</span></div><div class="fig"><span class="k">'+(room>=0?"room left":"over by")+'</span><span class="v'+
    (room<0?" o":"")+'">'+R(Math.abs(room))+'</span></div><div class="fig"><span class="k">verdict</span><span class="v'+
    (v==="AUTO"?"":" o")+'">'+v.replace("_"," ")+'</span></div><div class="fig"><span class="k">re-decided in</span><span class="v">'+
    us+'µs</span></div></div><p class="ml">drift '+dv+
    " · same basket, different permission · no model call, no payment, no writes</p></div>";
  $("#sl").addEventListener("input",function(e){boundary(+e.target.value)});
  if(window.GLB)GLB(.30+lp/100*.62);
  if(window.GLM)GLM(v==="AUTO"?1:2);
  var u=$("#us");if(u)u.textContent="~"+us+"µs";
}
function levers(){
  $("#lv").innerHTML=D.lev.map(function(l,i){return'<button data-i="'+i+'"><div class="t">'+esc(l.t)+
    '</div><div class="d">'+esc(l.d)+'</div><div class="c" data-c></div></button>'}).join("");
  $("#lv").addEventListener("click",function(e){
    var b=e.target.closest("button");if(!b)return;
    var l=D.lev[+b.dataset.i];
    b.dataset.f="1";
    b.querySelector("[data-c]").textContent=l.v+" · "+(l.f&&l.f.length?l.f.join(", "):"caught");
    $("#lo").innerHTML='<div class="panel"><div class="vr"><span class="bg '+l.v+'">'+l.v.replace("_"," ")+
      '</span><span class="mn">'+esc(l.t)+" — "+esc(l.d)+'</span></div><p class="said">'+esc(l.r)+"</p>"+
      clauses(l.p)+'<p class="ml">drift '+l.dr+" · basket "+R(l.tt)+" · payment "+l.pay+"</p></div>";
    if(window.GLF)GLF(true);
    $("#lo").scrollIntoView({behavior:red?"auto":"smooth",block:"nearest"});
  });
}
/* the reveal */
function reveal(){
  var seq=[[".eb",0,.25],["h1 .i",1,.35],[".sub",0,.95],[".cta-row",0,1.15],[".st",1,1.4],[".tg",0,1.6]];
  seq.forEach(function(s){[].forEach.call(document.querySelectorAll(s[0]),function(e,i){
    e.style.opacity=0;e.style.transform="translateY(26px)";e.style.filter="blur(10px)";
    e.style.transition="opacity .9s cubic-bezier(.16,1,.3,1),transform 1.1s cubic-bezier(.16,1,.3,1),filter .9s";
    setTimeout(function(){e.style.opacity=1;e.style.transform="none";e.style.filter="none"},red?0:(s[2]+(s[1]?i*.16:0))*1000)})});
  var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){
    e.target.style.opacity=1;e.target.style.transform="none";e.target.style.filter="none";io.unobserve(e.target)}})},{rootMargin:"0px 0px -12% 0px"});
  [].forEach.call(document.querySelectorAll(".hd,.grid,.tw,ol li,.plate p,.plate cite"),function(e){
    if(red)return;e.style.opacity=0;e.style.transform="translateY(24px)";e.style.filter="blur(6px)";
    e.style.transition="opacity .8s cubic-bezier(.16,1,.3,1),transform .9s cubic-bezier(.16,1,.3,1),filter .8s";io.observe(e)});
  addEventListener("scroll",function(){$("#nav").dataset.s=scrollY>40?"1":"0"},{passive:true});
}
function boot(){
  $("#hb").textContent=D.meta.products+" products · policy "+D.meta.policy+" · "+D.meta.cal+" · docket intact";
  $("#ch").innerHTML=D.j.map(function(j,i){return'<button data-i="'+i+'">'+esc(j.u)+"</button>"}).join("");
  $("#ch").addEventListener("click",function(e){var b=e.target.closest("button");if(b)render(+b.dataset.i)});
  levers();render(0);reveal();
  $("#go").addEventListener("click",function(){document.getElementById("s").scrollIntoView({behavior:red?"auto":"smooth"})});
  console.log("%cREMIT%c  ·  built by techuilaguy (pranauv shrinaath s.)\n\n  the model may interpret, recommend and propose.\n  it may not compute an amount, and it may never authorise money.\n\n  built mostly at night. one more can and i'd rewrite the whole thing.\n",
    "font:700 22px/1 ui-monospace,monospace;color:#E5352B","font:12px/1.6 ui-monospace,monospace;color:#A9A6A2");
}
if(document.readyState!=="loading")boot();else addEventListener("DOMContentLoaded",boot);
})();
