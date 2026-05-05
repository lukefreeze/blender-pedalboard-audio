"""
deepfilter_runner.py
====================
Standalone CLI runner for DeepFilterNet3 ONNX inference.
Frozen into a single executable by PyInstaller — ships with the addon.

Model I/O (from inspection):
  enc:     feat_erb [1,1,S,32], feat_spec [1,2,S,96]
           → e0[1,64,S,32], e1[1,64,S,16], e2[1,64,S,8], e3[1,64,S,8],
             emb[1,S,512], c0[1,64,S,96], lsnr[1,S,1]
  erb_dec: emb[1,S,512], e3,e2,e1,e0
           → m[1,1,S,32]  (ERB gains 0-1)
  df_dec:  emb[1,S,512], c0[1,64,S,96]
           → coefs[1,S,NB_DF,10]  (complex DF coeffs, df_order=5 → 10=5*2)

Usage:
    deepfilter_runner.exe --input  <wav> --output <wav>
                          --models <folder>
                          --atten  0.0-1.0
                          --sensitiv 0.0-1.0
                          --postgain <dB>
"""

import sys, os, argparse, wave, math
import numpy as np


# ---------------------------------------------------------------------------
# WAV I/O
# ---------------------------------------------------------------------------
def _read_wav(path):
    with wave.open(path, 'rb') as wf:
        sr   = wf.getframerate()
        nch  = wf.getnchannels()
        sw   = wf.getsampwidth()
        nfr  = wf.getnframes()
        raw  = wf.readframes(nfr)
    if sw == 2:
        s = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        s = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    elif sw == 3:
        arr = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        i32 = (arr[:,0].astype(np.int32) |
               (arr[:,1].astype(np.int32) << 8) |
               (arr[:,2].astype(np.int32) << 16))
        i32[i32 >= 0x800000] -= 0x1000000
        s = i32.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"Unsupported sample width: {sw}")
    if nch > 1:
        s = s.reshape(-1, nch)
    return s, sr, nch


def _write_wav(path, samples, sr):
    if samples.ndim == 1:
        nch = 1
        s16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    else:
        nch = samples.shape[1]
        s16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(s16.tobytes())


def _resample(x, sr_in, sr_out):
    if sr_in == sr_out:
        return x
    n_in  = x.shape[0]
    n_out = int(n_in * sr_out / sr_in)
    xi    = np.arange(n_in,  dtype=np.float64)
    xo    = np.arange(n_out, dtype=np.float64) / (sr_out / sr_in)
    if x.ndim == 1:
        return np.interp(xo, xi, x).astype(np.float32)
    return np.stack([np.interp(xo, xi, x[:,c]).astype(np.float32)
                     for c in range(x.shape[1])], axis=1)


# ---------------------------------------------------------------------------
# DeepFilterNet3 constants (from config.ini)
# ---------------------------------------------------------------------------
SR       = 48000
FFT_SIZE = 960
HOP_SIZE = 480
NB_ERB   = 32
NB_DF    = 96
DF_ORDER = 5   # coefs last dim = 10 = DF_ORDER * 2 (complex)


def _erb_centres(sr, fft_size, nb_erb):
    """Return (nb_erb,) array of FFT bin indices per ERB band — centre bin."""
    n_bins = fft_size // 2 + 1
    freqs  = np.linspace(0, sr / 2, n_bins)
    erb_lo = 21.4 * math.log10(1 + 0    / 229.0 + 1e-9)
    erb_hi = 21.4 * math.log10(1 + (sr/2) / 229.0)
    erb_pts = np.linspace(erb_lo, erb_hi, nb_erb + 1)
    hz_pts  = 229.0 * (10.0 ** (erb_pts / 21.4) - 1.0)
    indices = []
    for b in range(nb_erb):
        lo = np.searchsorted(freqs, hz_pts[b])
        hi = np.searchsorted(freqs, hz_pts[b + 1])
        hi = max(hi, lo + 1)
        indices.append((lo, min(hi, n_bins)))
    return indices


def _compute_erb_feat(spec_mag2, erb_idx):
    """spec_mag2: (S, n_bins) power. Returns (S, NB_ERB) log-normalised."""
    S      = spec_mag2.shape[0]
    nb_erb = len(erb_idx)
    feat   = np.zeros((S, nb_erb), dtype=np.float32)
    for b, (lo, hi) in enumerate(erb_idx):
        feat[:, b] = np.mean(spec_mag2[:, lo:hi], axis=1)
    feat = 10.0 * np.log10(feat + 1e-10)
    feat = (feat + 60.0) / 40.0          # normalise ~0 mean / unit var
    return feat


