"""Small per-server Discord text helpers. Stored game data remains language neutral."""


def phrase(language: str, english: str, german: str) -> str:
    return german if language == "de" else english


def scoring_label(language: str, mode: str) -> str:
    if mode == "players_beaten":
        return phrase(language, "1 + players beaten", "1 + besiegte Spieler")
    return phrase(language, "placement", "Platzierung")


PARSER_ERRORS_DE = {
    "Krillion: paste the daily dive number and a score from 0 to 700.": (
        "Krillion: Poste die Nummer des täglichen Tauchgangs und eine Punktzahl von 0 bis 700."
    ),
    "The Wiki Game Daily: paste the complete Share result with steps and time.": (
        "The Wiki Game Daily: Poste den vollständigen Teilen-Text mit Schritten und Zeit."
    ),
    "TimeGuessr: expected '#puzzle — score/50,000'.": (
        "TimeGuessr: erwartet wird '#<Nummer> — <Punkte>/50,000' aus dem Teilen-Text."
    ),
    "TimeGuessr: puzzle or score is outside the allowed range.": (
        "TimeGuessr: Rätselnummer oder Punktzahl liegt außerhalb des gültigen Bereichs."
    ),
    "Only standard Size It Up is supported, not other variants.": (
        "Nur das normale Size It Up wird unterstützt, keine anderen Varianten."
    ),
    "Size It Up: include a date or #puzzle and 'Score: number/500'.": (
        "Size It Up: Datum oder #Rätsel und 'Score: Zahl/500' angeben."
    ),
    "Size It Up: include only one puzzle date or number.": (
        "Size It Up: nur ein Datum oder eine Rätselnummer angeben."
    ),
    "Size It Up: invalid puzzle date.": "Size It Up: ungültiges Rätseldatum.",
    "Size It Up: puzzle number is outside the allowed range.": (
        "Size It Up: Rätselnummer liegt außerhalb des gültigen Bereichs."
    ),
    "Size It Up: score must be between 0 and 500.": (
        "Size It Up: die Punktzahl muss zwischen 0 und 500 liegen."
    ),
    "Metazooa: missing Animal puzzle number.": "Metazooa: Animal-Rätselnummer fehlt.",
    "Metazooa: include the game's link or #metazooa from Share.": (
        "Metazooa: den Spiellink oder #metazooa aus dem Teilen-Text angeben."
    ),
    "Metazooa: result contains both a solve and a failure.": (
        "Metazooa: das Ergebnis enthält zugleich einen Erfolg und einen Fehlschlag."
    ),
    "Metazooa: expected a solve in 1–20 guesses or the failure message.": (
        "Metazooa: erwartet wird ein Erfolg in 1–20 Versuchen oder der Fehlschlag-Text."
    ),
    "Result contains an invalid or excessively large number.": (
        "Das Ergebnis enthält eine ungültige oder zu große Zahl."
    ),
}

COMMAND_ERRORS_DE = {
    "Choose today, yesterday, or an ISO date such as 2026-11-13.": (
        "Wähle today, yesterday oder ein Datum wie 2026-11-13."
    ),
    "Use an IANA timezone such as Europe/Berlin or America/New_York.": (
        "Verwende eine Zeitzone wie Europe/Berlin oder America/New_York."
    ),
    "Cutoff must use 24-hour HH:MM, such as 00:00.": (
        "Der Tageswechsel muss im 24-Stunden-Format HH:MM stehen, z. B. 00:00."
    ),
    "Cutoff must be a valid 24-hour time.": (
        "Der Tageswechsel muss eine gültige Uhrzeit im 24-Stunden-Format sein."
    ),
    "A finalized day's puzzle mapping cannot change.": (
        "Die Rätselzuordnung eines abgeschlossenen Tages kann nicht geändert werden."
    ),
    "Cannot remap a puzzle or day with recorded submissions.": (
        "Ein Rätsel oder Tag mit gespeicherten Ergebnissen kann nicht neu zugeordnet werden."
    ),
    "Another puzzle is already explicitly assigned to that day.": (
        "Für diesen Tag wurde bereits ein anderes Rätsel festgelegt."
    ),
    "A finalized day cannot be changed.": (
        "Ein abgeschlossener Spieltag kann nicht geändert werden."
    ),
}


def parser_error(language: str, message: str) -> str:
    return PARSER_ERRORS_DE.get(message, message) if language == "de" else message


def command_error(language: str, message: str) -> str:
    return COMMAND_ERRORS_DE.get(message, message) if language == "de" else message
