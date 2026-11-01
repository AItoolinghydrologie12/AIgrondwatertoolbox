# Grondwatertoolbox

Streamlit-webapp met rekenproducten voor geohydrologische vraagstukken. De app is bedoeld
voor hydrologen, adviseurs en reviewers van bemalings- en peilbesluitrapportages.

> **Disclaimer:** indicatieve berekeningen; geen garantie op juistheid. Controleer uitkomsten
> en aannames zelf voordat je ze in een rapportage gebruikt.

| Product | Regime | Methoden |
|---|---|---|
| Grondwatertoolbox (rooster, numeriek) | stationair | numeriek rooster in bovenaanzicht (cellen 10–50 m, max 100 × 100 cellen: tot 5 km bij 50 m), watergangen tekenen per cel, zones met eigen lagen en maaiveld, RIV/DRN, putten (WEL), GHB-groepen, kwel via de onderrand, modelrand per laag, buisdrainage per cel, maaivelddrainage, neerslagoverschot |

**Ondergrond:** kies de bovenkant (deklaag met freatisch niveau, of freatisch zandpakket) en
1–4 watervoerende pakketten met scheidende lagen ertussen. **Watergangen** teken je op de kaart
en geef je op met peil, bodem, breedte en intreeweerstand; de lagen waarop ze aansluiten
volgen uit de geometrie (open wanden).

De eerdere producten *Watergang als rand bij een bemaling* en *Peilopzet met
buurwatergangen* zijn verwijderd (terug te halen uit de git-geschiedenis, commit `2a9aac2`).
De analytische rekenkern (meerlagen, x-z doorsnede, 1D) staat nog in `gwproducten/`.

## Installatie op Windows (schone omgeving)

Vereist: **Python 3.14** (`py -3.14 --version` moet werken) en Git.

```powershell
git clone <repo-url> grondwatertools
cd grondwatertools
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[app,dev]"
```

Werk je liever met een geactiveerde omgeving: `.venv\Scripts\Activate.ps1` (PowerShell). Geeft
dat een fout over het uitvoeringsbeleid, gebruik dan de volledige paden zoals hierboven.

> **OneDrive:** staat de projectmap in OneDrive, dan synchroniseert OneDrive ook `.venv`
> (duizenden bestanden). Dat is traag en kan bestanden vergrendelen. Zet de map
> bij voorkeur buiten OneDrive, of sluit `.venv` uit van synchronisatie.

## Gebruik

| Wat | Commando | VS Code |
|---|---|---|
| Tests | `.venv\Scripts\python -m pytest` | taak **Tests**, of de testweergave |
| Tests tegen de echte kaartdiensten (internet) | `.venv\Scripts\python -m pytest -m online` | – |
| Lint | `.venv\Scripts\python -m ruff check .` | taak **Lint** |
| Formatteren (controle) | `.venv\Scripts\python -m ruff format --check .` | taak **Lint** |
| Formatteren | `.venv\Scripts\python -m ruff format .` | bij opslaan (ruff-extensie) |
| App starten | `.venv\Scripts\python -m streamlit run app/Home.py --server.address localhost` | taak **Start app**, of F5 → *Start app* |

**MODFLOW 6 installeren (Windows, eenmalig).** flopy komt mee met `.[app]`; het programma
`mf6.exe` haal je zo op:

```powershell
.venv\Scripts\get-modflow.exe :flopy --subset mf6
.venv\Scripts\python -m pytest tests/test_mf6_comparison.py -v
```

`get-modflow` downloadt `mf6.exe` van GitHub (MODFLOW-ORG/executables) naar
`%LOCALAPPDATA%\flopy\bin`. Zonder `mf6` rekent de app met de eigen solver en worden de
MF6-tests overgeslagen. Op een Linux-server (Streamlit Cloud) installeert de app `mf6` zelf
bij de eerste keer (zie *Veiligheid*).

De app opent op <http://localhost:8501> (alleen lokaal bereikbaar door `--server.address localhost`).
VS Code: open de map, installeer de aanbevolen extensies (Python, Ruff) en kies de interpreter
`.venv`. In `.vscode/launch.json` staan *Start app* en *Debug huidige test*.

