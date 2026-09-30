# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.0", "mini-racer==0.14.1"]
#
# [tool.orcaslicer.plugin]
# name = "Support Fins"
# description = "Adds printfins.com breakaway support fins under overhangs at slice time. Parts with Orca supports turned on are left alone."
# author = "Matthew Trahan (engine), J (Orca plugin)"
# version = "0.1.0"
# ///
"""Support Fins for OrcaSlicer -- fins added at slice time.

WHAT IT DOES
  When Orca slices a part, this plugin runs the printfins.com fin engine on the
  part as it sits on the plate, then adds the fin (and bed pad) cross-sections to
  every layer as ordinary printed geometry. Orca then builds walls, infill and
  G-code for the fins like any other part of the model. Nothing is exported or
  re-imported, and the fins follow the part: rotate it, re-slice, new fins.

WHY AT SLICE TIME
  Orca's model API is read-only for plugins (no add-object / import-mesh call), so
  fins can't be dropped onto the plate. The slicing pipeline IS writable: at
  Step.posSlice a plugin may edit each layer's slice polygons, and Orca's own
  samples ("Inset Every Slice", "Twistify") show the edits flowing through to
  perimeters, infill and G-code. A fin is geometry; after slicing, geometry is
  just per-layer polygons. So we slice the fin mesh ourselves and hand Orca the
  polygons it would have got from a finned STL.

WHICH PARTS GET FINS
  Every part on the plate whose "Enable support" is OFF. Turn Orca's supports on
  for a part and this plugin leaves it alone -- so the choice between fins and
  Orca supports is the existing per-object support toggle. `apply_to = "all"` in
  the plugin config ignores the toggle.

THE ENGINE
  The fin geometry is Matthew Trahan's printfins.com engine (MIT), unmodified,
  bundled from web/*.js with esbuild and run in an embedded V8 (mini-racer) --
  so the fins match the website instead of being a re-implementation that drifts.

LIMITS
  * The fins appear in the sliced Preview, not in the Prepare 3D view.
  * This targets the current Orca Python plugin system. Older 2.4-era forks may
    have only classic post-processing scripts and will not load this file.
  * One fin set per print object (all instances of an object share it).
"""
import atexit
import base64
import json
import os
import time

import orca

# ---------------------------------------------------------------------------------
# Dependencies (numpy, mini-racer)
# ---------------------------------------------------------------------------------
# Orca installs these from the PEP 723 header above on first load, then runs the
# plugin. They MUST be imported here, at module-load time, and NEVER lazily from a
# capability. Orca's plugin audit hook is OFF while the module loads but ON during
# any capability (slice/script) call, and it refuses to open any file whose path
# contains "conf", "cert" or "secret" (OrcaSlicer issue #15944). `import numpy`
# eagerly reads numpy/__config__.py and numpy/_core/_ufunc_config.py -- both hold
# "conf" -- so importing numpy from inside a capability is blocked with
#   PermissionError: Plugin attempted an audited operation without permission
# Importing everything now, during the audit-free load, sidesteps that entirely.
#
# _DEPS_ERROR is None when the dependencies are ready; otherwise it is a
# user-facing message the capabilities surface instead of crashing the slice.
np = None
_DEPS_ERROR = None


def _import_deps():
    """Import numpy (and warm its 'conf'-named submodules) during module load."""
    global np, _DEPS_ERROR
    try:
        import numpy
        # Pull the files numpy opens into sys.modules NOW, while the audit hook is
        # off, so none of them are opened again from inside a capability (Orca
        # blocks any path containing "conf" -- see the note above).
        import numpy.__config__            # noqa: F401  ("conf" in the path)
        import numpy._core._ufunc_config   # noqa: F401  ("conf" in the path)
        import numpy.linalg                # noqa: F401
        np = numpy
        _DEPS_ERROR = None
    except ImportError:
        # Deps not installed yet. Orca installs them from the PEP 723 header on
        # first load; they only become importable after Orca is restarted.
        _DEPS_ERROR = ("Support Fins is finishing its first-time dependency "
                       "install (numpy, mini-racer). Fully quit and reopen "
                       "OrcaSlicer, then try again.")
    except PermissionError:
        # Being opened under Orca's capability audit (blocks numpy's "conf" files,
        # OrcaSlicer #15944). Shouldn't happen at load; a restart re-imports numpy
        # during the audit-free startup window.
        _DEPS_ERROR = ("OrcaSlicer's plugin sandbox blocked a Support Fins "
                       "dependency (a known numpy audit limitation). Fully quit "
                       "and reopen OrcaSlicer, then try again.")
    except Exception as e:  # pragma: no cover - defensive
        _DEPS_ERROR = f"Support Fins could not load numpy: {type(e).__name__}: {e}"


_import_deps()

