/* the neighbourhood web — hand-written WebGL1. points are the catalog, threads
   are what the agent can reach, the vertical anchor is what you authorised, and
   the field is not allowed to cross it. */
(function(){
var cv=document.getElementById("gl"),gl=null;
try{gl=cv.getContext("webgl",{alpha:true,antialias:true,premultipliedAlpha:false})}catch(e){}
if(!gl)return;
function sh(t,s){var x=gl.createShader(t);gl.shaderSource(x,s);gl.compileShader(x);return x}
var pr=gl.createProgram();
gl.attachShader(pr,sh(gl.VERTEX_SHADER,"attribute vec2 p;attribute float a;attribute float s;attribute float c;uniform vec2 r;varying float va;varying float vc;void main(){va=a;vc=c;vec2 q=p/r*2.0-1.0;gl_Position=vec4(q.x,-q.y,0.0,1.0);gl_PointSize=s;}"));
gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,"precision mediump float;varying float va;varying float vc;uniform float o;void main(){float t=va;if(o>0.5){vec2 d=gl_PointCoord-vec2(0.5);float k=length(d);if(k>0.5)discard;t*=smoothstep(0.5,0.28,k);}gl_FragColor=vec4(mix(vec3(1.0),vec3(0.898,0.208,0.169),vc),t);}"));
gl.linkProgram(pr);gl.useProgram(pr);
var LP=gl.getAttribLocation(pr,"p"),LA=gl.getAttribLocation(pr,"a"),LS=gl.getAttribLocation(pr,"s"),LC=gl.getAttribLocation(pr,"c"),LR=gl.getUniformLocation(pr,"r"),LO=gl.getUniformLocation(pr,"o");
var buf=gl.createBuffer();gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
var W=0,H=0,DPR=1;
function size(){DPR=Math.min(2,devicePixelRatio||1);W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR|0;cv.height=H*DPR|0;gl.viewport(0,0,cv.width,cv.height)}
var sd=5;function rnd(){sd|=0;sd=sd+0x6D2B79F5|0;var t=Math.imul(sd^sd>>>15,1|sd);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}
var N=[],n=innerWidth<760?120:230,i;
for(i=0;i<n;i++)N.push({x:rnd(),y:rnd(),vx:(rnd()-.5)*7e-5,vy:(rnd()-.5)*7e-5,r:.9+rnd()*1.9,z:.35+rnd()*.65,ph:rnd()*6.283});
var bx=.72,mood=0,shot=null,amp=0,pha=0,rip=0,red=matchMedia("(prefers-reduced-motion: reduce)").matches,t0=performance.now();
window.GLB=function(f){bx=Math.max(.12,Math.min(.94,f))};
window.GLM=function(m){mood=m};
window.GLF=function(blocked){shot={t:0,b:blocked,s:false};mood=blocked?2:1};
size();addEventListener("resize",size,{passive:true});
function frame(now){requestAnimationFrame(frame);var T=(now-t0)/1000,L=[],P=[],j;
for(i=0;i<N.length;i++){var p=N[i];if(!red){p.x+=p.vx*p.z*60;p.y+=p.vy*p.z*60}
if(p.x<.02||p.x>.98)p.vx*=-1;if(p.y<.02||p.y>.98)p.vy*=-1;if(p.x>bx-.012&&p.vx>0)p.vx*=-1}
var RE=Math.min(W,H)*.115,R2=RE*RE,cell=RE,cols=Math.max(1,Math.ceil(W/cell)),rows=Math.max(1,Math.ceil(H/cell)),g=[],px=new Float32Array(N.length),py=new Float32Array(N.length);
for(i=0;i<N.length;i++){var q=N[i],wo=red?0:Math.sin(T*.55+q.ph)*3.5*q.z;px[i]=q.x*W;py[i]=q.y*H+wo;
var ci=Math.min(cols-1,Math.max(0,px[i]/cell|0)),ri=Math.min(rows-1,Math.max(0,py[i]/cell|0)),k=ri*cols+ci;(g[k]||(g[k]=[])).push(i)}
var tb=mood===2?.34:.46;
for(i=0;i<N.length;i++){var c0=Math.min(cols-1,Math.max(0,px[i]/cell|0)),r0=Math.min(rows-1,Math.max(0,py[i]/cell|0));
for(var dr=0;dr<=1;dr++)for(var dc=-1;dc<=1;dc++){if(dr===0&&dc<0)continue;var cc=c0+dc,rr=r0+dr;
if(cc<0||cc>=cols||rr<0||rr>=rows)continue;var bk=g[rr*cols+cc];if(!bk)continue;
for(var b=0;b<bk.length;b++){j=bk[b];if(j<=i)continue;var dx=px[i]-px[j],dy=py[i]-py[j],d2=dx*dx+dy*dy;
if(d2>R2)continue;var al=Math.sqrt(1-Math.sqrt(d2)/RE)*tb;L.push(px[i],py[i],al,1,0,px[j],py[j],al,1,0)}}}
var ax=bx*W;if(amp>.001){amp*=.955;pha+=.42}else amp=0;
for(var s2=0;s2<46;s2++){var y0=s2/46*H,y1=(s2+1)/46*H,e0=Math.sin(s2/46*Math.PI),e1=Math.sin((s2+1)/46*Math.PI),
o0=Math.sin(s2/46*Math.PI*3+pha)*amp*26*e0,o1=Math.sin((s2+1)/46*Math.PI*3+pha)*amp*26*e1,aa=.55+(mood===2?.4:0)+amp*.5;
L.push(ax+o0,y0,aa,1,1,ax+o1,y1,aa,1,1)}
if(shot){shot.t+=red?.5:.022;var hx=W*.06,hy=H*.5,d3=W*.94,lim=shot.b?(ax-hx)/(d3-hx):1,re=Math.min(shot.t,lim);
for(var k2=0;k2<20;k2++){var f0=k2/20*re,f1=(k2+1)/20*re,sg=red?0:Math.sin(f0*Math.PI)*9*(1-Math.min(1,shot.t)),s1=red?0:Math.sin(f1*Math.PI)*9*(1-Math.min(1,shot.t));
L.push(hx+(d3-hx)*f0,hy+sg,.85,1,1,hx+(d3-hx)*f1,hy+s1,.85,1,1)}
if(shot.b&&shot.t>=lim&&!shot.s){shot.s=true;amp=1;pha=0;rip=1;var th=document.getElementById("thwip");
if(th){th.style.opacity=1;setTimeout(function(){th.style.opacity=0},520)}}
if(!shot.b&&shot.t>=1&&!shot.s){shot.s=true;rip=1}
P.push(hx+(d3-hx)*re,hy,.95,9*DPR,1);if(shot.t>2.6)shot=null}
P.push(W*.06,H*.5,.55,8*DPR,0);P.push(W*.94,H*.5,mood===2?.25:.5,8*DPR,mood===2?0:1);
if(rip>.002){var rr2=(1-rip)*Math.max(W,H)*.9,ra=rip*.22;
for(var m=0;m<64;m++){var t1=m/64*6.283,t2=(m+1)/64*6.283;
L.push(ax+Math.cos(t1)*rr2,H*.5+Math.sin(t1)*rr2,ra,1,1,ax+Math.cos(t2)*rr2,H*.5+Math.sin(t2)*rr2,ra,1,1)}rip*=.955}else rip=0;
for(i=0;i<N.length;i++){var u=N[i],be=u.x>bx;P.push(px[i],py[i],(be?.22:.62)*(.45+u.z*.55),u.r*u.z*2.6*DPR,be?1:0)}
var nL=L.length/5,nP=P.length/5,arr=new Float32Array(L.length+P.length);arr.set(L,0);arr.set(P,L.length);
gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);gl.bindBuffer(gl.ARRAY_BUFFER,buf);gl.bufferData(gl.ARRAY_BUFFER,arr,gl.DYNAMIC_DRAW);
gl.enableVertexAttribArray(LP);gl.vertexAttribPointer(LP,2,gl.FLOAT,false,20,0);
gl.enableVertexAttribArray(LA);gl.vertexAttribPointer(LA,1,gl.FLOAT,false,20,8);
gl.enableVertexAttribArray(LS);gl.vertexAttribPointer(LS,1,gl.FLOAT,false,20,12);
gl.enableVertexAttribArray(LC);gl.vertexAttribPointer(LC,1,gl.FLOAT,false,20,16);
gl.uniform2f(LR,W,H);gl.uniform1f(LO,0);if(nL)gl.drawArrays(gl.LINES,0,nL-nL%2);
gl.uniform1f(LO,1);if(nP)gl.drawArrays(gl.POINTS,nL,nP)}
requestAnimationFrame(frame);})();