## Structuur

```
gwproducten/            rekenkern (pip-installeerbaar, los van de UI)
  core/                 parameters met eenheden en validatie, lagenopbouw, grids
  analytic/             meerlagen-eigenwaarde-oplossing, watergangconductance C', stroken-weerstand
  numeric/              rooster in bovenaanzicht (RIV/DRN), 1D eindige volumes, x-z doorsnede
  products/<product>/   META, Params, compute(), schematic(), theory.md
  geo/                  kaartgegevens voor een locatie: PDOK (adres, kaarten, TOP10NL, AHN), REGIS II
app/                    Streamlit: Home.py, pages/, gedeelde UI-bouwstenen
tests/                  validatie per product + rooktest van alle pagina's
```

Elk product heeft dezelfde opbouw: schematische doorsnede, invoer in de zijbalk
(ondergrond / watergangen / ingreep / numeriek) en de tabs *Resultaat · Theorie · Aannames ·
Validatie · Export*.

## Afhankelijkheden (vastgepind in `pyproject.toml` en `requirements.txt`)

| Pakket | Reden |
|---|---|
| numpy, scipy | rekenwerk; `scipy.sparse` voor de numerieke modellen, `brentq` voor de 5 cm-grens, elliptische integralen |
| pandas | resultaattabellen en CSV-export |
| plotly | interactieve figuren en doorsnedes |
| streamlit | de webapp |
| kaleido | PNG-export van figuren; heeft Chrome nodig (bij ontbreken: `plotly_get_chrome`, of gebruik het camera-icoon in de figuur) |
| pytest, ruff | tests en lint/format (alleen ontwikkeling) |
| flopy (+ matplotlib, vereist door flopy) | MODFLOW 6 als tweede rekenkern: modelbestanden schrijven, draaien en uitlezen |
| requests, pillow | kaartgegevens voor een modellocatie (PDOK, REGIS): webverzoeken en kaartbeelden/AHN-raster; beide zaten al in de omgeving via streamlit |

De app rekent standaard met een eigen solver; MODFLOW 6 (via flopy) is de tweede rekenkern en
de referentie voor de tests.

## Rekenmethode en verantwoording

De Grondwatertoolbox heeft **twee rekenkernen** die dezelfde vergelijkingen oplossen:

- **eigen solver** (standaard): een numerieke oplosser in Python (numpy/scipy), snel;
- **MODFLOW 6** (USGS): de app bouwt een MF6-model met flopy, laat `mf6` rekenen en leest
  stijghoogten en waterbalans terug. Eerst de standaardformulering; bij droogvallende cellen
  of geen convergentie Newton; lukt ook dat niet, dan rekent de eigen solver en meldt de app
  dat.

Kiezen: zijbalk *Beoordeling en numeriek* → **Rekenkern**. Het resultaat toont altijd waarmee
gerekend is. De pagina **Rekenkern MODFLOW 6** legt de verschillen uit en rekent je model met
beide kernen; onder *Export* staat het MODFLOW 6-model als zip. In beide gevallen maakt de app
zelf de invoer (conductances van watergangen, doorlopende rand) en de putstijghoogte
(Peaceman). De volledige afleiding met formules staat in
de app onder **Theorie** (bron: `gwproducten/products/peilopzet_grid/theory.md`).

**Waar wat staat in de code**

| Onderdeel | Bestand |
|---|---|
| Rooster, matrix, randvoorwaarden, Picard-iteratie, massabalans | `gwproducten/numeric/grid2d.py` |
| Schematisering, zones, conductance per cel, waarschuwingen | `gwproducten/products/peilopzet_grid/__init__.py` |
| Lagenopbouw, rekenlagen, weerstand tussen lagen (incl. kv/kh) | `gwproducten/core/layers.py` |
| Aansluiting watergang op de lagen (natte omtrek, bodem in klei) | `gwproducten/products/_layered.py` |
| Radiale weerstand (Ernst), strookweerstand, watergang-conductance | `gwproducten/analytic/boundaries.py` |
| Analytische meerlagenoplossing (alleen voor validatie) | `gwproducten/analytic/multilayer.py` |
| Validatie (vergelijkingen met analytische oplossingen) | `tests/` |