ENGINE_JS = "var SupportFinsEngine=(()=>{var pn=Object.defineProperty;var _e=Object.getOwnPropertyDescriptor;var Ee=Object.getOwnPropertyNames;var Ce=Object.prototype.hasOwnProperty;var Oe=(t,n)=>{for(var e in n)pn(t,e,{get:n[e],enumerable:!0})},De=(t,n,e,s)=>{if(n&&typeof n==\"object\"||typeof n==\"function\")for(let i of Ee(n))!Ce.call(t,i)&&i!==e&&pn(t,i,{get:()=>n[i],enumerable:!(s=_e(n,i))||s.enumerable});return t};var We=t=>De(pn({},\"__esModule\",{value:!0}),t);var Lo={};Oe(Lo,{ENGINE_DEFAULTS:()=>En,b64ToBytes:()=>Te,bytesToB64:()=>Fe,computeFins:()=>Cn,computeFinsB64:()=>Bo});var _t=[1,0,0,0,1,0,0,0,1],je=(t,n,e)=>`${Math.round(t*1e3)},${Math.round(n*1e3)},${Math.round(e*1e3)}`;function jn(t){let n=t.getAttribute(\"position\").array,e=n.length/9,s=new Float64Array(e*3),i=new Float64Array(e),o=new Map,a=new Int32Array(e*3);for(let c=0;c<e;c++){let r=c*9,g=n[r],M=n[r+1],x=n[r+2],p=n[r+3],d=n[r+4],u=n[r+5],y=n[r+6],b=n[r+7],m=n[r+8],A=p-g,S=d-M,P=u-x,W=y-g,O=b-M,T=m-x,C=S*T-P*O,_=P*W-A*T,I=A*O-S*W,w=Math.hypot(C,_,I);w>1e-12&&(s[c*3]=C/w,s[c*3+1]=_/w,s[c*3+2]=I/w),i[c]=.5*w;for(let k=0;k<3;k++){let z=je(n[r+k*3],n[r+k*3+1],n[r+k*3+2]),R=o.get(z);R===void 0&&o.set(z,R=o.size),a[c*3+k]=R}}let h=new Map,l=[],f=[];for(let c=0;c<e;c++)for(let r=0;r<3;r++){let g=a[c*3+r],M=a[c*3+(r+1)%3],x=g<M?`${g}_${M}`:`${M}_${g}`,p=h.get(x);p===void 0?h.set(x,c):(l.push(p),f.push(c))}return{pos:n,nFaces:e,nrm:s,area:i,adjA:Int32Array.from(l),adjB:Int32Array.from(f),vertexCount:o.size,edgeCount:h.size,_zr:new Float64Array(e*3),_parent:new Int32Array(e),_over:new Uint8Array(e),_kept:new Uint8Array(e),_onBed:new Uint8Array(e)}}function Gn(t,n=45,e=_t){let{pos:s,nFaces:i,nrm:o,area:a,adjA:h,adjB:l}=t,f=-(Math.cos(n*Math.PI/180)+1e-4),c=t._zr,r=1/0,g=-1/0,M=1/0,x=-1/0,p=1/0,d=-1/0;for(let _=0,I=0;_<i*3;_++,I+=3){let w=s[I],k=s[I+1],z=s[I+2],R=e[0]*w+e[3]*k+e[6]*z,H=e[1]*w+e[4]*k+e[7]*z,F=e[2]*w+e[5]*k+e[8]*z;c[_]=F,R<r&&(r=R),R>g&&(g=R),H<M&&(M=H),H>x&&(x=H),F<p&&(p=F),F>d&&(d=F)}let u=t._onBed.fill(0),y=t._over.fill(0),b=0,m=0,A=0;for(let _=0;_<i;_++){if(Math.max(c[_*3],c[_*3+1],c[_*3+2])-p<.35){u[_]=1,b+=a[_];continue}e[2]*o[_*3]+e[5]*o[_*3+1]+e[8]*o[_*3+2]<f&&(y[_]=1,m+=a[_],A++)}let S=t._parent;for(let _=0;_<i;_++)S[_]=_;let P=_=>{for(;S[_]!==_;)_=S[_]=S[S[_]];return _};for(let _=0;_<h.length;_++){let I=h[_],w=l[_];if(!y[I]||!y[w])continue;let k=P(I),z=P(w);k!==z&&(S[k]=z)}let W=new Map;for(let _=0;_<i;_++){if(!y[_])continue;let I=P(_),w=W.get(I);w?(w.faces.push(_),w.area+=a[_]):W.set(I,{faces:[_],area:a[_]})}let O=[...W.values()],T=O.filter(_=>_.area>=12).sort((_,I)=>I.area-_.area),C=t._kept.fill(0);for(let _ of T)for(let I of _.faces)C[I]=1;return{over:y,kept:C,onBed:u,regions:T,rawRegionCount:O.length,overArea:m,bedArea:b,overFaceCount:A,offset:{x:-(r+g)/2,y:-(M+x)/2,z:-p},size:{x:g-r,y:x-M,z:d-p}}}function Nn(t,n,e=_t){let{pos:s,nFaces:i,adjA:o,adjB:a}=t,h=.3,l=n.offset,f=new Int32Array(i);for(let T=0;T<i;T++)f[T]=T;let c=T=>{for(;f[T]!==T;)T=f[T]=f[f[T]];return T};for(let T=0;T<o.length;T++){let C=c(o[T]),_=c(a[T]);C!==_&&(f[C]=_)}let r=new Float64Array(i*9);for(let T=0;T<i*9;T+=3)r[T]=e[0]*s[T]+e[3]*s[T+1]+e[6]*s[T+2]+l.x,r[T+1]=e[1]*s[T]+e[4]*s[T+1]+e[7]*s[T+2]+l.y,r[T+2]=e[2]*s[T]+e[5]*s[T+1]+e[8]*s[T+2]+l.z;let g=new Int32Array(i),M=new Map;for(let T=0;T<i;T++){let C=g[T]=c(T),_=M.get(C);_||M.set(C,_={faces:0,lowest:null,vol:0}),_.faces++;let I=T*9;for(let w=0;w<9;w+=3)(!_.lowest||r[I+w+2]<_.lowest[2])&&(_.lowest=[r[I+w],r[I+w+1],r[I+w+2]]);_.vol+=r[I]*(r[I+4]*r[I+8]-r[I+5]*r[I+7])-r[I+1]*(r[I+3]*r[I+8]-r[I+5]*r[I+6])+r[I+2]*(r[I+3]*r[I+7]-r[I+4]*r[I+6])}if(M.size<2)return[];let x=[];for(let[T,C]of M)C.vol>0&&C.lowest[2]>=.35&&x.push([T,C]);if(!x.length)return[];let p=1/0,d=1/0,u=-1/0,y=-1/0;for(let T=0;T<r.length;T+=3)r[T]<p&&(p=r[T]),r[T]>u&&(u=r[T]),r[T+1]<d&&(d=r[T+1]),r[T+1]>y&&(y=r[T+1]);let b=Math.max(1,Math.min(512,Math.round(Math.sqrt(i/4)))),m=Math.max(1e-6,(u-p)/b),A=Math.max(1e-6,(y-d)/b),S=T=>Math.min(b-1,Math.max(0,Math.floor((T-p)/m))),P=T=>Math.min(b-1,Math.max(0,Math.floor((T-d)/A))),W=new Map;for(let T=0;T<i;T++){let C=T*9,_=S(Math.min(r[C],r[C+3],r[C+6])),I=S(Math.max(r[C],r[C+3],r[C+6])),w=P(Math.min(r[C+1],r[C+4],r[C+7])),k=P(Math.max(r[C+1],r[C+4],r[C+7]));for(let z=_;z<=I;z++)for(let R=w;R<=k;R++){let H=z*b+R,F=W.get(H);F||W.set(H,F=[]),F.push(T)}}let O=[];for(let[T,C]of x){let[_,I,w]=C.lowest,k=-1/0;for(let R of W.get(S(_)*b+P(I))??[]){if(g[R]===T)continue;let H=R*9,F=r[H],j=r[H+1],L=r[H+3],G=r[H+4],E=r[H+6],N=r[H+7],U=(G-N)*(F-E)+(E-L)*(j-N);if(Math.abs(U)<1e-12)continue;let Z=((G-N)*(_-E)+(E-L)*(I-N))/U,q=((N-j)*(_-E)+(F-E)*(I-N))/U,K=1-Z-q;if(Z<-1e-9||q<-1e-9||K<-1e-9)continue;let Q=Z*r[H+2]+q*r[H+5]+K*r[H+8];Q<=w+1e-6&&Q>k&&(k=Q)}let z=k===-1/0?w:w-k;z>h&&O.push({faces:C.faces,lowest:C.lowest,drop:z})}return O}function jt(t,n,e,s=null){let{pos:i,nFaces:o,nrm:a,area:h}=t,l=Math.sin(45*Math.PI/180)+1e-6,f=Math.cos(35*Math.PI/180),c=new Float64Array(o*3),r=new Uint8Array(o);for(let b=0;b<o;b++){let m=b*3,A=a[m],S=a[m+1],P=a[m+2],W=n[0]*A+n[3]*S+n[6]*P,O=n[1]*A+n[4]*S+n[7]*P,T=n[2]*A+n[5]*S+n[8]*P;c[m]=W,c[m+1]=O,c[m+2]=T,Math.abs(T)<=l&&Math.hypot(W,O)>1e-9&&(r[b]=1)}let{start:g,nbr:M}=gn(t),x=[];for(let b=0;b<o;b++)r[b]&&x.push(b);x.sort((b,m)=>h[m]-h[b]);let p=new Uint8Array(o),d=[0,0,0],u=[],y=[];for(let b of x){if(p[b])continue;let m=c[b*3],A=c[b*3+1],S=c[b*3+2],P=0;for(let C=0;C<3;C++)nn(i,b*9+C*3,n,e,d),P+=d[0]*m+d[1]*A+d[2]*S;P/=3,p[b]=1;let W=[b],O=h[b];for(y.length=0,y.push(b);y.length;){let C=y.pop();for(let _=g[C];_<g[C+1];_++){let I=M[_];if(p[I]||!r[I]||c[I*3]*m+c[I*3+1]*A+c[I*3+2]*S<f)continue;let k=-1/0,z=1/0;for(let R=0;R<3;R++){nn(i,I*9+R*3,n,e,d);let H=d[0]*m+d[1]*A+d[2]*S-P;H>k&&(k=H),H<z&&(z=H)}k>.35||z<-1.2||(p[I]=1,W.push(I),O+=h[I],y.push(I))}}if(s&&(s.grown=(s.grown??0)+1),O<25){s&&(s.tooSmall=(s.tooSmall??0)+1);continue}let T=Ge(t,{faces:W,area:O},c,n,e,s);T&&u.push(T)}return u.sort((b,m)=>m.area-b.area),u}function gn(t){if(t._adjStart)return{start:t._adjStart,nbr:t._adjNbr};let{nFaces:n,adjA:e,adjB:s}=t,i=new Int32Array(n+1);for(let h=0;h<e.length;h++)i[e[h]+1]++,i[s[h]+1]++;for(let h=0;h<n;h++)i[h+1]+=i[h];let o=new Int32Array(i[n]),a=i.slice(0,n);for(let h=0;h<e.length;h++)o[a[e[h]]++]=s[h],o[a[s[h]]++]=e[h];return t._adjStart=i,t._adjNbr=o,{start:i,nbr:o}}function nn(t,n,e,s,i){let o=t[n],a=t[n+1],h=t[n+2];return i[0]=e[0]*o+e[3]*a+e[6]*h+s.x,i[1]=e[1]*o+e[4]*a+e[7]*h+s.y,i[2]=e[2]*o+e[5]*a+e[8]*h+s.z,i}function Ge(t,n,e,s,i,o=null){let{pos:a,area:h}=t,l=0,f=0,c=0;for(let R of n.faces)l+=e[R*3]*h[R],f+=e[R*3+1]*h[R],c+=e[R*3+2]*h[R];let r=Math.hypot(l,f,c);if(r<1e-9)return null;l/=r,f/=r,c/=r;let g=Math.hypot(l,f);if(g<1e-6)return o&&(o.notUpright=(o.notUpright??0)+1),null;let M=-f/g,x=l/g,p=-c*(l/g),d=-c*(f/g),u=g,y=[0,0,0],b=-1/0;for(let R of n.faces)for(let H=0;H<3;H++){nn(a,R*9+H*3,s,i,y);let F=y[0]*l+y[1]*f+y[2]*c;F>b&&(b=F)}let m=new Float64Array(n.faces.length*9),A=-1/0,S=1/0,P=1/0,W=-1/0,O=1/0,T=-1/0,C=1/0,_=-1/0;for(let R=0;R<n.faces.length;R++){let H=n.faces[R];for(let F=0;F<3;F++){nn(a,H*9+F*3,s,i,y);let j=y[0]*l+y[1]*f+y[2]*c-b;j>A&&(A=j),j<S&&(S=j);let L=y[0]*M+y[1]*x,G=y[0]*p+y[1]*d+y[2]*u;m[R*9+F*3]=L,m[R*9+F*3+1]=G,m[R*9+F*3+2]=j,L<P&&(P=L),L>W&&(W=L),G<O&&(O=G),G>T&&(T=G),y[2]<C&&(C=y[2]),y[2]>_&&(_=y[2])}}let I=R=>(o&&(o[R]=(o[R]??0)+1),null);if(S<-1.2)return I(\"notFlat\");if(T-O<4)return I(\"tooShort\");if(W-P<4)return I(\"tooNarrow\");let w=(P+W)/2,k=(O+T)/2,z={x:l*b+M*w+p*k,y:f*b+x*w+d*k};return{faces:n.faces,area:n.area,mid:z,n:{x:l,y:f,z:c},u:{x:M,y:x},t:{x:p,y:d,z:u},h:g,d:b,u0:P,u1:W,t0:O,t1:T,z0:C,z1:_,lean:Math.abs(Math.asin(c)*180/Math.PI),flatness:Math.max(A,-S),tris:m}}function St(t,n,e){let s=t.tris;for(let i=0;i<s.length;i+=9){let o=s[i],a=s[i+1],h=s[i+3],l=s[i+4],f=s[i+6],c=s[i+7],r=(l-c)*(o-f)+(f-h)*(a-c);if(Math.abs(r)<1e-9)continue;let g=((l-c)*(n-f)+(f-h)*(e-c))/r,M=((c-a)*(n-f)+(o-f)*(e-c))/r,x=1-g-M;if(!(g<-1e-9||M<-1e-9||x<-1e-9))return g*s[i+2]+M*s[i+5]+x*s[i+8]}return null}function Rt(t,n,e,s){let i=t.d+n;return[t.n.x*i+t.u.x*e+t.t.x*s,t.n.y*i+t.u.y*e+t.t.y*s,t.n.z*i+t.t.z*s]}var zt=(t,n,e)=>(e-t.n.z*(t.d+n))/t.t.z,Bn=(t,n,e)=>t.n.z*(t.d+n)+t.t.z*e;function xn(t){if(t._insideGrid)return t._insideGrid;let{pos:n,nFaces:e}=t,s=1/0,i=-1/0,o=1/0,a=-1/0;for(let x=0;x<e*3;x++){let p=n[x*3+1],d=n[x*3+2];p<s&&(s=p),p>i&&(i=p),d<o&&(o=d),d>a&&(a=d)}let h=64/Math.max(1e-6,i-s),l=64/Math.max(1e-6,a-o),f=(x,p)=>{let d=Math.min(63,Math.max(0,Math.floor((x-s)*h))),u=Math.min(63,Math.max(0,Math.floor((p-o)*l)));return d*64+u},c=new Int32Array(4097),r=x=>{let p=x*9,d=Math.min(n[p+1],n[p+4],n[p+7]),u=Math.max(n[p+1],n[p+4],n[p+7]),y=Math.min(n[p+2],n[p+5],n[p+8]),b=Math.max(n[p+2],n[p+5],n[p+8]);return[Math.min(63,Math.max(0,Math.floor((d-s)*h))),Math.min(63,Math.max(0,Math.floor((u-s)*h))),Math.min(63,Math.max(0,Math.floor((y-o)*l))),Math.min(63,Math.max(0,Math.floor((b-o)*l)))]};for(let x=0;x<e;x++){let[p,d,u,y]=r(x);for(let b=p;b<=d;b++)for(let m=u;m<=y;m++)c[b*64+m+1]++}for(let x=0;x<4096;x++)c[x+1]+=c[x];let g=new Int32Array(c[4096]),M=c.slice(0,4096);for(let x=0;x<e;x++){let[p,d,u,y]=r(x);for(let b=p;b<=d;b++)for(let m=u;m<=y;m++)g[M[b*64+m]++]=x}return t._insideGrid={start:c,items:g,cell:f,minY:s,minZ:o,sy:h,sz:l}}function Ne(t,n,e,s,i){let o=n*9,a=t[o],h=t[o+1],l=t[o+2],f=t[o+3]-a,c=t[o+4]-h,r=t[o+5]-l,g=t[o+6]-a,M=t[o+7]-h,x=t[o+8]-l,p=e-a,d=s-h,u=i-l,y=f*p+c*d+r*u,b=g*p+M*d+x*u;if(y<=0&&b<=0)return[a,h,l];let m=e-t[o+3],A=s-t[o+4],S=i-t[o+5],P=f*m+c*A+r*S,W=g*m+M*A+x*S;if(P>=0&&W<=P)return[t[o+3],t[o+4],t[o+5]];let O=y*W-P*b;if(O<=0&&y>=0&&P<=0){let j=y/(y-P);return[a+f*j,h+c*j,l+r*j]}let T=e-t[o+6],C=s-t[o+7],_=i-t[o+8],I=f*T+c*C+r*_,w=g*T+M*C+x*_;if(w>=0&&I<=w)return[t[o+6],t[o+7],t[o+8]];let k=I*b-y*w;if(k<=0&&b>=0&&w<=0){let j=b/(b-w);return[a+g*j,h+M*j,l+x*j]}let z=P*w-I*W;if(z<=0&&W-P>=0&&I-w>=0){let j=(W-P)/(W-P+(I-w));return[t[o+3]+(t[o+6]-t[o+3])*j,t[o+4]+(t[o+7]-t[o+4])*j,t[o+5]+(t[o+8]-t[o+5])*j]}let R=1/(z+k+O),H=k*R,F=O*R;return[a+f*H+g*F,h+c*H+M*F,l+r*H+x*F]}function qn(t,n,e,s,i,o,a=.45){let{pos:h}=t,l=xn(t),f=s-e.x,c=i-e.y,r=o-e.z,g=n[0]*f+n[1]*c+n[2]*r,M=n[3]*f+n[4]*c+n[5]*r,x=n[6]*f+n[7]*c+n[8]*r,p=C=>Math.min(63,Math.max(0,C)),d=p(Math.floor((M-a-l.minY)*l.sy)),u=p(Math.floor((M+a-l.minY)*l.sy)),y=p(Math.floor((x-a-l.minZ)*l.sz)),b=p(Math.floor((x+a-l.minZ)*l.sz)),m=a*a,A=0,S=0,P=0,W=!1;for(let C=d;C<=u;C++)for(let _=y;_<=b;_++){let I=C*64+_;for(let w=l.start[I];w<l.start[I+1];w++){let k=Ne(h,l.items[w],g,M,x),z=k[0]-g,R=k[1]-M,H=k[2]-x,F=z*z+R*R+H*H;F<m&&(m=F,A=z,S=R,P=H,W=!0)}}if(!W)return null;let O=Math.sqrt(m),T=n[2]*A+n[5]*S+n[8]*P;return{d:O,cosUp:O>1e-9?T/O:0}}function Be(t,n,e,s){let i=n[0]-t[0],o=n[1]-t[1],a=n[2]-t[2],h=s[0]-e[0],l=s[1]-e[1],f=s[2]-e[2],c=t[0]-e[0],r=t[1]-e[1],g=t[2]-e[2],M=i*i+o*o+a*a,x=h*h+l*l+f*f,p=h*c+l*r+f*g,d,u;if(M<=1e-9&&x<=1e-9)d=0,u=0;else if(M<=1e-9)d=0,u=Math.min(1,Math.max(0,p/x));else{let y=i*c+o*r+a*g;if(x<=1e-9)u=0,d=Math.min(1,Math.max(0,-y/M));else{let b=i*h+o*l+a*f,m=M*x-b*b;d=m>1e-9?Math.min(1,Math.max(0,(b*p-y*x)/m)):0,u=(b*d+p)/x,u<0?(u=0,d=Math.min(1,Math.max(0,-y/M))):u>1&&(u=1,d=Math.min(1,Math.max(0,(b-y)/M)))}}return[[t[0]+i*d,t[1]+o*d,t[2]+a*d],[e[0]+h*u,e[1]+l*u,e[2]+f*u]]}function Le(t,n){let e=n*9;return[[t[e],t[e+1],t[e+2]],[t[e+3],t[e+4],t[e+5]],[t[e+6],t[e+7],t[e+8]]]}var dn=(t,n)=>{let e=t[0]-n[0],s=t[1]-n[1],i=t[2]-n[2];return e*e+s*s+i*i};function Gt(t,n,e,s,i=.45){let{pos:o}=t,a=xn(t),h=S=>Math.min(63,Math.max(0,S)),l=new Array(s.length);for(let S=0;S<s.length;S++){let P=s[S][0]-e.x,W=s[S][1]-e.y,O=s[S][2]-e.z;l[S]=[n[0]*P+n[1]*W+n[2]*O,n[3]*P+n[4]*W+n[5]*O,n[6]*P+n[7]*W+n[8]*O]}let f=t._scSeen&&t._scSeen.length===t.nFaces?t._scSeen:t._scSeen=new Int32Array(t.nFaces),c=t._scGen|0;c>2e9&&(f.fill(0),c=0);let r=i*i,g=null,M=null;for(let S=0;S<l.length;S+=3){c++;let P=[l[S],l[S+1],l[S+2]],W=1/0,O=-1/0,T=1/0,C=-1/0,_=1/0,I=-1/0;for(let H of P)H[0]<W&&(W=H[0]),H[0]>O&&(O=H[0]),H[1]<T&&(T=H[1]),H[1]>C&&(C=H[1]),H[2]<_&&(_=H[2]),H[2]>I&&(I=H[2]);let w=h(Math.floor((T-i-a.minY)*a.sy)),k=h(Math.floor((C+i-a.minY)*a.sy)),z=h(Math.floor((_-i-a.minZ)*a.sz)),R=h(Math.floor((I+i-a.minZ)*a.sz));for(let H=w;H<=k;H++)for(let F=z;F<=R;F++){let j=H*64+F;for(let L=a.start[j];L<a.start[j+1];L++){let G=a.items[L];if(f[G]===c)continue;f[G]=c;let E=G*9;if(Math.min(o[E],o[E+3],o[E+6])>O+i||Math.max(o[E],o[E+3],o[E+6])<W-i||Math.min(o[E+1],o[E+4],o[E+7])>C+i||Math.max(o[E+1],o[E+4],o[E+7])<T-i||Math.min(o[E+2],o[E+5],o[E+8])>I+i||Math.max(o[E+2],o[E+5],o[E+8])<_-i)continue;let et=Le(o,G),X=Ue(P,et);if(X){r=0,g=X,M=X,H=k+1,F=R+1,S=l.length;break}for(let D of et){let B=Un(P,D),Y=dn(B,D);Y<r&&(r=Y,g=B,M=D)}for(let D of P){let B=Un(et,D),Y=dn(D,B);Y<r&&(r=Y,g=D,M=B)}for(let D=0;D<3;D++)for(let B=0;B<3;B++){let[Y,V]=Be(P[D],P[(D+1)%3],et[B],et[(B+1)%3]),at=dn(Y,V);at<r&&(r=at,g=Y,M=V)}}}}if(t._scGen=c,!g)return null;let x=Math.sqrt(r),p=M[0]-g[0],d=M[1]-g[1],u=M[2]-g[2],y=n[2]*p+n[5]*d+n[8]*u,b=n[0]*g[0]+n[3]*g[1]+n[6]*g[2]+e.x,m=n[1]*g[0]+n[4]*g[1]+n[7]*g[2]+e.y,A=n[2]*g[0]+n[5]*g[1]+n[8]*g[2]+e.z;return{d:x,cosUp:x>1e-9?y/x:0,x:b,y:m,z:A}}function Ln(t,n,e,s,i){let o=n[0]-t[0],a=n[1]-t[1],h=n[2]-t[2],l=s[0]-e[0],f=s[1]-e[1],c=s[2]-e[2],r=i[0]-e[0],g=i[1]-e[1],M=i[2]-e[2],x=a*M-h*g,p=h*r-o*M,d=o*g-a*r,u=l*x+f*p+c*d;if(Math.abs(u)<1e-12)return null;let y=1/u,b=t[0]-e[0],m=t[1]-e[1],A=t[2]-e[2],S=y*(b*x+m*p+A*d);if(S<0||S>1)return null;let P=m*c-A*f,W=A*l-b*c,O=b*f-m*l,T=y*(o*P+a*W+h*O);if(T<0||S+T>1)return null;let C=y*(r*P+g*W+M*O);return C<0||C>1?null:[t[0]+o*C,t[1]+a*C,t[2]+h*C]}function Ue(t,n){for(let e=0;e<3;e++){let s=Ln(t[e],t[(e+1)%3],n[0],n[1],n[2])||Ln(n[e],n[(e+1)%3],t[0],t[1],t[2]);if(s)return s}return null}function Un(t,n){let[e,s,i]=t,o=s[0]-e[0],a=s[1]-e[1],h=s[2]-e[2],l=i[0]-e[0],f=i[1]-e[1],c=i[2]-e[2],r=n[0]-e[0],g=n[1]-e[1],M=n[2]-e[2],x=o*r+a*g+h*M,p=l*r+f*g+c*M;if(x<=0&&p<=0)return e;let d=n[0]-s[0],u=n[1]-s[1],y=n[2]-s[2],b=o*d+a*u+h*y,m=l*d+f*u+c*y;if(b>=0&&m<=b)return s;let A=x*m-b*p;if(A<=0&&x>=0&&b<=0){let z=x/(x-b);return[e[0]+o*z,e[1]+a*z,e[2]+h*z]}let S=n[0]-i[0],P=n[1]-i[1],W=n[2]-i[2],O=o*S+a*P+h*W,T=l*S+f*P+c*W;if(T>=0&&O<=T)return i;let C=O*p-x*T;if(C<=0&&p>=0&&T<=0){let z=p/(p-T);return[e[0]+l*z,e[1]+f*z,e[2]+c*z]}let _=b*T-O*m;if(_<=0&&m-b>=0&&O-T>=0){let z=(m-b)/(m-b+(O-T));return[s[0]+(i[0]-s[0])*z,s[1]+(i[1]-s[1])*z,s[2]+(i[2]-s[2])*z]}let I=1/(_+C+A),w=C*I,k=A*I;return[e[0]+o*w+l*k,e[1]+a*w+f*k,e[2]+h*w+c*k]}function pt(t,n,e,s,i,o){let{pos:a}=t,h=xn(t),l=s-e.x,f=i-e.y,c=o-e.z,r=n[0]*l+n[1]*f+n[2]*c,g=n[3]*l+n[4]*f+n[5]*c,M=n[6]*l+n[7]*f+n[8]*c,x=h.cell(g,M),p=0;for(let d=h.start[x];d<h.start[x+1];d++){let u=h.items[d]*9,y=a[u+1],b=a[u+2],m=a[u+4],A=a[u+5],S=a[u+7],P=a[u+8],W=(A-P)*(y-S)+(S-m)*(b-P);if(Math.abs(W)<1e-9)continue;let O=((A-P)*(g-S)+(S-m)*(M-P))/W,T=((P-b)*(g-S)+(y-S)*(M-P))/W,C=1-O-T;if(O<0||T<0||C<0)continue;O*a[u]+T*a[u+3]+C*a[u+6]>r&&p++}return(p&1)===1}var v={th:1,gap:.2,tip:.6,baseH:.6,tipH:1.5,footMax:3,footMin:1.6,footRatio:.12,minSpan:7,minSpanShort:4,maxShortAspect:6,minHeight:1.5,minHeightSquat:.6,minSpanSquat:4,squatBrimH:.4,squatBrimW:2.5,stationStep:1,minStations:3,clearProbes:12,sideClear:.35,maxWander:.1,contourSlopeMin:.15,splitAgreeDeg:15,tubeSpreadDeg:25,tubeCurvedFrac:.4,tubeMinArea:300,tubeSmallMinArea:12,minSpanTube:3,tubeConvexFrac:.7,tubeTwoSidedFrac:.25,maxUnsupportedSpan:12,tineH:.2,tineW:.5,tineBite:.5,tineStep:2,tineStepSparse:5,tineOverlap:.3,minGripTines:3,tineEdgeBand:8,tineMidFactor:2,tineSlopeMin:1,tineTopClear:.5};function xt(t,n){let e=t[0].length,s=(i,o,a)=>n.push(i,a,o);for(let i=0;i<t.length-1;i++)for(let o=0;o<e;o++){let a=(o+1)%e;s(t[i][o],t[i][a],t[i+1][a]),s(t[i][o],t[i+1][a],t[i+1][o])}for(let i=1;i<e-1;i++){s(t[0][0],t[0][i+1],t[0][i]);let o=t[t.length-1];s(o[0],o[i],o[i+1])}}function en(t,n,e,s,i){let o=t.length,a=t.map(([l,f])=>s(l,f,n)),h=t.map(([l,f])=>s(l,f,e));for(let l=0;l<o;l++){let f=(l+1)%o;i.push(a[l],a[f],h[f]),i.push(a[l],h[f],h[l])}for(let l=1;l<o-1;l++)i.push(h[0],h[l],h[l+1]),i.push(a[0],a[l+1],a[l])}var J={pattern:\"none\",pitch:8,web:1.6,rail:1.2,post:2,minHalf:1.2,minHole:3,slope:1.4,maxSlope:3,eps:.05,latPitch:6,latStrut:1.2,latSlope:1.5,roofMin:1,minArea:3,minSaved:.1},Mn={diamond:{prof:[[0,0],[1,.5],[0,1]],roof:.5},triangle:{prof:[[1,0],[0,1]],roof:1},arch:{prof:[[1,0],[1,null],[0,1]],roof:null}},bn=[\"none\",...Object.keys(Mn),\"lattice\"];function qe(t,n,e){let s=Mn[t],i=(n-J.web)/2;if(!s||i<J.minHalf||e<J.minHole)return[];let o=s.roof??0,a=r=>o?J.slope*r/o:J.slope*r+J.minHole/2,h=r=>Math.max(a(r),2*J.maxSlope*r),l=Math.max(1,Math.ceil((e+J.web)/(h(i)+J.web))),f=(e-(l-1)*J.web)/l;if(f<a(i)&&l>1)l--,f=(e-(l-1)*J.web)/l;else if(f<a(i)&&(i=o?f*o/J.slope:(f-J.minHole/2)/J.slope,i<J.minHalf))return[];let c=[];for(let r=0;r<l;r++){let g=r*(f+J.web);c.push({a:i,z0:g,z1:g+f})}return c}function Ze(t,n,e,s,i){let o=(n+e)/2;return i.map(({a,z0:h,z1:l})=>{let f=s+h,c=s+l,r=Mn[t].prof.map(([M,x])=>[M*a,x===null?c-J.slope*a:f+x*(c-f)]),g=[...r.map(([M,x])=>[o+M,x]),...r.slice().reverse().map(([M,x])=>[o-M,x])];return g.filter((M,x)=>{let p=g[(x+1)%g.length];return Math.abs(p[0]-M[0])>1e-9||Math.abs(p[1]-M[1])>1e-9})})}function vt(t,n){let e=[];for(let s=0;s<t.length;s++){let i=t[s],o=t[(s+1)%t.length],a=n(i),h=n(o);if(a>=0&&e.push(i),a>=0!=h>=0){let l=a/(a-h);e.push([i[0]+(o[0]-i[0])*l,i[1]+(o[1]-i[1])*l])}}return e}var yn=t=>{let n=0;for(let e=0;e<t.length;e++){let[s,i]=t[e],[o,a]=t[(e+1)%t.length];n+=s*a-o*i}return n/2};function Zn(t,n){for(let e=0;e<t.length;e++){let[s,i]=t[e],[o,a]=t[(e+1)%t.length],h=o-s,l=a-i;if(h<-1e-9&&Math.abs(l)<n*-h-1e-9)return!1}return!0}function Ye(t,n,e,s,i){let o=J.latSlope,a=J.latPitch,h=J.latStrut/2/(o/Math.hypot(1,o)),l=a/2-h,f=o*l;if(l<J.minHalf)return[];let c=[];for(let u=0;u+1<t.length;u++)t[u+1]<=s||t[u]>=i||t[u+1]-t[u]<1e-9||c.push(u);let r=(u,y)=>b=>u[y]+(u[y+1]-u[y])*(b[0]-t[y])/(t[y+1]-t[y]),g=u=>{let y=vt(u,m=>m[0]-s);y=vt(y,m=>i-m[0]);for(let m of c){let[A,S]=[t[m],t[m+1]];if(!y.some(O=>O[0]>A-1e-9)||!y.some(O=>O[0]<S+1e-9))continue;let P=r(e,m),W=r(n,m);if(y=vt(y,O=>P(O)-O[1]),y=vt(y,O=>O[1]-W(O)),y.length<3)return null}for(let m=0;m<4&&y.length>=3&&!Zn(y,J.roofMin);m++)for(let A=0;A<y.length;A++){let S=y[A],P=y[(A+1)%y.length],W=P[0]-S[0],O=P[1]-S[1];if(W<-1e-9&&Math.abs(O)<J.roofMin*-W-1e-9){if(Math.abs(O)<.25*-W){let T=[(S[0]+P[0])/2,Math.min(S[1],P[1])];y=vt(y,C=>T[1]-J.roofMin*(C[0]-T[0])-C[1]),y=vt(y,C=>T[1]+J.roofMin*(C[0]-T[0])-C[1])}else{let[T,C]=S[1]>=P[1]?[S,P]:[P,S],_=Math.sign(C[0]-T[0]);y=vt(y,I=>T[1]-J.roofMin*(I[0]-T[0])*_-I[1])}break}}if(y.length<3||yn(y)<J.minArea||!Zn(y,J.roofMin))return null;let b=0;for(let m=0;m<y.length;m++){let A=y[m],S=y[(m+1)%y.length];b+=Math.hypot(S[0]-A[0],S[1]-A[1])}return 2*yn(y)/b<J.minHalf/1.5?null:y},M=Math.min(...n.filter((u,y)=>t[y]>=s&&t[y]<=i)),x=Math.max(...e.filter((u,y)=>t[y]>=s&&t[y]<=i)),p=Math.ceil((i-s)/a)+1,d=[];for(let u=0;M+u*(o*a/2)-f<=x;u++){let y=M+f*.5+u*(o*a/2);for(let b=-1;b<=p;b++){let m=s+J.latStrut/2+l+b*a+(u%2?a/2:0);if(!(m+l<s||m-l>i))for(let A of[1,.8,.62,.46,.34]){let S=g([[m,y-f*A],[m+l*A,y],[m,y+f*A],[m-l*A,y]]);if(S){d.push(S);break}}}}return d}function At(t,n){let e=1/0,s=-1/0;for(let i=0;i<t.length;i++){let[o,a]=t[i],[h,l]=t[(i+1)%t.length];if((a-n)*(l-n)>0)continue;if(Math.abs(l-a)<1e-12){e=Math.min(e,o,h),s=Math.max(s,o,h);continue}let f=o+(h-o)*(n-a)/(l-a);e=Math.min(e,f),s=Math.max(s,f)}return e<=s?[e,s]:null}function Xe(t,n,e,s,i){let o=i.filter(c=>c.some(r=>r[0]>t)&&c.some(r=>r[0]<n)&&c.some(r=>r[1]>e)&&c.some(r=>r[1]<s)),a=[e,s];for(let c of o)for(let r=0;r<c.length;r++){let[g,M]=c[r],[x,p]=c[(r+1)%c.length];a.push(M);for(let d of[t,n]){let u=(d-g)/(x-g);x!==g&&u>0&&u<1&&a.push(M+u*(p-M))}}let h=[...new Set(a.filter(c=>c>=e&&c<=s).map(c=>+c.toFixed(9)))].sort((c,r)=>c-r),l=c=>Math.max(t,Math.min(n,c)),f=[];for(let c=0;c+1<h.length;c++){let r=h[c],g=h[c+1];if(g-r<1e-7)continue;let M=(r+g)/2,x=[];for(let b of o){let m=At(b,M);m&&m[1]>t&&m[0]<n&&x.push(b)}x.sort((b,m)=>At(b,M)[0]-At(m,M)[0]);let p=[()=>t],d=[],u=r+(g-r)/4,y=g-(g-r)/4;for(let b of x){let m=At(b,u)??At(b,M),A=At(b,y)??At(b,M),S=P=>W=>l(m[P]+(A[P]-m[P])*(W-u)/(y-u));d.push(S(0)),p.push(S(1))}d.push(()=>n);for(let b=0;b<p.length;b++){let m=[[p[b](r),r],[d[b](r),r],[d[b](g),g],[p[b](g),g]],A=m[1][0]-m[0][0],S=m[2][0]-m[3][0];A<1e-7&&S<1e-7||f.push(A<1e-7?[m[0],m[2],m[3]]:S<1e-7?[m[0],m[1],m[2]]:m)}}return f}function Nt(t,n,e,s){let i=J.pattern;if(!bn.includes(i)||i===\"none\"||t.length<s.minStations)return!1;let o=[0];for(let I=1;I<t.length;I++)o.push(o[I-1]+Math.hypot(t[I].p[0]-t[I-1].p[0],t[I].p[1]-t[I-1].p[1]));let a=o[o.length-1],h=Math.floor((a-2*J.post)/J.pitch);if(h<1)return!1;let l=I=>{let w=0;for(let k=1;k<o.length;k++)Math.abs(o[k]-I)<Math.abs(o[w]-I)&&(w=k);return w},f=[];for(let I=0;I<=h;I++){let w=l(J.post+I*(a-2*J.post)/h);(!f.length||w>f[f.length-1])&&f.push(w)}if(f.length<2)return!1;let c=[],r=[];for(let I of t){let w=I.ztip-J.rail,k=I.botTip+J.rail,z=(w+k)/2;c.push(w>k?w:z),r.push(w>k?k:z)}let g=o[f[0]],M=o[f[f.length-1]],x;if(i===\"lattice\")x=Ye(o,r,c,g,M);else{x=[];for(let I=0;I+1<f.length;I++){let w=f[I],k=f[I+1],z=1/0,R=-1/0;for(let H=w;H<=k;H++)z=Math.min(z,c[H]),R=Math.max(R,r[H]);x.push(...Ze(i,o[w],o[k],R,qe(i,o[k]-o[w],z-R)))}}if(!x.length)return!1;let p=0,d=0;for(let I=0;I+1<t.length;I++)p+=(o[I+1]-o[I])*(t[I].top-t[I].bot+(t[I+1].top-t[I+1].bot))/2;for(let I of x)d+=yn(I);if(d<J.minSaved*p)return!1;let u=s.th/2,y=s.tip/2,b=[],m=[];for(let I=f[0];I<=f[f.length-1];I++){let w=t[I],k=(H,F)=>[w.p[0]+w.sx*H,w.p[1]+w.sy*H,F],z=Math.max(w.botTip,Math.min(c[I],w.ztip-.01)),R=Math.min(w.ztip,Math.max(r[I],w.botTip+.01));b.push([k(+u,z),k(+u,w.ztip),k(+y,w.top),k(-y,w.top),k(-u,w.ztip),k(-u,z)]),m.push(w.taperBot?[k(+y,w.bot),k(+u,w.botTip),k(+u,R),k(-u,R),k(-u,w.botTip),k(-y,w.bot)]:[k(+u,w.bot),k(+u,R),k(-u,R),k(-u,w.bot)])}xt(b,e),xt(m,e),f[0]>0&&xt(n.slice(0,f[0]+1),e),f[f.length-1]<t.length-1&&xt(n.slice(f[f.length-1]),e);let A=I=>{let w=0;for(;w<o.length-2&&o[w+1]<I;)w++;let k=Math.max(-1,Math.min(2,(I-o[w])/Math.max(1e-9,o[w+1]-o[w]))),z=t[w],R=t[w+1],H=(G,E)=>G+(E-G)*k,F=H(z.sx,R.sx),j=H(z.sy,R.sy),L=Math.hypot(F,j);return F/=L,j/=L,{x:H(z.p[0],R.p[0]),y:H(z.p[1],R.p[1]),sx:F,sy:j}},S=J.eps,P=Math.min(...r)-1,W=Math.max(...c)+1,O=[];for(let I=f[0];I<f[f.length-1];I++)o[I+1]-o[I]<1e-9||(O.push([[o[I],c[I]+S],[o[I+1],c[I+1]+S],[o[I+1],W+1],[o[I],W+1]]),O.push([[o[I],P-1],[o[I+1],P-1],[o[I+1],r[I+1]-S],[o[I],r[I]-S]]));let T=[...x,...O],C=(I,w)=>x.some(k=>{let z=At(k,w);return z&&I>z[0]&&I<z[1]}),_=(I,w,k)=>{let z=A(I);return[z.x+z.sx*k,z.y+z.sy*k,w]};for(let I=0;I+1<f.length;I++){let w=o[f[I]],k=o[f[I+1]],z=1/0,R=-1/0;for(let H=f[I];H<=f[I+1];H++)z=Math.min(z,r[H]-S),R=Math.max(R,c[H]+S);for(let H of Xe(w,k,z,R,T)){let F=H.map((j,L)=>{let[G,E]=j;if(G!==w&&G!==k)return j;let N=[H[(L+H.length-1)%H.length],H[(L+1)%H.length]].filter(K=>K[0]!==G);if(N.length!==1)return j;let[U]=N,Z=S/Math.abs(U[0]-G),q=[G+(G-U[0])*Z,E+(E-U[1])*Z];return C(q[0],q[1])?j:q});en(F,-u,u,_,e)}}return!0}var Bt=t=>{let n=Math.max(v.footMin,(v.maxUnsupportedSpan-1)/2);return Math.max(v.footMin,Math.min(v.footMax,n,t*v.footRatio))};function on(t,n){let e=Math.max(n-v.tipH,v.baseH+.1);return t<v.baseH?Bt(n):t<e?v.th/2:v.th/2-(v.th-v.tip)/2*((t-e)/Math.max(1e-6,n-e))}function wn(t,n,e,s=v.minHeight){let i=[],o=[],a=[];for(let h=0;h<t.length;h++){let l=t[h],f=t[Math.max(0,h-1)],c=t[Math.min(t.length-1,h+1)],r=c[0]-f[0],g=c[1]-f[1],M=Math.hypot(r,g);if(M<1e-9)return!1;r/=M,g/=M;let x=g,p=-r,d=l[2]-v.gap,u=d-n;if(u<s)return!1;let y=Bt(u),b=Math.min(v.baseH,u/2),m=Math.max(d-v.tipH,n+b+.1),A=n+b,S=(P,W)=>[l[0]+x*P,l[1]+p*P,W];i.push([S(+v.th/2,n),S(+v.th/2,m),S(+v.tip/2,d),S(-v.tip/2,d),S(-v.th/2,m),S(-v.th/2,n)]),o.push([S(+y,n),S(+y,A),S(-y,A),S(-y,n)]),a.push({p:l,sx:x,sy:p,top:d,ztip:m,bot:n,botTip:A,taperBot:!1})}return Nt(a,i,e,v)||xt(i,e),xt(o,e),!0}function In(t,n,e){let s=[],i=[];for(let o=0;o<t.length;o++){let a=t[o],h=t[Math.max(0,o-1)],l=t[Math.min(t.length-1,o+1)],f=l[0]-h[0],c=l[1]-h[1],r=Math.hypot(f,c);if(r<1e-9)return!1;f/=r,c/=r;let g=c,M=-f,x=a[2]-v.gap,p=n[o][2],d=x-p;if(d<v.minHeight)return!1;let u=Math.min(v.tipH,d/2),y=p+u,b=x-u,m=(A,S)=>[a[0]+g*A,a[1]+M*A,S];s.push([m(+v.tip/2,p),m(+v.th/2,y),m(+v.th/2,b),m(+v.tip/2,x),m(-v.tip/2,x),m(-v.th/2,b),m(-v.th/2,y),m(-v.tip/2,p)]),i.push({p:a,sx:g,sy:M,top:x,ztip:b,bot:p,botTip:y,taperBot:!0})}return Nt(i,s,e,v)||xt(s,e),!0}function Lt(t,n,e,s,i){let o=t[n],a=t[Math.max(0,n-1)],h=t[Math.min(t.length-1,n+1)],l=h[0]-a[0],f=h[1]-a[1],c=Math.hypot(l,f);if(c<1e-9)return!0;let r=f/c,g=-l/c,M=o[2]-v.gap,x=Math.max(v.clearProbes,Math.ceil(M/1.5));for(let p=1;p<=x;p++){let d=M*p/x-.05;if(d<=0)continue;let u=on(d,M);for(let y of[.12,.24,v.sideClear]){let b=u+y;if(pt(e,s,i,o[0]+r*b,o[1]+g*b,d)||pt(e,s,i,o[0]-r*b,o[1]-g*b,d))return!1}if(pt(e,s,i,o[0],o[1],d))return!1}return!0}function Ut(t,n,e,s,i){let o=t[n],a=t[Math.max(0,n-1)],h=t[Math.min(t.length-1,n+1)],l=h[0]-a[0],f=h[1]-a[1],c=Math.hypot(l,f);if(c<1e-9)return!0;let r=f/c,g=-l/c,M=o[2]-v.gap,x=[[0,M],[v.tip/2,M],[-v.tip/2,M]];for(let p=.4;p<M-.05;p+=2){let d=on(p,M);x.push([d,p],[-d,p])}for(let[p,d]of x){if(d<=.05)continue;let u=qn(e,s,i,o[0]+r*p,o[1]+g*p,d);if(u){if(u.cosUp>.7){if(u.d<v.gap-.065)return!1}else if(u.d<.205)return!1}}return!0}function It(t){let n=null,e=-1;for(let s=0;s<=t.length;s++)s<t.length&&t[s]?e<0&&(e=s):e>=0&&((!n||s-e>n[1]-n[0])&&(n=[e,s]),e=-1);return n}function sn(t,n){let e=It(t),s=t.map(()=>!1);if(!e)return s;let i=e[0],o=e[1];for(;i>0&&n[i-1];)i--;for(;o<n.length&&n[o];)o++;for(let a=i;a<o;a++)s[a]=!0;return s}function Yn(t){return t.map(n=>n[2]-v.gap>=v.minHeight)}function rn(t){let n=t.indexOf(!0),e=t.lastIndexOf(!0);return n<0||e<=n?null:[n,e]}function Sn(t,n){let e=rn(n);return e?Math.hypot(t[e[1]][0]-t[e[0]][0],t[e[1]][1]-t[e[0]][1]):0}function zn(t,n,e=!1,s=!1){if(e)return v.minSpanTube;let i=s?rn(n):null;if(!i)return v.minSpan;let o=0;for(let a=i[0];a<=i[1];a++)o=Math.max(o,t[a][2]-v.gap);return Math.min(v.minSpan,Math.max(v.minSpanShort,o/v.maxShortAspect))}function An(t){let n=v.minHeightSquat+v.gap+.02,e=(h,l)=>{let f=t[h][2],c=t[l][2];if(!(f<n&&c>n+.001))return null;let r=(n-f)/(c-f);return t[h].map((g,M)=>g+(t[l][M]-g)*r)},s=t.length-1;for(;s>0&&t[s][2]<n;)s--;let i=s<t.length-1?e(s+1,s):null;i&&t.splice(s+1,0,i);let o=0;for(;o<t.length-1&&t[o][2]<n;)o++;let a=o>0?e(o-1,o):null;return a&&t.splice(o,0,a),t}function Pt(t,n,e,s,i){let o=t[n],a=t[n+1],h=t[n+2];return i[0]=e[0]*o+e[3]*a+e[6]*h+s.x,i[1]=e[1]*o+e[4]*a+e[7]*h+s.y,i[2]=e[2]*o+e[5]*a+e[8]*h+s.z,i}var lt=64,Xn=new WeakMap;function $n(t){let n=Xn.get(t);if(n)return n;let e=1/0,s=1/0,i=-1/0,o=-1/0;for(let x=0;x<t.length;x+=3){let p=t[x],d=t[x+1];p<e&&(e=p),p>i&&(i=p),d<s&&(s=d),d>o&&(o=d)}let a=lt/Math.max(1e-6,i-e),h=lt/Math.max(1e-6,o-s),l=x=>Math.min(lt-1,Math.max(0,Math.floor((x-e)*a))),f=x=>Math.min(lt-1,Math.max(0,Math.floor((x-s)*h))),c=x=>{let p=t[x],d=t[x+1],u=t[x+3],y=t[x+4],b=t[x+6],m=t[x+7];return[l(Math.min(p,u,b)),l(Math.max(p,u,b)),f(Math.min(d,y,m)),f(Math.max(d,y,m))]},r=new Int32Array(lt*lt+1);for(let x=0;x<t.length;x+=9){let[p,d,u,y]=c(x);for(let b=p;b<=d;b++)for(let m=u;m<=y;m++)r[b*lt+m+1]++}for(let x=0;x<lt*lt;x++)r[x+1]+=r[x];let g=new Int32Array(r[lt*lt]),M=r.slice(0,lt*lt);for(let x=0;x<t.length;x+=9){let[p,d,u,y]=c(x);for(let b=p;b<=d;b++)for(let m=u;m<=y;m++)g[M[b*lt+m]++]=x}return n={start:r,items:g,minX:e,minY:s,maxX:i,maxY:o,sx:a,sy:h},Xn.set(t,n),n}function Vn(t,n,e){if(n<t.minX||n>t.maxX||e<t.minY||e>t.maxY)return null;let s=Math.min(lt-1,Math.max(0,Math.floor((n-t.minX)*t.sx))),i=Math.min(lt-1,Math.max(0,Math.floor((e-t.minY)*t.sy)));return s*lt+i}function Kn(t,n,e,s){let i=t[n],o=t[n+1],a=t[n+2],h=t[n+3],l=t[n+4],f=t[n+5],c=t[n+6],r=t[n+7],g=t[n+8],M=(l-r)*(i-c)+(c-h)*(o-r);if(Math.abs(M)<1e-12)return null;let x=((l-r)*(e-c)+(c-h)*(s-r))/M,p=((r-o)*(e-c)+(i-c)*(s-r))/M,d=1-x-p;return x<-1e-9||p<-1e-9||d<-1e-9?null:x*a+p*f+d*g}function ht(t,n,e){if(t.length===0)return null;let s=$n(t),i=Vn(s,n,e);if(i===null)return null;let o=1/0;for(let a=s.start[i];a<s.start[i+1];a++){let h=Kn(t,s.items[a],n,e);h!==null&&h<o&&(o=h)}return o===1/0?null:o}function Rn(t,n,e){let s=[];if(t.length===0)return s;let i=$n(t),o=Vn(i,n,e);if(o===null)return s;for(let a=i.start[o];a<i.start[o+1];a++){let h=Kn(t,i.items[a],n,e);h!==null&&s.push(h)}return s}function qt(t){let n=t.length;if(n<3)return 1/0;let e=0,s=0;for(let u of t)e+=u[0],s+=u[1];e/=n,s/=n;let i=0,o=0,a=0;for(let u of t){let y=u[0]-e,b=u[1]-s;i+=y*y,o+=y*b,a+=b*b}let h=i+a,l=i*a-o*o,f=h/2+Math.sqrt(Math.max(0,h*h/4-l)),c=o,r=f-i;Math.hypot(c,r)<1e-9&&(c=1,r=0);let g=Math.hypot(c,r);c/=g,r/=g;let M=0,x=1/0,p=-1/0;for(let u of t){let y=u[0]-e,b=u[1]-s,m=y*c+b*r,A=-y*r+b*c;M+=A*A,m<x&&(x=m),m>p&&(p=m)}let d=p-x;return d<1e-9?1/0:Math.sqrt(M/n)/d}function vn(t,n,e){if(t.length<3)return null;let s=0,i=0;for(let y of t)s+=y[0],i+=y[1];s/=t.length,i/=t.length;let o=0,a=0,h=0;for(let y of t){let b=y[0]-s,m=y[1]-i;o+=b*b,a+=b*m,h+=m*m}let l=o+h,f=o*h-a*a,c=l/2+Math.sqrt(Math.max(0,l*l/4-f)),r=a,g=c-o;Math.hypot(r,g)<1e-9&&(r=1,g=0);let M=Math.hypot(r,g);r/=M,g/=M;let x=1/0,p=-1/0;for(let y of t){let b=(y[0]-s)*r+(y[1]-i)*g;b<x&&(x=b),b>p&&(p=b)}if(p-x<1e-6)return null;let d=new Array(e).fill(null);for(let y of t){let b=(y[0]-s)*r+(y[1]-i)*g,m=Math.floor((b-x)/(p-x)*e);m>=e&&(m=e-1),(!d[m]||y[2]<d[m][2])&&(d[m]=y)}let u=d.filter(Boolean);if(u.length<3)return null;for(let y=0;y<u.length;y++){let b=ht(n,u[y][0],u[y][1]);b!==null&&(u[y]=[u[y][0],u[y][1],b])}return kt(u,n),Et(u,n),u}function Et(t,n,e=1/0){for(let s=0;s<3;s++){let i=!1;for(let o=0;o<t.length-1;o++){let a=(t[o][0]+t[o+1][0])/2,h=(t[o][1]+t[o+1][1])/2,l=ht(n,a,h);if(l===null)continue;let c=(t[o][2]+t[o+1][2])/2-l;c>e||c>1e-4&&(t[o]=[t[o][0],t[o][1],t[o][2]-c],t[o+1]=[t[o+1][0],t[o+1][1],t[o+1][2]-c],i=!0)}if(!i)break}return t}function kt(t,n,e=1/0){let s=v.tip/2;for(let i=0;i<t.length;i++){let o=t[Math.max(0,i-1)],a=t[Math.min(t.length-1,i+1)],h=a[0]-o[0],l=a[1]-o[1],f=Math.hypot(h,l);if(f<1e-9)continue;let c=l/f,r=-h/f,g=t[i][2],M=g;for(let x of[-s,s]){let p=ht(n,t[i][0]+c*x,t[i][1]+r*x);p!==null&&p<M&&g-p<=e&&(M=p)}t[i]=[t[i][0],t[i][1],M]}return t}function Tt(t,n,e=.25,s=1/0){let i=v.tip/2;for(let o=0;o<3;o++){let a=new Float64Array(t.length),h=!1;for(let l=0;l<t.length-1;l++){let f=t[l],c=t[l+1],r=c[0]-f[0],g=c[1]-f[1],M=Math.hypot(r,g);if(M<1e-9)continue;let x=g/M,p=-r/M,d=Math.max(1,Math.ceil(M/e));for(let u=0;u<=d;u++){let y=u/d,b=f[0]+r*y,m=f[1]+g*y,A=f[2]+(c[2]-f[2])*y-v.gap;for(let S of[-i,0,i]){let P=ht(n,b+x*S,m+p*S);if(P===null||A-P>s)continue;let W=v.gap-(P-A);if(W<=1e-4)continue;let O=y<.5?l:l+1;W>a[O]&&(a[O]=W,h=!0)}}}if(!h)break;for(let l=0;l<t.length;l++)a[l]>0&&(t[l]=[t[l][0],t[l][1],t[l][2]-a[l]])}return t}function cn(t,n,e=1){let s=v.tip/2,i=[];for(let o=0;o<t.length;o++){let a=t[Math.max(0,o-1)],h=t[Math.min(t.length-1,o+1)],l=h[0]-a[0],f=h[1]-a[1],c=Math.hypot(l,f)||1,r=f/c,g=-l/c,M=t[o][2]-e,x=0;for(let p of[-s,0,s])for(let d of Rn(n,t[o][0]+r*p,t[o][1]+g*p))d<M&&d>x&&(x=d);i.push([t[o][0],t[o][1],x])}return i}var an=3,Ct={radius:10,dirs:8,walledMin:6,step:.5},Jn=(t,n,e)=>(n[e][2]+(t[e][2]-v.gap))/2;function $e(t,n,e,s,i,o){return pt(s,i,o,t[e][0],t[e][1],Jn(t,n,e))}function Ve(t,n,e,s,i,o){let a=t[e],h=Jn(t,n,e),l=0;for(let f=0;f<Ct.dirs;f++){let c=f/Ct.dirs*2*Math.PI,r=Math.cos(c),g=Math.sin(c);for(let M=Ct.step;M<=Ct.radius;M+=Ct.step)if(pt(s,i,o,a[0]+r*M,a[1]+g*M,h)){l++;break}}return l>=Ct.walledMin}function Ke(t,n,e,s,i,o){let a=t[e],h=t[Math.max(0,e-1)],l=t[Math.min(t.length-1,e+1)],f=l[0]-h[0],c=l[1]-h[1],r=Math.hypot(f,c);if(r<1e-9)return!0;let g=c/r,M=-f/r,x=a[2]-v.gap,p=n[e][2],d=Math.max(3,Math.ceil((x-p)/1.5));for(let u=1;u<d;u++){let y=p+(x-p)*u/d;for(let b of[.12,.24,v.sideClear]){let m=v.th/2+b;if(pt(s,i,o,a[0]+g*m,a[1]+M*m,y)||pt(s,i,o,a[0]-g*m,a[1]-M*m,y))return!1}}return!0}function Qn(t,n,e,s,i,o){let a=t.map(m=>[m[0],m[1],m[2]]);kt(a,n,an),Et(a,n,an),Tt(a,n,.25,an);let h=cn(a,n),l=0;for(let m of h)m[2]>v.gap+.5&&l++;if(l<Math.max(v.minStations,Math.ceil(h.length*.5)))return{};let f=[],c=a.map((m,A)=>h[A][2]<=v.gap+.5?!1:m[2]-v.gap-h[A][2]<v.minHeight?(f.push(\"stub\"),!1):$e(a,h,A,e,s,i)?(f.push(\"buried\"),!1):Ke(a,h,A,e,s,i)?!0:(f.push(\"blocked\"),!1)),r=It(c);if(!r||r[1]-r[0]<v.minStations){let m={};for(let S of f)m[S]=(m[S]??0)+1;let A=Object.keys(m).sort((S,P)=>m[P]-m[S])[0];return{floored:f.length*2>=h.length&&A?A:\"stub\"}}let g=a.slice(r[0],r[1]),M=h.slice(r[0],r[1]),x=Math.hypot(g[g.length-1][0]-g[0][0],g[g.length-1][1]-g[0][1]);if(x<v.minSpan)return{floored:\"stub\"};let p=o.length;if(!In(g,M,o))return o.length=p,{floored:\"degenerate\"};let d=0;for(let m=r[0];m<r[1];m++)Ve(a,h,m,e,s,i)&&d++;let u=d*2>r[1]-r[0],y=0,b=0;for(let m=0;m<g.length;m++)y=Math.max(y,g[m][2]-v.gap-M[m][2]);for(let m=p;m<o.length;m+=3){let A=o[m],S=o[m+1],P=o[m+2];b+=(A[0]*(S[1]*P[2]-S[2]*P[1])+A[1]*(S[2]*P[0]-S[0]*P[2])+A[2]*(S[0]*P[1]-S[1]*P[0]))/6}return{ok:!0,prop:{span:x,height:y,stations:g.length,volume:Math.abs(b),partAttached:!0,inBore:u,line:g.map(m=>[m[0],m[1],m[2]-v.gap])}}}function Zt(t,n,e){let{nrm:s,area:i}=t,o=Math.cos(v.splitAgreeDeg*Math.PI/180),{start:a,nbr:h}=gn(t),l=new Map;for(let g of n){let M=s[g*3],x=s[g*3+1],p=s[g*3+2];l.set(g,[e[0]*M+e[3]*x+e[6]*p,e[1]*M+e[4]*x+e[7]*p,e[2]*M+e[5]*x+e[8]*p])}let f=[...n].sort((g,M)=>i[M]-i[g]),c=new Set,r=[];for(let g of f){if(c.has(g))continue;let[M,x,p]=l.get(g);c.add(g);let d=[g],u=i[g],y=[g];for(;y.length;){let b=y.pop();for(let m=a[b];m<a[b+1];m++){let A=h[m];if(c.has(A))continue;let S=l.get(A);S&&(S[0]*M+S[1]*x+S[2]*p<o||(c.add(A),d.push(A),u+=i[A],y.push(A)))}}r.push({faces:d,area:u})}return r}function Yt(t,n,e,s,i,o=v.stationStep){let{nrm:a,area:h}=t,l=0;for(let E of n)l+=h[E];let f=l<v.tubeMinArea;if(f&&l<v.tubeSmallMinArea)return null;let c=0,r=0,g=0,M=0,x=[];for(let E of n){let N=a[E*3],U=a[E*3+1],Z=a[E*3+2],q=[e[0]*N+e[3]*U+e[6]*Z,e[1]*N+e[4]*U+e[7]*Z,e[2]*N+e[5]*U+e[8]*Z];x.push([q,h[E]]),M+=h[E],c+=q[0]*h[E],r+=q[1]*h[E],g+=q[2]*h[E]}let p=Math.hypot(c,r,g);if(p<1e-9||M<1e-9)return null;c/=p,r/=p,g/=p;let d=Math.cos(v.tubeSpreadDeg*Math.PI/180),u=0;for(let[E,N]of x)E[0]*c+E[1]*r+E[2]*g<d&&(u+=N);if(u/M<v.tubeCurvedFrac)return null;let y=[1/0,1/0],b=[-1/0,-1/0];for(let E of s)for(let N of[0,1])E[N]<y[N]&&(y[N]=E[N]),E[N]>b[N]&&(b[N]=E[N]);let m=Math.hypot(b[0]-y[0],b[1]-y[1]),A=Math.max(8,Math.min(400,Math.ceil(m/o))),S=vn(f?Je(i,s,o/2):s,i,A);if(!S||qt(S)>v.maxWander)return null;let P=0,W=0;for(let E of S)P+=E[0],W+=E[1];P/=S.length,W/=S.length;let O=0,T=0,C=0;for(let E of S){let N=E[0]-P,U=E[1]-W;O+=N*N,T+=N*U,C+=U*U}let _=O+C,I=O*C-T*T,w=_/2+Math.sqrt(Math.max(0,_*_/4-I)),k=T,z=w-O;Math.hypot(k,z)<1e-9&&(k=1,z=0);let R=Math.hypot(k,z);k/=R,z/=R;let H=1/0,F=-1/0;for(let E of S){let N=(E[0]-P)*k+(E[1]-W)*z;N<H&&(H=N),N>F&&(F=N)}if(F-H<1e-6||f&&!Qe(x,i,P,W,k,z))return null;let j=Math.max(2,Math.min(400,Math.ceil((F-H)/o))),L=[],G=[];for(let E=0;E<=j;E++){let N=H+(F-H)*E/j,U=P+k*N,Z=W+z*N,q=ht(i,U,Z);q===null?G.length&&(L.push(G),G=[]):G.push([U,Z,q])}return G.length&&L.push(G),L.filter(E=>E.length>=v.minStations)}function Je(t,n,e){let s=n.slice();for(let i=0;i<t.length;i+=9)for(let o=0;o<3;o++){let a=i+o*3,h=i+(o+1)%3*3,l=Math.hypot(t[h]-t[a],t[h+1]-t[a+1],t[h+2]-t[a+2]),f=Math.ceil(l/e);for(let c=1;c<f;c++){let r=c/f;s.push([t[a]+(t[h]-t[a])*r,t[a+1]+(t[h+1]-t[a+1])*r,t[a+2]+(t[h+2]-t[a+2])*r])}}return s}function Qe(t,n,e,s,i,o){let a=-o,h=i,l=0,f=0,c=0,r=0;for(let g=0;g<t.length;g++){let M=g*9,x=(n[M]+n[M+3]+n[M+6])/3,p=(n[M+1]+n[M+4]+n[M+7])/3,d=(x-e)*a+(p-s)*h;if(Math.abs(d)<.05)continue;let[u,y]=t[g];(u[0]*a+u[1]*h)*Math.sign(d)>0?l+=y:f+=y,d>0?c+=y:r+=y}return l>v.tubeConvexFrac*(l+f)&&Math.min(c,r)>v.tubeTwoSidedFrac*(c+r)}function Xt(t,n,e=v.stationStep,s=null,i=v.maxUnsupportedSpan,o=null){if(!t.length)return[];let a=0,h=0,l=0;for(let R of t)a+=R[0],h+=R[1],l+=R[2];a/=t.length,h/=t.length,l/=t.length;let f=0,c=0,r=0,g=0,M=0;for(let R of t){let H=R[0]-a,F=R[1]-h,j=R[2]-l;f+=H*H,c+=H*F,r+=F*F,g+=H*j,M+=F*j}let x=f*r-c*c,p=x>1e-9?(r*g-c*M)/x:0,d=x>1e-9?(f*M-c*g)/x:0,u=Math.hypot(p,d),y,b;if(o)[y,b]=o;else if(u>=v.contourSlopeMin)y=p,b=d;else{let R=1/0,H=-1/0,F=1/0,j=-1/0;for(let E of t)E[0]<R&&(R=E[0]),E[0]>H&&(H=E[0]),E[1]<F&&(F=E[1]),E[1]>j&&(j=E[1]);let L=H-R,G=j-F;if(s&&L>0&&G>0){let E=Math.max(1,e),N=Math.min(80,Math.ceil(L/E)),U=Math.min(80,Math.ceil(G/E)),Z=[];for(let X=0;X<=N;X++){let D=R+L*X/N,B=[];for(let Y=0;Y<=U;Y++){let V=F+G*Y/U,at=ht(n,D,V);B.push(at!==null&&!pt(s.topo,s.rot,s.offset,D,V,at-.1))}Z.push(B)}let q=L/N,K=G/U,Q=0,et=0;for(let X=0;X<=U;X++){let D=0;for(let B=0;B<=N;B++)D=Z[B][X]?D+1:0,D>Q&&(Q=D)}for(let X=0;X<=N;X++){let D=0;for(let B=0;B<=U;B++)D=Z[X][B]?D+1:0,D>et&&(et=D)}Q>0&&et>0&&(L=Q*q,G=et*K)}L>=G?(y=1,b=0):(y=0,b=1)}let m=Math.hypot(y,b);y/=m,b/=m;let A=-b,S=y,P=1/0,W=-1/0,O=1/0,T=-1/0;for(let R of t){let H=R[0]*y+R[1]*b,F=R[0]*A+R[1]*S;H<P&&(P=H),H>W&&(W=H),F<O&&(O=F),F>T&&(T=F)}if(W-P<1e-6)return[];let C=Math.max(2,Math.min(400,Math.ceil((W-P)/e))),_=T-O,I=Math.max(1,i),w=Math.max(1,Math.round(_/I)),k=[];for(let R=0;R<w;R++){let H=O+_*(R+.5)/w,F=[];for(let j=0;j<=C;j++){let L=P+(W-P)*j/C,G=y*L+A*H,E=b*L+S*H,N=ht(n,G,E);N===null?F.length&&(k.push(F),F=[]):F.push([G,E,N])}F.length&&k.push(F)}let z=k.filter(R=>R.length>=v.minStations);return z.spacing=w>1?_/w:0,z}var bt=.5,to=3.5,no=5,eo=1.5;function te(t,n,e){let s=0;for(let w of n)s+=t.area[w];if(s<v.tubeMinArea)return null;let i=n.map(e),o=1/0,a=1/0,h=-1/0,l=-1/0;for(let w of i)for(let k of w)k[0]<o&&(o=k[0]),k[0]>h&&(h=k[0]),k[1]<a&&(a=k[1]),k[1]>l&&(l=k[1]);o-=2*bt,a-=2*bt;let f=Math.ceil((h-o)/bt)+3,c=Math.ceil((l-a)/bt)+3;if(f*c>4e6)return null;let r=(w,k)=>k*f+w,g=(w,k)=>[Math.floor((w-o)/bt),Math.floor((k-a)/bt)],M=new Uint8Array(f*c);for(let[w,k,z]of i){let[R,H]=g(Math.min(w[0],k[0],z[0]),Math.min(w[1],k[1],z[1])),[F,j]=g(Math.max(w[0],k[0],z[0]),Math.max(w[1],k[1],z[1])),L=(k[0]-w[0])*(z[1]-w[1])-(z[0]-w[0])*(k[1]-w[1]);for(let G=H;G<=j;G++)for(let E=R;E<=F;E++){let N=o+(E+.5)*bt,U=a+(G+.5)*bt;if(Math.abs(L)<1e-12){M[r(E,G)]=1;continue}let Z=((k[0]-N)*(z[1]-U)-(z[0]-N)*(k[1]-U))/L,q=((z[0]-N)*(w[1]-U)-(w[0]-N)*(z[1]-U))/L,K=bt/Math.sqrt(Math.abs(L));Z>=-K&&q>=-K&&1-Z-q>=-K&&(M[r(E,G)]=1)}}let x=new Float32Array(f*c);for(let w=0;w<f*c;w++)x[w]=M[w]?1e9:0;let p=(w,k,z)=>{x[k]+z<x[w]&&(x[w]=x[k]+z)};for(let w=1;w<c-1;w++)for(let k=1;k<f-1;k++){let z=r(k,w);M[z]&&(p(z,z-1,3),p(z,z-f,3),p(z,z-f-1,4),p(z,z-f+1,4))}for(let w=c-2;w>0;w--)for(let k=f-2;k>0;k--){let z=r(k,w);M[z]&&(p(z,z+1,3),p(z,z+f,3),p(z,z+f+1,4),p(z,z+f-1,4))}let d=[];for(let w=0;w<f*c;w++)M[w]&&d.push(x[w]/3*bt);if(d.sort((w,k)=>w-k),!d.length||d[Math.floor(.99*(d.length-1))]>to)return null;let u=M.slice(),y=w=>[u[w-f],u[w-f+1],u[w+1],u[w+f+1],u[w+f],u[w+f-1],u[w-1],u[w-f-1]];for(let w=!0;w;){w=!1;for(let k of[0,1]){let z=[];for(let R=1;R<c-1;R++)for(let H=1;H<f-1;H++){let F=r(H,R);if(!u[F])continue;let j=y(F),L=j.reduce((E,N)=>E+N,0);if(L<2||L>6)continue;let G=0;for(let E=0;E<8;E++)!j[E]&&j[(E+1)%8]&&G++;G===1&&((k===0?j[0]*j[2]*j[4]||j[2]*j[4]*j[6]:j[0]*j[2]*j[6]||j[0]*j[4]*j[6])||z.push(F))}for(let R of z)u[R]=0;z.length&&(w=!0)}}let b=[-f-1,-f,-f+1,-1,1,f-1,f,f+1],m=[];for(let w=0;w<f*c;w++){if(!u[w])continue;let k=0;for(let z of b)u[w+z]&&k++;k>=3&&m.push(w)}if(!m.length)return null;let A=u.slice(),S=Math.ceil(eo);for(let w of m){let k=w%f,z=(w-k)/f;for(let R=-S;R<=S;R++)for(let H=-S;H<=S;H++){let F=k+H,j=z+R;F>=0&&j>=0&&F<f&&j<c&&(A[r(F,j)]=0)}}let P=new Int32Array(f*c).fill(-1),W=0;for(let w=0;w<f*c;w++){if(!A[w]||P[w]>=0)continue;let k=[w];for(P[w]=W;k.length;){let z=k.pop();for(let R of b)A[z+R]&&P[z+R]<0&&(P[z+R]=W,k.push(z+R))}W++}if(W<2)return null;let O=P.slice(),T=n.map((w,k)=>{let z=i[k],[R,H]=g((z[0][0]+z[1][0]+z[2][0])/3,(z[0][1]+z[1][1]+z[2][1])/3);return r(R,H)}),C=new Uint8Array(W),_;for(;;){let w=[];for(let z=0;z<f*c;z++)P[z]=O[z]>=0&&!C[O[z]]?O[z]:-1,P[z]>=0&&w.push(z);for(;w.length;){let z=[];for(let R of w)for(let H of b){let F=R+H;M[F]&&P[F]<0&&(P[F]=P[R],z.push(F))}w=z}_=Array.from({length:W},()=>[]),n.forEach((z,R)=>{let H=P[T[R]];H>=0&&_[H].push(z)});let k=!1;if(_.forEach((z,R)=>{z.length&&z.reduce((H,F)=>H+t.area[F],0)<v.tubeSmallMinArea&&(C[R]=1,k=!0)}),!k)break}let I=_.filter(w=>w.length);return I.length<no?null:(I.lost=n.length-I.reduce((w,k)=>w+k.length,0),I)}function ne(t,n,e,s,i,o,a){let h=[];for(let l of e){let f=new Float64Array(l.length*9),c=[],r=0;l.forEach((x,p)=>{let d=s(x);r+=t.area[x];for(let u=0;u<3;u++)f.set(d[u],p*9+u*3),c.push(d[u]);c.push([0,1,2].map(u=>(d[0][u]+d[1][u]+d[2][u])/3))});let g=oo(c),M=Yt(t,l,n,c,f,i);if(M?.length){let p=r<v.tubeMinArea?Zt(t,l,n).map(d=>Object.assign(d,{strut:!0,axis:g})):null;h.push({faces:l,area:r,region:o,tris:a,reach:f,lines:M,smallTube:p,strut:!0});continue}h.push({faces:l,area:r,region:o,tris:a,strut:!0,axis:g})}return h}function oo(t){let n=0,e=0;for(let g of t)n+=g[0],e+=g[1];n/=t.length,e/=t.length;let s=0,i=0,o=0;for(let g of t){let M=g[0]-n,x=g[1]-e;s+=M*M,i+=M*x,o+=x*x}let a=s+o,h=s*o-i*i,l=a/2+Math.sqrt(Math.max(0,a*a/4-h)),f=i,c=l-s;Math.hypot(f,c)<1e-9&&(f=s>=o?1:0,c=s>=o?0:1);let r=Math.hypot(f,c);return[f/r,c/r]}var so=.1,ie=5,io=.95,ee=.1;function re(t,n,e,s,i,o){let a=[],h=Xt(t,n,s,o,i);a.spacing=h.spacing;for(let l of h){let f=cn(l,e),c=[],r=null;for(let g=0;g<l.length;g++){let M=f[g][2]>v.gap+.5;r!==null&&M!==r&&(c.length>=v.minStations&&a.push(c),c=[]),r=M,c.push(l[g])}c.length>=v.minStations&&a.push(c)}return a}function ae(t,n){let e=[];for(let[s,i]of[[0,n[0]],[n[1],t.length]]){if(i-s<v.minStations)continue;let o=t.slice(s,i);o.from=s,e.push(o)}return e}function ro(t,n,e){let{pos:s}=t,i=n.offset,o=[0,0,0],a=[];return n.regions.forEach((h,l)=>{for(let f of h.faces){let c=0,r=0,g=0;for(let M=0;M<3;M++)Pt(s,f*9+M*3,e,i,o),c+=o[0]/3,r+=o[1]/3,g+=o[2]/3;a.push([c,r,g,t.area[f],l])}}),a}function Dt(t,n){let e=v.maxUnsupportedSpan/2,s=0;for(let[i,o,a,h]of t)for(let l of n){let f=a-l[2];if(f>=-.05&&f<=1.5&&Math.hypot(l[0]-i,l[1]-o)<=e){s+=h;break}}return s}var fn=t=>t.flatMap(n=>n.line??[]),oe=t=>t.squat?v.squatBrimW:t.partAttached?v.th/2:Bt(t.height??0),Ot=t=>Math.min(1/0,...t.map(n=>n.grip??1/0));function ao(t,n){let e=(i,o,a)=>{let h=a[0]-o[0],l=a[1]-o[1],f=h*h+l*l,c=f>1e-12?Math.max(0,Math.min(1,((i[0]-o[0])*h+(i[1]-o[1])*l)/f)):0;return Math.hypot(i[0]-o[0]-c*h,i[1]-o[1]-c*l)},s=1/0;for(let[i,o]of[[t,n],[n,t]])for(let a of i){o.length===1&&(s=Math.min(s,Math.hypot(a[0]-o[0][0],a[1]-o[0][1])));for(let h=0;h+1<o.length;h++)s=Math.min(s,e(a,o[h],o[h+1]))}return s}var se=t=>{let n=new Map;for(let e of t)n.has(e.region)||n.set(e.region,[]),n.get(e.region).push(e);return n};function ce(t,n,e,s,i,o,a){let h=ro(t,n,e),l=co(h,n,i);if(!l.size)return i;let f=globalThis.__TINECAP,c=f?f.slice():null;f&&(globalThis.__TINECAP=[]);let r=a(l);s.rasterVeto&&(r.props=r.props.filter(x=>!s.rasterVeto(x,i.props)));let g=globalThis.__TINECAP;globalThis.__TINECAP=f;let M=ho(h,n,i,r);if(!M.rasterRegions)return i;if(f){f.length=o;for(let x of M.props)x.caps&&f.push(...(x.raster?g:c).slice(...x.caps))}return M}function co(t,n,e){let s=fn(e.props),i=new Set;for(let o=0;o<n.regions.length;o++){let a=t.filter(l=>l[4]===o),h=0;for(let l of a)h+=l[3];h>0&&Dt(a,s)<io*h&&i.add(o)}return i}function fo(t,n){let e=1/0,s=1/0,i=-1/0,o=-1/0;for(let h of t)h[4]===n&&(e=Math.min(e,h[0]),i=Math.max(i,h[0]),s=Math.min(s,h[1]),o=Math.max(o,h[1]));let a=2*v.maxUnsupportedSpan;return t.filter(h=>h[0]>=e-a&&h[0]<=i+a&&h[1]>=s-a&&h[1]<=o+a)}function lo(t,n,e){let s=[...n],i=[],o=e.filter(l=>!Dt([l],fn(s))),a=l=>s.every(f=>ao(l.line??[],f.line??[])>=oe(l)+oe(f)+1),h=[...t].sort((l,f)=>Ot([l])-Ot([f]));for(let l of h){if(!l.line?.length||!a(l))continue;let f=Dt(o,l.line)>=ie,c=Ot([l])<Ot(s)-ee;if(!(!f&&!c)){i.push(l),s.push(l);for(let r=o.length-1;r>=0;r--)Dt([o[r]],l.line)&&o.splice(r,1)}}return Ot(s)>Ot(t)+ee?null:i}function ho(t,n,e,s){let i=se(e.props),o=se(s.props),a=[],h=[],l=new Set,f=0,c=0,r=0,g=e.sagRisk,M=(x,p)=>{let d=[];for(let[u,y]of p.triRanges){let b=a.length;for(let m=u;m<y;m++)a.push(x.triangles[m]);d.push([b,a.length])}h.push({...p,id:h.length,triRanges:d,raster:x===s}),l.add(p.region),f+=p.tines??0,c+=p.volume??0};for(let x=0;x<n.regions.length;x++){let p=i.get(x)??[],d=o.get(x)??[],u=null;if(d.length){let y=fo(t,x),b=p.length?Dt(y,fn(p)):0;Dt(y,fn(d))>b+Math.max(ie,so*b)&&(u=lo(p,d,y))}if(!u){for(let y of p)M(e,y);continue}r++,g||=s.sagRegions?.has(x)??!1;for(let y of d)M(s,y);for(let y of u)M(e,y)}return{triangles:a,props:h,skipped:e.skipped,served:l.size,servedRegions:[...l],tines:f,sagRisk:g,volume:c,rasterRegions:r}}function fe(t,n,e,s){let{out:i,props:o,skipped:a,served:h,tally:l}=t,f=()=>({out:i.length,props:o.length,tines:l.tines,skipped:{...a},sagRisk:l.sagRisk,served:new Set(h)}),c=d=>{i.length=d.out,o.length=d.props,l.tines=d.tines,l.sagRisk=d.sagRisk,Object.assign(a,d.skipped),h.clear();for(let u of d.served)h.add(u)},r=d=>{let u=d.tube;d.start&&c(d.start);let y=i.length-u.mark.out;for(let b of u.tris)i.push(b);for(let b of u.props)o.push({...b,triRanges:b.triRanges.map(([m,A])=>[m+y,A+y])});l.tines+=u.end.tines-u.mark.tines;for(let b in a)a[b]+=u.end.skipped[b]-u.mark.skipped[b];l.sagRisk||=u.end.sagRisk,u.props.length?h.add(d.region):a.sliver+=d.slivers},g=null,M=(d,u,y)=>{let b=v.maxUnsupportedSpan;if(!g){g=[];for(let C of e.regions)for(let _ of C.faces){let I=_*9;g.push([(s[I]+s[I+3]+s[I+6])/3,(s[I+1]+s[I+4]+s[I+7])/3,(s[I+2]+s[I+5]+s[I+8])/3,n.area[_]])}}let m=d.reach??d.tris,A=1/0,S=1/0,P=-1/0,W=-1/0;for(let C=0;C<m.length;C+=3)A=Math.min(A,m[C]),P=Math.max(P,m[C]),S=Math.min(S,m[C+1]),W=Math.max(W,m[C+1]);let O=2*b,T=0;for(let[C,_,I,w]of g)if(!(C<A-O||C>P+O||_<S-O||_>W+O))for(let k=y;k<u.length;k++){let z=u[k];if(z[2]>I-3&&z[2]<I+.5&&Math.hypot(z[0]-C,z[1]-_)<=b){T+=w;break}}return T},x=null,p=d=>{let u=d?.rival;!u||d!==u.last||(u.tube.held>M(u,i,u.start.out)+1e-6?r(u):a.sliver+=u.slivers)};return{begin(d){return p(x),x=d,d.rival&&!d.rival.start&&(d.rival.start=f()),d.smallTube?f():null},tubeDone(d,u,y){let b={mark:u,end:f(),tris:i.slice(u.out),props:o.slice(u.props),held:M(d,i,u.out)};c(u);let m=d.smallTube.filter(S=>S.area>=12),A={last:m[m.length-1],tube:b,faces:d.faces,tris:d.tris,reach:d.reach,slivers:d.smallTube.length-m.length,region:d.region,start:null};m.length||r(A);for(let S of m)S.region=d.region,S.tris=d.tris,S.rival=A,y.push(S)},finish(){p(x)}}}function le(t,n,e){let s=[],i=[],o=n+v.squatBrimH;for(let a=0;a<t.length;a++){let h=t[a],l=t[Math.max(0,a-1)],f=t[Math.min(t.length-1,a+1)],c=f[0]-l[0],r=f[1]-l[1],g=Math.hypot(c,r);if(g<1e-9)return!1;c/=g,r/=g;let M=r,x=-c,p=h[2]-v.gap;if(p-n<v.minHeightSquat)return!1;let u=Math.max(p-v.tipH,o+.05),y=(b,m)=>[h[0]+M*b,h[1]+x*b,m];s.push([y(+v.th/2,n),y(+v.th/2,u),y(+v.tip/2,p),y(-v.tip/2,p),y(-v.th/2,u),y(-v.th/2,n)]),i.push([y(+v.squatBrimW,n),y(+v.squatBrimW,o),y(-v.squatBrimW,o),y(-v.squatBrimW,n)])}return xt(s,e),xt(i,e),!0}function kn(t,n,e,s,i,o,a=null){let l=[],f=g=>g[2]-v.gap-0,c=t.map((g,M)=>{let x=f(g);return x>=v.minHeightSquat&&x<v.minHeight&&!(a&&a[M])&&Lt(t,M,e,s,i)}),r=0;for(;r<c.length;){if(!c[r]){r++;continue}let g=r;for(;g<c.length&&c[g];)g++;let M=t.slice(r,g).map(P=>[P[0],P[1],P[2]]);if(r=g,M.length<v.minStations||Math.hypot(M[M.length-1][0]-M[0][0],M[M.length-1][1]-M[0][1])<v.minSpanSquat)continue;Tt(M,n);let p=M.map((P,W)=>{let O=f(P);return O>=v.minHeightSquat&&O<v.minHeight&&Ut(M,W,e,s,i)}),d=It(p);if(!d||d[1]-d[0]<v.minStations)continue;let u=M.slice(d[0],d[1]),y=Math.hypot(u[u.length-1][0]-u[0][0],u[u.length-1][1]-u[0][1]);if(y<v.minSpanSquat)continue;let b=o.length;if(!le(u,0,o)){o.length=b;continue}let m=Gt(e,s,i,o.slice(b),.25);if(m&&(m.cosUp>.7?m.d<v.gap-.065:m.d<.205)){o.length=b;continue}let A=Math.max(...u.map(P=>P[2]))-v.gap,S=0;for(let P=b;P<o.length;P+=3){let W=o[P],O=o[P+1],T=o[P+2];S+=(W[0]*(O[1]*T[2]-O[2]*T[1])+W[1]*(O[2]*T[0]-O[0]*T[2])+W[2]*(O[0]*T[1]-O[1]*T[0]))/6}l.push({span:y,height:A-0,stations:u.length,volume:Math.abs(S),squat:!0,line:u.map(P=>[P[0],P[1],P[2]-v.gap]),triRange:[b,o.length]})}return l}function uo(t,n,e,s){let i=(O,T)=>[O[0]-T[0],O[1]-T[1],O[2]-T[2]],o=(O,T)=>O[0]*T[0]+O[1]*T[1]+O[2]*T[2],a=i(e,n),h=i(s,n),l=i(t,n),f=o(a,l),c=o(h,l);if(f<=0&&c<=0)return o(l,l);let r=i(t,e),g=o(a,r),M=o(h,r);if(g>=0&&M<=g)return o(r,r);let x=i(t,s),p=o(a,x),d=o(h,x);if(d>=0&&p<=d)return o(x,x);let u=f*M-g*c;if(u<=0&&f>=0&&g<=0){let O=f/(f-g),T=[n[0]+O*a[0],n[1]+O*a[1],n[2]+O*a[2]],C=i(t,T);return o(C,C)}let y=p*c-f*d;if(y<=0&&c>=0&&d<=0){let O=c/(c-d),T=[n[0]+O*h[0],n[1]+O*h[1],n[2]+O*h[2]],C=i(t,T);return o(C,C)}let b=g*d-p*M;if(b<=0&&M-g>=0&&p-d>=0){let O=(M-g)/(M-g+(p-d)),T=[e[0]+O*(s[0]-e[0]),e[1]+O*(s[1]-e[1]),e[2]+O*(s[2]-e[2])],C=i(t,T);return o(C,C)}let m=1/(b+y+u),A=y*m,S=u*m,P=[n[0]+a[0]*A+h[0]*S,n[1]+a[1]*A+h[1]*S,n[2]+a[2]*A+h[2]*S],W=i(t,P);return o(W,W)}function po(t,n,e,s,i,o){let a=t.pos,h=t.nrm,l=t.nFaces,f=[s,i,o],c=p=>[n[0]*a[p]+n[3]*a[p+1]+n[6]*a[p+2]+e.x,n[1]*a[p]+n[4]*a[p+1]+n[7]*a[p+2]+e.y,n[2]*a[p]+n[5]*a[p+1]+n[8]*a[p+2]+e.z],r=new Float64Array(l),g=1/0;for(let p=0;p<l;p++){let d=p*9;r[p]=uo(f,c(d),c(d+3),c(d+6)),r[p]<g&&(g=r[p])}if(!(g<1/0))return[];let M=g*1e-9+1e-12,x=[];for(let p=0;p<l;p++){if(r[p]>g+M)continue;let d=h[p*3],u=h[p*3+1],y=h[p*3+2],b=n[0]*d+n[3]*u+n[6]*y,m=n[1]*d+n[4]*u+n[7]*y,A=-b,S=-m,P=Math.hypot(A,S);P<.34||x.push({x:A/P,y:S/P})}return x}function mo(t,n,e){let s=0,i=0;for(let f=0;f<n.length-1&&!(s||i);f++)for(let c of[e+f,e-f]){if(c<0||c>=n.length-1)continue;let r=n[c+1][0]-n[c][0],g=n[c+1][1]-n[c][1];if(Math.hypot(r,g)>1e-9){s=r,i=g;break}}let o=Math.hypot(s,i);if(o<1e-9)return t;s/=o,i/=o;let a=[[s,i],[-s,-i],[-i,s],[i,-s]],h=[],l=new Set;for(let f of t){let c=a.map(([r,g],M)=>({i:M,x:r,y:g,dot:r*f.x+g*f.y})).filter(r=>r.dot>1e-6).sort((r,g)=>g.dot-r.dot);for(let r of c)l.has(r.i)||(l.add(r.i),h.push({x:r.x,y:r.y}))}return h.concat(t)}function $t(t){let n=Math.max(0,Math.min(1,t??1));return v.tineStepSparse-n*(v.tineStepSparse-v.tineStep)}function Ft(t,n,e,s,i,o,a=v.tineStep,h=v.baseH+.2,l=v.tineH,f=null){if(t.length<2)return 0;let c=[0];for(let _=1;_<t.length;_++)c.push(c[_-1]+Math.hypot(t[_][0]-t[_-1][0],t[_][1]-t[_-1][1]));let r=f?c[f[0]]:0,M=(f?c[f[1]]:c[c.length-1])-r;if(!(M>0))return 0;let x=Math.min(a,M/v.minGripTines);if(M<x)return 0;let p=v.tineW/2,d=0,u=_=>{let I=_+r,w=0;for(;w<c.length-1&&c[w+1]<I;)w++;let k=Math.max(1e-9,c[w+1]-c[w]),z=(I-c[w])/k,R=t[w][0]+(t[w+1][0]-t[w][0])*z,H=t[w][1]+(t[w+1][1]-t[w][1])*z,F=t[w][2]+(t[w+1][2]-t[w][2])*z;if(F-v.gap<h)return!1;let L=Math.round(F/l)*l,G=L-l,E=F-l/2,N=mo(po(e,s,i,R,H,E),t,w).find(D=>pt(e,s,i,R+D.x*v.tineBite,H+D.y*v.tineBite,E));if(!N)return!1;let U=N.x,Z=N.y,q=-Z,K=U,Q=[R,H,0],et=(D,B,Y)=>[Q[0]+U*D+q*B,Q[1]+Z*D+K*B,Y],X=[[-v.tineOverlap,-p],[v.tineBite,-p],[v.tineBite,p],[-v.tineOverlap,p]];return en(X,G,L,et,o),globalThis.__TINECAP&&globalThis.__TINECAP.push({x:R,y:H,z:E,biteX:U,biteY:Z}),d++,!0},y=c[c.length-1],b=t[0][2]<=t[t.length-1][2],m=_=>u((b?_:y-_)-r),A=Math.min(v.tineEdgeBand,y/2),S=Math.min(x*v.tineMidFactor,v.tineStepSparse),P=y-Math.min(x/2,v.tineTopClear),W=Math.min(t[0][2],t[t.length-1][2]),O=Math.max(t[0][2],t[t.length-1][2]);if(!f&&O-W<v.tineSlopeMin){for(let _=x/2;_<y;)u(_),_+=Math.min(_,y-_)<=A?x:S;return d}let T=Math.min(v.tineW,x/2),C=Math.min(p,x/2);for(;C<=P&&!m(C);)C+=T;for(;;){let _=Math.min(C,y-C)<=A||y-(C+S)<=A;if(C+=_?x:S,C>P)break;m(C)}return d}function he(){return{triangles:[],props:[],served:0,volume:0,skipped:{noLine:0,wanders:0,stub:0,blocked:0,degenerate:0,buried:0,weld:0,sliver:0}}}var go=30;function xo(t){let n=Math.max(0,Math.min(1,t)),e=v.maxUnsupportedSpan;return n<=.5?e+(.5-n)/.5*(go-e):e-(n-.5)/.5*(e/2)}function ue(t,n,e,s={}){let i=globalThis.__TINECAP?.length??0,o=Tn(t,n,e,s,null);return s.raster===!1?o:ce(t,n,e,s,o,i,a=>Tn(t,n,e,s,a))}function pe(t,n,e,s,i){return Tn(t,n,e,{...s,onlyRegions:i,shortWalls:!0},null)}function Tn(t,n,e,s,i){let{pos:o}=t,a=s.step??v.stationStep,h=Math.max(0,Math.min(1,s.coverage??.5)),l=xo(h),f=l>v.maxUnsupportedSpan+.5,c=0,r=n.offset,g=s.tines===!0,M=s.onlyRegions??null,x=s.shortWalls===!0,p={tines:0,sagRisk:!1,sagRegions:new Set},d=[],u=[],y=0,b={noLine:0,wanders:0,stub:0,blocked:0,degenerate:0,buried:0,weld:0,sliver:0},m=[0,0,0],A=new Float64Array(o.length),S=1/0,P=-1/0,W=1/0,O=-1/0,T=1/0,C=-1/0;for(let F=0;F<t.nFaces;F++)for(let j=0;j<3;j++)Pt(o,F*9+j*3,e,r,m),A[F*9+j*3]=m[0],A[F*9+j*3+1]=m[1],A[F*9+j*3+2]=m[2],m[0]<S&&(S=m[0]),m[0]>P&&(P=m[0]),m[1]<W&&(W=m[1]),m[1]>O&&(O=m[1]),m[2]<T&&(T=m[2]),m[2]>C&&(C=m[2]);let _=$t(s.tineDensity),I=s.layerHeight??v.tineH,w=()=>globalThis.__TINECAP?.length??null,k=F=>{let j=1/0;for(let L=F;L<d.length;L++)d[L][2]<j&&(j=d[L][2]);return j},z=[];for(let F=0;F<n.regions.length;F++){if(M&&!M.has(F))continue;let j=n.regions[F].faces,L=new Float64Array(j.length*9),G=[],E=0;for(let q=0;q<j.length;q++){E+=t.area[j[q]];let K=0,Q=0,et=0;for(let X=0;X<3;X++)Pt(o,j[q]*9+X*3,e,r,m),L[q*9+X*3]=m[0],L[q*9+X*3+1]=m[1],L[q*9+X*3+2]=m[2],G.push([m[0],m[1],m[2]]),K+=m[0],Q+=m[1],et+=m[2];G.push([K/3,Q/3,et/3])}if(i){if(!i.has(F))continue;let q=et=>[0,1,2].map(X=>Pt(o,et*9+X*3,e,r,[0,0,0])),K=te(t,j,q);if(K){K.lost&&b.sliver++,z.push(...ne(t,e,K,q,a,F,L));continue}let Q=re(G,L,A,a,l,{topo:t,rot:e,offset:r});f&&Q.spacing>v.maxUnsupportedSpan&&p.sagRegions.add(F),Q.length&&z.push({faces:j,area:E,region:F,tris:L,lines:Q});continue}let N=Yt(t,j,e,G,L,a),U=E<v.tubeMinArea,Z=U||!N?.length?Zt(t,j,e):null;if(N&&N.length){z.push({faces:j,area:E,region:F,tris:L,lines:N,smallTube:U?Z:null});continue}for(let q of Z){if(q.area<12){b.sliver++;continue}q.region=F,q.tris=L,z.push(q)}}let R=new Set,H=fe({out:d,props:u,skipped:b,served:R,tally:p},t,n,A);for(let F of z){let j=F.tris,L=H.begin(F),G;if(F.lines)G=F.lines;else{let E=[],N=new Float64Array(F.faces.length*9);for(let U=0;U<F.faces.length;U++){let Z=F.faces[U],q=0,K=0,Q=0;for(let et=0;et<3;et++)Pt(o,Z*9+et*3,e,r,m),E.push([m[0],m[1],m[2]]),N[U*9+et*3]=m[0],N[U*9+et*3+1]=m[1],N[U*9+et*3+2]=m[2],q+=m[0],K+=m[1],Q+=m[2];E.push([q/3,K/3,Q/3])}G=Xt(E,N,a,{topo:t,rot:e,offset:r},l,F.axis??null),f&&G.length&&G.spacing>v.maxUnsupportedSpan&&(p.sagRisk=!0)}if(!G.length){b.noLine++;continue}for(let E=0;E<G.length;E++){let N=G[E],U=d.length,Z=Qn(N,A,t,e,r,d);if(Z.ok&&F.smallTube){let tt=Gt(t,e,r,d.slice(U),.25);if(tt&&(tt.cosUp>.7?tt.d<v.gap-.065:tt.d<.205)){d.length=U,b.weld++;continue}}if(Z.ok){R.add(F.region);let tt=0,nt=w(),st=d.length;if(g){let yt=Z.prop.line.map(gt=>[gt[0],gt[1],gt[2]+v.gap]);tt=Ft(yt,A,t,e,r,d,_,void 0,I),p.tines+=tt}u.push({...Z.prop,area:F.area,region:F.region,tines:tt,caps:nt===null?void 0:[nt,w()],grip:k(st),trimmed:N.length-Z.prop.stations,id:y++,kind:\"prop\",triRanges:[[U,d.length]]});continue}if(Z.floored){b[Z.floored]++;continue}kt(N,j),Et(N,j),An(N).length&&kt(N,j);let q=N.map(tt=>[tt[0],tt[1],tt[2]]),K=N.map(()=>!1),Q=()=>{for(let tt of kn(q,j,t,e,r,d,K)){let nt=d.length,st=w(),yt=g?Ft(tt.line.map(dt=>[dt[0],dt[1],dt[2]+v.gap]),j,t,e,r,d,_,v.squatBrimH,I):0;p.tines+=yt,R.add(F.region);let gt=[tt.triRange];d.length>nt&&gt.push([nt,d.length]),u.push({...tt,area:F.area,region:F.region,tines:yt,caps:st===null?void 0:[st,w()],grip:k(nt),id:y++,kind:\"prop\",triRanges:gt})}};if(qt(N)>v.maxWander){b.wanders++,Q();continue}let et=N.map((tt,nt)=>tt[2]-v.gap>=v.minHeightSquat&&Lt(N,nt,t,e,r)),X=sn(N.map((tt,nt)=>et[nt]&&tt[2]-v.gap>=v.minHeight),et),D=It(X);if(!D||D[1]-D[0]<v.minStations){b.blocked++,Q();continue}if(i&&!F.strut)for(let tt of ae(N,D)){G.push(tt);for(let nt=tt.from;nt<tt.from+tt.length;nt++)K[nt]=!0}let B=N.slice(D[0],D[1]),Y=Yn(B);if(Sn(B,Y)<zn(B,Y,!!F.smallTube,x)){b.stub++,Q();continue}Tt(B,j);let V=B.map((tt,nt)=>tt[2]-v.gap>=v.minHeightSquat&&Ut(B,nt,t,e,r)),at=B.map((tt,nt)=>V[nt]&&tt[2]-v.gap>=v.minHeight),mt=!1,ft=null;for(let tt=0;tt<4&&!mt;tt++){let nt=It(sn(at,V));if(!nt||nt[1]-nt[0]<v.minStations){ft=\"blocked\";break}let st=B.slice(nt[0],nt[1]),yt=Math.hypot(st[st.length-1][0]-st[0][0],st[st.length-1][1]-st[0][1]),gt=Y.slice(nt[0],nt[1]);if(Sn(st,gt)<zn(st,gt,!!F.smallTube,x)){ft=\"stub\";break}let dt=d.length;if(!wn(st,c,d,v.minHeightSquat)){d.length=dt,ft=\"degenerate\";break}let wt=Gt(t,e,r,d.slice(dt),.25);if(wt&&(wt.cosUp>.7?wt.d<v.gap-.065:wt.d<.205)){d.length=dt;let ct=0,Ht=1/0;for(let ut=0;ut<st.length;ut++){let Qt=st[ut][0]-wt.x,tn=st[ut][1]-wt.y;Qt*Qt+tn*tn<Ht&&(Ht=Qt*Qt+tn*tn,ct=ut)}let Mt=nt[0]+ct;for(let ut of[Math.max(0,Mt-1),Mt,Math.min(V.length-1,Mt+1)])V[ut]=!1,at[ut]=!1;ft=\"weld\";continue}let Jt=Math.max(...st.map(ct=>ct[2]))-v.gap,On=0;for(let ct=dt;ct<d.length;ct+=3){let Ht=d[ct],Mt=d[ct+1],ut=d[ct+2];On+=(Ht[0]*(Mt[1]*ut[2]-Mt[2]*ut[1])+Ht[1]*(Mt[2]*ut[0]-Mt[0]*ut[2])+Ht[2]*(Mt[0]*ut[1]-Mt[1]*ut[0]))/6}R.add(F.region);let Dn=w(),He=d.length,Wn=g?Ft(st,j,t,e,r,d,_,void 0,I,rn(gt)):0;p.tines+=Wn,u.push({region:F.region,tines:Wn,caps:Dn===null?void 0:[Dn,w()],grip:k(He),span:yt,height:Jt-c,area:F.area,stations:st.length,trimmed:N.length-st.length,volume:Math.abs(On),line:st.map(ct=>[ct[0],ct[1],ct[2]-v.gap]),id:y++,kind:\"prop\",triRanges:[[dt,d.length]]});for(let ct=D[0]+nt[0];ct<D[0]+nt[1];ct++)K[ct]=!0;mt=!0}!mt&&ft&&b[ft]++,Q()}F.smallTube&&H.tubeDone(F,L,z)}return H.finish(),{triangles:d,props:u,skipped:b,served:R.size,servedRegions:[...R],tines:p.tines,sagRisk:p.sagRisk,...i?{sagRegions:p.sagRegions}:{},volume:u.reduce((F,j)=>F+j.volume,0)}}var $={maxLeanDeg:30,minFaceH:30,minTopFrac:.4,minPartH:30,thMin:1.2,thPerMm:.004,thMax:2.4,reach:.15,topDepth:4,minDepth:8,maxDepth:60,topClear:1,minRibH:20,footH:.6,footHalf:3,footPad:3,gap:.2,bite:.3,tineW:.5,tineOverlap:.3,tineSpanMax:1.5,tineSpacing:6,minTines:3,minGripShare:.3,stiltMax:40,stiltMaxFrac:.4,pitch:100,maxPerFace:4,endInset:10,maxFaces:4,minBearingSep:60,nudges:[0,3,-3,6,-6,10,-10],clearance:1,levelStep:10,wallHalf:3.6},ge=()=>Math.sin($.maxLeanDeg*Math.PI/180);function yo(t={}){let n=(e,s)=>Number.isFinite(e)?e:s;return{tines:t.tines!==!1,layerH:Math.max(.04,n(t.layerHeight,.2)),gap:n(t.gap,$.gap),bite:n(t.bite,$.bite),gripFrom:Math.max(0,n(t.gripFrom,0)),spacing:Math.max(1,n(t.tineSpacing,$.tineSpacing)),reach:Math.max(.05,Math.min(.5,n(t.reach,$.reach))),allowStilt:t.allowStilt===!0}}function Mo(t,n,e){let{pos:s,nFaces:i}=t,o=new Float64Array(i*9);for(let a=0;a<o.length;a+=3){let h=s[a],l=s[a+1],f=s[a+2];o[a]=n[0]*h+n[3]*l+n[6]*f+e.x,o[a+1]=n[1]*h+n[4]*l+n[7]*f+e.y,o[a+2]=n[2]*h+n[5]*l+n[8]*f+e.z}return o}function Vt(t,n){let e=[];for(let s=0;s<t.length;s++){let i=t[s],o=t[(s+1)%t.length],a=n(i),h=n(o);if(a>=0&&e.push(i),a>=0!=h>=0){let l=a/(a-h);e.push(i.map((f,c)=>f+(o[c]-f)*l))}}return e}function bo(t,n){let e=0;for(let s=0;s<t.length;s+=3){let i=t[s],o=t[s+1],a=t[s+2];e+=i[0]*(o[1]*a[2]-o[2]*a[1])+i[1]*(o[2]*a[0]-o[0]*a[2])+i[2]*(o[0]*a[1]-o[1]*a[0])}if(e<0)for(let s=0;s<t.length;s+=3)n.push(t[s],t[s+2],t[s+1]);else for(let s of t)n.push(s)}function Fn(t,n,e,s,i){let o=t.length,a=t.map(([f,c])=>s(f,c,n)),h=t.map(([f,c])=>s(f,c,e)),l=[];for(let f=0;f<o;f++){let c=(f+1)%o;l.push(a[f],a[c],h[c],a[f],h[c],h[f])}for(let f=1;f<o-1;f++)l.push(h[0],h[f],h[f+1],a[0],a[f+1],a[f]);bo(l,i)}function wo(t){let n={x:t.n.x/t.h,y:t.n.y/t.h};return{nh:n,toWorld:(i,o,a)=>[n.x*i+t.u.x*o,n.y*i+t.u.y*o,a],sOf:i=>i[0]*n.x+i[1]*n.y}}function me(t,n,e,s,i){let o=1/0,a=[[0,0,0],[0,0,0],[0,0,0]];for(let h=0;h<t.length;h+=9){let l=1/0,f=-1/0;for(let r=0;r<3;r++){let g=t[h+r*3],M=t[h+r*3+1],x=t[h+r*3+2];a[r][0]=g*n.nh.x+M*n.nh.y,a[r][1]=g*n.uDir.x+M*n.uDir.y,a[r][2]=x,a[r][1]<l&&(l=a[r][1]),a[r][1]>f&&(f=a[r][1])}if(f<e||l>s)continue;let c=a.map(r=>r.slice());c=Vt(c,r=>r[1]-e),c=Vt(c,r=>s-r[1]);for(let r of i){if(c.length<2)break;c=Vt(c,r)}if(!(c.length<2))for(let r of c)r[2]<o&&(o=r[2])}return o}function Io(t,n,e,s,i,o,a={}){let h=yo(a);if(Math.abs(t.n.z)>ge())return{ok:!1,reason:\"that face leans too far to stand a brace against \\u2014 pick an upright side\"};let l={...wo(t),uDir:{x:t.u.x,y:t.u.y}},f=1/0,c=-1/0;for(let R=Math.max(0,t.z0);R<=t.z1+1e-6;R+=1)St(t,n,zt(t,0,R))!==null&&(R<f&&(f=R),R>c&&(c=R));if(c===-1/0)return{ok:!1,reason:\"there is no face under that spot to brace\"};let r=c-$.topClear;if(r<$.minRibH)return{ok:!1,reason:`that face only reaches ${c.toFixed(0)}mm up \\u2014 too short to need a brace`};let g=R=>Math.min($.thMax,$.thMin+$.thPerMm*R),M=g(r),x=R=>{let H=-1/0;for(let F=0;F<t.tris.length;F+=9){let j=[[t.tris[F],t.tris[F+1],t.tris[F+2]],[t.tris[F+3],t.tris[F+4],t.tris[F+5]],[t.tris[F+6],t.tris[F+7],t.tris[F+8]]];j=Vt(j,L=>L[0]-(n-R)),j=Vt(j,L=>n+R-L[0]);for(let L of j)L[2]>H&&(H=L[2])}return H===-1/0?0:H},p=0,d=0,u=0,y=0,b=()=>{M=g(r),p=x(M/2+$.tineW)+h.gap;let R=l.sOf(Rt(t,p,n,zt(t,p,0))),H=l.sOf(Rt(t,p,n,zt(t,p,1)));d=R,u=H-R,y=Math.max($.minDepth,Math.min($.maxDepth,h.reach*r))},m=R=>d+u*R,A=R=>y+($.topDepth-y)*Math.min(1,Math.max(0,R/r)),S=1/0;for(let R=0;R<6;R++){b();let H=[F=>F[2]+.1,F=>r+.3-F[2],F=>F[0]-(m(F[2])-.05),F=>m(F[2])+A(F[2])+.1-F[0]];if(S=me(e,l,n-M/2-.3,n+M/2+.3,H),S===1/0||(r=S-1,r<$.minRibH))break}if(S!==1/0)return{ok:!1,reason:\"the part sticks out over that spot, so a brace standing on the plate can\\u2019t reach up the face\"};let P=m(0)+y+$.footPad,W=M/2+$.footHalf;if(me(e,l,n-W-.2,n+W+.2,[R=>R[2]+.1,R=>$.footH+.2-R[2],R=>R[0]-(m(0)-.05),R=>P+.1-R[0]])!==1/0)return{ok:!1,reason:\"the part\\u2019s base spreads out under this face, so there is no room on the plate for the brace\\u2019s foot\"};let T=[],C=(R,H,F)=>l.toWorld(R,F,H);Fn([[m(0),0],[m(0)+y,0],[m(r)+$.topDepth,r],[m(r),r]],n-M/2,n+M/2,C,T),Fn([[m(0),n-W],[P,n-W],[P,n+W],[m(0),n+W]],0,$.footH,(R,H,F)=>l.toWorld(R,H,F),T);let _=0,I=1/0,w=0;if(h.tines){let R=Math.max(f+.5,h.gripFrom,$.footH+.5),H=Math.min(c,r)-.5;for(let L=R;L<=H+1e-6;L+=h.spacing){let G=Math.round(L/h.layerH)*h.layerH,E=G+h.layerH;if(E>r)break;let N=G+h.layerH/2,U=St(t,n,zt(t,0,N));if(U===null)continue;let Z=St(t,n,zt(t,U,N));Z!==null&&(U=Z);let q=l.sOf(Rt(t,U,n,zt(t,U,N))),K=m(N);if(K-q>$.tineSpanMax)continue;let Q=l.toWorld(q-h.bite/2,n,N);pt(s,i,o,Q[0],Q[1],Q[2])&&(Fn([[q-h.bite,G],[K+$.tineOverlap,G],[K+$.tineOverlap,E],[q-h.bite,E]],n-$.tineW/2,n+$.tineW/2,C,T),_++,G<I&&(I=G))}let F=Math.max($.minTines,Math.floor($.minGripShare*(H-R)/h.spacing));if(_<F)return{ok:!1,reason:\"too little of this face lines up with the brace for its tines to grip \\u2014 try a flatter part of the side\"};w=Math.max(0,I-Math.max($.footH,h.gripFrom));let j=Math.min($.stiltMax,$.stiltMaxFrac*r);if(!h.allowStilt&&w>j)return{ok:!1,reason:`this side only starts ${I.toFixed(0)}mm up, so the brace would stand ${w.toFixed(0)}mm holding nothing before it grips (max ${j.toFixed(0)}mm) \\u2014 rotate so this side reaches the plate`}}let k=[l.toWorld(m(0),n,0),l.toWorld(P,n,0)],z=[];for(let R=0;z.push({z:R,a:l.toWorld(m(R),n,R),b:l.toWorld(m(R)+A(R),n,R)}),!(R>=r);R=Math.min(r,R+$.levelStep));return{ok:!0,tris:T,tines:_,height:r,depth:y,th:M,stilt:w,foot:k,halfW:W,levels:z}}function ln(t,n,e,s){let i=(c,r,g)=>{let M=g[0]-r[0],x=g[1]-r[1],p=M*M+x*x,d=p>0?Math.max(0,Math.min(1,((c[0]-r[0])*M+(c[1]-r[1])*x)/p)):0;return Math.hypot(c[0]-r[0]-M*d,c[1]-r[1]-x*d)},o=(c,r,g)=>(r[0]-c[0])*(g[1]-c[1])-(r[1]-c[1])*(g[0]-c[0]),a=o(t,n,e),h=o(t,n,s),l=o(e,s,t),f=o(e,s,n);return a>0!=h>0&&l>0!=f>0?0:Math.min(i(t,e,s),i(n,e,s),i(e,t,n),i(s,t,n))}function So(t,n){for(let e of n){if(!e?.foot)continue;if(ln(t.foot[0],t.foot[1],e.foot[0],e.foot[1])<t.halfW+e.halfW+$.clearance)return!0;let s=(t.th+e.th)/2+$.clearance,i=Math.min(t.height,e.height);for(let o of t.levels){if(o.z>i)break;let a=zo(e.levels,o.z);if(ln(o.a,o.b,a.a,a.b)<s)return!0}}return!1}function zo(t,n){let e=0;for(;e<t.length-2&&t[e+1].z<n;)e++;let s=t[e],i=t[Math.min(e+1,t.length-1)],o=i.z>s.z?Math.max(0,Math.min(1,(n-s.z)/(i.z-s.z))):0,a=(h,l)=>[h[0]+(l[0]-h[0])*o,h[1]+(l[1]-h[1])*o];return{a:a(s.a,i.a),b:a(s.b,i.b)}}function Ao(t,n){if(!n?.length)return!1;let e=t.halfW+$.wallHalf+$.clearance;for(let s of n)if(!(!Array.isArray(s)||s.length<1)){if(s.length===1){let i=s[0];if(ln(t.foot[0],t.foot[1],i,i)<e)return!0;continue}for(let i=1;i<s.length;i++)if(ln(t.foot[0],t.foot[1],s[i-1],s[i])<e)return!0}return!1}function Ro(t){return t?Array.isArray(t)?{braces:t,walls:[]}:{braces:t.braces??[],walls:t.walls??[]}:{braces:[],walls:[]}}var vo=So;function Po(t,n){let e=-1/0;for(let s=Math.max(0,t.z0);s<=t.z1+1e-6;s+=2)St(t,n,zt(t,0,s))!==null&&(e=s);return e}function ko(t){let n=t.u1-t.u0,e=Math.max(1,Math.min($.maxPerFace,Math.round(n/$.pitch))),s=Math.min($.endInset,n/4),i=t.u0+s,o=t.u1-s,a=Math.max(1,Math.min(24,Math.round((o-i)/5))),h=(i+o)/2,l=[];for(let r=0;r<=a;r++){let g=i+(o-i)*r/a;l.push({u:g,top:Po(t,g),edge:Math.abs(g-h)})}l.sort((r,g)=>g.top-r.top||g.edge-r.edge);let f=Math.max($.pitch/2,n/(e+1)),c=[];for(let r of l){if(c.length>=e||r.top===-1/0)break;c.some(g=>Math.abs(g-r.u)<f)||c.push(r.u)}return c.length?c:[h]}function de(t,n,e,s={}){let i=u=>({triangles:[],count:0,tines:0,skipped:0,ribs:[],reason:u}),o=Mo(t,e,n.offset),a=0;for(let u=2;u<o.length;u+=3)o[u]>a&&(a=o[u]);if(a<$.minPartH)return i(`the part is only ${a.toFixed(0)}mm tall, too short to sway`);let h=ge(),l=jt(t,e,n.offset).filter(u=>Math.abs(u.n.z)<=h&&u.z1-Math.max(0,u.z0)>=$.minFaceH&&u.z1>=$.minTopFrac*a).map(u=>({p:u,score:(u.z1-Math.max(0,u.z0))*(u.u1-u.u0),bearing:Math.atan2(u.n.y,u.n.x)})).sort((u,y)=>y.score-u.score);if(!l.length)return i(\"no upright side is tall and flat enough to brace in this pose\");let f=$.minBearingSep*Math.PI/180,c=(u,y)=>{let b=Math.abs(u-y)%(2*Math.PI);return Math.min(b,2*Math.PI-b)},r=[];for(let u of l){if(r.length>=$.maxFaces)break;r.some(y=>c(y.bearing,u.bearing)<f)||r.push(u)}let{walls:g}=Ro(s.avoid),M=[],x=[],p=0,d=0;for(let{p:u}of r)for(let y of ko(u)){let b=null;for(let m of $.nudges){let A=y+m;if(A<u.u0||A>u.u1)continue;let S=Io(u,A,o,t,e,n.offset,s);if(S.ok&&!vo(S,M)&&!Ao(S,g)){b=S;break}}if(!b){d++;continue}M.push(b),b.triRange=[x.length,x.length+b.tris.length];for(let m of b.tris)x.push(m);p+=b.tines}return{triangles:x,count:M.length,tines:p,skipped:d,ribs:M,reason:M.length?null:\"the upright sides are blocked by other parts of the model in this pose\"}}var it={tineH:.2,tineBite:.3,padH:.5,padMargin:4,padSegs:48,padMinArea:60,coverExtraSparse:88,coverSparse:55,coverDense:22,coverDefault:.5};var rt={cell:1.2,grab:.05,style:\"auto\",custom:{h:.5,gap:0,grip:.05,margin:4},brimGap:.12,minGripOutline:20,brimCell:.1};function To(t,n){let e=[],s=0;for(let i=0;i<t.length;i+=9){let o=[];for(let a=0;a<3;a++){let h=i+a*3,l=i+(a+1)%3*3,f=t[h+2]-n,c=t[l+2]-n;if(f<0==c<0)continue;let r=f/(f-c);o.push([t[h]+r*(t[l]-t[h]),t[h+1]+r*(t[l+1]-t[h+1])])}o.length===2&&(e.push(o[0][0],o[0][1],o[1][0],o[1][1]),s+=Math.hypot(o[1][0]-o[0][0],o[1][1]-o[0][1]))}return{segs:e,length:s}}function ye(t,n,e,s=it.tineH){if(t.length<3)return null;let i=0,o=0;for(let G of t)i+=G[0],o+=G[1];i/=t.length,o/=t.length;let a=0,h=0,l=0;for(let G of t){let E=G[0]-i,N=G[1]-o;a+=E*E,h+=E*N,l+=N*N}let f=a+l,c=a*l-h*h,r=f/2+Math.sqrt(Math.max(0,f*f/4-c)),g=h,M=r-a;Math.hypot(g,M)<1e-9&&(g=1,M=0);let x=Math.hypot(g,M);g/=x,M/=x;let p=-M,d=g,u=0,y=0;for(let G of t){let E=G[0]-i,N=G[1]-o;u=Math.max(u,Math.abs(E*g+N*M)),y=Math.max(y,Math.abs(E*p+N*d))}let b=rt.style===\"custom\"?rt.custom.margin:it.padMargin,m=u+b,A=y+b,S=rt.grab,P=(G,E)=>{let N=ht(n,G,E);return N===null?it.padH:Math.max(.05,Math.min(it.padH,N+S))},W={cx:i,cy:o,ax:g,ay:M,bx:p,by:d,r1:m,r2:A},O=Number.isFinite(s)&&s>.05?s:it.tineH,T=To(n,O/2),C=T.length<rt.minGripOutline,_=G=>G&&Object.assign(G,{smallFoot:C,outline:T.length});if(rt.style===\"light\"||rt.style===\"auto\"&&!C)return _(xe(n,t,W,O,rt.brimGap,0,O,T.segs,e));if(rt.style===\"custom\"){let G=rt.custom;return _(xe(n,t,W,G.h,G.gap,G.grip,O,T.segs,e))}let w=rt.style===\"auto\";w&&(S=Math.max(rt.grab,0));let k=it.padSegs,z=Math.max(2,Math.ceil(Math.max(m,A)/rt.cell)),R=(G,E)=>{let N=2*Math.PI*E/k,U=m*G*Math.cos(N),Z=A*G*Math.sin(N);return[i+g*U+p*Z,o+M*U+d*Z]},H=[];for(let G=0;G<=z;G++){let E=G/z,N=[];for(let U=0;U<k;U++){let[Z,q]=R(E,U);N.push([Z,q,P(Z,q)])}H.push(N)}let F=H[0][0],j=(G,E,N)=>e.push(G,E,N),L=0;for(let G of H)for(let E of G)E[2]>L&&(L=E[2]);for(let G=0;G<k;G++){let E=(G+1)%k;j(F,H[1][G],H[1][E]);for(let X=1;X<z;X++)j(H[X][G],H[X+1][G],H[X+1][E]),j(H[X][G],H[X+1][E],H[X][E]);let N=[i,o,0],U=(X,D)=>[H[X][D][0],H[X][D][1],0];j(N,U(1,E),U(1,G));for(let X=1;X<z;X++)j(U(X,G),U(X+1,E),U(X+1,G)),j(U(X,G),U(X,E),U(X+1,E));let Z=z,q=H[Z][G],K=H[Z][E],Q=[q[0],q[1],0],et=[K[0],K[1],0];j(q,Q,et),j(q,et,K)}return{r1:m,r2:A,cells:k*z,height:L,points:t.length,oval:!0,style:\"sure\",autoSure:w,smallFoot:C,outline:T.length}}function xe(t,n,e,s,i,o,a,h,l){let{cx:f,cy:c,ax:r,ay:g,bx:M,by:x,r1:p,r2:d}=e,u=Number.isFinite(s)&&s>.05?s:it.tineH,y=rt.brimCell,b=a/2,m=[];if(i>0)for(let D=0;D<16;D++)m.push([i*Math.cos(D*Math.PI/8),i*Math.sin(D*Math.PI/8)]);let A=.5,S=Math.max(i,0)+2*y,P=new Map,W=(D,B)=>D*73856093^B*19349663;for(let D=0;D<h.length;D+=4){let B=h[D],Y=h[D+1],V=h[D+2],at=h[D+3];for(let mt=Math.floor((Math.min(B,V)-S)/A);mt<=Math.floor((Math.max(B,V)+S)/A);mt++)for(let ft=Math.floor((Math.min(Y,at)-S)/A);ft<=Math.floor((Math.max(Y,at)+S)/A);ft++){let tt=W(mt,ft),nt=P.get(tt);nt||P.set(tt,nt=[]),nt.push(B,Y,V,at)}}let O=(D,B)=>{let Y=P.get(W(Math.floor(D/A),Math.floor(B/A))),V=S*S;if(Y)for(let ft=0;ft<Y.length;ft+=4){let tt=Y[ft],nt=Y[ft+1],st=Y[ft+2]-tt,yt=Y[ft+3]-nt,gt=st*st+yt*yt,dt=gt>0?Math.max(0,Math.min(1,((D-tt)*st+(B-nt)*yt)/gt)):0,wt=tt+dt*st-D,Jt=nt+dt*yt-B;V=Math.min(V,wt*wt+Jt*Jt)}let at=Math.sqrt(V),mt=ht(t,D,B);return mt!==null&&mt<b?-at:at},T=Math.max(.01,b-.05)/y,C=(D,B)=>{let Y=ht(t,D,B)??1/0;for(let[at,mt]of m)Y=Math.min(Y,ht(t,D+at,B+mt)??1/0);let V=i>0?b+T*(O(D,B)-i):1/0;return Math.max(.05,Math.min(u,Y+o,V))},_=(D,B)=>[f+r*D+M*B,c+g*D+x*B],I=(D,B)=>{let[Y,V]=_(D,B);return[Y,V,C(Y,V)]},w=Math.max(3,Math.ceil(2*p/y)+1),k=D=>d*Math.sqrt(Math.max(0,1-(D/p)**2)),z=0;for(let D=-p+.25;D<p;D+=.5){let B=k(D);if(!(B<y))for(let Y=0;Y<=B;Y+=y)for(let V of[1,-1]){let[at,mt]=_(D,V*Y);C(at,mt)<u-1e-9&&(z=Math.max(z,Y/B))}}let R=y/d,H=Math.max(R,1.2/d),F=Math.min(1,z+2*R),j=new Set([-1,1]);for(let D=0;D<=F+1e-12;D+=R)j.add(+Math.min(D,1).toFixed(9)),j.add(-Math.min(+D.toFixed(9),1));for(let D=F+H;D<1;D+=H)j.add(+D.toFixed(9)),j.add(-+D.toFixed(9));let L=[...j].sort((D,B)=>D-B),G=L.length,E=[];for(let D=1;D<w-1;D++){let B=-p+2*p*D/(w-1),Y=k(B);E.push(L.map(V=>I(B,Y*V)))}let N=I(-p,0),U=I(p,0),Z=[],q=(D,B,Y)=>Z.push(D,B,Y);for(let D=0;D<G-1;D++){q(N,E[0][D+1],E[0][D]);let B=E[E.length-1];q(U,B[D],B[D+1])}for(let D=0;D<E.length-1;D++){let B=E[D],Y=E[D+1];for(let V=0;V<G-1;V++)q(B[V],Y[V],Y[V+1]),q(B[V],Y[V+1],B[V+1])}let K=[N,...E.map(D=>D[G-1]),U,...E.map(D=>D[0]).reverse()],Q=K.map(D=>[D[0],D[1],0]),et=[f,c,0];for(let D=0;D<K.length;D++){let B=(D+1)%K.length;q(K[D],Q[D],Q[B]),q(K[D],Q[B],K[B]),q(et,Q[B],Q[D])}let X=0;for(let D=0;D<Z.length;D+=3){let[B,Y,V]=[Z[D],Z[D+1],Z[D+2]];X+=B[0]*(Y[1]*V[2]-Y[2]*V[1])-B[1]*(Y[0]*V[2]-Y[2]*V[0])+B[2]*(Y[0]*V[1]-Y[1]*V[0])}for(let D=0;D<Z.length;D+=3)X<0?l.push(Z[D],Z[D+2],Z[D+1]):l.push(Z[D],Z[D+1],Z[D+2]);return{r1:p,r2:d,cells:E.length*G,height:u,points:n.length,oval:!0,style:rt.style===\"custom\"?\"custom\":\"light\"}}function hn(t,n,e){let{pos:s,nFaces:i}=t,{x:o,y:a,z:h}=e,l=new Float64Array(i*9);for(let f=0;f<i;f++)for(let c=0;c<3;c++){let r=f*9+c*3,g=s[r],M=s[r+1],x=s[r+2];l[r]=n[0]*g+n[3]*M+n[6]*x+o,l[r+1]=n[1]*g+n[4]*M+n[7]*x+a,l[r+2]=n[2]*g+n[5]*M+n[8]*x+h}return l}function Me(t,n,e){let{pos:s,nFaces:i,area:o}=t,{x:a,y:h,z:l}=n.offset,f=0,c=0,r=0,g=[];for(let M=0;M<i;M++){let x=0,p=0;for(let d=0;d<3;d++){let u=M*9+d*3,y=s[u],b=s[u+1],m=s[u+2],A=e[0]*y+e[3]*b+e[6]*m+a,S=e[1]*y+e[4]*b+e[7]*m+h,P=e[2]*y+e[5]*b+e[8]*m+l;x+=A,p+=S,P<.35&&g.push([A,S])}f+=x/3*o[M],c+=p/3*o[M],r+=o[M]}return r>0&&(f/=r,c/=r),{pts:g,mx:f,my:c}}var Fo=2;function be(t,n){let e=1/0,s=-1/0,i=1/0,o=-1/0;for(let[l,f]of n)l<e&&(e=l),l>s&&(s=l),f<i&&(i=f),f>o&&(o=f);let a=n.length?Math.hypot(s-e,o-i):0;return{kind:t.bedArea>=it.padMinArea?\"face\":a<Fo?\"point\":\"edge\",span:a,bedArea:t.bedArea}}var ot={th:1.2,gap:.2,footHalf:3,footH:.6,pitch:24,tStep:1.5,minH:2,inset:2,maxRow:14,minArea:500,minWidth:22};function we(t,n){let e=0;for(let s=0;s<t.length;s+=3){let i=t[s],o=t[s+1],a=t[s+2];e+=(i[0]*(o[1]*a[2]-o[2]*a[1])+i[1]*(o[2]*a[0]-o[0]*a[2])+i[2]*(o[0]*a[1]-o[1]*a[0]))/6}if(e<0)for(let s=0;s<t.length;s+=3)n.push(t[s],t[s+2],t[s+1]);else for(let s of t)n.push(s)}function Ho(t,n,e,s){let i=t.length,o=t.map(l=>[l[0]-n.x*e,l[1]-n.y*e,l[2]]),a=t.map(l=>[l[0]+n.x*e,l[1]+n.y*e,l[2]]),h=[];for(let l=0;l<i;l++){let f=(l+1)%i;h.push(o[l],o[f],a[f],o[l],a[f],a[l])}for(let l=1;l<i-1;l++)h.push(a[0],a[l],a[l+1],o[0],o[l+1],o[l]);we(h,s)}var _o=.05;function Eo(t,n,e,s,i){let o=[],a=[];for(let h=0;h<t.length;h++){let l=t[Math.max(0,h-1)],f=t[Math.min(t.length-1,h+1)],c=f[0]-l[0],r=f[1]-l[1],g=Math.hypot(c,r);if(g<1e-9){o.length=0;break}c/=g,r/=g;let M=r,x=-c,p=t[h],d=p[2],u=(y,b)=>[p[0]+M*y,p[1]+x*y,b];a.push([u(+s,0),u(+s,d),u(-s,d),u(-s,0)]),o.push({p,sx:M,sy:x,top:d,ztip:d-_o,bot:0,botTip:ot.footH,taperBot:!1})}o.length&&Nt(o,a,i,{th:ot.th,tip:ot.th,minStations:3})||Ho(n,e,s,i)}function Co(t,n,e,s,i=null){let o=n[0]-t[0],a=n[1]-t[1],h=Math.hypot(o,a);if(h<1e-6)return;let l=o/h,f=a/h,c=ot.th/2+ot.footHalf,r=h/2+ot.footHalf,g=(t[0]+n[0])/2,M=(t[1]+n[1])/2,x=(A,S,P)=>[g+l*A+e.x*S,M+f*A+e.y*S,P],p=-r,d=r;if(i){let S=ot.footH+ot.gap,P=c+ot.gap,W=Math.ceil(2*r/.1),O=Math.ceil(2*P/.1),T=I=>{for(let w=0;w<=O;w++){let[k,z]=x(I,-P+2*P*w/O,0),R=ht(i,k,z);if(R!==null&&R<S)return!1}return!0},C=null,_=null;for(let I=0;I<=W;I++){let w=-r+2*r*I/W,k=T(w);if(k&&_===null&&(_=w),_!==null&&(!k||I===W)){let z=k?w:w-2*r/W;(!C||z-_>C[1]-C[0])&&(C=[_,z]),_=null}}if(!C||C[1]-C[0]<ot.th)return;p=C[0]>-r?C[0]+ot.gap:C[0],d=C[1]<r?C[1]-ot.gap:C[1]}let u=[[p,-c],[d,-c],[d,c],[p,c]],y=u.map(([A,S])=>x(A,S,0)),b=u.map(([A,S])=>x(A,S,ot.footH)),m=[];for(let A=0;A<4;A++){let S=(A+1)%4;m.push(y[A],y[S],b[S],y[A],b[S],b[A])}for(let A=1;A<3;A++)m.push(b[0],b[A],b[A+1],y[0],y[A+1],y[A]);we(m,s)}function Oo(t,n){let e=Math.max(2,Math.ceil((t.t1-t.t0)/ot.tStep)),s=!1,i=!1,o=0,a=0;for(let h=0;h<=e;h++){let l=t.t0+(t.t1-t.t0)*h/e,f=St(t,n,l);if(f===null){s&&(i=!0);continue}if(i){a++;continue}s=!0;let c=Bn(t,f,l)-ot.gap;c>o&&(o=c)}return o>=ot.minH&&a<2}function Ie(t,n,e,s){let i=e-n;if(i<=0)return[];let o=Math.max(2,Math.ceil(i/1)),a=[];for(let c=0;c<=o;c++)a.push(Oo(t,n+i*c/o));let h=[],l=-1;for(let c=0;c<=o;c++)if(a[c]&&l<0&&(l=c),l>=0&&(!a[c]||c===o)){let r=a[c]?c:c-1;r>=l&&h.push([n+i*l/o,n+i*r/o]),l=-1}h.length||h.push([n,e]);let f=[];for(let[c,r]of h){let g=r-c,M=Math.max(1,Math.min(ot.maxRow,Math.round(g/s)));for(let x=0;x<M;x++)f.push(M===1?(c+r)/2:c+g*x/(M-1))}return f.slice(0,ot.maxRow)}function Hn(t,n,e,s,i={}){let o={x:t.u.x,y:t.u.y},a=ot.th/2,h=t.u0+ot.inset,l=t.u1-ot.inset;if(l-h<=0)return{triangles:[],tines:0,count:0,wedges:[]};let f=[],c=[],r=0,g=0,M=null;for(let x of Ie(t,h,l,i.pitch??ot.pitch)){let p=[],d=Math.max(2,Math.ceil((t.t1-t.t0)/ot.tStep));for(let m=0;m<=d;m++){let A=t.t0+(t.t1-t.t0)*m/d,S=St(t,x,A);if(S===null){if(p.length)break;continue}let P=Rt(t,S,x,A);P[2]>.3&&p.push(P)}if(p.length<2)continue;let u=p.map(m=>[m[0],m[1],m[2]-ot.gap]);if(Math.max(...u.map(m=>m[2]))<ot.minH)continue;let y=[[u[0][0],u[0][1],0],...u,[u[u.length-1][0],u[u.length-1][1],0]],b=f.length;if(Eo(u,y,o,a,f),M??=hn(n,e,s),Co([u[0][0],u[0][1],0],[u[u.length-1][0],u[u.length-1][1],0],o,f,M),i.tines!==!1&&(r+=Ft(p,null,n,e,s,f,$t(i.tineDensity))),f.length>b){g++;let m=Math.max(...u.map(S=>S[2])),A=Math.hypot(p[p.length-1][0]-p[0][0],p[p.length-1][1]-p[0][1]);c.push({triRange:[b,f.length],line:p,height:m,span:A})}}return{triangles:f,tines:r,count:g,wedges:c}}function un(t,n){if(!n||!n.length)return!1;let e=1/0,s=-1/0,i=1/0,o=-1/0;for(let h of[t.u0,t.u1])for(let l of[t.t0,t.t1]){let f=Rt(t,0,h,l);f[0]<e&&(e=f[0]),f[0]>s&&(s=f[0]),f[1]<i&&(i=f[1]),f[1]>o&&(o=f[1])}let a=8;for(let h of n)for(let l of h.line??[])if(!(!h.squat&&l[2]<v.minHeight)&&l[0]>=e-a&&l[0]<=s+a&&l[1]>=i-a&&l[1]<=o+a)return!0;return!1}function Se(t,n,e,s,i){let o=null,a=[];return(h,l)=>(l!==o&&(o=l,a=t.filter(f=>!un(f,l)&&Hn(f,n,e,s,i).count>0)),a.some(f=>un(f,[h])))}function ze(t,n,e,s,i){return _n(t,n,e,s,i).length}function _n(t,n,e,s,i){let{pos:o}=t,a=e.offset,h=new Set(s),l=v.maxUnsupportedSpan*v.maxUnsupportedSpan,f=[];return e.regions.forEach((c,r)=>{if(!h.has(r)){for(let g of c.faces){let M=0,x=0,p=0;for(let d=0;d<3;d++){let u=o[g*9+d*3],y=o[g*9+d*3+1],b=o[g*9+d*3+2];M+=(n[0]*u+n[3]*y+n[6]*b+a.x)/3,x+=(n[1]*u+n[4]*y+n[7]*b+a.y)/3,p+=(n[2]*u+n[5]*y+n[8]*b+a.z)/3}for(let d of i)if(d[2]>p-3&&d[2]<p+.5&&(d[0]-M)**2+(d[1]-x)**2<=l)return}f.push(r)}}),f}function Ae(t,n,e){let s=[1/0,1/0,1/0,-1/0,-1/0,-1/0];for(let i=n;i<e;i++){let o=t[i];for(let a=0;a<3;a++)o[a]<s[a]&&(s[a]=o[a]),o[a]>s[a+3]&&(s[a+3]=o[a])}return s}var Do=(t,n,e)=>t[0]-e<=n[3]&&n[0]<=t[3]+e&&t[1]-e<=n[4]&&n[1]<=t[4]+e&&t[2]-e<=n[5]&&n[2]<=t[5]+e;function Re(t,n,e,s,i,o,a){let h=_n(t,e,n,i,o),l={triangles:[],props:[],served:[],tines:0};if(!h.length)return l;let f=pe(t,n,e,s,new Set(h));if(!f.props.length)return l;let c=[];for(let p=0;p<a.length;p+=3)c.push(Ae(a,p,p+3));let r=[],g=[],M=new Set,x=0;for(let p of f.props){let d=p.triRanges??[],u=d.reduce((b,[m,A])=>{let S=Ae(f.triangles,m,A);return[0,1,2].map(P=>Math.min(b[P],S[P])).concat([3,4,5].map(P=>Math.max(b[P],S[P])))},[1/0,1/0,1/0,-1/0,-1/0,-1/0]);if(c.some(b=>Do(u,b,v.sideClear)))continue;let y=d.map(([b,m])=>{let A=r.length;for(let S=b;S<m;S++)r.push(f.triangles[S]);return[A,r.length]});c.push(u),g.push({...p,triRanges:y,short:!0}),M.add(p.region),x+=p.tines??0}return{triangles:r,props:g,served:[...M],tines:x}}function Wo(t){let n=Math.max(0,Math.min(1,t));return n<=.5?it.coverSparse+(.5-n)/.5*(it.coverExtraSparse-it.coverSparse):it.coverSparse-(n-.5)/.5*(it.coverSparse-it.coverDense)}function jo(t){if(!t)return;let n=(e,s,i)=>{Number.isFinite(i)&&(e[s]=i)};if(n(it,\"tineBite\",t.tineBite),n(it,\"padH\",t.padH),n(rt,\"grab\",t.padGrab),[\"auto\",\"light\",\"sure\",\"custom\"].includes(t.padStyle)&&(rt.style=t.padStyle),t.padCustom)for(let e of Object.keys(rt.custom))n(rt.custom,e,t.padCustom[e]);n(v,\"gap\",t.propGap),n(ot,\"gap\",t.propGap),bn.includes(t.cutout)&&(J.pattern=t.cutout)}function ve(t,n,e,s={}){let i=Go(t,n,e,s);return i.floating=Nn(t,n,e),i}function Go(t,n,e,s={}){jo(s.tunables);let i=Pe(t,n,e,s);if(!s.sway?.on)return i;let o=(i.fins??[]).map(r=>r.line).filter(r=>Array.isArray(r)&&r.length),a=de(t,n,e,{...s.sway,tines:s.tines,layerHeight:s.layerHeight,avoid:{walls:o}}),h=i.triangles.length,l=i.fins??[],f=l.reduce((r,g)=>Math.max(r,(g.id??-1)+1),l.length),c=a.ribs.map(r=>({height:r.height,length:r.depth,tines:r.tines,rows:0,stilt:0,lean:0,bearing:0,site:null,id:f++,kind:\"sway\",triRanges:[[r.triRange[0]+h,r.triRange[1]+h]],line:r.foot,span:r.depth}));return{...i,triangles:[...i.triangles,...a.triangles],fins:[...l,...c],sway:{count:a.count,tines:a.tines,skipped:a.skipped,reason:a.reason,braces:(a.ribs??[]).map(r=>({foot:r.foot,halfW:r.halfW,th:r.th,height:r.height,levels:r.levels}))}}}function Pe(t,n,e,s={}){let i=s.mode??\"prop\",o=[];if(i===\"auto\"||i===\"stabilize\"){let c=s.tines??!0,r=Math.max(0,Math.min(1,s.coverage??it.coverDefault)),g=Wo(r),M=jt(t,e,n.offset).filter(I=>I.n.z<-.05&&I.area>=ot.minArea&&I.u1-I.u0>=ot.minWidth),x={tines:c,pitch:g,tineDensity:s.tineDensity},p=Pe(t,n,e,{...s,mode:\"prop\",tines:c,rasterVeto:Se(M,t,e,n.offset,x)}),d=[],u=[],y=0,b=0;for(let I of M){if(un(I,p.props))continue;let w=Hn(I,t,e,n.offset,x);if(!w.count)continue;let k=d.length;for(let z of w.triangles)d.push(z);for(let z of w.wedges)u.push({triRange:[z.triRange[0]+k,z.triRange[1]+k],line:z.line,height:z.height,span:z.span});y+=w.tines,b++}let m=u.length,S=s.lastResort===!1||p.seating?.kind===\"point\"&&!p.pad?{triangles:[],props:[],served:[],tines:0}:Re(t,n,e,{...s,tines:c,coverage:r},p.servedRegions??[],d,[...p.triangles,...d]),P=p.triangles.length,W=p.props.map((I,w)=>({...p.fins[w]??{},id:I.id??w,kind:\"prop\",triRanges:I.triRanges??[],line:I.line,span:I.span,height:I.height})),O=W.length;for(let I of u)W.push({height:I.height,length:I.span,tines:0,rows:0,stilt:0,lean:0,bearing:0,site:null,id:O++,kind:\"wedge\",triRanges:[[I.triRange[0]+P,I.triRange[1]+P]],line:I.line,span:I.span});let T=P+d.length,C=S.props.map(I=>({...I,triRanges:I.triRanges.map(([w,k])=>[w+T,k+T])}));for(let I of C)W.push({height:I.height,length:I.span,tines:I.tines??0,rows:0,stilt:0,lean:0,bearing:0,site:null,id:O++,kind:\"prop\",short:!0,triRanges:I.triRanges,line:I.line,span:I.span});let _=[...p.servedRegions??[],...S.served];return{...p,mode:i,triangles:[...p.triangles,...d,...S.triangles],props:[...p.props,...C],servedRegions:_,fins:W,tines:(p.tines??0)+y+S.tines,braceCount:c?W.length:m,propCount:c?0:p.fins.length+S.props.length,unserved:b?ze(t,e,n,_,d):(p.unserved??0)-S.served.length}}let a=Me(t,n,e),h=be(n,a.pts),l=(s.bedPad??!0)&&n.bedArea<it.padMinArea?ye(a.pts,hn(t,e,n.offset),o,s.layerHeight):null,f=h.kind===\"point\"&&!l?he():ue(t,n,e,s);return{triangles:f.triangles,padTriangles:o,pad:l,mode:i,fins:f.props.map(c=>({height:c.height,length:c.span,tines:0,rows:0,stilt:0,lean:0,bearing:0,site:null,id:c.id,kind:c.kind??\"prop\",triRanges:c.triRanges??[],line:c.line,span:c.span})),props:f.props,volume:f.volume,skipped:f.skipped,rejected:{blocked:f.skipped.blocked,tooFewTines:0,sites:n.regions.length,tried:n.regions.length},patchCount:0,patchStats:{},tines:f.tines??0,servedRegions:f.servedRegions??[],unserved:n.regions.length-f.served,sagRisk:f.sagRisk??!1,seating:h,tip:null}}var En=Object.freeze({mode:\"auto\",bedPad:!0,tines:!0,tineDensity:0,coverage:.5,layerHeight:.2,threshold:45});function Cn(t,n={}){let e={...En,...n},s=t instanceof Float32Array||t instanceof Float64Array?t:Float64Array.from(t);if(s.length===0||s.length%9!==0)throw new Error(`positions must be a non-empty triangle soup (9 floats/face), got ${s.length}`);let i=1/0,o=-1/0,a=1/0,h=-1/0,l=1/0;for(let b=0;b<s.length;b+=3){let m=s[b],A=s[b+1],S=s[b+2];m<i&&(i=m),m>o&&(o=m),A<a&&(a=A),A>h&&(h=A),S<l&&(l=S)}let f=(i+o)/2,c=(a+h)/2,r=1e6,g=new Float64Array(s.length);for(let b=0;b<s.length;b+=3)g[b]=Math.round((s[b]-f)*r)/r,g[b+1]=Math.round((s[b+1]-c)*r)/r,g[b+2]=Math.round((s[b+2]-l)*r)/r;let M=jn({getAttribute:b=>b===\"position\"?{array:g}:null}),x=Gn(M,e.threshold,_t),p=ve(M,x,_t,{mode:e.mode,bedPad:e.bedPad,tines:e.tines,tineDensity:e.tineDensity,layerHeight:e.layerHeight,coverage:e.coverage}),d=ke(p.triangles),u=ke(p.padTriangles||[]),y=new Float32Array(d.length+u.length);return y.set(d,0),y.set(u,d.length),{triangles:y,offset:{x:x.offset.x-f,y:x.offset.y-c,z:x.offset.z-l},stats:{overhangRegions:x.regions.length,finTriangles:d.length/9,padTriangles:u.length/9,braces:p.braceCount??0,tines:p.tines??0,unserved:p.unserved??null,floating:p.floating?.length??0,floatingDrop:p.floating?.[0]?.drop??0}}}function ke(t){if(!t||t.length===0)return new Float32Array(0);if(typeof t[0]==\"number\")return Float32Array.from(t);let n=new Float32Array(t.length*3),e=0;for(let s of t)Array.isArray(s)||ArrayBuffer.isView(s)?(n[e++]=s[0],n[e++]=s[1],n[e++]=s[2]):(n[e++]=s.x,n[e++]=s.y,n[e++]=s.z);return n.subarray(0,e)}var Wt=\"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/\",Kt=new Uint8Array(256);for(let t=0;t<Wt.length;t++)Kt[Wt.charCodeAt(t)]=t;function Te(t){let n=0;t.endsWith(\"==\")?n=2:t.endsWith(\"=\")&&(n=1);let e=t.length/4*3-n,s=new Uint8Array(e),i=0;for(let o=0;o<t.length;o+=4){let a=Kt[t.charCodeAt(o)],h=Kt[t.charCodeAt(o+1)],l=Kt[t.charCodeAt(o+2)],f=Kt[t.charCodeAt(o+3)],c=a<<18|h<<12|l<<6|f;i<e&&(s[i++]=c>>16&255),i<e&&(s[i++]=c>>8&255),i<e&&(s[i++]=c&255)}return s}function Fe(t){let n=[],e=\"\";for(let s=0;s<t.length;s+=3){let i=t[s],o=s+1<t.length?t[s+1]:0,a=s+2<t.length?t[s+2]:0,h=i<<16|o<<8|a;e+=Wt[h>>18&63]+Wt[h>>12&63]+(s+1<t.length?Wt[h>>6&63]:\"=\")+(s+2<t.length?Wt[h&63]:\"=\"),e.length>65536&&(n.push(e),e=\"\")}return n.push(e),n.join(\"\")}function Bo(t,n){let e=Te(t),s=new Float64Array(e.buffer,e.byteOffset,e.byteLength/8),i=n?JSON.parse(n):{},o=Cn(s,i),a=new Uint8Array(o.triangles.buffer,o.triangles.byteOffset,o.triangles.byteLength);return JSON.stringify({triangles:Fe(a),offset:o.offset,stats:o.stats})}return We(Lo);})();\n"   # replaced by build.py with the esbuild bundle

