import re

# Lire le fichier
with open("app/services/scraping_service.py", "r") as f:
    content = f.read()

# 1. Corriger BSTN URLs
old_bstn = '''    "bstn": [
        "https://www.bstn.com/eu_en/sale.html",
        "https://www.bstn.com/eu_en/sneaker.html",
        "https://www.bstn.com/eu_en/new.html",
    ],'''

new_bstn = '''    "bstn": [
        "https://www.bstn.com/eu_en/men/footwear.html",
        "https://www.bstn.com/eu_en/women/footwear.html",
        "https://www.bstn.com/eu_en/men.html",
    ],'''

content = content.replace(old_bstn, new_bstn)

# 2. Corriger Footpatrol URLs  
old_footpatrol = '''    "footpatrol": [
        "https://www.footpatrol.com/sale/",
        "https://www.footpatrol.com/footwear/",
        "https://www.footpatrol.com/mens/footwear/sale/",
    ],'''

new_footpatrol = '''    "footpatrol": [
        "https://www.footpatrol.com/footwear/",
        "https://www.footpatrol.com/mens/footwear/trainers/",
        "https://www.footpatrol.com/womens/footwear/trainers/",
    ],'''

content = content.replace(old_footpatrol, new_footpatrol)

# 3. Corriger Printemps URLs (trouver et remplacer la section)
printemps_pattern = r'"printemps": \[\s*#[^]]+\],'
new_printemps = '''"printemps": [
        "https://www.printemps.com/fr/fr/homme/chaussures/baskets--sneakers",
        "https://www.printemps.com/fr/fr/femme/chaussures/baskets--sneakers",
        "https://www.printemps.com/fr/fr/soldes/homme",
        "https://www.printemps.com/fr/fr/soldes/femme",
        "https://www.printemps.com/fr/fr/homme/vetements/pulls--cardigans",
    ],'''

content = re.sub(printemps_pattern, new_printemps, content, flags=re.DOTALL)

# Vérifier les remplacements
checks = [
    ("BSTN", "https://www.bstn.com/eu_en/men/footwear.html"),
    ("Footpatrol", "https://www.footpatrol.com/mens/footwear/trainers/"),
    ("Printemps", "https://www.printemps.com/fr/fr/homme/chaussures/baskets--sneakers"),
]

for name, url in checks:
    if url in content:
        print(f"OK: {name} URLs updated")
    else:
        print(f"ERROR: {name} URLs not updated")

# Sauvegarder
with open("app/services/scraping_service.py", "w") as f:
    f.write(content)

print("Done")
