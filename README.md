# Mainwelle ICS

Wandelt den [Veranstaltungskalender von Radio Mainwelle](https://www.mainwelle.de/veranstaltungskalender/)
(Region Bayreuth) täglich in eine abonnierbare iCalendar-Datei um.

**Feed:** `https://<github-user>.github.io/<repo>/mainwelle.ics`

- Quelle: das in die Kalenderseite eingebettete Events-JSON (1 Abruf pro Tag).
- Uhrzeiten werden aus Freitext geparst („19 Uhr“, „10.00“, „Sa:17:30-20:00“ …).
  Fehlt die Endzeit, gilt 2 h. Mehrtägige oder zeitlose Events werden ganztägig.
  Die Originalangabe steht immer in der Beschreibung.
- Liefert die Seite weniger als 5 Events (Layout geändert?), bleibt die alte Datei stehen
  und der Workflow schlägt fehl – GitHub schickt dann eine Mail.

Lokal testen:

```bash
python3 mainwelle_ics.py -o /tmp/mainwelle.ics
python3 mainwelle_ics.py --items tests/items_2026-09-27.json -o /tmp/test.ics   # offline
```
