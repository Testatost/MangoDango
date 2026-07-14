# MangoDango Mobile Reader V9

Der Mobile Reader stellt die lokale MangoDango-Bibliothek im selben WLAN/LAN für Handys und Tablets bereit. Er enthält Bibliothek, Kapitelliste, Manga-Reader und ausgewählte Verwaltungsfunktionen der Desktop-Bibliothek. Einen direkten Downloader gibt es in der mobilen Oberfläche weiterhin nicht.

## Aktivieren

1. MangoDango starten.
2. **Einstellungen → Mobiler Reader** öffnen.
3. **Mobilen Reader im lokalen Netzwerk aktivieren** einschalten.
4. Bind-IP und Port festlegen.
5. Einstellungen mit **OK** speichern.
6. Eine der angezeigten Adressen auf dem Handy öffnen.

Beispiel:

```text
http://192.168.1.42:8765
```

Mit der Bind-IP `0.0.0.0` lauscht MangoDango auf allen lokalen IPv4-Schnittstellen. Alternativ kann eine konkrete lokale IPv4-Adresse des Computers eingetragen werden.

Wenn mDNS im Netzwerk verfügbar ist, wird zusätzlich folgende Adresse angeboten:

```text
http://mangodango.local:8765
```

Eine öffentliche Domain wie `mangodango.de` kann die Anwendung nicht selbst erzeugen. Dafür wären eine registrierte Domain und eine passende DNS-Konfiguration erforderlich.

## Gemeinsamer Lesefortschritt

Die gemeinsame Datei liegt innerhalb der aktiven Bibliothek:

```text
<deine-bibliothek>/.mangodango/reading_state.json
```

Dadurch kann ein Manga beispielsweise am Desktop begonnen, auf dem Handy fortgesetzt und später wieder am Desktop an derselben Position geöffnet werden. Eine separate Übersicht **Zuletzt gelesen** wird weder in der Desktop-Bibliothek noch im Mobile Reader angezeigt.

## Reader-Funktionen

- Responsive Manga-Bibliothek mit Original-MangoDango-Branding, Cover, Favoritenanzeige, Suche und Sortierung. Ein Tipp auf Logo oder MangoDango-Schriftzug führt jederzeit direkt zur Bibliotheksübersicht.
- Ein Häkchen `✓` links neben dem Manga-Titel erscheint automatisch, sobald die letzte Seite des aktuell neuesten Kapitels gelesen wurde. Wird später ein neues Kapitel hinzugefügt, verschwindet das Häkchen wieder, bis auch dieses Kapitel vollständig gelesen wurde.
- Beim Öffnen eines Manga stehen **Von Beginn an**, **Weiterlesen** und **Neustes Kapitel** untereinander zur Verfügung.
- Kapitelliste mit dem neuesten Kapitel oben und dem ersten Kapitel unten.
- Reader für Bildordner und CBZ-Dateien.
- Long-Strip-, Einzel- und Doppelseitenmodus.
- Seiten und Spreads passen sich im Einzel- und Doppelseitenmodus an die verfügbare Bildschirmhöhe im Hoch- und Querformat an.
- Doppelseiten werden als gemeinsamer Spread ohne Lücke dargestellt und gemeinsam gezoomt.
- Im Einzelseitenmodus: nach rechts oder oben wischen für die nächste Seite, nach links oder unten wischen für die vorherige Seite.
- Pinch-Zoom und Verschieben des vergrößerten Bildes in allen Lesemodi, ohne die Benutzeroberfläche zu skalieren.
- Reader-Leisten und Seitenanzeige werden nach zwei Sekunden automatisch ausgeblendet und nur durch kurzes Antippen einer Manga-Seite wieder eingeblendet. Normales Scrollen blendet sie nicht ein.

## Vorladen der Kapitelseiten

Vor dem Öffnen eines Kapitels lädt der Mobile Reader dessen Seiten mit begrenzter Parallelität vor. Die Daten liegen nur innerhalb der aktuellen Browser-Sitzung im Arbeitsspeicher und dienen ausschließlich dazu, beim Lesen Wartezeiten zwischen den Seiten zu vermeiden. Es gibt dafür keine zusätzlichen Cache-Schaltflächen im Manga-Menü.

## Manga-Verwaltung im Mobile Reader

Das `…`-Menü enthält die wichtigsten Funktionen der Desktop-Bibliothek:

- Umbenennen
- Cover ändern
- Favoriten ändern
- automatische Updates aktivieren oder deaktivieren
- Quellseite öffnen
- Löschen

Löschvorgänge werden vor der Ausführung bestätigt. Einen direkten Manga-Downloader gibt es im Mobile Reader weiterhin nicht.

## Sprachen

Der Mobile Reader übernimmt automatisch die aktuell in MangoDango eingestellte Sprache. Eine Sprachänderung in der Desktop-App wird von der geöffneten mobilen Oberfläche automatisch erkannt.

Alle 24 unterstützten Sprachen besitzen denselben vollständigen Satz von Übersetzungsschlüsseln:

```text
BG, CS, DA, DE, EL, EN, ES, ET, FI, FR, GA, HR,
HU, IT, LT, LV, MT, NL, PL, PT, RO, SK, SL, SV
```

## Technische Struktur

HTML, CSS und JavaScript des Mobile Readers sind getrennt abgelegt:

```text
mangodango/mobile/static/index.html
mangodango/mobile/static/app.css
mangodango/mobile/static/app.js
```

Die Python-Seite kümmert sich um Bibliothek, API, gemeinsamen Lesefortschritt und Dateiauslieferung; die Weboberfläche kann unabhängig davon gepflegt werden.

Bei PyInstaller-Builds müssen die Paket-Daten von `mangodango` eingebunden werden. Spec-Dateien mit `collect_data_files("mangodango")` nehmen die statischen Dateien automatisch mit.

## Performance

- Nach dem Schließen des Desktop-Readers wird nur der gemeinsame Lesefortschritt aktualisiert; ein vollständiger Bibliotheks-Rescan entfällt.
- Beim Schließen der Einstellungen werden unveränderte Manga-Metadaten nicht mehr neu geschrieben und die Bibliothek wird nicht unnötig komplett neu eingelesen.
- Manga-Details werden im mobilen Reader im Hintergrund vorgeladen.
- Der Bibliotheks-Cache des Mobile Readers bleibt bis zu fünf Minuten gültig und wird bei Änderungen gezielt verworfen.
- Kapitelseiten werden mit begrenzter Parallelität vorgeladen.
- Lesefortschritt wird gedrosselt geschrieben, damit schnelles Blättern nicht unnötig viele Schreibvorgänge erzeugt.

## Firewall

Falls die Adresse auf dem Handy nicht erreichbar ist, muss der konfigurierte TCP-Port möglicherweise in der lokalen Firewall freigegeben werden.

Fedora mit firewalld:

```bash
sudo firewall-cmd --permanent --add-port=8765/tcp
sudo firewall-cmd --reload
```

Linux Mint mit aktivem UFW:

```bash
sudo ufw allow 8765/tcp
```

Unter Windows kann beim ersten Start eine Firewall-Abfrage erscheinen. MangoDango muss für private Netzwerke zugelassen werden.

## Sicherheit

Der Server akzeptiert ausschließlich Loopback-, Link-Local- und private Netzwerkadressen. Es gibt keine Passwortanmeldung. Der Port sollte deshalb nicht per Router-Portweiterleitung ins öffentliche Internet freigegeben werden.

Das mobile Verwaltungsmenü kann lokale Manga-Dateien verändern oder löschen. Löschvorgänge werden deshalb vorher bestätigt.