_DEFAULTS = {
    "enabled": True,
    "apply_to": "no-supports",   # "no-supports" | "all"
    "coverage": 0.5,             # 0..1, website slider default
    "tines": True,
    "tine_density": 0.0,         # 0..1, website slider default
    "bed_pad": True,
}

# ---------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------
_engine = None


def _engine_ctx():
    """One V8 context per process (loading the engine costs ~3 ms).

    Started at module load (see the bottom of this section), because Orca's audit
    hook is off while plugins load but gates every file open during slicing, and V8
    reads its ICU data file when it starts. On macOS V8 runs --jitless: mini-racer's
    JIT hits SIGTRAP there, and a hardened app may refuse JIT memory anyway. Both
    are lessons from gittrahan's feat/orca-plugin branch.
    """
    global _engine
    if _engine is None:
        import sys
        from py_mini_racer import MiniRacer, init_mini_racer
        if ENGINE_JS.startswith("__FINS_ENGINE"):
            raise RuntimeError("fin engine bundle missing -- run plugins/orca/build.py")
        flags = ["--single-threaded"]
        if sys.platform == "darwin":
            flags.append("--jitless")
        init_mini_racer(flags=flags, ignore_duplicate_init=True)
        ctx = MiniRacer()
        ctx.eval(ENGINE_JS)
        # mini-racer never tears V8 down by itself: without an explicit close(), the
        # interpreter hangs forever at exit (seen in pytest on macOS, any V8 flags).
        atexit.register(ctx.close)
        _engine = ctx
    return _engine