**Vergelijking met MODFLOW 6**

| Onderdeel | Grondwatertoolbox | MODFLOW 6 |
|---|---|---|
| Discretisatie | eindige volumes, celcentra, vierkante cellen | idem (DIS, block-centred) |
| Lagen | één rekenlaag per watervoerend pakket; scheidende lagen als weerstand c (quasi-3D) | scheidende lagen zijn gewone modellagen met lage K33 |
| Horizontale geleiding | T = kh·D, harmonisch gemiddelde tussen cellen | idem (NPF, vaste T) |
| Verticale geleiding | A / (½D₁/kv₁ + c + ½D₂/kv₂) | idem bij één laag per pakket en de klei als eigen laag |
| Transmissiviteit | standaard kh × natte dikte, droge cellen op 1 % van D (uit te zetten: vast) | idem met "convertible" lagen en Newton |
| Neerslag | vast neerslagoverschot op de bovenste laag | RCH |
| Watergangen | RIV of DRN: q = C(h_peil − h), RIV begrensd op de bodem | RIV / DRN, zelfde formules |
| Conductance watergang | **berekend door de app**: natte omtrek per laag, intreeweerstand, radiale weerstand (Ernst), strookweerstand bij een bodem in klei, celgrootte-correctie (De Lange) | moet de gebruiker zelf opgeven |
| Buisdrainage, maaivelddrainage | DRN op drainniveau resp. maaiveld | DRN |
| Putten | vast debiet per cel en laag; stijghoogte in de put met Peaceman | WEL (stijghoogte in de put: MAW) |
| Vaste peilen met weerstand, kwel | GHB per celgroep; kwel als GHB op de onderste laag | GHB |
| Modelrand | buitenste ring vaste stijghoogte, doorlopend pakket (GHB met C = T·Δy/(Δx/2 + λ)) of gesloten, ook per laag | CHD, GHB (conductance zelf bepalen) of geen-stroming |
| Aan/uit van RIV/DRN | Picard-iteratie tot geen toestand meer wisselt | idem, binnen de buitenste iteraties van IMS |
| Lineaire oplosser | directe LU-ontbinding (SuperLU) met hergebruik als preconditioner (PCG, tolerantie 10⁻¹²) | iteratief (IMS: CG/BiCGSTAB met ILU) |
| Tijd | alleen stationair (referentie en opzet) | stationair en tijdsafhankelijk |

**Belangrijkste verschillen met een MODFLOW 6-model**
- Geen stroming *langs* scheidende lagen en geen verticale stroming binnen een pakket buiten
  de ½D/kv-term: elk pakket is één laag (Dupuit). Bij partieel doordringende watergangen
  vangt de radiale weerstand dat op.
- **Transmissiviteit volgt de natte dikte** (standaard aan, zoals een MODFLOW 6
  "convertible" laag): T = kh × (grondwaterstand of bovenkant − onderkant laag). Dat geldt
  voor het freatisch zandpakket en voor elk pakket waarvan de stijghoogte onder de bovenkant
  zakt (de app meldt dat). Een cel die droogvalt rekent door met 1 % van de laagdikte, zoals
  de Newton-methode van MODFLOW 6; de kaart toont daar de onderkant van de laag. Een deklaag
  van klei houdt een vaste T (die is al heel klein). Verschil met MODFLOW 6: de verticale
  weerstand blijft op de volle dikte (MF6 zonder VARIABLECV, de standaard) en tussen cellen
  geldt het harmonisch gemiddelde zonder upstream-weging. Uitgezet (zijbalk *Beoordeling en
  numeriek*) rekent de app met vaste T = kh · D. Effect: bij peilopzet klein (0,2 m op 5 m
  freatisch pakket ≈ 4 % meer T); bij putten in een freatisch pakket wordt de verlaging
  groter dan met vaste T.
- De uitwisseling met watergangen rekent de app zelf uit (Ernst/De Lange). In MODFLOW hangt
  dit helemaal af van wat de modelleur als conductance invult.

