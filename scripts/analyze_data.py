"""Analyze the product_jsdata.json to understand all available fields."""
import json

d = json.load(open("output/product_jsdata.json", "r", encoding="utf-8"))
prod = d["detailData"]["globalData"]["product"]
seller = d["detailData"]["globalData"]["seller"]
trade = d["detailData"]["globalData"]["trade"]
nm = d["detailData"]["nodeMap"]

print("=" * 60)
print("TITLE:", prod.get("subject"))
print("=" * 60)

print("\n=== MEDIA ITEMS ===")
for i, m in enumerate(prod.get("mediaItems", [])):
    url = m.get("imageUrl", {})
    if isinstance(url, dict):
        print(f"  [{i}] {url.get('big', '')[:80]}")
    else:
        print(f"  [{i}] {str(url)[:80]}")

print("\n=== PRICE ===")
price = prod.get("price", {})
print(f"  saleType: {price.get('saleType')}")
for tier in price.get("productLadderPrices", []):
    print(f"  tier: {json.dumps(tier)}")
print(f"  formatLadderPrice: {price.get('formatLadderPrice')}")

print("\n=== SKU ATTRS ===")
sku = prod.get("sku", {})
for a in sku.get("skuAttrs", []):
    name = a.get("attrName") or a.get("name") or "?"
    vals = []
    for v in a.get("skuAttrValues", []):
        val_info = {"value": v.get("value"), "name": v.get("name")}
        if v.get("imageUrl"):
            val_info["img"] = str(v.get("imageUrl"))[:60]
        vals.append(val_info)
    print(f"  {name}: {vals}")

print("\n=== SKU INFO MAP (first 5) ===")
for k, v in list(sku.get("skuInfoMap", {}).items())[:5]:
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")

print("\n=== PRODUCT BASIC PROPERTIES (first 15) ===")
for p in prod.get("productBasicProperties", [])[:15]:
    print(f"  {p.get('attrName', '?')}: {p.get('attrValue', '?')}")

print("\n=== PRODUCT KEY INDUSTRY PROPERTIES ===")
for p in prod.get("productKeyIndustryProperties", []):
    print(f"  {p.get('attrName', '?')}: {p.get('attrValue', '?')}")

print("\n=== PRODUCT OTHER PROPERTIES (first 15) ===")
for p in prod.get("productOtherProperties", [])[:15]:
    print(f"  {p.get('attrName', '?')}: {p.get('attrValue', '?')}")

print("\n=== SELLER INFO ===")
for k in sorted(seller.keys()):
    v = seller[k]
    if isinstance(v, (str, int, float, bool)):
        print(f"  {k}: {str(v)[:120]}")
    elif isinstance(v, dict):
        print(f"  {k}: dict -> {list(v.keys())[:6]}")
    elif isinstance(v, list):
        print(f"  {k}: list({len(v)})")

print("\n=== TRADE INFO ===")
for k in sorted(trade.keys()):
    v = trade[k]
    if isinstance(v, (str, int, float, bool)):
        print(f"  {k}: {str(v)[:120]}")
    elif isinstance(v, dict):
        print(f"  {k}: dict -> {list(v.keys())[:8]}")

print("\n=== DESCRIPTION MODULE ===")
desc = nm.get("module_description", {})
pd = desc.get("privateData", {})
print(f"  privateData keys: {list(pd.keys())[:10]}")
if pd.get("content"):
    print(f"  content: {str(pd['content'])[:300]}")
if pd.get("descriptionUrl"):
    print(f"  descriptionUrl: {pd['descriptionUrl']}")

print("\n=== COMPANY MODULE ===")
comp = nm.get("module_company", {})
print(f"  keys: {list(comp.keys())}")

print("\n=== SORTED ATTRIBUTE MODULE ===")
sa = nm.get("module_sorted_attribute", {})
sapd = sa.get("privateData", {})
print(f"  privateData keys: {list(sapd.keys())[:10]}")
if sapd.get("data"):
    for item in sapd["data"][:5]:
        print(f"  {item}")

print("\n=== PRODUCT SPECIFICATION MODULE ===")
spec = nm.get("module_product_specification", {})
print(f"  keys: {list(spec.keys())[:10]}")

print("\n=== MINI COMPANY CARD ===")
mcc = nm.get("module_mini_company_card", {})
mcc_pd = mcc.get("privateData", {})
print(f"  privateData keys: {list(mcc_pd.keys())[:10]}")
for k in mcc_pd:
    v = mcc_pd[k]
    if isinstance(v, (str, int, float, bool)):
        print(f"    {k}: {str(v)[:120]}")
    elif isinstance(v, dict):
        print(f"    {k}: {list(v.keys())[:8]}")
    elif isinstance(v, list):
        print(f"    {k}: list({len(v)})")