if not ENGINE_JS.startswith("__FINS_ENGINE"):
    try:
        _engine_ctx()
    except Exception:  # pragma: no cover - retried (and reported) on first slice
        _engine = None


def compute_fins(soup, layer_height, cfg):
    """Run the printfins.com engine on a posed part.

    soup: (M,3,3) float64 triangles, mm, in whatever frame the caller likes.
    Returns (fins (K,3,3) float64 in the SAME frame as `soup`, stats dict).
    """
    soup = np.ascontiguousarray(soup, dtype=np.float64)
    opts = {
        "mode": "auto",
        "bedPad": bool(cfg["bed_pad"]),
        "tines": bool(cfg["tines"]),
        "tineDensity": float(cfg["tine_density"]),
        "coverage": float(cfg["coverage"]),
        "layerHeight": float(layer_height),
    }
    raw = _engine_ctx().call(
        "SupportFinsEngine.computeFinsB64",
        base64.b64encode(soup.tobytes()).decode("ascii"),
        json.dumps(opts),
    )
    out = json.loads(raw)
    seated = np.frombuffer(base64.b64decode(out["triangles"]), dtype=np.float32)
    seated = seated.astype(np.float64).reshape(-1, 3, 3)
    off = out["offset"]  # seated = input + offset
    fins = seated - np.array([off["x"], off["y"], off["z"]], dtype=np.float64)
    return fins, out["stats"]