**Validatie** (geautomatiseerde tests, `pytest`)
- **Tegen MODFLOW 6.7** (flopy; elke laag een MF6-modellaag, scheidende lagen met
  K33 = D/c, dezelfde randgroepen en conductances): stijghoogten in alle lagen gelijk binnen
  10⁻⁶ m voor het standaardmodel (vaste en meebewegende T), een deklaag met put in pakket 1,
  kwel en doorlopende rand, een zone met rand per laag, en een freatische put waarbij de natte
  dikte van 10 naar 7,7 m gaat (MF6 "convertible", standaardformulering). MF6 met Newton
  wijkt daar tot enkele cm af in de putcel, door de stroomopwaartse weging van de natte dikte
  (verschil tussen de twee MF6-formuleringen). Bij **droogvallende cellen** wijken app en MF6
  af: MF6-Newton convergeerde in het testgeval niet, MF6 zonder Newton zet droge cellen uit;
  de app rekent door met 1 % natte dikte (zie *Bekende beperkingen*).
- Lange evenwijdige watergangen tegen de analytische meerlagenoplossing (eigenwaarde-aanpak,
  Bruggeman/Hemker): met celgrootte-correctie binnen 0,6 % voor 2 t/m 4 pakketten, ook met
  anisotropie.
- Grondwaterstand tussen GHB-randen met neerslag: exacte parabool.
- Put in een lekkend pakket tegen De Glee: binnen 2 % vanaf 3 cellen van de put.
- Overgang tussen zones (harmonisch gemiddelde): exact tegen twee pakketdelen in serie.
- Bodem in een scheidende laag tegen een numerieke x-z doorsnede (zie bekende beperkingen).
- Radiale weerstand met anisotropie (kv/kh = 1; 0,33; 0,1) tegen een x-z doorsnede: 0–1 %
  afwijking onder een deklaag, 1–5 % bij een freatisch pakket.
- Massabalans < 10⁻⁸ in alle scenario's; roosterverfijning convergeert naar de analytische
  oplossing.

**Bronnen**: Bruggeman (1999), Ernst (1962), De Lange (1999), De Glee (1930), Hemker
(meerlagensystemen), Harbaugh (2005, MODFLOW-2005: RIV/DRN) en Langevin e.a. (2017,
*Documentation for the MODFLOW 6 Groundwater Flow Model*, USGS Techniques and Methods
6-A55). Volledige verwijzingen staan onder **Theorie** in de app.

**MODFLOW 6 en flopy** zijn software van de U.S. Geological Survey, open source en in het
publieke domein: vrij te gebruiken en te verspreiden, ook beroepsmatig. Bij gebruik
vermelden: Langevin e.a. (2017, zie boven) en Bakker, M. e.a. (2016), *Scripting MODFLOW
model development using Python and FloPy*, Groundwater 54(5). De Grondwatertoolbox is geen
product van en niet goedgekeurd door de USGS.

## Bekende beperkingen

