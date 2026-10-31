## Probleemstelling

Een peilopzet in één of meer watergangen, in een gebied met een eigen patroon van sloten,
greppels en buisdrainage. In plaats van evenwijdige, oneindig lange watergangen teken je hier
zelf de watergangen op een rooster in bovenaanzicht. Het model rekent stationair met een
vast neerslagoverschot, twee keer: met het **referentiepeil** en met het **peil na
opzet**. Het verschil is de verhoging door de opzet, met de 5 cm-contour.

## Schematisering

- **Rooster** van maximaal 100 × 100 vierkante cellen van 10 tot 50 m (standaard 25 m):
  bij 10 m hooguit 1 km, bij 25 m 2,5 km en bij 50 m 5 km breed en hoog. De
  **basis**-lagenopbouw is een deklaag met freatisch niveau óf een freatisch zandpakket, met
  maximaal 4 watervoerende pakketten. Tot 5 **zones** krijgen eigen laagwaarden en een eigen
  maaiveld (zie *Zones*). Alle hoogtes zijn in m NAP.
- **Rekenlagen**: een freatische knoop en elk pakket. De transmissiviteit volgt standaard de
  **natte dikte** (zoals een MODFLOW 6 "convertible" laag, zie *Meebewegende
  transmissiviteit*); uitgeschakeld is ze vast, $T = k_h D$ (lineair).
- **Weerstand tussen knopen**: de $c$ van de scheidende laag plus de verticale weerstand van
  de halve pakketten erboven en eronder, net als in MODFLOW 6 met één modellaag per pakket:

$$
c_{i,i+1} = \frac{D_i}{2\,k_{v,i}} + c + \frac{D_{i+1}}{2\,k_{v,i+1}} ,\qquad
k_v = (k_v/k_h)\cdot k_h ,
$$

  met standaard $k_v/k_h$ = 0,33 per pakket. Een deklaag als bovenkant heeft geen halve term:
  zijn volle $c$ ligt tussen grondwaterstand en pakket 1. Ook de radiale weerstand van
  watergangen rekent met de anisotropie (zie *Conductance per cel*).
- **Neerslagoverschot** $N$ [m/d] op de freatische knoop van elke cel.
- **Modelrand**: vaste stijghoogte in de buitenste ring cellen (alle lagen), **doorlopend
  pakket**, gesloten (geen stroming), of **per laag** een van deze drie.

**Doorlopend pakket.** Buiten het model loopt elk pakket door als half oneindig lekkend
pakket dat ongestoord op het randpeil $h_r$ staat. Een afwijking aan de rand dempt daar uit
als $e^{-x/\lambda}$, met $\lambda = \sqrt{T c_\text{eff}}$ en $1/c_\text{eff}$ de som van de
geleidingen naar de aangrenzende knopen (en de kwel-onderrand voor de onderste laag). De
randcel krijgt daarom per buitenzijde een GHB naar $h_r$ met

$$
C = \frac{T\,\Delta y}{\Delta x/2 + \lambda} ,
$$

een halve cel plus de Mazure-weerstand $\lambda/T$ per meter rand; een hoekcel heeft twee
buitenzijden. Zonder lekkage ($\lambda \to \infty$) is $C = 0$: de rand werkt dan als
gesloten. Dit is de randvoorwaarde voor één lekkend pakket, per laag toegepast; de koppeling
tussen de lagen buiten het model is dus benaderd, en $T$ is de nominale $k_h D$. Validatie: een
put met de rand op $2\lambda$ volgt De Glee even goed als een model met de rand op $5\lambda$; en
een model van 1 km met deze rand wijkt bij een opzet van 0,2 m maximaal 1,1 cm af van het
midden van een model van 2,5 km (vaste rand op 1 km: 18 cm, gesloten: 10 cm).

Per cel en per knoop $i$ geldt de waterbalans (eindige volumes, celcentra):

$$
\sum_\text{buren} \frac{T_i\,\Delta y}{\Delta x}(h_\text{buur} - h) + \sum_\text{lagen}\frac{A}{c}(h_\text{ander} - h)
+ N A\,[i=0] + \sum Q_\text{rand} = 0 ,\qquad A = \Delta x\,\Delta y .
$$

## Zones

