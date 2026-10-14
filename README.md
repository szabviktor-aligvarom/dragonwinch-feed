# Dragon Winch ár- és készletfeed

Napi frissülő feed a https://www.dragonwinch.com webshopból (gyártói beszállítói oldal), a mi cikkszámainkra leképezve.

**Állandó linkek**
- JSON: https://raw.githubusercontent.com/szabviktor-aligvarom/dragonwinch-feed/main/feed/feed.json
- CSV: https://raw.githubusercontent.com/szabviktor-aligvarom/dragonwinch-feed/main/feed/feed.csv

Frissítés: GitHub Actions, naponta 05:30 UTC (7:30 nyári, 6:30 téli budapesti idő). A githubusercontent gyorsítótára miatt a friss fájl akár ~5 percet is késhet.

## Kulcs
`sku` = a saját (ClOUD ERP) cikkszám, egyedi. `supplier_sku` = beszállítói cikkszám. A gyártó oldalán nincs cikkszám, ezért a `sku_map.csv` köti össze a sort a termékoldallal (`product_id`) és a változattal (`voltage`, `rope_m`).

**Új termék felvétele:** egy sor a `sku_map.csv`-be (a `product_id` az URL végén lévő szám, pl. `...,51,86.html` → `86`). `expected=no` jelöli a kivezetett/nem létező termékeket (nem számítanak a védelmekbe).

## Mezők
| mező | jelentés |
|---|---|
| price_gross / price_net | bruttó (23% ÁFA) / nettó ár PLN-ben, kiskereskedelmi (a gyártó webshopjának ára) |
| list_price_gross, on_sale, discount_pct | áthúzott ár, akciós-e, kedvezmény % (a gyártó oldala jelenleg nem mutat áthúzott árat) |
| in_stock, stock_text | csak van/nincs (`minimum 100` = van, `chwilowy brak na magazynie` = nincs); darabszám nincs |
| weight_kg | bruttó szállítási súly a leírásból (kötélhossz szerint) |
| last_changed | mikor változott utoljára az ár/készlet; checked_at = utolsó ellenőrzés |
| duplicate_sku | igaz, ha a cikkszám duplikált volt (az in_stock sor maradt) |
| status | ok / not_on_site / fetch_error / variant_error |
| flags | figyelmeztetések (számított kötélfelár, nem ellenőrizhető feszültség stb.) |

A 30 m-es kötél ára az oldalon alapár + felár (a feed kiszámolja, `flags`-ben jelölve).

## Védelmek
A script hibával áll le (a workflow piros lesz, GitHub e-mailt küld), és **nem írja felül** a feedet, ha: kevesebb mint `MIN_OK_ROWS` (30) sor sikeres; a várt sorok >20%-ánál nincs ár; vagy az előző futáshoz képest >30%-kal esik a sikeres sorok száma.