# ---------------------------------------------------------------------------------
# Mesh -> per-layer polygons (pure numpy; no Orca types)
# ---------------------------------------------------------------------------------
# The engine emits fins as SEPARATE closed solids (wall, tines, pad) that overlap on
# purpose -- "the slicer unions them". So: split the soup into its closed shells,
# cross-section each shell on its own (where loop nesting is well defined), and let
# Orca's own union (ExPolygon.union_ex) merge shells with each other and with the part.
# Winding is never trusted: outer vs hole comes from nesting depth, and Orca's
# ExPolygon constructor normalises orientation itself.

def _vkey(v, quantum=1e-4):
    return np.round(v / quantum).astype(np.int64)


def split_shells(tris):
    """Closed shells of a triangle soup: triangles are in the same shell when they
    share an EDGE. Two solids that merely touch at a vertex (a tine tip on a wall
    corner, say) stay separate, which keeps each shell's cross-section nesting sane."""
    n = len(tris)
    if n == 0:
        return []
    _, vid = np.unique(_vkey(tris.reshape(-1, 3)), axis=0, return_inverse=True)
    vid = vid.reshape(n, 3)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for f, (a, b, c) in enumerate(vid):
        for u, v in ((a, b), (b, c), (c, a)):
            e = (u, v) if u < v else (v, u)
            g = owner.setdefault(e, f)
            if g != f:
                ra, rb = find(f), find(g)
                if ra != rb:
                    parent[rb] = ra
    roots = np.array([find(f) for f in range(n)])
    return [tris[roots == r] for r in np.unique(roots)]


