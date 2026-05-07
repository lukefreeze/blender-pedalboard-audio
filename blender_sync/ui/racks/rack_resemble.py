# ui/racks/rack_resemble.py
# Resemble Enhance — Voice Restoration rack (system Python / PyTorch, BETA).
import os, math, time
import bpy, gpu
from gpu_extras.batch import batch_for_shader
try:
    from ui.mixer.draw_utils import (
        draw_rect as _draw_rect, draw_line as _draw_line,
        draw_circle as _draw_circle, draw_text as _draw_text,
        text_width as _text_width, draw_knob as _draw_knob,
    )
except ImportError:
    pass

_BG=(.02,.05,.04,1.); _BORDER=(.06,.20,.16,1.); _PANEL=(.01,.03,.03,1.)
_PANEL_SEL=(.04,.12,.10,1.); _ACCENT=(.04,.80,.55,1.); _ACCENT_DIM=(.03,.34,.22,1.)
_TEXT=(.55,.95,.80,1.); _TEXT_DIM=(.16,.50,.36,1.); _TEXT_LABEL=(.06,.22,.16,1.)
_WARN_BG=(.08,.05,.00,1.); _WARN_BORDER=(.55,.33,.00,1.)
_WARN_TEXT=(.86,.53,.00,1.); _WARN_DIM=(.50,.28,.00,1.)
_GREEN=(.05,.80,.30,1.); _AMBER=(.86,.53,.00,1.)
RACK_RAIL_H=32
_NO_PYTORCH="NO_PYTORCH"; _READY="READY"; _PROCESSING="PROCESSING"
_DONE="DONE"; _ERROR="ERROR"

_re_dep_cache={"checked":False,"ok":False}
_re_last_check=0.0; _RE_INTERVAL=10.0

def _check_resemble():
    global _re_last_check,_re_dep_cache
    now=time.time()
    if _re_dep_cache["checked"] and (now-_re_last_check)<_RE_INTERVAL:
        return _re_dep_cache["ok"]
    _re_last_check=now; _re_dep_cache["checked"]=True
    import subprocess
    for cmd in ["python","python3","py"]:
        try:
            r=subprocess.run([cmd,"-c","import torch; import resemble_enhance"],
                             capture_output=True,timeout=4)
            if r.returncode==0:
                _re_dep_cache["ok"]=True; return True
        except Exception:
            continue
    _re_dep_cache["ok"]=False; return False