Een zone is een set cellen met een eigen lagenopbouw en een eigen maaiveld. Cellen buiten
elke zone hebben de basisopbouw; ligt een cel in meer zones, dan telt de laatste zone.

- **Zelfde structuur, eigen waarden.** Een zone heeft dezelfde schematisering van de
  bovenkant en hetzelfde aantal lagen als de basis, maar eigen dikte, $k$ en $c$ per laag.
  Een laag die lokaal ontbreekt (bijv. een onderbroken scheidende laag) benader je met een
  kleine $c$ of een dunne laag; echt uitwiggende lagen kent het model niet.
- **Lagen vanaf lokaal maaiveld.** De diktes gelden vanaf het maaiveld van de zone; de hele
  kolom schuift mee. Het lokale maaiveld bepaalt dus in welke laag een watergangbodem ligt.
- **Drainage relatief.** Maaivelddrainage werkt op het lokale maaiveld, buisdrainage op
  lokaal maaiveld min de (overal gelijke) draindiepte.
- **Overgang tussen cellen.** De horizontale geleiding tussen twee buurcellen met
  transmissiviteit $T_1$ en $T_2$ is het **harmonisch gemiddelde** (twee halve cellen in
  serie, zoals in MODFLOW). De verticale geleiding $A/c$ volgt de weerstand van de cel zelf.

$$
C_{12} = \frac{2\,T_1 T_2}{T_1 + T_2}\,\frac{\Delta y}{\Delta x}
$$

- **Watergangen per cel.** Natte omtrek per laag, aansluiting op de lagen, conductance en
  celgrootte-correctie worden per cel berekend met de lagen en het maaiveld van de zone van
  die cel. Een watergang die een zone in loopt, kan daar dus op een andere laag aansluiten.

## Watergangen, drainage en maaiveld

Alle randvoorwaarden zijn **head-dependent** zoals in MODFLOW:

| element | flux naar het grondwater | geldig |
|---|---|---|
| watergang **RIV** | $C(h_\text{peil} - h)$ | $h > h_\text{bodem}$ |
| | $C(h_\text{peil} - h_\text{bodem})$ (vast) | $h \le h_\text{bodem}$ |
| watergang **DRN** | $C(h_\text{peil} - h)$, alleen afvoer | $h > h_\text{peil}$ |
| **buisdrainage** (per cel aan/uit) | $\frac{A}{w_d}(h_d - h)$ | $h > h_d$ |
| **maaivelddrainage** (alle cellen) | $\frac{A}{w_\text{mv}}(h_\text{mv} - h)$ | $h > h_\text{mv}$ |

De afwisseling actief/inactief is niet-lineair. Het stelsel wordt daarom iteratief opgelost:
oplossen, per element de toestand bepalen, en herhalen tot er geen toestand meer verandert
(Picard).

De toestanden veranderen alleen de diagonaal van de matrix. Daarom wordt één
LU-ontbinding (minimum-graadordening) hergebruikt: exact als de matrix gelijk is, anders
als preconditioner voor geconjugeerde gradiënten (relatieve tolerantie $10^{-12}$); bij
veel gewijzigde cellen wordt opnieuw ontbonden. De opzetrun start vanuit de toestanden
van de referentie en deelt de ontbinding.

**Conductance per cel.** Uit de geometrie volgen per watergang de geraakte lagen: de natte
omtrek per laag, met open wanden. Per knoop is

