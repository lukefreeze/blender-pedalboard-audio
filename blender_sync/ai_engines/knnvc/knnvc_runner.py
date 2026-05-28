"""
ai_engines/knnvc/knnvc_runner.py
=================================
Standalone runner called via system Python subprocess.
Applies the PyTorch 2.6+ compatibility patch to matcher.py before inference.

Usage:
    python knnvc_runner.py --src <wav> --ref <wav> --output <wav>
                           --topk 4 --ref_secs 30 --patch <patch.py>
"""

import sys
import os
import argparse


def apply_patch(patch_path):
    """Apply the kNN-VC matcher.py compatibility patch if needed."""
    if not patch_path or not os.path.exists(patch_path):
        return
    try:
        import torch
        hub_dir = torch.hub.get_dir()
        matcher = os.path.join(hub_dir, "bshall_knn-vc_master", "matcher.py")
        if not os.path.exists(matcher):
            return
        # Check if already patched
        code = open(matcher, encoding="latin-1").read()
        if "normalize+matmul" in code:
            return   # already patched
        # Apply patch
        exec(open(patch_path, encoding="utf-8").read())
        print("[KNNVC_RUNNER] patch applied")
    except Exception as e:
        print(f"[KNNVC_RUNNER] patch warning: {e}")


def run(src, ref, output, topk=4, ref_secs=30, patch_path=None):
    import torch
    import torchaudio

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"PROGRESS:5")

    # Apply compatibility patch
    apply_patch(patch_path)
    print(f"PROGRESS:10")

    # Load model
    knn_vc = torch.hub.load(
        "bshall/knn-vc", "knn_vc",
        prematched=True, trust_repo=True, pretrained=True)
    knn_vc = knn_vc.to(device)
    print(f"PROGRESS:30")

    # Trim reference to ref_secs to avoid VRAM issues
    ref_wav, ref_sr = torchaudio.load(ref)
    max_samples = ref_sr * ref_secs
    if ref_wav.shape[1] > max_samples:
        ref_wav = ref_wav[:, :max_samples]
        tmp_ref = output + "_ref_trimmed.wav"
        torchaudio.save(tmp_ref, ref_wav, ref_sr)
        ref = tmp_ref
    print(f"PROGRESS:40")

    # Extract features
    query_seq   = knn_vc.get_features(src)
    print(f"PROGRESS:60")
    matching_set = knn_vc.get_matching_set([ref])
    print(f"PROGRESS:75")

    # Convert
    out_wav = knn_vc.match(query_seq, matching_set, topk=topk)
    print(f"PROGRESS:90")

    # Save
    torchaudio.save(output, out_wav[None], 16000)
    print(f"PROGRESS:100")
    print(f"DONE:{output}")

    # Clean up temp ref
    try:
        tmp = output + "_ref_trimmed.wav"
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--src",      required=True)
    parser.add_argument("--ref",      required=True)
    parser.add_argument("--output",   required=True)
    parser.add_argument("--topk",     type=int, default=4)
    parser.add_argument("--ref_secs", type=int, default=30)
    parser.add_argument("--patch",    default=None)
    args = parser.parse_args()

    try:
        run(args.src, args.ref, args.output,
            topk=args.topk, ref_secs=args.ref_secs, patch_path=args.patch)
    except Exception as e:
        import traceback
        print(f"ERROR:{e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