def _draw_warning(rx,ry,rw,rh,scale):
    sh=gpu.shader.from_builtin("UNIFORM_COLOR")
    rail_h=RACK_RAIL_H*scale; body_bot=ry; body_top=ry+rh-rail_h; body_h=body_top-body_bot
    margin=8*scale
    _draw_rect(rx,body_bot,rw,body_h,_BG)
    bvs=[(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
    bb=batch_for_shader(sh,"LINE_STRIP",{"pos":bvs})
    sh.bind(); sh.uniform_float("color",_BORDER); bb.draw(sh)
    warn_w=rw-margin*4; warn_h=min(body_h*.82,165*scale)
    warn_x=rx+(rw-warn_w)/2; warn_y=body_bot+(body_h-warn_h)/2
    _draw_rect(warn_x,warn_y,warn_w,warn_h,_WARN_BG)
    _draw_rect(warn_x,warn_y,4*scale,warn_h,_WARN_TEXT)
    wvs=[(warn_x,warn_y),(warn_x+warn_w,warn_y),(warn_x+warn_w,warn_y+warn_h),(warn_x,warn_y+warn_h),(warn_x,warn_y)]
    wb=batch_for_shader(sh,"LINE_STRIP",{"pos":wvs})
    sh.bind(); sh.uniform_float("color",_WARN_BORDER); wb.draw(sh)
    tx=warn_x+12*scale; fs_h=max(1,int(9*scale)); fs_b=max(1,int(8*scale))
    fs_s=max(1,int(7*scale)); lh=fs_b+5*scale; ty=warn_y+warn_h-fs_h-8*scale
    _draw_text("PYTORCH NOT FOUND — BETA RACK REQUIRES SETUP",tx,ty,fs_h,_WARN_TEXT)
    ty-=lh*1.4
    _draw_text("Resemble Enhance needs PyTorch in your system Python.",tx,ty,fs_b,_WARN_DIM)
    ty-=lh
    _draw_text("Open a terminal and run:",tx,ty,fs_b,_WARN_DIM)
    ty-=lh*1.2
    cmd_w=warn_w-24*scale; cmd_h=lh*2.4+6*scale; cmd_x=tx; cmd_y=ty-cmd_h
    _draw_rect(cmd_x,cmd_y,cmd_w,cmd_h,(.02,.01,.00,1.))
    cvs=[(cmd_x,cmd_y),(cmd_x+cmd_w,cmd_y),(cmd_x+cmd_w,cmd_y+cmd_h),(cmd_x,cmd_y+cmd_h),(cmd_x,cmd_y)]
    cb=batch_for_shader(sh,"LINE_STRIP",{"pos":cvs})
    sh.bind(); sh.uniform_float("color",(.44,.24,.00,1.)); cb.draw(sh)
    _draw_text("pip install torch torchaudio",cmd_x+6*scale,cmd_y+cmd_h-fs_b-4*scale,fs_b,_WARN_TEXT)
    _draw_text("pip install resemble-enhance",cmd_x+6*scale,cmd_y+4*scale,fs_b,_WARN_TEXT)
    ty=cmd_y-lh*1.2
    _draw_text("Models (~400MB) auto-download on first use. Restart Blender after install.",tx,ty,fs_s,_WARN_DIM)
    ty-=lh
    _draw_text("CPU: ~3-5x realtime processing. GPU significantly faster.",tx,ty,fs_s,_WARN_DIM)
    btn_w=min(140*scale,warn_w*.38); btn_h=max(16*scale,fs_s+8*scale)
    btn_x=warn_x+warn_w-btn_w-12*scale; btn_y=warn_y+8*scale
    _draw_rect(btn_x,btn_y,btn_w,btn_h,(.10,.06,.00,1.))
    bvs2=[(btn_x,btn_y),(btn_x+btn_w,btn_y),(btn_x+btn_w,btn_y+btn_h),(btn_x,btn_y+btn_h),(btn_x,btn_y)]
    bb2=batch_for_shader(sh,"LINE_STRIP",{"pos":bvs2})
    sh.bind(); sh.uniform_float("color",_WARN_BORDER); bb2.draw(sh)
    fs_btn=max(1,int(7*scale)); lbl="OPEN SETUP GUIDE  >"
    tw_btn=_text_width(lbl,fs_btn)
    _draw_text(lbl,btn_x+btn_w/2-tw_btn/2,btn_y+btn_h/2-fs_btn/2,fs_btn,_WARN_TEXT)
    return btn_x,btn_y,btn_w,btn_h

def _mini_wave(px,py,pw,ph,scale,label,rgb,ph_txt=None):
    sh=gpu.shader.from_builtin("UNIFORM_COLOR")
    _draw_rect(px,py,pw,ph,_PANEL)
    bvs=[(px,py),(px+pw,py),(px+pw,py+ph),(px,py+ph),(px,py)]
    bb=batch_for_shader(sh,"LINE_STRIP",{"pos":bvs})
    sh.bind(); sh.uniform_float("color",(rgb[0]*.4,rgb[1]*.4,rgb[2]*.4,1.)); bb.draw(sh)
    fs_l=max(1,int(6*scale))
    _draw_text(label,px+3*scale,py+ph-fs_l-2*scale,fs_l,(rgb[0]*.55,rgb[1]*.55,rgb[2]*.55,1.))
    cy=py+ph*.5
    _draw_line(px+2*scale,cy,px+pw-2*scale,cy,(rgb[0]*.2,rgb[1]*.2,rgb[2]*.2,.5),max(.5,scale*.5))
    if ph_txt:
        fs_p=max(1,int(6*scale)); tw_p=_text_width(ph_txt,fs_p)
        _draw_text(ph_txt,px+pw/2-tw_p/2,cy-fs_p/2,fs_p,(rgb[0]*.4,rgb[1]*.4,rgb[2]*.4,1.))

def _draw_resemble_body(rx,ry,rw,rh,rack,ai_idx,scale):
    sh=gpu.shader.from_builtin("UNIFORM_COLOR")
    if not _check_resemble():
        bx,by,bw,bh=_draw_warning(rx,ry,rw,rh,scale)
        try: rack['resemble_setup_btn']=(bx,by,bw,bh)
        except Exception: pass
        return

    rail_h=RACK_RAIL_H*scale; body_bot=ry; body_top=ry+rh-rail_h; body_h=body_top-body_bot
    margin=8*scale
    _draw_rect(rx,body_bot,rw,body_h,_BG)
    bvs=[(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
    bb=batch_for_shader(sh,"LINE_STRIP",{"pos":bvs}); sh.bind(); sh.uniform_float("color",_BORDER); bb.draw(sh)
    sbar_h=max(16*scale,body_h*.07); sbar_y=body_bot; sbar_x=rx+margin; sbar_w=rw-margin*2
    left_w=rw*.24; right_w=rw*.26; centre_w=rw-left_w-right_w-margin*4
    left_x=rx+margin; centre_x=left_x+left_w+margin; right_x=centre_x+centre_w+margin
    work_bot=sbar_y+sbar_h+2*scale; work_top=body_top-2*scale; work_h=work_top-work_bot
    fs_lbl=max(1,int(7*scale)); status=getattr(rack,"ai_status",_READY)

    # LEFT
    lx=left_x; ly=work_bot; lh=work_h
    _draw_rect(lx,ly,left_w,lh,_PANEL)
    lvs=[(lx,ly),(lx+left_w,ly),(lx+left_w,ly+lh),(lx,ly+lh),(lx,ly)]
    lb=batch_for_shader(sh,"LINE_STRIP",{"pos":lvs}); sh.bind(); sh.uniform_float("color",_BORDER); lb.draw(sh)
    _draw_text("SOURCE",lx+5*scale,ly+lh-fs_lbl-4*scale,fs_lbl,_TEXT_LABEL)
    ch_s=min(20*scale,left_w/5); ch_y=ly+lh-fs_lbl-ch_s-10*scale
    ch_idx=int(getattr(rack,"ai_source_channel",1))-1
    for ci in range(min(5,9)):
        bx2=lx+5*scale+ci*(ch_s+2*scale); by2=ch_y
        issel=(ci==ch_idx); bc=_ACCENT if issel else _ACCENT_DIM; bg=_PANEL_SEL if issel else _PANEL
        _draw_rect(bx2,by2,ch_s,ch_s,bg)
        cbvs=[(bx2,by2),(bx2+ch_s,by2),(bx2+ch_s,by2+ch_s),(bx2,by2+ch_s),(bx2,by2)]
        cb2=batch_for_shader(sh,"LINE_STRIP",{"pos":cbvs}); sh.bind(); sh.uniform_float("color",bc); cb2.draw(sh)
        fs_ci=max(1,int(7*scale)); lbl_c=str(ci+1); tw_c=_text_width(lbl_c,fs_ci)
        _draw_text(lbl_c,bx2+ch_s/2-tw_c/2,by2+ch_s/2-fs_ci/2,fs_ci,bc)
    mw=left_w-12*scale; mh=max(8*scale,work_h*.06); mx2=lx+6*scale
    in_y=ch_y-fs_lbl-mh-8*scale; out_y=in_y-fs_lbl-mh-6*scale
    _draw_text("INPUT LEVEL",mx2,in_y+mh+2*scale,fs_lbl,_TEXT_LABEL)
    _draw_rect(mx2,in_y,mw,mh,(.02,.05,.04,1.)); _draw_rect(mx2,in_y,mw*.68,mh,(.03,.30,.20,.85))
    _draw_text("OUTPUT LEVEL",mx2,out_y+mh+2*scale,fs_lbl,_TEXT_LABEL)
    _draw_rect(mx2,out_y,mw,mh,(.02,.05,.04,1.))
    if status==_DONE: _draw_rect(mx2,out_y,mw*.80,mh,(.04,.60,.35,.85))
    st_y=out_y-fs_lbl-2*scale-max(28*scale,work_h*.16); st_h=max(28*scale,work_h*.16)
    if st_y>ly+2*scale:
        _draw_text("STATS",mx2,st_y+st_h+2*scale,fs_lbl,_TEXT_LABEL)
        _draw_rect(mx2,st_y,mw,st_h,(.01,.04,.03,1.))
        stvs=[(mx2,st_y),(mx2+mw,st_y),(mx2+mw,st_y+st_h),(mx2,st_y+st_h),(mx2,st_y)]
        stb=batch_for_shader(sh,"LINE_STRIP",{"pos":stvs}); sh.bind(); sh.uniform_float("color",_BORDER); stb.draw(sh)
        fs_st=max(1,int(6*scale))
        if status==_DONE:
            _draw_text("NOISE  -18dB",mx2+4*scale,st_y+st_h*.65,fs_st,_ACCENT)
            _draw_text("BW  8->16kHz",mx2+4*scale,st_y+st_h*.25,fs_st,_ACCENT)
        else:
            _draw_text("RUN ENHANCE",mx2+4*scale,st_y+st_h*.60,fs_st,_TEXT_DIM)
            _draw_text("TO SEE STATS",mx2+4*scale,st_y+st_h*.25,fs_st,_TEXT_DIM)

    # CENTRE
    cx=centre_x; cy2=work_bot; ch2=work_h
    _draw_rect(cx,cy2,centre_w,ch2,_PANEL)
    cvs3=[(cx,cy2),(cx+centre_w,cy2),(cx+centre_w,cy2+ch2),(cx,cy2+ch2),(cx,cy2)]
    cb3=batch_for_shader(sh,"LINE_STRIP",{"pos":cvs3}); sh.bind(); sh.uniform_float("color",_BORDER); cb3.draw(sh)
    _draw_text("BEFORE / AFTER",cx+5*scale,cy2+ch2-fs_lbl-4*scale,fs_lbl,_TEXT_LABEL)
    wh2=(ch2-fs_lbl-10*scale)*.40; wx2=cx+4*scale; ww2=centre_w-8*scale
    top_wy=cy2+ch2-fs_lbl-10*scale-wh2; bot_wy=top_wy-wh2-3*scale
    _mini_wave(wx2,top_wy,ww2,wh2,scale,"ORIGINAL",(.75,.14,.06),"ORIGINAL AUDIO")
    _mini_wave(wx2,bot_wy,ww2,wh2,scale,"ENHANCED",(.04,.80,.55),"RUN ENHANCE TO SEE OUTPUT")
    bah=max(20*scale,ch2*.11); ba_y=cy2+2*scale
    ew=centre_w*.50; pw2=centre_w*.38; ex=cx+4*scale; px2=ex+ew+4*scale
    e_lbl="ENHANCING..." if status==_PROCESSING else "ENHANCE"
    _draw_rect(ex,ba_y,ew,bah,(.03,.10,.08,1.))
    evs=[(ex,ba_y),(ex+ew,ba_y),(ex+ew,ba_y+bah),(ex,ba_y+bah),(ex,ba_y)]
    eb=batch_for_shader(sh,"LINE_STRIP",{"pos":evs}); sh.bind(); sh.uniform_float("color",_ACCENT); eb.draw(sh)
    fs_btn=max(1,int(8*scale)); tw_e=_text_width(e_lbl,fs_btn)
    _draw_text(e_lbl,ex+ew/2-tw_e/2,ba_y+bah/2-fs_btn/2,fs_btn,_ACCENT)
    _draw_rect(px2,ba_y,pw2,bah,(.01,.04,.03,1.))
    pvs3=[(px2,ba_y),(px2+pw2,ba_y),(px2+pw2,ba_y+bah),(px2,ba_y+bah),(px2,ba_y)]
    pb3=batch_for_shader(sh,"LINE_STRIP",{"pos":pvs3}); sh.bind(); sh.uniform_float("color",_ACCENT_DIM); pb3.draw(sh)
    pv2="> PREVIEW"; tw_pv2=_text_width(pv2,fs_btn)
    _draw_text(pv2,px2+pw2/2-tw_pv2/2,ba_y+bah/2-fs_btn/2,fs_btn,_ACCENT)
    mo_y=ba_y+bah+4*scale; modes=["ENHANCE","DENOISE","BOTH"]; mode_sel=int(getattr(rack,"p3",0.0))
    mo_w=(centre_w-8*scale)/3
    for mi,m in enumerate(modes):
        mx3=cx+4*scale+mi*mo_w; issel=(mi==mode_sel)
        _draw_rect(mx3,mo_y,mo_w-2*scale,bah*.7,_PANEL_SEL if issel else _PANEL)
        mvs=[(mx3,mo_y),(mx3+mo_w-2*scale,mo_y),(mx3+mo_w-2*scale,mo_y+bah*.7),(mx3,mo_y+bah*.7),(mx3,mo_y)]
        mb=batch_for_shader(sh,"LINE_STRIP",{"pos":mvs}); sh.bind(); sh.uniform_float("color",_ACCENT if issel else _BORDER); mb.draw(sh)
        fs_m=max(1,int(6*scale)); tw_m=_text_width(m,fs_m)
        _draw_text(m,mx3+(mo_w-2*scale)/2-tw_m/2,mo_y+bah*.35-fs_m/2,fs_m,_TEXT if issel else _TEXT_DIM)

    # RIGHT
    rx2=right_x; ry2=work_bot; rh2=work_h
    _draw_rect(rx2,ry2,right_w,rh2,_PANEL)
    rvs=[(rx2,ry2),(rx2+right_w,ry2),(rx2+right_w,ry2+rh2),(rx2,ry2+rh2),(rx2,ry2)]
    rb2=batch_for_shader(sh,"LINE_STRIP",{"pos":rvs}); sh.bind(); sh.uniform_float("color",_BORDER); rb2.draw(sh)
    _draw_text("CONTROLS",rx2+5*scale,ry2+rh2-fs_lbl-4*scale,fs_lbl,_TEXT_LABEL)
    kr=min(22*scale,right_w*.30,work_h*.22); ky0=ry2+rh2*.65
    for i,(attr,lbl2,pdef_n) in enumerate([('p0','DENOISE',.9),('p1','ENHANCE',.8)]):
        kx2=rx2+right_w*(.28 if i==0 else .72); norm=float(getattr(rack,attr,pdef_n))
        _draw_knob(kx2,ky0,kr,norm,(_ACCENT[0],_ACCENT[1],_ACCENT[2]),lbl2,f"{norm:.2f}",scale)
    fs_n=max(1,int(6*scale)); note_y=ry2+rh2*.28
    for note in ["DENOISE: noise removal","ENHANCE: presence+clarity","0.0-1.0  (1.0=max)","","CPU: ~3-5x realtime","GPU: faster"]:
        _draw_text(note,rx2+6*scale,note_y,fs_n,_TEXT_LABEL); note_y-=(fs_n+3*scale)

    # STATUS BAR
    _draw_rect(sbar_x,sbar_y,sbar_w,sbar_h,(.02,.04,.03,1.))
    svs=[(sbar_x,sbar_y),(sbar_x+sbar_w,sbar_y),(sbar_x+sbar_w,sbar_y+sbar_h),(sbar_x,sbar_y+sbar_h),(sbar_x,sbar_y)]
    sb2=batch_for_shader(sh,"LINE_STRIP",{"pos":svs}); sh.bind(); sh.uniform_float("color",_BORDER); sb2.draw(sh)
    mn=["enhance","denoise","both"]; mi2=int(getattr(rack,"p3",0.0)); ms=mn[mi2] if mi2<len(mn) else "enhance"
    fs_sb=max(1,int(7*scale))
    _draw_text(f"ENGINE: resemble-enhance  |  MODE: {ms}  |  DEVICE: cpu  |  OFFLINE",
               sbar_x+7*scale,sbar_y+sbar_h/2-fs_sb/2,fs_sb,_TEXT_DIM)
    dc={_READY:_GREEN,_PROCESSING:(.90,.50,.10,1.),_DONE:_GREEN,_ERROR:(.90,.10,.05,1.)}
    _draw_circle(sbar_x+sbar_w-9*scale,sbar_y+sbar_h/2,3.5*scale,dc.get(status,_AMBER))
