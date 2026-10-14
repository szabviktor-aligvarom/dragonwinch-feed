#!/usr/bin/env python3
"""Dragon Winch ar- es keszletfeed. Csak standard library, nincs fuggoseg.

Bemenet : sku_map.csv  (sajat SKU -> gyartoi termek-ID + feszultseg/kotelhossz)
Kimenet : feed/feed.json, feed/feed.csv
Hiba eseten exit 1 es NEM irja felul a meglevo feedet.
"""
import csv, html, json, os, re, sys, time, urllib.request, concurrent.futures as cf
from datetime import datetime, timezone

BASE = "https://www.dragonwinch.com/"
UA = "Mozilla/5.0 (compatible; price-feed/1.0)"
VAT = 1.23
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "sku_map.csv")
OUT_JSON = os.path.join(ROOT, "feed", "feed.json")
OUT_CSV = os.path.join(ROOT, "feed", "feed.csv")

# --- vedelmek ---
MIN_OK_ROWS = int(os.environ.get("MIN_OK_ROWS", 30))   # ennyi sikeres sor kell minimum
MAX_NO_PRICE = 0.20                                    # 20%-nal tobbnek nem lehet ara hianya
MAX_DROP = 0.30                                        # elozo futashoz kepest max 30% esés

FIELDS = ["sku", "supplier_sku", "product_id", "name", "price_gross", "price_net",
          "currency", "list_price_gross", "on_sale", "discount_pct", "in_stock",
          "stock_text", "weight_kg", "url", "image_url", "last_changed", "checked_at",
          "duplicate_sku", "status", "flags"]