$$
C'_i = \frac{1}{c_\text{wg}/u_i + R_{\text{rad},i}} \quad [\text{m/d per m}],
\qquad C_{\text{cel},i} = \frac{L}{1/C'_i + R_{\text{grid},i}} ,
$$

met $L$ de lengte van de watergang in de cel. De waterdiepte volgt uit referentiepeil −
bodemhoogte.

**Radiale weerstand met anisotropie (Ernst).** Alleen in het pakket met de bodem stroomt het
water met een trechter naar de watergang. Rek de verticale maten op met
$a = \sqrt{k_h/k_v}$; dan is het pakket isotroop met $k' = \sqrt{k_h k_v}$, dikte $aD$ en
natte omtrek $u' = B + a\,(u - B)$ (bodembreedte $B$ blijft, de wanden worden langer):

$$
R_\text{rad} = \frac{\ln\!\left(a D / u'\right)}{\pi \sqrt{k_h k_v}} ,
\qquad R_\text{rad} = 0 \text{ als } u' \ge aD .
$$

Voor $k_v = k_h$ is dit de bekende $\ln(D/u)/(\pi k)$. De intreeweerstand $c_\text{wg}/u$
blijft op de werkelijke omtrek. Vergeleken met een x-z doorsnede (pakket 20 m, $k_h$ = 15 m/d,
watergang 2 m breed, 1 m water, $c_\text{wg}$ = 0,1–1 d, $k_v/k_h$ = 1; 0,33; 0,1) wijkt de
uitwisseling hiermee 0–1 % af onder een deklaag en 1–5 % bij een freatisch pakket; de
isotrope formule gaf bij $k_v/k_h$ = 0,1 tot 18 % te veel uitwisseling. Je kunt ook zelf een conductance per cel opgeven; die wordt dan in dezelfde
verhouding over de lagen verdeeld.

**Bodem in een scheidende laag.** Eindigt een watergang in een scheidende laag (de deklaag,
of een diepere scheidende laag onder een pakket), dan:

- koppelen de wanden in de pakketten erboven zoals hierboven ($c_\text{wg}/u_i$);
- koppelen de wanden binnen de scheidende laag niet (verwaarloosbare uitwisseling), behalve
  bij een deklaag als bovenkant: die wanden koppelen aan de freatische knoop;
- stroomt de bodem (breedte $B$) via de **resterende dikte** $t$ van die scheidende laag naar
  het pakket eronder, als strook op een anisotrope laag (conforme afbeelding):

$$
C'_\text{bodem} = \frac{1}{c_\text{wg}/B + R_\text{strook}} ,\qquad
R_\text{strook} = \frac{K(1-p)}{\sqrt{k_h k_v}\,K(p)} ,\quad
p = \exp\!\left(-\frac{\pi B}{t}\sqrt{k_v/k_h}\right),
$$

met $k_v = D/c$ van de scheidende laag en $K$ de volledige elliptische integraal. Voor een
brede strook gaat dit naar $t/(k_v B)$, verticale stroming. Dit is een benadering: de
uitwisseling via de wanden in de scheidende laag ontbreekt. Vergeleken met een x-z doorsnede
(freatisch pakket 5 m, scheidende laag 4 m met $c$ = 400 d, watergang 2 m breed, 1,5 m water)
is de afwijking +13 % als alleen de bodem in de scheidende laag ligt; ligt het hele natte
profiel in de scheidende laag, dan is de uitwisseling 2 à 2,5 keer te laag — maar dan is ze
ook zeer klein, omdat de klei de watergang grotendeels afsluit. Voor een bodem in de
deklaag geldt een afwijking van 25–50 %.

**Celgrootte-correctie (De Lange-principe).** Een watergang van een paar meter breed in een
cel van 25 m stuurt de **lijn**stijghoogte, terwijl het model één **cel**stijghoogte
kent. De Lange (1999) koppelt daarom de watergang aan de cel met een extra weerstand die
het verschil tussen lijn en cel compenseert. Die weerstand is hier afgeleid voor een lange
watergang in een lekkende laag met spreidingslengte $\lambda_i = \sqrt{T_i c_{\text{eff},i}}$,
met $1/c_{\text{eff},i}$ de som van de geleidingen naar de aangrenzende knopen:

- exacte stijfheid lijn → laag: $K_\text{exact} = 2T/\lambda$;
- stijfheid van de discrete cel: $K_\text{grid} = 2T(1-r)/\Delta x + \Delta x/c_\text{eff}$, met
  $r + 1/r = 2 + (\Delta x/\lambda)^2$;
- $R_\text{grid} = 1/K_\text{exact} - 1/K_\text{grid}$.

Voor $\Delta x \ll \lambda$ gaat $R_\text{grid}$ naar 0. Bij $\Delta x$ in de orde van
$\lambda$ of groter compenseert de correctie dat het rooster de steile stijghoogteverandering
rond de watergang niet kan volgen. De correctie is afgeleid voor één laag. In het
meerlagensysteem is ze daarom een benadering; de validatie toetst hoe goed ze werkt.
Getoetst tegen de analytische meerlagenoplossing (lange evenwijdige watergangen, 25 m):
met correctie binnen 0,6 % voor 2, 3 en 4 pakketten, ook onder een deklaag; zonder
correctie +3 % (freatisch zand) tot +54 % (deklaag met 4 pakketten).

## Putten, GHB en kwel

**Putten (WEL).** Een putgroep is een set cellen in één rekenlaag met per put een vast debiet
$Q$ [m³/d], apart voor referentie en opzet. Positief $Q$ = onttrekking (verlaging), negatief
= infiltratie. In de balans van de cel staat $-Q$. Een put zit, net als in MODFLOW, verdeeld
over de hele cel: de berekende celstijghoogte is een celgemiddelde en dus minder diep dan de
stijghoogte in de put zelf. Die rekent de app achteraf uit met de correctie van Peaceman
(1978), zoals het MAW-pakket van MODFLOW 6:

$$
h_\text{put} = h_\text{cel} - \frac{Q}{2\pi T}\,\ln\frac{r_e}{r_w} ,\qquad r_e = 0{,}198\,\Delta x ,
$$

met $r_w$ de putstraal en $T$ van de cel (bij meebewegende $T$ de actuele). Vergeleken met De
Glee op $r = r_w$ (lekkend pakket, $\lambda$ = 100 m) is de afwijking 0,1–0,9 % bij cellen van
10 tot 50 m, terwijl het celgemiddelde 40–65 % minder verlaging geeft. Een putweerstand
(skin) en partiële filterlengte zitten er niet in. Ligt de putstijghoogte onder de onderkant
van de laag, dan meldt de app dat het debiet niet haalbaar is.

De **verlaging door de putten** toont de app apart. Met vaste $T$ is het model lineair bij
vaste toestanden van RIV/DRN: de verlaging is dan de oplossing van hetzelfde stelsel met
alleen de putdebieten als belasting. Met meebewegende $T$ is het niet-lineair; dan rekent de
app de opzetsituatie nog een keer zonder putten en is de verlaging het verschil.

**Meebewegende transmissiviteit (MODFLOW 6 "convertible").** Per cel is

$$
T = k_h\, b ,\qquad b = \min\!\big(\max(\min(h, z_\text{top}) - z_\text{bot},\; 0{,}01\,D),\; D\big),
$$

met $b$ de natte dikte. Voor het freatisch zandpakket is $z_\text{top}$ het maaiveld; een
pakket onder een scheidende laag blijft volledig gevuld zolang de stijghoogte boven de
bovenkant ligt, en wordt plaatselijk freatisch als ze eronder zakt (melding). De knoop in een
deklaag van klei houdt een vaste $T$ (verwaarloosbaar klein). Tussen cellen geldt het
harmonisch gemiddelde van $k_h b$, zoals MODFLOW 6 standaard (zonder upstream-weging). De
verticale weerstand $\tfrac{1}{2}D/k_v$ blijft op de volle dikte (MODFLOW 6 zonder
VARIABLECV). $T$ wordt in dezelfde Picard-iteratie bijgewerkt als de RIV/DRN-toestanden, tot
de stijghoogte minder dan $10^{-5}$ m verandert. Een cel met de minimale natte dikte (1 % van
$D$) is **droog**: ze blijft meerekenen, zoals bij de Newton-methode van MODFLOW 6, en de
kaarten tonen daar de onderkant van de laag. Validatie: freatische strook met neerslag tussen
twee vaste peilen tegen Dupuit, $h^2 = h_0^2 + (N/k)\,x(L-x)$, binnen 1 cm.

**Algemene stijghoogterand (GHB).** Een GHB-groep is een set cellen in één rekenlaag met een
externe stijghoogte $h_\text{ext}$ (referentie en opzet apart) en een weerstand $c_\text{ghb}$
[d]:

$$
q = \frac{A}{c_\text{ghb}}\,(h_\text{ext} - h) ,
$$

altijd actief (in- en uitstroom). Bruikbaar voor een boezem of kanaal met een vast peil, een
plas, of een leiding die niet als watergang getekend wordt.

**Kwel/wegzijging via de onderrand.** Onder de onderste rekenlaag ligt een diep pakket met
stijghoogte $\varphi$ [m NAP], gescheiden door een laag met weerstand $c_\text{kwel}$ [d].
Dat is een GHB op de onderste rekenlaag in alle cellen: $q = A(\varphi - h)/c_\text{kwel}$
(positief = kwel). $\varphi$ is in referentie en opzet gelijk: de opzet verandert het diepe
pakket niet, alleen de kwelflux via $h$.

Met de kwel-onderrand en één pakket is de verlaging door een put in een oneindig gebied de
oplossing van De Glee:

$$
s(r) = \frac{Q}{2\pi T}\,K_0\!\left(\frac{r}{\lambda}\right),\qquad \lambda = \sqrt{T c_\text{kwel}} .
$$

## Resultaat

- stijghoogte per laag (referentie en opzet) en de grondwaterstand t.o.v. maaiveld;
- verhoging = opzet − referentie, met de 5 cm-contour;
- waterbalans per run: neerslag, watergangen (per watergang), putgroepen, GHB-groepen, kwel
  onderrand, buisdrainage, maaiveld, rand;
- verlaging door de putten (als er putten zijn);
- aantal cellen waar het grondwater boven maaiveld komt (maaivelddrainage actief).

## Aannames en geldigheid

- **Stationair**. Transmissiviteit standaard volgens de natte dikte; de verticale weerstand
  rekent met de volle laagdikte.
- **Ondergrond**: basis plus maximaal 5 zones met dezelfde laagstructuur; maximaal 4
  watervoerende pakketten. Een sprong in de lagenopbouw ligt op een celgrens; binnen een cel
  is de ondergrond uniform.
- **Celgrootte**: een watergang zit midden in de cel. Kleiner dan de cel kan hij niet
  liggen. Bij spreidingslengtes kleiner dan de cel (bijv. een kleiige deklaag) is de
  oplossing rond de watergang grof. De celgrootte-correctie compenseert de uitwisseling, maar
  niet het verloop binnen de cel. De app waarschuwt als $\lambda < \Delta x$.
- **Rand**: een vaste rand houdt de stijghoogte in alle lagen vast op het opgegeven peil; een
  gesloten rand is een waterscheiding; een doorlopende rand neemt aan dat de omgeving
  ongestoord op het randpeil staat (geen andere watergangen of onttrekkingen buiten het
  model). Leg de rand ver genoeg van de opzet, of controleer de invloed door de randtypen te
  vergelijken. Met een kwel-onderrand is het vaak logisch om alleen het
  diepste pakket aan de rand vast te houden (rand per laag).
- **Putten**: met meebewegende $T$ wordt een sterk verlaagde freatische laag dunner en de
  verlaging groter; cellen die droogvallen meldt de app. Met vaste $T$ waarschuwt de app bij
  een verlaging van meer dan ~25 % van de dikte. De celstijghoogte bij een put is een
  celgemiddelde.
- **Kwel-onderrand**: $\varphi$ is vast en gelijk in referentie en opzet.
- **Watergangen** volgen de cellen; kruisingen en bochten zijn gewoon cellen. De
  conductance per cel hangt niet af van de richting van de watergang in de cel.
- **Maaivelddrainage** met weerstand $w_\text{mv}$ voorkomt grondwater ver boven maaiveld
  (oppervlakkige afvoer). Het is een overloop, geen fysisch drainsysteem.

## Referenties

- De Lange, W.J. (1999). A Cauchy boundary condition for the lumped interaction between an
  arbitrary number of surface waters and a regional aquifer. *Journal of Hydrology* 226,
  250–261.
- Harbaugh, A.W. (2005). *MODFLOW-2005, The U.S. Geological Survey modular ground-water
  model — the Ground-Water Flow Process*. USGS Techniques and Methods 6-A16 (RIV- en
  DRN-pakket).
- Ernst, L.F. (1962). *Grondwaterstromingen in de verzadigde zone en hun berekening bij
  aanwezigheid van horizontale evenwijdige open leidingen*. Verslagen van Landbouwkundige
  Onderzoekingen 67.15, Pudoc, Wageningen.
- De Glee, G.J. (1930). *Over grondwaterstroomingen bij wateronttrekking door middel van
  putten*. Proefschrift, Technische Hoogeschool Delft.
- Bruggeman, G.A. (1999). *Analytical Solutions of Geohydrological Problems*. Elsevier.
