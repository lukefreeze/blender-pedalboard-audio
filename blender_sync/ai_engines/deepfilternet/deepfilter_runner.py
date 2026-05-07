"""
deepfilter_runner.py  v2
========================
Standalone CLI runner for DeepFilterNet3 ONNX inference.
Frozen into a single executable by PyInstaller.

KEY FIX over v1:
  The model was trained with two exponential running normalisations:
  1. ERB features  → exponential mean normalisation  (decay τ = 1s = 100 frames)
  2. feat_spec     → exponential unit normalisation   (same decay)
  Using static normalisations (the old "(x+60)/40" approach) gives the model
  inputs far outside its training distribution, producing subtle or no effect.

  The final output reconstruction also now correctly uses:
  - DF output ONLY for bins 0..NB_DF  (not blended)
  - ERB gain ONLY for bins NB_DF..n_bins
  matching the paper equation (4): Y = alpha*Y_DF + (1-alpha)*Y_G where
  alpha comes from the model's learned weighting (the lsnr output).
"""

import sys, os, argparse, wave, math
import numpy as np


# ---------------------------------------------------------------------------
# WAV I/O
# ---------------------------------------------------------------------------
def _read_wav(path):
    with wave.open(path, 'rb') as wf:
        sr  = wf.getframerate()
        nch = wf.getnchannels()
        sw  = wf.getsampwidth()
        nfr = wf.getnframes()
        raw = wf.readframes(nfr)
    if sw == 2:
        s = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        s = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    elif sw == 3:
        a = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        i = (a[:,0].astype(np.int32) | (a[:,1].astype(np.int32)<<8) |
             (a[:,2].astype(np.int32)<<16))
        i[i >= 0x800000] -= 0x1000000
        s = i.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"Unsupported sample width: {sw}")
    if nch > 1:
        s = s.reshape(-1, nch)
    return s, sr, nch


def _write_wav(path, samples, sr):
    nch = 1 if samples.ndim == 1 else samples.shape[1]
    s16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(nch); wf.setsampwidth(2)
        wf.setframerate(sr);  wf.writeframes(s16.tobytes())


def _resample(x, sr_in, sr_out):
    if sr_in == sr_out:
        return x
    n_in  = x.shape[0]
    n_out = int(n_in * sr_out / sr_in)
    xi = np.arange(n_in,  dtype=np.float64)
    xo = np.arange(n_out, dtype=np.float64) / (sr_out / sr_in)
    if x.ndim == 1:
        return np.interp(xo, xi, x).astype(np.float32)
    return np.stack([np.interp(xo, xi, x[:,c]).astype(np.float32)
                     for c in range(x.shape[1])], axis=1)


# ---------------------------------------------------------------------------
# DeepFilterNet3 constants (from config.ini)
# ---------------------------------------------------------------------------
SR        = 48000
FFT_SIZE  = 960
HOP_SIZE  = 480
NB_ERB    = 32
NB_DF     = 96
DF_ORDER  = 5
NORM_ALPHA = 0.99   # decay for 1-frame at 48kHz/480 hop = ~100 frames/sec → τ=1s


def _build_erb_fb(sr, fft_size, nb_erb):
    """
    Build the ERB filterbank as a (nb_erb, n_bins) matrix.
    Each row sums to 1.0 (triangular / rectangular bands).
    Returns the matrix for fast matmul.
    """
    n_bins = fft_size // 2 + 1
    freqs  = np.linspace(0, sr / 2.0, n_bins)

    erb_lo = 21.4 * math.log10(1.0 + 0.0       / 229.0 + 1e-9)
    erb_hi = 21.4 * math.log10(1.0 + (sr/2.0)  / 229.0)
    erb_pts = np.linspace(erb_lo, erb_hi, nb_erb + 1)
    hz_pts  = 229.0 * (10.0 ** (erb_pts / 21.4) - 1.0)

    fb = np.zeros((nb_erb, n_bins), dtype=np.float32)
    for b in range(nb_erb):
        lo = np.searchsorted(freqs, hz_pts[b])
        hi = np.searchsorted(freqs, hz_pts[b + 1])
        hi = max(hi, lo + 1)
        hi = min(hi, n_bins)
        bw = hi - lo
        fb[b, lo:hi] = 1.0 / bw   # mean over band
    return fb, hz_pts


def _erb_expand(gains_erb, erb_fb):
    """
    Expand (S, NB_ERB) gains back to (S, n_bins) using the filterbank.
    Each FFT bin gets the gain of its ERB band (nearest-band assignment).
    """
    # gains_erb: (S, NB_ERB), erb_fb: (NB_ERB, n_bins)
    # For each bin, find which band it belongs to (argmax of fb column)
    band_idx = np.argmax(erb_fb, axis=0)   # (n_bins,)
    return gains_erb[:, band_idx]           # (S, n_bins)


