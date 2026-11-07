"""All user-facing terms of the drawing step in one place: object names, column labels with
units, display formats (cm for levels) and help texts. Display only: the stored values and
the calculation do not depend on anything here."""

from __future__ import annotations

from typing import NamedTuple


class Field(NamedTuple):
    label: str
    fmt: str | None = None  # printf-style display format in tables
    help: str | None = None


LEVEL = "%.2f"  # levels in m NAP: cm
FLOW = "%.1f"  # m³/d
LENGTH = "%.1f"  # m
RESISTANCE = "%g"  # d

# what you draw (radio "Wat teken je?")
OBJECTS = {
    "watergang": "Watergang",
    "drain": "Buisdrainage",
    "put": "Put",
    "ghb": "Vast peil met weerstand (GHB)",
    "chd": "Vaste stijghoogte (CHD)",
    "damwand": "Damwand",
    "zone": "Zone",
}
# type of a watercourse (stored as "riv" / "drn")
KIND = {
    "riv": "watergang (RIV: in- en uitstroom)",
    "drn": "drain (DRN: alleen afvoer)",
}
KIND_HELP = (
    "Watergang (RIV): water stroomt in én uit, begrensd op de bodem. Drain (DRN): voert "
    "alleen af als het grondwater boven het peil staat, bijvoorbeeld een droogvallende sloot."
)

WATERCOURSE = {
    "name": Field("Naam"),
    "kind": Field("Type", help=KIND_HELP),
    "stage_ref": Field("Peil referentie [m NAP]", LEVEL, "Waterpeil in de huidige situatie."),
    "stage_new": Field(
        "Peil na opzet [m NAP]", LEVEL,
        "Waterpeil na de ingreep. Gelijk aan het referentiepeil = geen opzet.",
    ),
    "bottom": Field(
        "Bodem [m NAP]", LEVEL,
        "Bodemhoogte van de watergang. Met de breedte bepaalt die de natte omtrek en de lagen "
        "waarop de watergang aansluit.",
    ),
    "width": Field("Breedte [m]", "%.2f", "Breedte van de watergang (voor de natte omtrek)."),
    "length": Field(
        "Lengte per cel bij lasso [m]", LENGTH,
        "Alleen voor cellen die je met rechthoek/lasso kiest; bij een lijn volgt de lengte "
        "per cel uit de getekende lijn.",
    ),
    "entry_resistance": Field(
        "Intreeweerstand [d]", RESISTANCE,
        "Weerstand van slootbodem en -wand (slib, klei). 0 = geen intreeweerstand; de radiale "
        "weerstand rekent de app er zelf bij.",
    ),
    "conductance": Field(
        "C per volle cel [m²/d] (0 = automatisch)", "%.1f",
        "Laat op 0: de app berekent C uit breedte, waterdiepte, intreeweerstand, de geraakte "
        "lagen en de celgrootte-correctie. Vul alleen in om een conductance uit een ander "
        "model over te nemen; bij gedeeltelijke cellen schaalt die met de lengte.",
    ),
}  # fmt: skip

Q_HELP = "Positief = onttrekking (bemaling), negatief = infiltratie. Per put, dus per cel."
HEAD_REF_HELP = "Vaste stijghoogte in de huidige situatie."
HEAD_NEW_HELP = "Vaste stijghoogte na de ingreep."
GROUPS = {
    "wells": {
        "name": Field("Naam"),
        "layer": Field("Laag", help="Rekenlaag waaruit de put pompt."),
        "q_ref": Field("Debiet per put referentie [m³/d]", FLOW, Q_HELP),
        "q_new": Field("Debiet per put na opzet [m³/d]", FLOW, Q_HELP),
        "radius": Field(
            "Putstraal [m]", "%.2f",
            "Alleen voor de stijghoogte in de put zelf (Peaceman); de kaart toont het "
            "celgemiddelde.",
        ),
    },
    "ghb": {
        "name": Field("Naam"),
        "layer": Field("Laag"),
        "head_ref": Field("Stijghoogte referentie [m NAP]", LEVEL, HEAD_REF_HELP),
        "head_new": Field("Stijghoogte na opzet [m NAP]", LEVEL, HEAD_NEW_HELP),
        "resistance": Field(
            "Weerstand c [d]", RESISTANCE,
            "Weerstand tussen het vaste peil en het grondwater: conductance per cel = "
            "celoppervlak / c.",
        ),
    },
    "chd": {
        "name": Field("Naam"),
        "layer": Field("Laag"),
        "head_ref": Field("Stijghoogte referentie [m NAP]", LEVEL, HEAD_REF_HELP),
        "head_new": Field("Stijghoogte na opzet [m NAP]", LEVEL, HEAD_NEW_HELP),
    },
}  # fmt: skip
GROUP_TITLES = {
    "wells": "Putten",
    "ghb": "Vaste peilen met weerstand (GHB)",
    "chd": "Vaste stijghoogten (CHD)",
}
WALL = {
    "name": Field("Naam"),
    "layer": Field(
        "Tot en met laag", help="De wand loopt vanaf de bovenkant door alle lagen tot en met "
        "deze. Eindigt hij halverwege een pakket: splits dat pakket in stap 1."
    ),
    "impermeable": Field("Ondoorlatend"),
    "resistance": Field(
        "Weerstand c_w [d]", RESISTANCE,
        "Alleen als de wand niet ondoorlatend is. Doorstroming per meter wand = laagdikte × "
        "verschil in stijghoogte / c_w.",
    ),
    "reference": Field("Ook in referentie", help="Aan: bestaande wand, ook in de referentie."),
}  # fmt: skip