**Algemeen**
- Alleen stationair.
- De transmissiviteit volgt de natte dikte (uit te zetten); de verticale weerstand niet.
- De uitwisseling van een watergang volgt C' = 1/(c/u + R_rad), met de radiale weerstand
  R_rad = ln(aD/u')/(π√(kh·kv)), a = √(kh/kv) (Ernst, anisotroop). Dit geldt voor een
  natte omtrek die klein is ten opzichte van de laagdikte.
- Een watergang die **in de deklaag eindigt** is analytisch benaderd (wanden naar de
  freatische laag, bodem via de resterende klei naar pakket 1); dit kan 25–50 % afwijken van
  een x-z doorsnede.
- Een watergang die **in een diepere scheidende laag eindigt** koppelt de bodem via de rest
  van die laag aan het pakket eronder; wanden in de scheidende laag tellen niet mee. T.o.v.
  een x-z doorsnede: +13 % als alleen de bodem in de klei ligt, tot 2,5× te laag als het
  hele natte profiel erin ligt (de uitwisseling is dan wel zeer klein). De app waarschuwt.
- Bij een deklaag als bovenkant ligt het freatisch niveau bovenin de deklaag en zit de volle
  c tussen grondwaterstand en pakket 1.
- De referenties in `theory.md` zijn uit vakkennis samengesteld en nog niet online
  gecontroleerd op jaartal en exacte titel.

**Grondwatertoolbox (rooster, numeriek)**
- De pagina werkt in drie stappen: *1 Ondergrond en maaiveld*, *2 Watergangen tekenen*,
  *3 Berekenen en resultaat*. Er wordt pas gerekend na een klik op **Bereken** (zijbalk of
  stap 3); na een wijziging blijft het oude resultaat staan met een melding.
- Het dwarsprofiel (horizontaal, verticaal of een eigen geklikte lijn) is bilineair
  geïnterpoleerd tussen celmiddens; pieken tussen twee celmiddens worden dus afgevlakt.
- Rekentijd: het grootste rooster (100 × 100 cellen × 2 lagen) ca. 0,2 s per
  berekening (referentie + opzet); op een gedeelde cloudserver langer.
- Maximaal 4 pakketten. Met meebewegende transmissiviteit kost een model met
  droogvallende cellen rond putten meer rekentijd (lokaal ca. 5 s bij 100 × 100 cellen en 4
  pakketten; zonder droogval ca. 1,5 s). Een cel die tussen nat en droog blijft wisselen wordt
  per cel gedempt (zoals delta-bar-delta in MF6).
- **Droogvallende cellen** zijn een benadering: het putdebiet wordt niet verlaagd en het
  water komt dan van onderen of van opzij. Rond zo'n put is de uitkomst onzeker; de app meldt
  het aantal droge cellen. Een put die een freatische laag laat droogvallen is in de praktijk
  meestal niet haalbaar.
- **Anisotropie** kv/kh per pakket (standaard 0,33): de weerstand tussen pakketten is
  c + D₁/(2kv₁) + D₂/(2kv₂), zoals MODFLOW 6 met één laag per pakket. Ook de radiale
  weerstand van watergangen rekent met kv/kh (Ernst met opgerekte verticale maten).
- **Zones** (max 5): zelfde opbouw als de basis (schematisering en aantal lagen), eigen
  diktes, k, c en maaiveld; diktes tellen vanaf het lokale maaiveld, drainage volgt het
  lokale maaiveld. Tussen cellen het harmonisch gemiddelde van T. Lagen die echt uitwiggen
  kent het model niet (benader met een kleine c). Een cel in twee zones hoort bij de
  laatst gekozen zone.
- Watergangen liggen midden in hun cel; bij spreidingslengtes kleiner dan de cel is het
  verloop rond de watergang grof (de app waarschuwt). De celgrootte-correctie (De
  Lange-principe) houdt de uitwisseling goed: benchmark −0,3 % bij 25 m (+3,5 % zonder).
- Modelrand: vast (alle lagen één peil), doorlopend pakket, gesloten, of per laag een van
  deze drie. *Doorlopend pakket* neemt aan dat de omgeving ongestoord op het randpeil staat
  en benadert per laag een half oneindig lekkend pakket (spreidingslengte λ); een laag zonder
  lekkage krijgt zo een gesloten rand. Test: 1 km-model wijkt 1,1 cm af van een groot model
  bij 0,2 m opzet (vaste rand 18 cm). Leg de rand ver genoeg van de opzet.
- **Putten** (WEL): vast debiet per cel en laag, apart voor referentie en opzet. De kaart
  toont het celgemiddelde; de tabel *Putten* geeft ook de stijghoogte in de put (Peaceman,
  met putstraal), binnen 1 % van De Glee bij cellen van 10–50 m. Zonder putweerstand (skin)
  en partiële filterlengte. Validatie celgemiddelde: De Glee binnen 2 % vanaf 3 cellen van
  de put. Met vaste T waarschuwt de app bij een verlaging van meer dan 25 % van de
  freatische laagdikte. Een debiet wordt niet automatisch verlaagd als cellen droogvallen
  (zoals MODFLOW 6 zonder AUTO_FLOW_REDUCE).
- **GHB-groepen** en **kwel-onderrand**: q = A/c (h_ext − h), altijd actief. Het kwelpeil φ is
  gelijk in referentie en opzet.
- Watergangen teken je als **lijn** (klikpunten op een hulprooster van cel/5); de lengte per
  cel volgt uit de lijn, dus ook gedeeltelijke cellen, diagonalen en bochten. Met
  rechthoek/lasso kies je volle cellen (plassen, buisdrainage). De lengte per cel is
  achteraf per cel aan te passen. Een watergang ligt rekenkundig altijd in het celmidden.
- **Locatie en kaartgegevens** (optioneel, stap 1 → *Locatie*): adres zoeken (PDOK
  Locatieserver) of RD-middelpunt invullen. Daarmee:
  - **achtergrondkaart** onder teken- en resultaatkaart: topografie (BRT TOP10NL), luchtfoto
    (Beeldmateriaal Nederland) of AHN-hoogtekaart, elk binnen ca. 1 s.
  - **watergangen ophalen** (stap 2): TOP10NL (hartlijnen 0,5–3 m en 3–6 m, vlakken > 6 m;
    duikers vallen weg; breedte = midden van de breedteklasse; een vlak wordt lengte per cel =
    oppervlak in de cel / breedte). Kleine greppels ontbreken in TOP10NL.
    **Binnen WDODelta** (Waterschap Drents Overijsselse Delta) komen daar bij:
    - **peil uit de peilgebiedenkaart**: kies zomer-, winter-, min- of maxpeil; elk stuk
      watergang (verdicht tot 10 m) krijgt het peil van het peilgebied waarin het ligt, een
      lijn wordt dus gesplitst waar het peil verspringt. Ontbreekt het gekozen peil, dan
      winter- (bij min) of zomerpeil (bij max). Peil na opzet = dit peil.
    - **primaire en secundaire watergangen uit de legger**: lijnen uit de open lagen
      *Primaire/Secundaire watergang* (2018), breedte en bodem waar mogelijk uit de
      vastgestelde legger (13-12-2022, op watergangcode). **Bodem = gemiddelde van de
      bodemhoogte boven- en benedenstrooms.** TOP10NL-stukken binnen 10 m van een
      leggerlijn vallen weg (geen dubbeltelling).
    - waar de legger geen bodem geeft: **bodem = peil − waterdiepte** (aanname, aanpasbaar:
      legger primair 1,0 m, secundair 0,6 m, TOP10NL 0,4 / 0,8 / 1,5 m). In een proef
      (2 × 2 km) had de legger een bodemhoogte bij ~95 % van de watergangen op het platteland
      en bij ~25 % in Zwolle.
    - ligt de legger-bodem op of boven het gekozen peil (sloot staat droog, bijv. bij
      winterpeil), dan zet de app die bodem op peil − 0,10 m en meldt dat ("legger,
      aangepast"); pas zelf aan of kies type drain.
    - één watergang per (bron, peilgebied, breedte, bodem); bij veel profielen wordt grover
      afgerond (max. 40). Peilgebiedgrenzen staan als stippellijn op de tekenkaart.
    Buiten WDODelta (of met de schakelaar uit) is het peil een standaardwaarde (maaiveld −
    1,2 m) met de melding "(peil invullen)". Andere waterschappen kunnen later worden
    toegevoegd (`gwproducten/geo/waterschap.py`, tabel `WATERSCHAPPEN`). Geïmporteerde
    lijnen liggen in modelmeters: verschuif je daarna de locatie, haal ze dan opnieuw op.
  - **maaiveld uit AHN** (0,5 m): mediaan per cel (5 × 5 waarden), dan de mediaan voor het
    model en per zone, met P10/P90. De app rekent met één maaiveld per zone.
  - **ondergrond uit REGIS II** (TNO, 100 m-rooster, midden van het model): voorstel voor de
    lagenopbouw (zand/kalk = pakket, klei/complex/veen = scheidende laag, dunne klei met
    c < c_min telt mee in het pakket, kD en c blijven behouden, kv/kh 0,33 waar REGIS geen kv
    heeft, max. 4 pakketten en daaronder een dichte basis). **REGIS geeft geen weerstand
    voor de Holocene deklaag**: die blijft op de waarde in de app. Een regionaal model:
    controleer met boringen. De REGIS-server (dinodata.nl) heeft geen
    beschikbaarheidsgarantie; zonder internet werkt de app gewoon zonder kaartgegevens.
  - het **MODFLOW 6-model** krijgt de RD-hoek linksonder (DIS XORIGIN/YORIGIN) en rij 1 is
    de noordrand, zodat het model in GIS op zijn plek ligt.
  - Bronnen en licenties staan onder elke kaart/tabel: PDOK (Fair Use, gratis, geen key),
    Kadaster BRT TOP10NL en Beeldmateriaal Nederland (CC BY 4.0), AHN (CC0), REGIS II
    (TNO-GDN, BRO), peilgebieden en legger van WDODelta (open data / openbare legger).
    Elk verzoek betreft één modelgebied, met een timeout en de app-naam als
    User-Agent; adressen worden niet bewaard (alleen de RD-coördinaat in je eigen project).
- **Waterbalans** (stap 3 → *Waterbalans en conductances*): staafdiagram per post, referentie
  naast opzet; positief = water het grondwater in.
- **Tekenen** ververst alleen het tekenpaneel (Streamlit-fragment); bij 100 × 100 cellen gaat
  per klik ca. 0,5 MB naar de browser (was 1,3 MB).
- Invoer en tekeningen staan alleen in de browsersessie. Bewaar ze via de zijbalk →
  *Project opslaan of laden* (JSON-bestand met alle invoer, watergangen, buisdrainage,
  putten, GHB-groepen en zones) en laad ze later weer in.

## Publiceren op Streamlit Community Cloud

1. Zet de code in een GitHub-repo (alleen de branch `main`).
2. Log in op <https://share.streamlit.io> met je GitHub-account en kies **Create app**.
3. Kies de repo, branch `main` en als hoofdbestand `app/Home.py`.
4. Open **Advanced settings** en kies **Python 3.14** (moet gelijk zijn aan
   `requires-python` in `pyproject.toml`). De versie ligt na het deployen vast; wijzigen
   betekent de app verwijderen en opnieuw deployen.
5. **Deploy.** De cloud installeert `requirements.txt`; de regel `.` installeert ook het
   rekenpakket `gwproducten` zelf.

Goed om te weten:

- De app gebruikt geen geheimen (geen `st.secrets`, API-keys of wachtwoorden).
- **Veiligheid MODFLOW 6 op de server.** De enige verbinding naar buiten is één download bij
  de eerste start: het openbare Linux-pakket van MODFLOW 6.7.0 (release 29.0 van
  MODFLOW-ORG/executables op GitHub), via een vast adres zonder account of token. De app
  controleert de SHA-256 tegen de waarde in `gwproducten/products/peilopzet_grid/mf6.py`
  en gebruikt het pakket alleen bij een exacte overeenkomst; alleen `mf6` wordt uitgepakt
  (± 50 MB, in `~/.cache/grondwatertoolbox`). Mislukt dat, dan rekent de app met de eigen
  solver. Elke MF6-berekening draait in een eigen tijdelijke map die meteen wordt verwijderd;
  er worden geen gegevens van bezoekers bewaard of verstuurd. MODFLOW 6 is publiek domein
  (USGS).
- `.streamlit/config.toml` bevat bewust geen `[server]`-instellingen; die regelt de cloud.
  Lokaal start de app alleen op localhost via `--server.address localhost`.
- Gratis plan: ongeveer 1 GB geheugen en gedeelde rekenkracht. Het grootste rooster blijft
  daar ruim binnen; de rekencache is begrensd (`max_entries`). Zonder bezoekers valt de app
  na een tijd in slaap; de volgende bezoeker klikt op *wake up* en wacht even.
- PNG-export via kaleido werkt in de cloud niet (geen Chrome); gebruik het camera-icoon in de
  figuur. CSV-export werkt wel.
