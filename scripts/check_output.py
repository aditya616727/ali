import json

data = json.load(open("output/kukirin_products.json", "r", encoding="utf-8"))
print(f"Total products: {len(data)}")

for i, p in enumerate(data[:3]):
    print(f"\n=== Product {i+1} ===")
    print(f"Keys: {list(p.keys())}")
    print(f"Title: {p.get('title', '')[:80]}")
    desc = p.get("description", "")
    print(f"Description length: {len(desc)} chars")
    if desc:
        print(f"Description preview: {desc[:120]}...")
    print(f"Country: {p.get('country')}")
    print(f"State: {p.get('state')}")
    print(f"City: {p.get('city')}")
    print(f"Address: {p.get('address')}")
    print(f"Images: {len(p.get('images', []))}")
    if p.get("images"):
        print(f"  First image: {p['images'][0][:80]}...")
    print(f"ProductType: {p.get('productType')}")
    variants = p.get("variants", [])
    print(f"Variants: {len(variants)}")
    for j, v in enumerate(variants[:2]):
        print(f"  Variant {j+1}: name={v.get('name','')[:50]}, price={v.get('price')}, attrs={len(v.get('attributes',{}))}, images={len(v.get('images',[]))}")
    af = p.get("additionalFields", {})
    print(f"AdditionalFields ({len(af)} keys): {list(af.keys())}")
    print(f"  brand={af.get('brand')}, color={af.get('color')}, voltage={af.get('voltage')}")
    print(f"  maxSpeed={af.get('maxSpeed')}, motorPower={af.get('motorPower')}")
    print(f"Source URL: {p.get('source_url', '')[:80]}")
