import os
import torch

_hub_dir = torch.hub.get_dir()
path = os.path.join(_hub_dir, "bshall_knn-vc_master", "matcher.py")
if not os.path.exists(path):
    print(f"ERROR: matcher.py not found at {path}")
    raise SystemExit(1)

code = open(path, encoding='latin-1').read()

# Fix 1: fast_cosine_dist
idx_start = code.find('def fast_cosine_dist')
idx_end = code.find('\nclass KNeighborsVC')

if idx_start == -1 or idx_end == -1:
    print('ERROR: could not find fast_cosine_dist markers')
else:
    new_func = (
        "def fast_cosine_dist(source_feats, matching_pool, device='cpu'):\n"
        "    \"\"\"Cosine distance via normalize+matmul.\"\"\"\n"
        "    import torch.nn.functional as F\n"
        "    source_feats = source_feats.to(device)\n"
        "    matching_pool = matching_pool.to(device)\n"
        "    if source_feats.dim() == 3: source_feats = source_feats.mean(dim=0)\n"
        "    if matching_pool.dim() == 3: matching_pool = matching_pool.mean(dim=0)\n"
        "    src_norm = F.normalize(source_feats, p=2, dim=-1)\n"
        "    match_norm = F.normalize(matching_pool, p=2, dim=-1)\n"
        "    cosine_sim = torch.mm(src_norm, match_norm.t())\n"
        "    return 1 - cosine_sim\n"
    )
    code = code[:idx_start] + new_func + code[idx_end:]
    print('Fix 1 applied: fast_cosine_dist')

# Fix 2: squeeze query_seq and synth_set in match()
old2 = (
    "        if synth_set is None: synth_set = matching_set.to(device)\n"
    "        else: synth_set = synth_set.to(device)\n"
    "        matching_set = matching_set.to(device)\n"
    "        query_seq = query_seq.to(device)"
)
new2 = (
    "        if synth_set is None: synth_set = matching_set.to(device)\n"
    "        else: synth_set = synth_set.to(device)\n"
    "        matching_set = matching_set.to(device)\n"
    "        query_seq = query_seq.to(device)\n"
    "        # collapse stereo dims to (seq_len, dim)\n"
    "        if query_seq.dim() == 3: query_seq = query_seq.mean(dim=0)\n"
    "        if matching_set.dim() == 3: matching_set = matching_set.mean(dim=0)\n"
    "        if synth_set.dim() == 3: synth_set = synth_set.mean(dim=0)"
)

if old2 in code:
    code = code.replace(old2, new2)
    print('Fix 2 applied: match() squeeze')
else:
    print('Fix 2 NOT FOUND - printing match area:')
    idx = code.find('def match(')
    print(repr(code[idx:idx+600]))

open(path, 'w', encoding='utf-8').write(code)
print('Done')