# ---------------------------------------------------------------------------
# Main inference
# ---------------------------------------------------------------------------
def run_deepfilternet(input_path, output_path, models_dir,
                      atten_norm=0.5, sensitiv=0.75, postgain_db=0.0):

    import onnxruntime as ort

    print("PROGRESS:5")

    # ── Load audio ────────────────────────────────────────────────────────
    samples, sr_orig, nch = _read_wav(input_path)
    print("PROGRESS:8")

    mono = samples.mean(axis=1).astype(np.float32) if samples.ndim == 2 \
           else samples.astype(np.float32)

    peak = np.max(np.abs(mono))
    if peak < 1e-6:
        import shutil; shutil.copy2(input_path, output_path)
        print(f"DONE:{output_path}"); return

    mono_norm = mono / peak
    mono_48   = _resample(mono_norm, sr_orig, SR)
    n_orig    = len(mono_48)
    print("PROGRESS:12")

    # ── STFT ─────────────────────────────────────────────────────────────
    window  = np.hanning(FFT_SIZE).astype(np.float32)
    n_bins  = FFT_SIZE // 2 + 1
    pad     = FFT_SIZE - HOP_SIZE
    padded  = np.pad(mono_48, (pad, FFT_SIZE))
    n_frames = (len(padded) - FFT_SIZE) // HOP_SIZE + 1

    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, FFT_SIZE),
        strides=(padded.strides[0] * HOP_SIZE, padded.strides[0])
    ).copy()
    spec = np.fft.rfft(frames * window[None, :], n=FFT_SIZE).astype(np.complex64)
    # spec: (S, n_bins)
    print("PROGRESS:18")

    # ── ERB features ─────────────────────────────────────────────────────
    erb_idx  = _erb_centres(SR, FFT_SIZE, NB_ERB)
    spec_pow = (spec.real ** 2 + spec.imag ** 2)   # (S, n_bins)
    erb_feat = _compute_erb_feat(spec_pow, erb_idx) # (S, NB_ERB)
    print("PROGRESS:22")

    # ── Build model tensors ───────────────────────────────────────────────
    # enc expects:
    #   feat_erb:  [1, 1, S, 32]
    #   feat_spec: [1, 2, S, 96]  — ch0=real, ch1=imag of first NB_DF bins
    S = n_frames
    feat_erb  = erb_feat[None, None, :, :]                  # (1,1,S,32)
    spec_re   = spec.real[:, :NB_DF][None, None, :, :]      # (1,1,S,96)
    spec_im   = spec.imag[:, :NB_DF][None, None, :, :]      # (1,1,S,96)
    feat_spec = np.concatenate([spec_re, spec_im], axis=1)  # (1,2,S,96)

    print("PROGRESS:26")

    # ── Load ONNX sessions ────────────────────────────────────────────────
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    enc_sess = ort.InferenceSession(
        os.path.join(models_dir, "enc.onnx"), opts)
    erb_sess = ort.InferenceSession(
        os.path.join(models_dir, "erb_dec.onnx"), opts)
    df_sess  = ort.InferenceSession(
        os.path.join(models_dir, "df_dec.onnx"), opts)
    print("PROGRESS:35")

    # ── Encoder ──────────────────────────────────────────────────────────
    enc_out = enc_sess.run(None, {
        "feat_erb":  feat_erb,
        "feat_spec": feat_spec,
    })
    enc_names = [o.name for o in enc_sess.get_outputs()]
    enc_dict  = dict(zip(enc_names, enc_out))
    # enc_dict keys: e0, e1, e2, e3, emb, c0, lsnr
    emb  = enc_dict["emb"]   # (1, S, 512)
    c0   = enc_dict["c0"]    # (1, 64, S, 96)
    e0   = enc_dict["e0"]
    e1   = enc_dict["e1"]
    e2   = enc_dict["e2"]
    e3   = enc_dict["e3"]
    lsnr = enc_dict.get("lsnr")
    print("PROGRESS:55")

    # ── ERB decoder ───────────────────────────────────────────────────────
    erb_out = erb_sess.run(None, {
        "emb": emb,
        "e3":  e3,
        "e2":  e2,
        "e1":  e1,
        "e0":  e0,
    })
    m = erb_out[0]   # (1, 1, S, 32) — gains per ERB band, sigmoid output
    print("PROGRESS:70")

    # ── DF decoder ────────────────────────────────────────────────────────
    df_out = df_sess.run(None, {
        "emb": emb,
        "c0":  c0,
    })
    coefs = df_out[0]   # (1, S, NB_DF, 10) where 10 = DF_ORDER*2 complex
    print("PROGRESS:80")

    # ── Apply ERB mask ────────────────────────────────────────────────────
    # m: (1,1,S,32) → (S,32)
    gains_erb = np.clip(m[0, 0, :, :], 0.0, 1.0)   # (S, NB_ERB)

    # Apply attenuation limit: atten_norm=1 → full suppression, 0 → bypass
    gains_erb = 1.0 - atten_norm * (1.0 - gains_erb)

    # Expand ERB gains to FFT bins — (S, n_bins)
    gains_bins = np.ones((S, n_bins), dtype=np.float32)
    for b, (lo, hi) in enumerate(erb_idx):
        gains_bins[:, lo:hi] = gains_erb[:, b:b+1]

    # Apply sensitivity: blend toward unity on louder frames
    if sensitiv < 1.0:
        frame_rms = np.sqrt(np.mean(spec_pow, axis=1))   # (S,)
        frame_db  = 10.0 * np.log10(frame_rms + 1e-10)
        thresh_db = -60.0 + sensitiv * 50.0
        blend = np.clip((frame_db - thresh_db) / 10.0, 0.0, 1.0) * (1.0 - sensitiv)
        gains_bins = gains_bins * (1.0 - blend[:, None]) + blend[:, None]

    spec_out = spec * gains_bins   # (S, n_bins)
    print("PROGRESS:87")

    # ── Apply deep filter (vectorised) ───────────────────────────────────
    # coefs: (1, S, NB_DF, 10) — 10 = DF_ORDER pairs of (re, im)
    coefs_sq = coefs[0]                          # (S, NB_DF, 10)
    spec_nb  = spec[:, :NB_DF].copy()            # (S, NB_DF) original spec bins
    df_acc   = np.zeros((S, NB_DF), dtype=np.complex64)

    for k in range(DF_ORDER):
        # Shift spec by k frames (pad with zeros at start)
        if k == 0:
            past_spec = spec_nb
        else:
            past_spec = np.concatenate([
                np.zeros((k, NB_DF), dtype=np.complex64),
                spec_nb[:-k]
            ], axis=0)
        c_re = coefs_sq[:, :, k * 2]            # (S, NB_DF)
        c_im = coefs_sq[:, :, k * 2 + 1]        # (S, NB_DF)
        c    = (c_re + 1j * c_im).astype(np.complex64)
        df_acc += c * past_spec                   # (S, NB_DF)

    # Blend DF output with ERB-masked: 50/50
    alpha = 0.5
    spec_out[:, :NB_DF] = (alpha * df_acc +
                            (1 - alpha) * spec_out[:, :NB_DF])
    print("PROGRESS:93")

    # ── ISTFT ─────────────────────────────────────────────────────────────
    out_len = (n_frames - 1) * HOP_SIZE + FFT_SIZE
    output  = np.zeros(out_len, dtype=np.float32)
    norm    = np.zeros(out_len, dtype=np.float32)
    w2      = window ** 2

    for i in range(n_frames):
        frame = np.fft.irfft(spec_out[i], n=FFT_SIZE).real.astype(np.float32)
        s = i * HOP_SIZE
        output[s:s + FFT_SIZE] += frame * window
        norm[s:s + FFT_SIZE]   += w2

    output /= np.maximum(norm, 1e-8)
    audio_out = output[pad:pad + n_orig]

    # Restore level + post gain
    audio_out = audio_out * peak * (10.0 ** (postgain_db / 20.0))
    audio_out = np.clip(audio_out, -1.0, 1.0)
    print("PROGRESS:97")

    # ── Stereo reconstruction ─────────────────────────────────────────────
    if nch == 2 and samples.ndim == 2:
        orig_l = _resample(samples[:, 0] / (peak + 1e-9), sr_orig, SR)
        orig_r = _resample(samples[:, 1] / (peak + 1e-9), sr_orig, SR)
        mono_e = ((orig_l + orig_r) * 0.5)
        ref_e  = np.mean(mono_e ** 2) + 1e-9
        ls = np.sqrt(np.mean(orig_l ** 2) / ref_e)
        rs = np.sqrt(np.mean(orig_r ** 2) / ref_e)
        al = _resample(audio_out, SR, sr_orig) * ls * peak
        ar = _resample(audio_out, SR, sr_orig) * rs * peak
        final = np.stack([np.clip(al,-1,1), np.clip(ar,-1,1)], axis=1)
    else:
        final = _resample(audio_out, SR, sr_orig)

    # ── Write ─────────────────────────────────────────────────────────────
    _write_wav(output_path, final, sr_orig)
    print("PROGRESS:100")
    print(f"DONE:{output_path}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",    required=True)
    parser.add_argument("--output",   required=True)
    parser.add_argument("--models",   required=True)
    parser.add_argument("--atten",    type=float, default=0.5)
    parser.add_argument("--sensitiv", type=float, default=0.75)
    parser.add_argument("--postgain", type=float, default=0.0)
    args = parser.parse_args()

    try:
        run_deepfilternet(
            args.input, args.output, args.models,
            max(0.0, min(1.0, args.atten)),
            max(0.0, min(1.0, args.sensitiv)),
            max(-24.0, min(24.0, args.postgain)),
        )
    except Exception as e:
        import traceback
        print(f"ERROR:{e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
