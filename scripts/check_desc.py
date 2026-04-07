"""Extract description from saved HTML."""
import re

html = open("output/product_detail.html", "r", encoding="utf-8").read()

# Meta description
m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I)
if m:
    print("META DESC:", m.group(1)[:500])
else:
    print("No meta description found")

# Look for description sections in class names
desc_classes = re.findall(r'class="[^"]*(?:desc|description)[^"]*"', html[:200000], re.I)
print(f"\nDescription-related classes ({len(desc_classes)}):")
for c in desc_classes[:10]:
    print(f"  {c[:100]}")

# Check for detail-decorate-root (common Alibaba description container)
if "detail-decorate-root" in html:
    print("\nHAS detail-decorate-root")
    # Extract a snippet
    idx = html.index("detail-decorate-root")
    print(html[idx-50:idx+500])

# Check for lazy-loaded description iframe
iframes = re.findall(r'<iframe[^>]*src="([^"]*)"', html, re.I)
print(f"\nIframes: {len(iframes)}")
for iframe in iframes[:5]:
    print(f"  {iframe[:150]}")
