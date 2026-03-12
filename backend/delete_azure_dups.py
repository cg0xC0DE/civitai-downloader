"""Delete duplicate Azure blobs via backend API (local dev backend: :53133)."""
import json, urllib.request

API_BASE = "http://127.0.0.1:53133"

blobs = [
    "generated/2026-02-18/661d03b2_befc3b9d_0.png",
    "generated/2026-02-18/1049635a_02b38e58_0.png",
    "generated/2026-02-18/6a0b0872_6da84dd5_0.png",
    "generated/2026-02-19/477ef8d4_57d9abae_0.png",
    "generated/2026-02-19/b10edaa2_be5c9941_0.png",
    "generated/2026-02-19/e3064d8f_43e169a0_0.png",
    "generated/2026-02-19/b9121f89_7de4aacf_0.png",
    "generated/2026-02-20/0c2e727c_dfb193c0_0.png",
    "generated/2026-02-20/4b6449bb_54a874aa_0.png",
    "generated/2026-02-20/74d353e8_1d31e2a5_0.png",
    "generated/2026-02-20/7f504dce_a96e17ed_0.png",
    "generated/2026-02-20/89606d7d_d3f490a9_0.png",
    "generated/2026-02-20/062d7701_fc683992_0.png",
    "generated/2026-02-20/daf5ef3b_af4a5498_0.png",
    "generated/2026-02-20/f140022d_e9c2737e_0.png",
    "generated/2026-02-20/616a3515_956790b9_0.png",
    "generated/2026-02-20/08d70e90_75922f96_0.png",
    "generated/2026-02-20/9d89dda9_1804d938_0.png",
    "generated/2026-02-21/26f5ab2e_2b75fb99_0.png",
    "generated/2026-02-21/73291fe5_53f158bc_0.png",
    "generated/2026-02-21/8543a51e_7b6bce15_0.png",
]

ok = 0
fail = 0
for bp in blobs:
    try:
        data = json.dumps({"path": bp}).encode()
        req = urllib.request.Request(
            f"{API_BASE}/api/azure/delete",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        print(f"  OK: {bp}")
        ok += 1
    except Exception as e:
        print(f"  FAIL: {bp} — {e}")
        fail += 1

print(f"\nDone. OK={ok}, Failed={fail}")
