# MangoDango – Server- & Automatisierungsmodus

MangoDango kann jetzt ohne grafische Oberfläche auf einem Server laufen und
selbstständig prüfen, ob es neue Kapitel der bereits geladenen Manga gibt.

## Einmaliger Lauf (z. B. für cron)

    python main.py --once

Prüft den Zielordner (bzw. `--output`) auf neue Kapitel und lädt sie herunter,
sofern der automatische Download aktiviert ist. Danach beendet sich das Programm.

## Dauerbetrieb nach Zeitplan

    python main.py --server

Läuft dauerhaft und startet zu den Zeiten, die im Einstellungen-Dialog unter
„Automatisierung" gespeichert wurden. Der Zeitplan wird bei jedem Prüfzyklus neu
gelesen, Änderungen aus der GUI wirken also ohne Neustart.

## Nützliche Optionen

| Option            | Wirkung                                                        |
|-------------------|---------------------------------------------------------------|
| `--output DIR`    | Zielordner, der überwacht wird (Standard: gespeicherte Einstellung) |
| `--queue DATEI`   | Zusätzliche gespeicherte Warteliste (JSON) einbeziehen        |
| `--lang CODE`     | Sprache der Log-Ausgaben (z. B. `de`, `en`)                   |
| `--interval SEK`  | Prüfintervall des Zeitplans in Sekunden (Standard: 60)        |
| `--no-download`   | Nur prüfen, nichts herunterladen                              |
| `--download`      | Neue Kapitel herunterladen (Standard)                         |

Der Server benötigt keine Anzeige (kein X11/Wayland). Es wird ausschließlich
`QtCore` geladen; die GUI-Bibliotheken werden im Server-Modus nicht importiert.

## Zeitplan konfigurieren

Zeitpunkte werden in der GUI gesetzt: Button **„Einstellungen"** (oben rechts
neben „Personalisieren") → Reiter **„Automatisierung"**. Dort „Automatisierung
aktivieren" anhaken und beliebig viele Tag/Uhrzeit-Kombinationen hinzufügen. Der
Zeitplan gilt sowohl in der laufenden GUI als auch im Server-Modus.

## Beispiel: systemd-Dienst

    [Unit]
    Description=MangoDango Server
    After=network-online.target

    [Service]
    ExecStart=/usr/bin/python3 /pfad/zu/main.py --server --output /srv/manga
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target
