from pathlib import Path

path = Path("shops.html")
text = path.read_text(encoding="utf-8")
rule = ".directory-card[hidden]{display:none}"
if rule in text:
    print("Visibility fix already exists")
    raise SystemExit(0)
needle = ".directory-card{display:flex;min-width:0;flex-direction:column;"
if needle not in text:
    raise SystemExit("Directory card CSS was not found")
text = text.replace(needle, rule + needle, 1)
if rule not in text:
    raise SystemExit("Failed to add hidden card rule")
path.write_text(text, encoding="utf-8")
