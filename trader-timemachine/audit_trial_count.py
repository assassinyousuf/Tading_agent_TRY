"""
Audit of historical trials across V1 to V5.
"""
import re

def inspect_file(fname):
    with open(fname, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    print(f"=== {fname} ===")
    # Look for dict definitions or experiment names
    variants = re.findall(r'(\b(?:EXP|H1|H2|H3|H4|H5|CFG|V1|V2|V3|V4|V5)[A-Za-z0-9_\-]+)', text)
    unique_vars = sorted(set(variants))
    print(f"Unique variant tokens found: {len(unique_vars)}")
    print("Sample:", unique_vars[:15])

inspect_file('trader-timemachine/experiments.py')
inspect_file('trader-timemachine/research_htf.py')
inspect_file('trader-timemachine/research_v3_mean_reversion.py')
inspect_file('trader-timemachine/research_v4_relative_strength.py')
inspect_file('trader-timemachine/research_v5_trend_following.py')

def inspect_experiments():
    import json
    # V1
    print("\n--- V1 ---")
    with open('trader-timemachine/experiments.py') as f:
        text = f.read()
    cfgs = re.findall(r'"id":\s*"([^"]+)"', text)
    print("V1 Configs:", len(cfgs), cfgs)

    # V2
    print("\n--- V2 ---")
    with open('trader-timemachine/research_htf.py') as f:
        text = f.read()
    cfgs2 = re.findall(r'"id":\s*"([^"]+)"', text)
    print("V2 Configs:", len(cfgs2), cfgs2)

    # V3
    print("\n--- V3 ---")
    with open('trader-timemachine/research_v3_mean_reversion.py') as f:
        text = f.read()
    cfgs3 = re.findall(r'"id":\s*"([^"]+)"', text)
    print("V3 Configs:", len(cfgs3), cfgs3)

    # V4
    print("\n--- V4 ---")
    with open('trader-timemachine/research_v4_relative_strength.py') as f:
        text = f.read()
    cfgs4 = re.findall(r'"id":\s*"([^"]+)"', text)
    print("V4 Configs:", len(cfgs4), cfgs4)

    # V5
    print("\n--- V5 ---")
    with open('trader-timemachine/research_v5_trend_following.py') as f:
        text = f.read()
    cfgs5 = re.findall(r'"id":\s*"([^"]+)"', text)
    print("V5 Configs:", len(cfgs5), cfgs5)

inspect_experiments()