PLANE_NUDGE = -1e-7  # mm


def slice_soup(tris, z, quantum=1e-6):
    """Cross-section ONE closed shell with the plane Z = z -> list of closed loops,
    each an (N,2) float64 array (orientation unspecified).

    The plane is nudged DOWN by 1e-7 mm so no vertex ever lies exactly on it. It
    matters: the engine sizes the bed pad to the layer height, so the pad's top face
    can land exactly on a layer's slice_z, and a face ON the plane is ambiguous
    (half the layer is pad). Orca's own slicer puts that layer INSIDE the solid --
    measured in an Orca 2.5 nightly: lbracket's pad top sits at z = 0.5 = slice_z of layer 3,
    and a finned STL sliced by Orca prints pad there. Nudging down gives the same
    answer, so the plugin's layers match a finned STL exactly."""
    if len(tris) == 0:
        return []
    z = z + PLANE_NUDGE
    zs = tris[:, :, 2]
    sel = (zs.min(axis=1) <= z) & (zs.max(axis=1) >= z)
    if not np.any(sel):
        return []
    t = tris[sel]
    above = (t[:, :, 2] - z) >= 0.0
    n_above = above.sum(axis=1)
    keep = (n_above == 1) | (n_above == 2)
    t, above = t[keep], above[keep]
    if len(t) == 0:
        return []
    d = t[:, :, 2] - z
    segs = []
    for p, dk, ab in zip(t, d, above):
        lone = int(np.nonzero(ab if ab.sum() == 1 else ~ab)[0][0])
        a, b = (lone + 1) % 3, (lone + 2) % 3
        segs.append((_edge_cross(p[lone], p[a], dk[lone], dk[a]),
                     _edge_cross(p[lone], p[b], dk[lone], dk[b])))
    return _chain(segs, quantum)