def fetch(pid):
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(f"{BASE}x,0,{pid}.html", headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    print(f"FETCH FAIL {pid}: {last}", file=sys.stderr)
    return None


def num(s):
    return float(s.replace(" ", "").replace(",", "."))


def clean(t):
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def parse_page(h):
    """Termekoldal -> dict. None, ha nem termekoldal (pl. kivezetett termek -> fooldal)."""
    nm = re.search(r'class="productName">([^<]*)', h)
    pg = re.search(r'itemprop="price" content="([\d.]+)"', h)
    if not nm or not pg:
        return None
    d = {"name": clean(nm.group(1)), "gross": float(pg.group(1))}
    net = re.search(r'class="priceTax priceNet[^"]*"><td class="tag">[^<]*<span class="colon">:</span></td>'
                    r'<td class="value"><span class="price[^"]*">([\d.]+)', h)
    d["net"] = float(net.group(1)) if net else round(d["gross"] / VAT, 2)
    cur = re.search(r'itemprop="priceCurrency" content="(\w+)"', h)
    d["currency"] = cur.group(1) if cur else ""
    d["on_sale"] = "pricePromotion" in h or "priceDiscount" in h
    old = re.search(r'class="priceTax price(?:Old|Before|Regular|List)[^"]*"[^>]*>.*?<span class="price[^"]*">([\d.]+)', h, re.S)
    d["list_gross"] = float(old.group(1)) if old else None
    av = re.search(r'itemprop="availability" href="[^"]*/(\w+)"', h)
    d["avail"] = av.group(1) if av else ""
    st = re.search(r'class="avaState"\s+alt="([^"]*)"', h)
    d["stock_text"] = clean(st.group(1)) if st else ""
    img = re.search(r'id="duzaFotkaLink"[^>]*data-foto="([^"]+)"', h)
    og = re.search(r'<meta property="og:image" content="([^"]+)"', h)
    d["image"] = (BASE + img.group(1).strip()) if img else (og.group(1) if og else "")
    al = re.search(r'<link rel="alternate" hreflang="pl" href="([^"]+)"', h)
    d["url"] = al.group(1) if al else ""
    d["selects"] = [[clean(o) for o in re.findall(r"<option[^>]*>([^<]*)</option>", b)]
                    for b in re.findall(r'<select name="a\[\d+\]\[\d+\]"[^>]*>(.*?)</select>', h, re.S)]
    txt = clean(re.sub(r"<[^>]+>", " ", h))
    w = re.search(r"Waga zestawu brutto(?: z liną ([\dm/ ]+?))?\s*:\s*([\d.,]+(?:\s*kg)?(?:\s*/\s*[\d.,]+\s*kg)?)", txt)
    d["weights"] = None
    if w:
        d["weights"] = ([x.replace("m", "").strip() for x in w.group(1).split("/")] if w.group(1) else None,
                        [num(x) for x in re.findall(r"[\d.,]+", w.group(2).replace("kg", ""))])
    return d


def build_row(m, d, now):
    flags = []
    gross, net = d["gross"], d["net"]
    # opciok: feszultseg, kotelhossz (felarral)
    for opts in d["selects"]:
        want = None
        if m["voltage"] and any(re.fullmatch(r"\d+V", o) for o in opts):
            want = [o for o in opts if o == m["voltage"]]
            if not want:
                flags.append(f"feszultseg_{m['voltage']}_nincs_az_oldalon(lehetosegek:{'/'.join(opts)})")
            continue
        if any(re.match(r"\d+ m\b", o) for o in opts):
            if m["rope_m"]:
                pick = [o for o in opts if re.match(rf"{m['rope_m']} m\b", o)]
            else:
                pick = opts[:1]
                flags.append("kotelhossz_nincs_megadva_alapopcio")
            if not pick:
                flags.append(f"kotelhossz_{m['rope_m']}m_nincs_az_oldalon")
                gross = net = None
                break
            sur = re.search(r"\(([+-])([\d\s.,]+)\s*[A-Z]{3}\s*brutto\)", pick[0])
            if sur:
                add = num(sur.group(2)) * (1 if sur.group(1) == "+" else -1)
                gross = round(gross + add, 2)
                net = round(gross / VAT, 2)
                flags.append(f"kotelfelar_{add:+.2f}_{d['currency']}_szamitott")
            continue
    if m["voltage"] and not any(any(re.fullmatch(r"\d+V", o) for o in s) for s in d["selects"]):
        flags.append("feszultseg_nem_jelolt_az_oldalon")
    if m["voltage"] == "24V" and not any("24V" in s for s in d["selects"]):
        flags.append("24V_nem_ellenorizheto_12V_arral_azonos_feltetelezes")
    # suly
    wkg = ""
    if d["weights"]:
        labels, vals = d["weights"]
        if labels and m["rope_m"] and m["rope_m"] in labels and len(vals) == len(labels):
            wkg = vals[labels.index(m["rope_m"])]
        elif vals:
            wkg = vals[0]
    list_gross = d["list_gross"]
    if list_gross and gross and list_gross > gross:
        disc = round((1 - gross / list_gross) * 100, 1)
    else:
        list_gross, disc = None, 0
    variant = " ".join(x for x in [m["voltage"], f"{m['rope_m']}m kötél" if m["rope_m"] else ""] if x)
    return {
        "sku": m["sku"], "supplier_sku": m["supplier_sku"], "product_id": m["product_id"],
        "name": (d["name"] + (" " + variant if variant else "")).strip(),
        "price_gross": gross if gross is not None else "", "price_net": net if net is not None else "",
        "currency": d["currency"], "list_price_gross": list_gross if list_gross else "",
        "on_sale": bool(d["on_sale"]), "discount_pct": disc,
        "in_stock": d["avail"] == "InStock", "stock_text": d["stock_text"],
        "weight_kg": wkg, "url": d["url"], "image_url": d["image"],
        "last_changed": now, "checked_at": now, "duplicate_sku": False,
        "status": "ok" if gross is not None else "variant_error", "flags": ";".join(flags),
    }


def empty_row(m, now, status):
    r = {k: "" for k in FIELDS}
    r.update(sku=m["sku"], supplier_sku=m["supplier_sku"], product_id=m["product_id"], price_gross="",
             price_net="", currency="PLN", in_stock=False, on_sale=False, discount_pct=0, duplicate_sku=False,
             last_changed=now, checked_at=now, status=status, flags="")
    return r


def dedupe(rows):
    by = {}
    for r in rows:
        by.setdefault(r["sku"], []).append(r)
    out = []
    for sku, lst in by.items():
        if len(lst) > 1:
            lst.sort(key=lambda r: (r["status"] != "ok", not r["in_stock"]))
            keep = lst[0]
            keep["duplicate_sku"] = True
            out.append(keep)
        else:
            out.append(lst[0])
    return out


def fail(msg):
    print(f"GUARD FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mp = list(csv.DictReader(open(MAP, encoding="utf-8")))
    pids = sorted({m["product_id"] for m in mp})
    with cf.ThreadPoolExecutor(4) as ex:
        pages = dict(zip(pids, ex.map(fetch, pids)))
    parsed = {p: (parse_page(h) if h else None) for p, h in pages.items()}
    prev = {}
    if os.path.exists(OUT_JSON):
        try:
            prev = {r["sku"]: r for r in json.load(open(OUT_JSON, encoding="utf-8"))["products"]}
        except Exception:
            prev = {}
    rows = []
    for m in mp:
        d = parsed[m["product_id"]]
        if d is None:
            rows.append(empty_row(m, now, "not_on_site" if pages[m["product_id"]] else "fetch_error"))
        else:
            rows.append(build_row(m, d, now))
    rows = dedupe(rows)
    # last_changed: csak ha ar/keszlet valtozott
    for r in rows:
        p = prev.get(r["sku"])
        if p and (p.get("price_gross"), p.get("in_stock")) == (r["price_gross"], r["in_stock"]):
            r["last_changed"] = p.get("last_changed", now)
    # --- vedelmek ---
    exp_skus = {m["sku"] for m in mp if m["expected"] == "yes"}
    exp_rows = [r for r in rows if r["sku"] in exp_skus]
    ok = [r for r in exp_rows if r["status"] == "ok" and r["price_gross"] != ""]
    no_price = len(exp_rows) - len(ok)
    print(f"sorok: {len(rows)}, vart: {len(exp_rows)}, ok: {len(ok)}, ar nelkul: {no_price}")
    if len(ok) < MIN_OK_ROWS:
        fail(f"csak {len(ok)} sikeres sor, minimum {MIN_OK_ROWS}")
    if exp_rows and no_price / len(exp_rows) > MAX_NO_PRICE:
        fail(f"{no_price}/{len(exp_rows)} sornal nincs ar (> {MAX_NO_PRICE:.0%})")
    prev_ok = sum(1 for r in prev.values() if r.get("status") == "ok" and r.get("price_gross") != "")
    if prev_ok and len(ok) < prev_ok * (1 - MAX_DROP):
        fail(f"a katalogus {prev_ok} -> {len(ok)} sorra esett (> {MAX_DROP:.0%})")
    revived = [r["sku"] for r in rows if r["sku"] not in exp_skus and r["status"] == "ok"]
    if revived:
        print("FIGYELEM: ujra elerheto, expected=no sorok:", revived)
    rows.sort(key=lambda r: r["sku"])
    meta = {
        "source": BASE, "currency": "PLN", "price_basis": "gyártó webshop (lengyel nyelvű oldal) kiskereskedelmi ára, bruttó = 23% ÁFÁ-val, nettó is külön mezőben",
        "stock_note": "csak van/nincs jelzés (a gyártó nem ad darabszámot)", "generated_at": now,
        "count": len(rows), "count_ok": len([r for r in rows if r["status"] == "ok"]),
        "count_in_stock": len([r for r in rows if r["in_stock"]]),
        "key": "sku (saját cikkszám, egyedi)",
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    tj, tc = OUT_JSON + ".tmp", OUT_CSV + ".tmp"
    with open(tj, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "products": rows}, f, ensure_ascii=False, indent=1)
    with open(tc, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("true" if v is True else "false" if v is False else v) for k, v in r.items()})
    json.load(open(tj, encoding="utf-8"))          # ervenyes JSON?
    assert len(list(csv.DictReader(open(tc, encoding="utf-8")))) == len(rows)
    os.replace(tj, OUT_JSON)
    os.replace(tc, OUT_CSV)
    print("OK, feed irva")


if __name__ == "__main__":
    main()