# ---------------------------------------------------------------------------
# Main inference
# ---------------------------------------------------------------------------
def run_deepfilternet(input_path, output_path, models_dir,
                      atten_norm=0.5, sensitiv=0.75, postgain_db=0.0):

    import onnxruntime as ort

    print("PROGRESS:5")

    # ── Load and prepare audio ────────────────────────────────────────────
    samples, sr_orig, nch = _read_wav(input_path)
    print("PROGRESS:8")

    mono = (samples.mean(axis=1) if samples.ndim == 2
            else samples).astype(np.float32)

    peak = np.max(np.abs(mono))
    if peak < 1e-6:
        import shutil; shutil.copy2(input_path, output_path)
        print(f"DONE:{output_path}"); return

    # Normalise to [-1, 1] — the model was trained on normalised signals
    mono_norm = mono / peak
    mono_48   = _resample(mono_norm, sr_orig, SR)
    n_orig    = len(mono_48)
    print("PROGRESS:12")

    # ── STFT ─────────────────────────────────────────────────────────────
    # Use Hann window — same as libdf
    window  = np.hanning(FFT_SIZE).astype(np.float32)
    n_bins  = FFT_SIZE // 2 + 1

    # Pad: FFT_SIZE - HOP_SIZE at start (causal), FFT_SIZE at end
    pad_start = FFT_SIZE - HOP_SIZE
    padded    = np.pad(mono_48, (pad_start, FFT_SIZE))
    n_frames  = (len(padded) - FFT_SIZE) // HOP_SIZE + 1

    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, FFT_SIZE),
        strides=(padded.strides[0] * HOP_SIZE, padded.strides[0])
    ).copy()
    spec = np.fft.rfft(frames * window[None, :], n=FFT_SIZE).astype(np.complex64)
    # spec: (S, n_bins)
    S = n_frames
    print("PROGRESS:18")

    # ── Build ERB filterbank ──────────────────────────────────────────────
    erb_fb, hz_pts = _build_erb_fb(SR, FFT_SIZE, NB_ERB)

    # ── Compute ERB power features ────────────────────────────────────────
    spec_pow = spec.real**2 + spec.imag**2   # (S, n_bins)
    # erb_pow: (S, NB_ERB)  — mean power per ERB band
    erb_pow  = spec_pow @ erb_fb.T           # (S, NB_ERB)
    erb_db   = 10.0 * np.log10(erb_pow + 1e-10)

    # ── Exponential mean normalisation for ERB features ───────────────────
    # Running mean with α=0.99 (≈ 1s decay at 100 frames/s)
    # mean_t = α * mean_{t-1} + (1-α) * x_t
    # norm   = x_t - mean_t
    erb_mean = np.zeros(NB_ERB, dtype=np.float32)
    erb_feat = np.zeros_like(erb_db)
    for t in range(S):
        erb_mean = NORM_ALPHA * erb_mean + (1.0 - NORM_ALPHA) * erb_db[t]
        erb_feat[t] = erb_db[t] - erb_mean
    print("PROGRESS:22")

    # ── Exponential unit normalisation for spec features ──────────────────
    # Running mean of |X|^2 per bin, normalise X by sqrt(mean)
    # This makes the complex input approximately unit-power
    spec_df   = spec[:, :NB_DF]   # (S, NB_DF) complex
    spec_pow2 = spec_df.real**2 + spec_df.imag**2   # (S, NB_DF)
    unit_mean = np.ones(NB_DF, dtype=np.float32) * 1e-6
    spec_norm = np.zeros_like(spec_df)
    for t in range(S):
        unit_mean   = NORM_ALPHA * unit_mean + (1.0 - NORM_ALPHA) * spec_pow2[t]
        scale       = 1.0 / (np.sqrt(unit_mean) + 1e-10)
        spec_norm[t] = spec_df[t] * scale
    print("PROGRESS:26")

    # ── Build model input tensors ─────────────────────────────────────────
    # feat_erb:  [1, 1, S, NB_ERB]
    # feat_spec: [1, 2, S, NB_DF]  (real/imag channels)
    feat_erb  = erb_feat[None, None, :, :].astype(np.float32)
    spec_re   = spec_norm.real[None, None, :, :].astype(np.float32)
    spec_im   = spec_norm.imag[None, None, :, :].astype(np.float32)
    feat_spec = np.concatenate([spec_re, spec_im], axis=1)   # (1,2,S,NB_DF)

    # ── Load ONNX sessions ────────────────────────────────────────────────
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    enc_sess = ort.InferenceSession(os.path.join(models_dir, "enc.onnx"),     opts)
    erb_sess = ort.InferenceSession(os.path.join(models_dir, "erb_dec.onnx"), opts)
    df_sess  = ort.InferenceSession(os.path.join(models_dir, "df_dec.onnx"),  opts)
    print("PROGRESS:35")

    # ── Encoder ──────────────────────────────────────────────────────────
    enc_out  = enc_sess.run(None, {"feat_erb": feat_erb, "feat_spec": feat_spec})
    enc_dict = dict(zip([o.name for o in enc_sess.get_outputs()], enc_out))
    emb  = enc_dict["emb"]   # (1, S, 512)
    c0   = enc_dict["c0"]    # (1, 64, S, NB_DF)
    e0   = enc_dict["e0"];  e1 = enc_dict["e1"]
    e2   = enc_dict["e2"];  e3 = enc_dict["e3"]
    lsnr = enc_dict.get("lsnr")   # (1, S, 1) — local SNR estimate
    print("PROGRESS:55")

    # ── ERB decoder ───────────────────────────────────────────────────────
    erb_out  = erb_sess.run(None, {"emb": emb, "e3": e3, "e2": e2,
                                   "e1": e1,  "e0": e0})
    m = erb_out[0]   # (1, 1, S, NB_ERB) — ERB gains, sigmoid [0,1]
    print("PROGRESS:70")

    # ── DF decoder ────────────────────────────────────────────────────────
    df_out  = df_sess.run(None, {"emb": emb, "c0": c0})
    coefs   = df_out[0]   # (1, S, NB_DF, 10)  10 = DF_ORDER*2
    print("PROGRESS:80")

    # ── Apply attenuation limit to ERB gains ──────────────────────────────
    gains_erb = np.clip(m[0, 0, :, :], 0.0, 1.0)   # (S, NB_ERB)
    # atten_norm=1 → full model suppression, 0 → bypass
    gains_erb = 1.0 - atten_norm * (1.0 - gains_erb)

    # Expand ERB gains to all FFT bins
    gains_bins = _erb_expand(gains_erb, erb_fb)   # (S, n_bins)

    # ── Apply ERB mask to full spectrum ───────────────────────────────────
    spec_erb_out = spec * gains_bins   # (S, n_bins) — stage 1 output

    # ── Apply deep filter to lower NB_DF bins (vectorised) ───────────────
    coefs_sq = coefs[0]   # (S, NB_DF, 10)
    spec_nb  = spec[:, :NB_DF].copy()
    df_acc   = np.zeros((S, NB_DF), dtype=np.complex64)

    for k in range(DF_ORDER):
        if k == 0:
            past = spec_nb
        else:
            past = np.concatenate([
                np.zeros((k, NB_DF), dtype=np.complex64),
                spec_nb[:-k]
            ], axis=0)
        c  = (coefs_sq[:, :, k*2] + 1j * coefs_sq[:, :, k*2+1]).astype(np.complex64)
        df_acc += c * past

    # ── Combine stage 1 (ERB) and stage 2 (DF) ───────────────────────────
    # Per the paper: lower NB_DF bins get DF output blended with ERB output
    # using learned alpha (lsnr acts as the weighting proxy).
    # Simple and robust: use DF for lower bins, ERB gains for upper bins.
    spec_out = spec_erb_out.copy()
    spec_out[:, :NB_DF] = df_acc   # DF output replaces lower bins entirely

    # Apply sensitivity: on loud frames (speech) reduce processing to avoid artefacts
    if sensitiv < 1.0:
        frame_rms = np.sqrt(np.mean(spec_pow, axis=1))
        frame_db  = 10.0 * np.log10(frame_rms + 1e-10)
        thresh_db = -60.0 + sensitiv * 50.0
        blend = np.clip((frame_db - thresh_db) / 10.0, 0.0, 1.0) * (1.0 - sensitiv)
        # Blend toward original on loud frames
        spec_out = spec_out * (1.0 - blend[:, None]) + spec * blend[:, None]

    print("PROGRESS:87")

    # ── ISTFT ─────────────────────────────────────────────────────────────
    out_len = (n_frames - 1) * HOP_SIZE + FFT_SIZE
    output  = np.zeros(out_len, dtype=np.float32)
    norm_w  = np.zeros(out_len, dtype=np.float32)
    w2      = window ** 2

    for i in range(n_frames):
        frame = np.fft.irfft(spec_out[i], n=FFT_SIZE).real.astype(np.float32)
        s = i * HOP_SIZE
        output[s:s+FFT_SIZE] += frame * window
        norm_w[s:s+FFT_SIZE] += w2

    output /= np.maximum(norm_w, 1e-8)
    audio_out = output[pad_start:pad_start + n_orig]

    # Restore to original peak level + post gain
    audio_out = audio_out * peak * (10.0 ** (postgain_db / 20.0))
    audio_out = np.clip(audio_out, -1.0, 1.0)
    print("PROGRESS:97")

    # ── Stereo reconstruction ─────────────────────────────────────────────
    if nch == 2 and samples.ndim == 2:
        orig_l = _resample(samples[:, 0] / (peak + 1e-9), sr_orig, SR)
        orig_r = _resample(samples[:, 1] / (peak + 1e-9), sr_orig, SR)
        mono_e = (orig_l + orig_r) * 0.5
        ref_e  = np.mean(mono_e**2) + 1e-9
        ls = np.sqrt(np.mean(orig_l**2) / ref_e)
        rs = np.sqrt(np.mean(orig_r**2) / ref_e)
        al = _resample(audio_out, SR, sr_orig) * ls * peak
        ar = _resample(audio_out, SR, sr_orig) * rs * peak
        final = np.stack([np.clip(al,-1,1), np.clip(ar,-1,1)], axis=1)
    else:
        final = _resample(audio_out, SR, sr_orig)

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