def _edge_cross(p0, p1, d0, d1):
    s = d0 / (d0 - d1)
    return (p0 + s * (p1 - p0))[:2]


def _chain(segs, quantum):
    """Join undirected segments into closed loops via shared endpoints."""
    key = lambda q: (round(float(q[0]) / quantum), round(float(q[1]) / quantum))
    at = {}
    for i, (p, q) in enumerate(segs):
        at.setdefault(key(p), []).append(i)
        at.setdefault(key(q), []).append(i)
    used = [False] * len(segs)
    loops = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        start, cur_pt = segs[i]
        loop = [start]
        start_key = key(start)
        closed = False
        for _ in range(len(segs)):
            k = key(cur_pt)
            if k == start_key:
                closed = True
                break
            loop.append(cur_pt)
            nxt = next((j for j in at.get(k, ()) if not used[j]), None)
            if nxt is None:
                break  # open chain: non-manifold input, drop it
            used[nxt] = True
            p, q = segs[nxt]
            cur_pt = q if key(p) == k else p
        if closed and len(loop) >= 3:
            loops.append(np.array(loop))
    return loops


def signed_area(loop):
    x, y = loop[:, 0], loop[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def point_in_loop(pt, loop):
    x, y = loop[:, 0], loop[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cond = (y > pt[1]) != (yn > pt[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = (xn - x) * (pt[1] - y) / (yn - y) + x
    return bool(np.count_nonzero(cond & (pt[0] < xc)) % 2)


def group_loops(loops, min_area=1e-4):
    """Loops of one shell -> [(outer, [holes])] by nesting depth (even = outer)."""
    loops = [lp for lp in loops if abs(signed_area(lp)) >= min_area]
    n = len(loops)
    contains = [[j for j in range(n) if j != i and point_in_loop(loops[i][0], loops[j])]
                for i in range(n)]
    depth = [len(c) for c in contains]
    outers = [i for i in range(n) if depth[i] % 2 == 0]
    groups = {i: [] for i in outers}
    for i in range(n):
        if depth[i] % 2 == 1:
            # its outer is the container exactly one level up
            parent = next((j for j in contains[i] if depth[j] == depth[i] - 1), None)
            if parent is not None:
                groups[parent].append(loops[i])
    return [(loops[i], groups[i]) for i in outers]


# ---------------------------------------------------------------------------------
# Print frame -> Orca's slice frame
# ---------------------------------------------------------------------------------
class SliceFrame:
    """Affine map from the print-frame (mm, object bottom at z=0) to slice coords.

    Orca slices each object in its own XY frame, in scaled integer units, and does
    not expose the centring offset directly. PrintObject.bounding_box() does give
    the sliced footprint in that frame, so we calibrate: the posed part's XY
    bounding box (mm) must land exactly on it. That also absorbs XY shrinkage
    compensation, which Orca applies as a scale in the object transform.
    """

    def __init__(self, part_xy_min, part_xy_max, slice_bbox, unit):
        (bx0, by0, bx1, by1) = slice_bbox
        w_mm = part_xy_max - part_xy_min
        if w_mm[0] <= 0 or w_mm[1] <= 0:
            raise ValueError("degenerate part footprint")
        self.sx = (bx1 - bx0) / w_mm[0]
        self.sy = (by1 - by0) / w_mm[1]
        self.tx = bx0 - part_xy_min[0] * self.sx
        self.ty = by0 - part_xy_min[1] * self.sy
        nominal = 1.0 / unit
        # Shrinkage compensation is a few percent at most. Anything else means the
        # bbox isn't the footprint we think it is -- refuse rather than misplace fins.
        for s in (self.sx, self.sy):
            if not (0.9 * nominal < s < 1.1 * nominal):
                raise ValueError(f"slice frame calibration off: scale {s:.1f} vs nominal {nominal:.1f}")

    def to_scaled(self, loop):
        out = np.empty((len(loop), 2), dtype=np.int64)
        out[:, 0] = np.rint(loop[:, 0] * self.sx + self.tx)
        out[:, 1] = np.rint(loop[:, 1] * self.sy + self.ty)
        return out


# ---------------------------------------------------------------------------------
# Orca glue
# ---------------------------------------------------------------------------------
def _cfg(self):
    try:
        src = json.loads(self.get_config() or "{}")
    except (AttributeError, TypeError, ValueError):
        src = {}
    cfg = dict(_DEFAULTS)
    for k, v in src.items():
        if k in cfg:
            cfg[k] = v
    return cfg


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def posed_part_soup(print_object):
    """The part as Orca slices it: model-part volumes through the object->print
    transform, as (M,3,3) float64. Modifiers, negative volumes and support
    enforcers/blockers are not part of the solid and are skipped."""
    mo = print_object.model_object()
    trafo = np.asarray(print_object.trafo(), dtype=np.float64)
    chunks = []
    for vol in mo.volumes():
        if not vol.is_model_part():
            continue
        mesh = vol.mesh()
        V = np.asarray(mesh.vertices(), dtype=np.float64)
        T = np.asarray(mesh.triangles(), dtype=np.int64)
        if len(T) == 0:
            continue
        M = trafo @ np.asarray(vol.matrix(), dtype=np.float64)
        Vh = V @ M[:3, :3].T + M[:3, 3]
        tris = Vh[T]
        if np.linalg.det(M[:3, :3]) < 0:
            # A mirrored part flips every triangle's winding; the engine reads
            # overhangs from face normals, so restore outward-facing order.
            tris = tris[:, [0, 2, 1], :]
        chunks.append(tris)
    if not chunks:
        return np.empty((0, 3, 3))
    return np.concatenate(chunks, axis=0)


def _elephant_foot(print_object):
    """(compensation mm, layers) as Orca will apply it; 0 when printing on a raft."""
    def num(key, cast, default):
        try:
            v = print_object.config_value(key)
            return cast(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default
    if num("raft_layers", int, 0) > 0:
        return 0.0, 0
    return max(0.0, num("elefant_foot_compensation", float, 0.0)), max(1, num("elefant_foot_compensation_layers", int, 1))


def _shrink_keep_thin(expolys, delta_scaled):
    """Inward offset for elephant-foot compensation. A thin fin wall that would lose
    most of its width keeps its original outline -- Orca's own compensation also
    protects features below a minimum width rather than erasing them."""
    if delta_scaled <= 0:
        return expolys
    out = []
    for e in expolys:
        shrunk = e.offset(-delta_scaled)
        kept = sum(x.area() for x in shrunk)
        out.extend(shrunk if kept >= 0.5 * e.area() else [e])
    return out


def _bbox_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _expoly_bbox(e):
    c = np.asarray(e.contour.as_array())
    return (c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max())


def add_fins_to_layer(layer, fin_expolys):
    """Merge fin ExPolygons into the layer's first region, unioning with any part
    slice they overlap (tines are meant to fuse), then re-derive the islands."""
    regions = layer.regions()
    if not regions or not fin_expolys:
        return 0
    region = regions[0]
    existing = [(s.surface_type, s.expolygon) for s in region.slices.surfaces]
    # Copy: set()/append() below invalidate references into the live collection.
    existing = [(t, orca.host.ExPolygon(e.contour, list(e.holes))) for t, e in existing]
    boxes = [_expoly_bbox(e) for _, e in existing]
    added = 0
    for fin in fin_expolys:
        merged, mtype, mbox = fin, orca.host.SurfaceType.stInternal, _expoly_bbox(fin)
        keep, keep_boxes = [], []
        for (t, e), bb in zip(existing, boxes):
            if _bbox_overlap(bb, mbox):
                u = merged.union_ex(e)
                if len(u) == 1:          # they really overlapped: fuse, keep the part's type
                    merged, mtype, mbox = u[0], t, _expoly_bbox(u[0])
                    continue
            keep.append((t, e))
            keep_boxes.append(bb)
        existing, boxes = keep + [(mtype, merged)], keep_boxes + [mbox]
        added += 1
    by_type = {}
    for t, e in existing:
        by_type.setdefault(t, []).append(e)
    items = list(by_type.items())
    region.slices.set(items[0][1], items[0][0])
    for t, es in items[1:]:
        region.slices.append(es, t)
    layer.make_slices()
    return added


def inject_fins(print_object, cfg, layer_height, unit, log=None):
    """Compute fins for one PrintObject and add them to its layers. Returns a
    short human-readable result string. If `log` is a dict it is filled with
    diagnostics (frame calibration, per-layer fin area) for the spike."""
    log = {} if log is None else log
    soup = posed_part_soup(print_object)
    if len(soup) == 0:
        return "no model-part volumes"
    zmin = soup[:, :, 2].min()
    soup = soup - np.array([0.0, 0.0, zmin])       # object bottom at z = 0, like slice_z
    fins, stats = compute_fins(soup, layer_height, cfg)
    if len(fins) == 0:
        return "no fins needed"
    pts = soup.reshape(-1, 3)
    bbox = print_object.bounding_box()
    frame = SliceFrame(pts[:, :2].min(axis=0), pts[:, :2].max(axis=0), bbox, unit)
    log.update({
        "part_faces": int(len(soup)), "part_size_mm": (pts.max(axis=0) - pts.min(axis=0)).round(4).tolist(),
        "slice_bbox_scaled": list(bbox), "unit": unit,
        "frame_scale": [frame.sx, frame.sy], "frame_scale_vs_nominal": [frame.sx * unit, frame.sy * unit],
        "engine": stats, "fin_triangles": int(len(fins)), "layers": [],
    })
    shells = split_shells(fins)
    shell_z = [(sh[:, :, 2].min(), sh[:, :, 2].max()) for sh in shells]
    efc_mm, efc_layers = _elephant_foot(print_object)
    log["elephant_foot"] = [efc_mm, efc_layers]
    touched = 0
    for layer_id, layer in enumerate(print_object.layers()):
        z = float(layer.slice_z)
        expolys = []
        for sh, (z0, z1) in zip(shells, shell_z):
            if z < z0 or z > z1:
                continue
            for outer, holes in group_loops(slice_soup(sh, z)):
                expolys.append(orca.host.ExPolygon(frame.to_scaled(outer),
                                                   [frame.to_scaled(h) for h in holes]))
        if expolys and efc_mm > 0 and layer_id < efc_layers:
            # Orca shrinks the first layer(s) by the elephant-foot compensation at
            # slice time, BEFORE our hook runs, so the part is already compensated and
            # the fins are not. Match it (measured in an Orca 2.5 nightly: without this the first
            # layer of a finned STL and of the plugin differ by the 0.1 mm EFC).
            shrink = efc_mm - (efc_mm / efc_layers) * layer_id
            expolys = _shrink_keep_thin(expolys, int(round(shrink / unit)))
        if expolys and add_fins_to_layer(layer, expolys):
            touched += 1
            log["layers"].append([round(z, 4), round(sum(e.area() for e in expolys) * unit * unit, 4)])
    return (f"{stats.get('braces', 0)} fin(s), {stats.get('tines', 0)} tine(s) "
            f"on {touched} layer(s)")


class SupportFinsSlicing(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Support Fins"

    def get_default_config(self):
        return _DEFAULTS

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.posSlice or ctx.object is None:
            return orca.ExecutionResult.success()
        cfg = _cfg(self)
        if not cfg["enabled"]:
            return orca.ExecutionResult.success("Support Fins: disabled in plugin config")
        if np is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                _DEPS_ERROR or "Support Fins needs numpy (install failed?)")
        po = ctx.object
        if cfg["apply_to"] != "all" and _truthy(po.config_value("enable_support")):
            return orca.ExecutionResult.success("Support Fins: skipped (Orca supports are on for this part)")
        try:
            lh = float(po.config_value("layer_height") or ctx.config_value("layer_height") or 0.2)
        except (TypeError, ValueError):
            lh = 0.2
        log = {"object_id": _safe(lambda: po.id()), "layer_height": lh, "started": time.time()}
        try:
            msg = inject_fins(po, cfg, lh, orca.slicing.unscale(1), log)
        except Exception as e:  # never break a slice over fins; report and carry on
            log["error"] = f"{type(e).__name__}: {e}"
            _write_log(log)
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,
                                                f"Support Fins: {type(e).__name__}: {e}")
        log["result"] = msg
        log["seconds"] = round(time.time() - log["started"], 3)
        _write_log(log)
        return orca.ExecutionResult.success(f"Support Fins: {msg}")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _write_log(entry):
    """Spike diagnostics: append one JSON line per sliced object next to the plugin
    (support_fins_log.jsonl). Best effort -- never fails the slice."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "support_fins_log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------------
# Script capability: a Plugins-dialog smoke test for new Orca installs
# ---------------------------------------------------------------------------------
class SupportFinsSetupCheck(orca.script.ScriptPluginCapabilityBase):
    """Run from Plugins -> Support Fins -> Run to verify the latest plugin API.

    The actual fin injector is a slicing-pipeline capability, so the first failure
    users see would otherwise be mid-slice. This script capability gives a quick
    checklist: dependency install, engine bundle, host mesh read, and model stats.
    """

    def get_name(self):
        return "Support Fins - Check setup"

    def execute(self):
        lines = ["Support Fins setup check"]
        if np is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                _DEPS_ERROR or
                "Support Fins needs numpy. Orca should install it from the plugin metadata; "
                "try disabling/enabling the plugin or reinstalling it.")
        lines.append("deps: numpy loaded at startup (audit-safe)")
        try:
            _engine_ctx()
            lines.append("engine: bundled printfins.com engine loaded")
        except Exception as e:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"Support Fins engine failed to load: {type(e).__name__}: {e}")
        try:
            model = orca.host.model()
            objects = list(model.objects())
        except Exception as e:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"orca.host.model() failed: {type(e).__name__}: {e}")
        if not objects:
            lines.append("model: no objects on the plate (load a part, then run this again)")
            return orca.ExecutionResult.success("\n".join(lines))
        lines.append(f"model: {len(objects)} object(s) on the plate")
        total_faces = 0
        for oi, obj in enumerate(objects[:8]):
            vols = _safe_list(lambda: obj.volumes())
            insts = _safe_list(lambda: obj.instances())
            inst_msg = f", {len(insts)} instance(s)" if insts is not None else ""
            lines.append(f"object {oi}: {len(vols)} volume(s){inst_msg}")
            for vi, vol in enumerate(vols[:8]):
                try:
                    mesh = vol.mesh()
                    V = np.asarray(mesh.vertices(), dtype=np.float64)
                    T = np.asarray(mesh.triangles(), dtype=np.int64)
                    total_faces += int(len(T))
                    bbox = _volume_bbox_mm(V, vol, insts)
                    lines.append(
                        f"  volume {vi}: {len(V):,} vertices, {len(T):,} faces, "
                        f"bbox {bbox} mm")
                except Exception as e:
                    lines.append(f"  volume {vi}: mesh read failed ({type(e).__name__}: {e})")
        if len(objects) > 8:
            lines.append(f"... {len(objects) - 8} more object(s) not listed")
        lines.append(f"ready: host mesh read works; {total_faces:,} face(s) visible")
        lines.append("next: choose the Support Fins slicing capability in a process preset under Others -> Slicing Pipeline Plugin")
        return orca.ExecutionResult.success("\n".join(lines))


def _safe_list(fn):
    try:
        return list(fn())
    except Exception:
        return []


def _volume_bbox_mm(V, vol, insts):
    if len(V) == 0:
        return [0.0, 0.0, 0.0]
    if insts:
        try:
            # Host Model graph convention from Orca's docs: instance @ volume,
            # row vectors transformed with M.T.
            M = np.asarray(insts[0].matrix(), dtype=np.float64) @ np.asarray(vol.matrix(), dtype=np.float64)
            V = V @ M[:3, :3].T + M[:3, 3]
        except Exception:
            pass
    size = V.max(axis=0) - V.min(axis=0)
    return np.round(size, 3).tolist()


@orca.plugin
class SupportFinsPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(SupportFinsSlicing)
        orca.register_capability(SupportFinsSetupCheck)
